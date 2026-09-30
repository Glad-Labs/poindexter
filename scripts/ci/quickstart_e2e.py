#!/usr/bin/env python3
# mirror-tree-exempt: a live end-to-end driver (needs Docker, Ollama and a real stack), not a lint
"""Run the README quick start, literally, and prove it ends in a post.

A stranger's first hour with Poindexter is the ``## Quick start`` block in
README.md: clone, install, ``poindexter setup --auto``, pull models, start the
stack, queue a post. For months that block could not produce a post — the
stack it started had no dispatcher, ``setup --auto`` wrote the CLI into a
different database than the stack read, and the CLI needed an env var the
README never set. Every piece was reviewed; the sequence was never run.

This script runs the sequence. It reads the block out of the README of the
tree it is given (the public mirror's tree, built by the real sync filter in
.github/workflows/quickstart-e2e.yml) and executes it as ONE bash session, the
way a person pastes it, so a ``cd``, a ``source`` or a venv carries from one
line to the next. Exactly three substitutions, all printed as they happen:

* the ``git clone ... && cd <dir>`` line becomes ``cd <tree>`` — the tree
  under test IS the clone;
* each ``ollama pull <tag>`` becomes ``ollama cp <tiny> <tag>``: a hosted
  runner has no GPU and ~14 GB of disk, so every model the README names is
  served by one small CPU model under that exact name. Tags passed with
  ``--real-pull`` (embedding models, whose vector width the schema depends
  on) are pulled for real. A model the pipeline calls that the README does
  NOT list is therefore missing, and the run fails the way a stranger's would;
* the rows in ``STAND_IN_SETTINGS`` (today: ``min_curation_score=0``), written
  with the product's own ``poindexter settings set`` right before the
  ``poindexter tasks create`` line. The stand-in model also judges its own
  draft, so the quality score is noise: 94 in one run of this quick start and
  54 in another, and the post-pipeline curation step auto-rejects anything
  under its 75 bar within a second of the pipeline queueing it for approval.
  That verdict is about the model, and this job answers whether the machine
  runs (dispatch, graph, QA, approval queue), so the bar is taken out of the
  measurement instead of the assertion being loosened.

Then it waits for the queued task and requires ``awaiting_approval`` — the
state the README promises — checks every ``http://localhost:<port>`` link the
README tells the reader to open, runs the README's own verification command
(``poindexter tasks list --status awaiting_approval``) to see the task there,
and finishes the README's loop with ``poindexter tasks approve <prefix>``, after
which a ``posts`` row must exist for the task: an approve that reports success
but staged nothing (the niche allowlist refuses a task with no niche) is the
first-hour failure this job exists to catch, and the task's own status cannot
show it. The README's next command, ``poindexter tasks publish <prefix>``, is
run too, and the post is then read back the way a reader would get it, over HTTP
from the worker's ``/site/`` (the post's JSON, the index that lists it, the
viewer page and its assets, the viewer's security headers); a storage failure
in a pipeline container's log fails the run even when the reads pass. It also fails when a pipeline container logged Ollama refusing a model as not
pulled — a model the README's pull list is missing. ``--timeout-min`` bounds the wait; the pipeline on a 2-4 core CPU with
a sub-1B model takes a while, which is the point of running it weekly rather
than per commit.

Stdlib only: it runs on the runner's Python before anything is installed.

    python scripts/ci/quickstart_e2e.py --tree /tmp/poindexter \\
        --tiny-model qwen2.5:0.5b --real-pull nomic-embed-text

# scan-floor-exempt: a test driver, not a tree-scanning lint -- it executes a README and a live stack
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

QUICK_START_HEADING = "## Quick start"
_FENCE_OPEN = re.compile(r"^```(?:bash|sh|shell)\s*$")
_CLONE = re.compile(r"^git clone\s+(\S+)(?:\s+(\S+))?\s*&&\s*cd\s+(\S+)\s*$")
_PULL = re.compile(r"\bollama\s+pull\s+([A-Za-z0-9._:/-]+)")
_CREATE = re.compile(r"^poindexter\s+tasks\s+create\b")
_CREATED = re.compile(r"Created:\s+([0-9a-fA-F-]{8,})")
_LOCAL_LINK = re.compile(r"\((http://localhost:(\d+)[^)\s]*)\)")
# Ollama's answer for a model it does not have. The pipeline does not pull on
# demand, so every hit is a model the README's pull list is missing.
_MODEL_NOT_FOUND = re.compile(r"model ['\"\\]*([A-Za-z0-9._:/-]+?)['\"\\]* not found, try pulling it first")
# Containers whose logs carry the pipeline's model calls.
PIPELINE_CONTAINERS = ("poindexter-prefect-worker", "poindexter-worker")
# Task states that end a wait. awaiting_approval is the README's promise; the
# rest are a pipeline that stopped short of it.
SUCCESS = "awaiting_approval"
# The stand-in accommodations, written as app_settings rows (DB-first config,
# through `poindexter settings set`) right before the README queues its task.
# A CLOSED list: every entry is a place this job stops measuring the README's
# own promise, so adding one takes a reason in the module docstring and an edit
# to the test that pins the list. `settings set` refuses a key that is not
# already a row, so a renamed setting fails the run loudly.
STAND_IN_SETTINGS = {"min_curation_score": "0"}
# Where `poindexter tasks approve` may leave a task: approved, or already
# picked up by the publisher.
APPROVED_STATES = frozenset({"approved", "published"})
# The worker's local site (`storage_provider=local`, the fresh-install default),
# where the README says to read the published post.
SITE_URL = "http://localhost:8002/site"
# What the storage layer logs when it cannot keep or find a published object.
STORAGE_FAILURES = ("[STORAGE] No object-store credentials", "[STORAGE] Local write failed")
# `rejected_retry` is deliberately absent: it is a regeneration in flight (the
# task is re-claimed and re-run), and a genuinely stuck one is the stall rule's.
TERMINAL_FAILURES = frozenset(
    {"failed", "rejected", "rejected_final", "cancelled", "dismissed", "expired"}
)
# How long the task row may be absent before the id the CLI printed is judged
# wrong. The POST inserts the row before it answers, so absence is not a race.
ROW_GRACE_S = 120.0


@dataclass
class QuickStart:
    """The quick-start block, parsed."""

    lines: list[str]
    pulled_models: list[str] = field(default_factory=list)
    clone_line: str | None = None
    create_line: str | None = None


def extract_quick_start(readme: str) -> QuickStart:
    """The first ```bash fence under ``## Quick start``.

    Raises ValueError when the section or its fence is missing, or when the
    fence lacks an anchor this driver acts on (a clone line, at least one
    ``ollama pull``, the ``poindexter tasks create`` line) — a restructured
    README must fail here, loudly, rather than run something that is no longer
    the quick start.
    """
    lines = readme.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == QUICK_START_HEADING)
    except StopIteration as exc:
        raise ValueError(f"README has no {QUICK_START_HEADING!r} section") from exc
    body: list[str] | None = None
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if body is None:
            if _FENCE_OPEN.match(ln.strip()):
                body = []
            continue
        if ln.strip() == "```":
            break
        body.append(ln)
    if not body:
        raise ValueError(f"no ```bash block under {QUICK_START_HEADING!r}")
    qs = QuickStart(lines=body)
    for ln in body:
        stripped = ln.strip()
        if _CLONE.match(stripped):
            qs.clone_line = stripped
        if _CREATE.match(stripped):
            qs.create_line = stripped
        qs.pulled_models.extend(_PULL.findall(stripped.split("#", 1)[0]))
    if qs.clone_line is None:
        raise ValueError("quick start has no `git clone <url> && cd <dir>` line")
    if not qs.pulled_models:
        raise ValueError("quick start pulls no models (`ollama pull <tag>`)")
    if qs.create_line is None:
        raise ValueError("quick start has no `poindexter tasks create ...` line")
    return qs


def build_script(
    qs: QuickStart, *, tree: Path, tiny_model: str, real_pulls: set[str], handoff: Path,
    settings: dict[str, str] | None = None,
) -> str:
    """The bash session to run: the block, with the three substitutions applied.

    Ends by writing the resolved ``poindexter`` path (and the created task id,
    captured from the block's own output) to ``handoff`` so the checks after
    the block run the same CLI the block installed.
    """
    settings = STAND_IN_SETTINGS if settings is None else settings
    out = [
        "set -euo pipefail",
        f"exec > >(tee -a {shlex.quote(str(handoff.with_suffix('.log')))}) 2>&1",
    ]
    for raw in qs.lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            out.append(raw)
            continue
        if stripped == qs.clone_line:
            out.append(f"echo '[quickstart-e2e] clone -> cd {tree}'")
            out.append(f"cd {shlex.quote(str(tree))}")
            continue
        models = _PULL.findall(stripped.split("#", 1)[0])
        if models:
            out.append(f"echo '[quickstart-e2e] models -> {' '.join(models)}'")
            for tag in models:
                if tag in real_pulls:
                    out.append(f"ollama pull {shlex.quote(tag)}")
                else:
                    out.append(f"ollama cp {shlex.quote(tiny_model)} {shlex.quote(tag)}")
            continue
        if stripped == qs.create_line and settings:
            applied = " ".join(f"{k}={v}" for k, v in settings.items())
            out.append(f"echo '[quickstart-e2e] stand-in model -> app_settings {applied}'")
            for key, value in settings.items():
                out.append(f"poindexter settings set {shlex.quote(key)} {shlex.quote(value)}")
        out.append(raw)
    out.append(f"echo \"PDX_BIN=$(command -v poindexter)\" >> {shlex.quote(str(handoff))}")
    return "\n".join(out) + "\n"


def _psql(sql: str, **params: str) -> str:
    """Run one query in the stack's Postgres (local socket, trusted).

    Values travel as psql variables (``:'name'`` in the query), never
    formatted into the SQL; the query goes over stdin because ``psql -c``
    does not interpolate variables.
    """
    argv = ["docker", "exec", "-i", "poindexter-postgres-local", "psql", "-X", "-q", "-A", "-t",
            "-U", "poindexter", "-d", "poindexter_brain", "-v", "ON_ERROR_STOP=1"]
    for name, value in params.items():
        argv += ["-v", f"{name}={value}"]
    proc = subprocess.run(
        [*argv, "-f", "-"], input=sql.rstrip().rstrip(";") + ";\n",
        capture_output=True, text=True, timeout=60, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def wait_for_task(
    task_id: str,
    *,
    timeout_min: float,
    dispatch_min: float = 12.0,
    stall_min: float = 90.0,
    poll_s: float = 30.0,
    read_row=None,
    clock=time.monotonic,
    sleep=time.sleep,
) -> str:
    """Poll the task until it leaves the pipeline; return its final status.

    Fails fast rather than burning the whole budget: a task still ``pending``
    after ``dispatch_min`` was never dispatched (the Prefect cron fires every
    two minutes), and a row whose status/stage/percentage/last_progress_at has
    not moved for ``stall_min`` is stuck. Either returns a ``"never dispatched …"`` /
    ``"stalled …"`` reason instead of a status.

    The stall window is generous on purpose: on a 2-core CPU the video-director
    node alone ran 33 minutes without moving ``last_progress_at`` (2026-09-28),
    and a false stall verdict costs a 75-minute rerun.
    """
    # last_progress_at moves on every graph node, so a long QA phase whose
    # stage/percentage stay put is still visibly progressing.
    read_row = read_row or (lambda: _psql(
        "SELECT status || '|' || COALESCE(stage, '') || '|' || COALESCE(percentage, 0) "
        "|| '|' || COALESCE(last_progress_at::text, '') "
        "FROM pipeline_tasks WHERE task_id = :'task_id'",
        task_id=task_id,
    ))
    start = clock()
    deadline = start + timeout_min * 60
    last = None
    last_change = start
    while clock() < deadline:
        row = read_row()
        now = clock()
        if row != last:
            print(f"[quickstart-e2e] {time.strftime('%H:%M:%S')} task {task_id[:8]}: {row}", flush=True)
            last, last_change = row, now
        status = row.split("|", 1)[0] if row else ""
        if not row and now - start > ROW_GRACE_S:
            return (
                f"task {task_id} is not in pipeline_tasks — the id `tasks create` "
                "printed is not the row's task_id"
            )
        if status == SUCCESS or status in TERMINAL_FAILURES:
            return status
        if status == "pending" and now - start > dispatch_min * 60:
            return f"never dispatched: still pending after {dispatch_min:.0f} min"
        if now - last_change > stall_min * 60:
            return f"stalled: no change for {stall_min:.0f} min (last: {last})"
        sleep(poll_s)
    return f"timeout after {timeout_min:.0f} min (last: {last})"


def explain_task(task_id: str, *, query=None) -> str:
    """Why a task stopped short of ``awaiting_approval``, in the pipeline's words.

    The run that first failed on a curation reject printed only the status
    ``rejected``; the reason was two containers' logs away. The task row's
    ``error_message`` and its ``pipeline_gate_history`` rows (the auto-curator
    writes one with the score and the bar) say it directly. Never raises: a
    diagnosis that cannot be read is itself part of the diagnosis.
    """
    query = query or _psql
    parts = []
    try:
        row = query(
            "SELECT COALESCE(stage, '?') || ' ' || COALESCE(percentage, 0) || '%' || "
            "COALESCE(' - ' || NULLIF(error_message, ''), '') "
            "FROM pipeline_tasks WHERE task_id = :'task_id'",
            task_id=task_id,
        )
        if row:
            parts.append(f"last stage {row}")
        history = query(
            "SELECT gate_name || ' ' || event_kind || ': ' || COALESCE(feedback, '') "
            "FROM pipeline_gate_history WHERE task_id = :'task_id' ORDER BY created_at",
            task_id=task_id,
        )
        parts.extend(ln for ln in history.splitlines() if ln.strip())
    except RuntimeError as exc:
        parts.append(f"(could not read the task's history: {exc})")
    return "; ".join(parts)


def staged_posts(task_id: str, *, query=None) -> list[str]:
    """``slug (status)`` of each ``posts`` row staged from the task.

    ``posts.metadata->>'pipeline_task_id'`` is the seam from a post back to its
    source task. The row is what publishing, the site and the scheduler read, so
    its existence is the only proof that approving a task produced something
    publishable: the task itself reads ``approved`` whether or not staging
    worked. An unreadable database raises, since "could not look" must not pass
    as "found one".
    """
    query = query or _psql
    rows = query(
        "SELECT slug || ' (' || status || ')' FROM posts "
        "WHERE metadata->>'pipeline_task_id' = :'task_id' ORDER BY created_at",
        task_id=task_id,
    )
    return [ln for ln in rows.splitlines() if ln.strip()]


def judge_approve(
    task_id: str, *, exit_code: int, stderr: str, status_after: str, query=None,
) -> str | None:
    """The problem with ``poindexter tasks approve``'s outcome, or None if it is sound.

    Sound means: exit 0, the task left ``approved``/``published``, and a post was
    staged for it. The third is what an "approved" status alone cannot show, so
    an approve the niche allowlist neutered (no niche on the task) fails here
    with the warning the CLI printed rather than passing on the status.
    """
    prefix = task_id[:8]
    if exit_code != 0 or status_after not in APPROVED_STATES:
        return f"`poindexter tasks approve {prefix}` left the task {status_after!r} (exit {exit_code})"
    try:
        staged = staged_posts(task_id, query=query)
    except RuntimeError as exc:
        return f"could not look for the approved task's post: {exc}"
    if staged:
        return None
    said = next(
        (ln for ln in stderr.splitlines() if ln.startswith("Warning:")),
        "the CLI printed no warning",
    )
    return f"`poindexter tasks approve {prefix}` reported success but staged no post ({said})"


def published_post(task_id: str, *, query=None) -> tuple[str, str, str] | None:
    """``(slug, status, published_at)`` of the task's post, or None when it has none.

    ``published_at`` is empty when the column is NULL. An unreadable database
    raises, so "could not look" cannot pass as "no post".
    """
    query = query or _psql
    row = query(
        "SELECT slug || '|' || status || '|' || COALESCE(published_at::text, '') FROM posts "
        "WHERE metadata->>'pipeline_task_id' = :'task_id' ORDER BY created_at DESC LIMIT 1",
        task_id=task_id,
    )
    if not row:
        return None
    slug, status, published_at = row.split("|", 2)
    return slug, status, published_at


def judge_publish(
    task_id: str, *, exit_code: int, stderr: str, query=None,
) -> tuple[str | None, str | None]:
    """``(problem, slug)`` for ``poindexter tasks publish``.

    Sound means exit 0, the post ``published`` with a ``published_at``, and the
    task ``published`` to match. The slug comes back for the read-back, and is
    None whenever there is nothing to read.
    """
    prefix = task_id[:8]
    if exit_code != 0:
        tail = stderr.strip().splitlines()[-1:] or ["no error printed"]
        return f"`poindexter tasks publish {prefix}` exited {exit_code} ({tail[0]})", None
    query = query or _psql
    try:
        post = published_post(task_id, query=query)
        task_status = query(
            "SELECT status FROM pipeline_tasks WHERE task_id = :'task_id'", task_id=task_id,
        )
    except RuntimeError as exc:
        return f"could not read back the published post's rows: {exc}", None
    if post is None:
        return f"`poindexter tasks publish {prefix}` succeeded but the task has no post", None
    slug, status, published_at = post
    if status != "published" or not published_at:
        return (
            f"post {slug!r} is {status!r} with published_at={published_at or 'NULL'!r} "
            f"after `poindexter tasks publish {prefix}`",
            slug,
        )
    if task_status != "published":
        return f"post {slug!r} is published but its task reads {task_status!r}", slug
    return None, slug


def _fetch(url: str) -> tuple[int, dict[str, str], str]:
    """GET ``url`` -> ``(status, lower-cased headers, body)``; raises RuntimeError if unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # nosec B310 - a http://localhost URL this script builds
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        raise RuntimeError(f"{url}: {type(exc).__name__}: {exc}") from exc


def judge_site(slug: str, *, fetch=None, base: str = SITE_URL) -> list[str]:
    """Problems reading a published post back from the worker's local ``/site/``.

    What a reader's browser gets, over HTTP: the post's own JSON with a body, the
    index that lists it, the viewer page (with the security headers that make it
    safe to serve a pipeline-written post) at ``/site/`` and at the post's own
    route, and the viewer's script and stylesheet. It does not run the viewer in a
    browser.
    """
    fetch = fetch or _fetch
    problems: list[str] = []

    def get(path: str, *, want_type: str | None = None):
        url = f"{base}/{path}"
        try:
            status, headers, body = fetch(url)
        except RuntimeError as exc:
            problems.append(str(exc))
            return None
        if status != 200:
            problems.append(f"{url}: HTTP {status}")
            return None
        ctype = headers.get("content-type", "")
        if want_type and want_type not in ctype:
            problems.append(f"{url}: content-type {ctype!r}, wanted {want_type!r}")
            return None
        return headers, body

    def json_body(path: str):
        got = get(path, want_type="json")
        if got is None:
            return None
        try:
            return json.loads(got[1])
        except ValueError as exc:
            problems.append(f"{base}/{path}: not JSON ({exc})")
            return None

    post = json_body(f"static/posts/{slug}.json")
    if post is not None:
        if post.get("slug") != slug:
            problems.append(f"static/posts/{slug}.json carries slug {post.get('slug')!r}")
        if not str(post.get("title") or "").strip():
            problems.append(f"static/posts/{slug}.json has no title")
        if not str(post.get("content") or "").strip():
            problems.append(f"static/posts/{slug}.json has no content")

    index = json_body("static/posts/index.json")
    if index is not None:
        listed = [p.get("slug") for p in index.get("posts", []) if isinstance(p, dict)]
        if slug not in listed:
            problems.append(f"static/posts/index.json does not list {slug!r} (lists {listed})")

    for path in ("", f"posts/{slug}"):
        page = get(path, want_type="text/html")
        if page is None:
            continue
        headers, body = page
        if "script-src 'self'" not in headers.get("content-security-policy", ""):
            problems.append(f"{base}/{path}: no Content-Security-Policy restricting scripts to 'self'")
        if "_viewer/viewer.js" not in body:
            problems.append(f"{base}/{path}: the page does not load the viewer script")

    for asset in ("_viewer/viewer.js", "_viewer/viewer.css"):
        get(asset)
    return problems


def storage_failures(log_text: str) -> list[str]:
    """Log lines where the storage layer said it could not keep or find an object."""
    return [ln.strip() for ln in log_text.splitlines() if any(f in ln for f in STORAGE_FAILURES)]


def optional_models(readme: str) -> list[str]:
    """Tags in the README's "Optional" model table (the ones NOT in the pull line).

    A model the pipeline reaches only on a fallback path (the fallback critic,
    when the stand-in for the primary critic misbehaves on a tiny CPU model) is
    documented as optional; refusing it must not fail the run. Only a model
    the README does not mention at all is a gap in the quick start.
    """
    m = re.search(r"\*\*Optional[^\n]*\n\n((?:\|[^\n]*\n)+)", readme)
    if not m:
        return []
    rows = m.group(1).splitlines()[2:]  # header + separator
    tags = []
    for row in rows:
        cell = re.match(r"\|\s*`([^`]+)`", row)
        if cell:
            tags.append(cell.group(1))
    return tags


def missing_models(log_text: str) -> list[str]:
    """Model tags Ollama refused as not pulled, deduplicated, in first-seen order."""
    seen: dict[str, None] = {}
    for tag in _MODEL_NOT_FOUND.findall(log_text):
        seen.setdefault(tag, None)
    return list(seen)


def _container_logs(name: str) -> str:
    proc = subprocess.run(
        ["docker", "logs", name], capture_output=True, text=True, timeout=120, check=False,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def check_links(readme: str) -> list[str]:
    """GET every localhost link the README tells the reader to open."""
    problems = []
    for url, _port in sorted(set(_LOCAL_LINK.findall(readme))):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:  # nosec B310 - http://localhost:<port> links parsed from our own README
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except OSError as exc:
            problems.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        print(f"[quickstart-e2e] {url} -> HTTP {code}")
        if code >= 500:
            problems.append(f"{url}: HTTP {code}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tree", required=True, type=Path, help="the public tree (the 'clone')")
    ap.add_argument("--tiny-model", required=True, help="small Ollama model every README tag aliases")
    ap.add_argument("--real-pull", action="append", default=[], help="tag to pull for real (repeatable)")
    ap.add_argument("--timeout-min", type=float, default=150.0)
    args = ap.parse_args(argv)

    tree = args.tree.resolve()
    readme = (tree / "README.md").read_text(encoding="utf-8")
    qs = extract_quick_start(readme)
    print(f"[quickstart-e2e] README models: {qs.pulled_models}")

    handoff = Path(tempfile.mkdtemp(prefix="quickstart-e2e-")) / "handoff.env"
    script = build_script(
        qs, tree=tree, tiny_model=args.tiny_model, real_pulls=set(args.real_pull), handoff=handoff,
    )
    print("[quickstart-e2e] running the quick start:\n" + script, flush=True)
    proc = subprocess.run(["bash", "-c", script], cwd=tree, check=False)
    if proc.returncode != 0:
        print(f"::error::the README quick start failed (exit {proc.returncode})")
        return 1

    log = handoff.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    ids = _CREATED.findall(log)
    if not ids:
        print("::error::`poindexter tasks create` printed no 'Created: <id>' line")
        return 1
    task_id = ids[-1]
    handoff_vals = dict(
        ln.split("=", 1) for ln in handoff.read_text().splitlines() if "=" in ln
    )
    pdx = handoff_vals.get("PDX_BIN", "").strip() or "poindexter"

    status = wait_for_task(task_id, timeout_min=args.timeout_min)
    print(f"[quickstart-e2e] final status: {status}")
    why = explain_task(task_id) if status != SUCCESS else ""
    if why:
        print(f"[quickstart-e2e] why: {why}")

    problems = check_links(readme)

    listed = subprocess.run(
        [pdx, "tasks", "list", "--status", SUCCESS], cwd=tree,
        capture_output=True, text=True, check=False,
    )
    print(listed.stdout + listed.stderr)
    if status != SUCCESS:
        problems.append(
            f"task {task_id} ended {status!r}, not {SUCCESS!r}" + (f" ({why})" if why else "")
        )
    elif task_id[:8] not in listed.stdout:
        problems.append(
            f"`poindexter tasks list --status {SUCCESS}` does not list task {task_id[:8]}"
        )
    else:
        # The README's last instruction: approve it by the prefix `tasks list` shows.
        approved = subprocess.run(
            [pdx, "tasks", "approve", task_id[:8]], cwd=tree,
            capture_output=True, text=True, check=False,
        )
        print(approved.stdout + approved.stderr)
        after = _psql(
            "SELECT status FROM pipeline_tasks WHERE task_id = :'task_id'", task_id=task_id,
        )
        verdict = judge_approve(
            task_id, exit_code=approved.returncode, stderr=approved.stderr, status_after=after,
        )
        if verdict:
            problems.append(verdict)
        else:
            print(f"[quickstart-e2e] approve left the task {after!r} and staged a post")
            # The README's next command, then the post read back as a reader gets it.
            published = subprocess.run(
                [pdx, "tasks", "publish", task_id[:8]], cwd=tree,
                capture_output=True, text=True, check=False,
            )
            print(published.stdout + published.stderr)
            problem, slug = judge_publish(
                task_id, exit_code=published.returncode, stderr=published.stderr,
            )
            if problem:
                problems.append(problem)
            if slug:
                site_problems = judge_site(slug)
                problems.extend(site_problems)
                if not site_problems:
                    print(f"[quickstart-e2e] read {slug!r} back from {SITE_URL}/")

    pipeline_logs = "\n".join(_container_logs(c) for c in PIPELINE_CONTAINERS)
    for line in storage_failures(pipeline_logs)[:5]:
        problems.append(f"a pipeline container logged a storage failure: {line}")
    refused = missing_models(pipeline_logs)
    optional = set(optional_models(readme))
    unpulled = [t for t in refused if t not in optional]
    if unpulled:
        problems.append(
            f"the pipeline called models the README does not pull: {unpulled} "
            "(add them to the quick start's `ollama pull` line, or to its optional table)"
        )
    if [t for t in refused if t in optional]:
        print(
            "[quickstart-e2e] note: the pipeline reached optional model(s) "
            f"{[t for t in refused if t in optional]} on a fallback path — documented as "
            "optional, so not a failure"
        )

    try:
        usage = _psql(
            "SELECT json_agg(t) FROM (SELECT model, count(*) AS calls FROM cost_logs "
            "GROUP BY model ORDER BY 2 DESC) t"
        )
        print(f"[quickstart-e2e] models the pipeline called: {json.loads(usage or 'null')}")
    except (RuntimeError, ValueError) as exc:
        print(f"[quickstart-e2e] could not read cost_logs: {exc}")

    if problems:
        for p in problems:
            print(f"::error::{p}")
        return 1
    print(f"[quickstart-e2e] OK — task {task_id} reached {SUCCESS} via the README quick start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
