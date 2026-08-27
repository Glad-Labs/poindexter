"""Shared helpers for the deterministic + local-LLM ops sessions.

Standalone by design: needs only Python + asyncpg + httpx + the installed
``brain`` package (for DB-URL resolution and operator notification). No
FastAPI app / SiteConfig / DI bootstrap — these run as short cron scripts.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import subprocess
import time
import tomllib
from pathlib import Path

import asyncpg
import httpx

OPS_OLLAMA_URL = os.environ.get("OPS_OLLAMA_URL", "http://localhost:11434")

# Both defaults must be OSI-permissive: `scripts/ops_sessions/` is NOT in the
# strip list in `scripts/sync-to-github.sh`, so whatever is pinned here is what
# the open-source product hands a fresh install. `llama3.2:3b` was the last
# non-permissive default in the tree (2026-08-27 license audit) — the Llama 3.2
# Community License is not OSI-approved, carries an acceptable-use policy and a
# 700M-MAU ceiling, and the upstream HF repo is `gated: manual`, so a downstream
# user could not even fetch the weights the default names. Granite 4.2 and
# Qwen2.5-Coder are both Apache-2.0. Keep it that way: check the model's license
# layer (`/v2/library/<m>/blobs/<license-digest>`) before changing a pin.
MODEL_TRIAGE = os.environ.get("OPS_OLLAMA_MODEL_TRIAGE", "granite4.2:3b")
MODEL_TESTFIX = os.environ.get("OPS_OLLAMA_MODEL_TESTFIX", "qwen2.5-coder:7b")

# Every model pin the session fleet uses, keyed by its env knob. The single
# source the pin-check session and any future preflight consume — a pin added
# above but not here is invisible to both, so add them as a pair (pinned by
# test_ops_common.py::test_model_pins_registry_covers_every_pin).
MODEL_PINS: dict[str, str] = {
    "OPS_OLLAMA_MODEL_TRIAGE": MODEL_TRIAGE,
    "OPS_OLLAMA_MODEL_TESTFIX": MODEL_TESTFIX,
}

_LOG_DIR = Path.home() / ".poindexter" / "logs" / "claude-sessions"


class OllamaUnavailable(RuntimeError):
    """Raised when the local Ollama endpoint cannot be USED.

    Covers both "nothing is listening" (connect/timeout) and "the daemon
    answered, but not usefully" — most importantly a 404 for a model that was
    never pulled. Callers treat this as "the LLM half of this session cannot
    run", notify the operator, and exit non-zero.

    The distinction matters because sessions only catch THIS type. A raw
    ``httpx.HTTPStatusError`` escaping to the top killed test-health with an
    uncaught traceback on 2026-08-07/08/09 — silently, because the session's
    own ``notify_fail`` path never ran. The exception nobody catches is the
    one that pages nobody.
    """


def bootstrap_value(key: str) -> str:
    path = Path.home() / ".poindexter" / "bootstrap.toml"
    if not path.exists():
        return ""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(data.get(key, "") or "")


def db_url() -> str:
    from brain.bootstrap import resolve_database_url

    url = resolve_database_url()
    if not url:
        notify_fail(
            "Ops session cannot start",
            "No database URL resolved (bootstrap.toml / DATABASE_URL).",
            "ops_sessions",
        )
        raise SystemExit(2)
    return url


async def fetch_all(sql: str, *args) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(db_url())
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def parse_ollama_content(payload: dict) -> str:
    return payload["message"]["content"]


def ollama_chat(
    user: str,
    *,
    model: str,
    system: str | None = None,
    as_json: bool = False,
    timeout: float = 120.0,
    temperature: float = 0.0,
) -> str:
    """Chat once against ``OPS_OLLAMA_URL``, greedily by default.

    ``temperature`` is sent EXPLICITLY rather than left to the model, because
    otherwise every session's determinism is a property of whichever pin
    happens to be set. A model's baked-in sampling params vary wildly:
    ``llama3.2:3b`` ships none (so Ollama's 0.8 applied), while
    ``granite4.2:3b`` bakes in ``temperature 1 / top_p 0.95``. Absent this
    kwarg the 2026-08-27 triage-pin swap would have moved alert classification
    to full-entropy sampling as a side effect: in the bake-off that preceded
    the swap, Granite was self-consistent on only 4 of 8 real alerts, returning
    different verdicts for the same input across runs — in a session whose
    output is *filing GitHub issues*. These are classification and code-repair
    tasks with one right answer, so greedy decode is the correct default; pass
    a non-zero value only where sampling is actually wanted.

    ``think: false`` is sent for the same reason — to stop behaviour depending
    on the pin. ``granite4.2:3b`` is a *thinking* model, and left to reason it
    spends the bulk of its budget in ``message.thinking`` before answering:
    41s for a one-key JSON reply, versus 8s with thinking off. The far worse
    failure mode is the one already documented for ``ops_triage_writer_model``
    — a thinking model that exhausts its budget mid-trace returns an EMPTY
    ``message.content``, which ``parse_ollama_content`` hands back as ``""``
    and the caller then rejects as unparseable. Sessions here want the answer,
    never the trace. Harmless on non-thinking models (verified against
    ``qwen2.5-coder:7b``) and on any Ollama that ignores the field.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature},
    }
    if as_json:
        body["format"] = "json"
    try:
        resp = httpx.post(f"{OPS_OLLAMA_URL}/api/chat", json=body, timeout=timeout)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise OllamaUnavailable(f"{OPS_OLLAMA_URL}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        # A reachable daemon that refuses the request is still "unusable" from
        # the caller's point of view, and MUST surface as OllamaUnavailable —
        # that is the only exception sessions catch, and catching it is what
        # triggers notify_fail. Letting HTTPStatusError through is what made
        # the test-health failure silent for three days.
        #
        # 404 is overwhelmingly "model was never pulled" (Ollama returns it for
        # an unknown model on /api/chat), so name the remedy instead of echoing
        # a bare status line — the operator's next action is `ollama pull`.
        detail = f"HTTP {exc.response.status_code}"
        if exc.response.status_code == 404:
            detail = (
                f"model {model!r} is not present on {OPS_OLLAMA_URL} "
                f"(Ollama 404s an unknown model). Pull it with "
                f"`ollama pull {model}`, or point the session at a model you "
                f"already have via the matching OPS_OLLAMA_MODEL_* env var."
            )
        raise OllamaUnavailable(f"{OPS_OLLAMA_URL}: {detail}") from exc
    return parse_ollama_content(resp.json())


def preflight_model_pins(*models: str, timeout: float = 10.0) -> None:
    """Verify every model pin exists on ``OPS_OLLAMA_URL`` before doing work.

    One ``GET /api/tags`` + a set difference (stack#3163). The alternative
    detector is the session 404ing minutes into its 03:00 run — a full day
    per iteration — and before #3154 that failure was silent too: the
    testfix pin sat missing for over two weeks after the Pop!_OS fleet
    re-pull skipped the one model no interactive workflow uses.

    Raises :class:`OllamaUnavailable` naming every missing pin and its
    ``ollama pull`` remedy. Connect/HTTP failures classify the same way —
    sessions catch exactly this type, and catching it is what fires
    ``notify_fail``. Checks the endpoint the session will actually call
    (there are two Ollama instances on the operator box; presence on the
    *other* one doesn't help).
    """
    try:
        resp = httpx.get(f"{OPS_OLLAMA_URL}/api/tags", timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise OllamaUnavailable(f"{OPS_OLLAMA_URL}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise OllamaUnavailable(
            f"{OPS_OLLAMA_URL}: /api/tags answered HTTP {exc.response.status_code}"
        ) from exc

    present = {str(m.get("name", "")) for m in payload.get("models", [])}

    def _norm(pin: str) -> str:
        # Ollama registers an untagged pull as ``<name>:latest``.
        return pin if ":" in pin else f"{pin}:latest"

    missing = [m for m in models if _norm(m) not in present]
    if missing:
        pulls = "; ".join(f"`ollama pull {m}`" for m in missing)
        raise OllamaUnavailable(
            f"model pin(s) not present on {OPS_OLLAMA_URL}: "
            f"{', '.join(repr(m) for m in missing)}. Pull with {pulls}, or "
            "repoint the matching OPS_OLLAMA_MODEL_* env var at a model you "
            "already have."
        )


def run(cmd: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    logging.getLogger("ops").info("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        logging.getLogger("ops").warning("rc=%s stderr=%s", proc.returncode, proc.stderr[:500])
    return proc


def gh(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run ``gh``. ALWAYS pass ``cwd`` for repo-context subcommands (``pr create``).

    ``run-session.sh`` invokes sessions with the process CWD set to the *shared*
    checkout's package dir (that's where the poetry env lives), while the session
    itself commits inside an isolated worktree. A bare ``gh pr create`` therefore
    infers its head branch from the shared checkout — which is on ``main`` — and
    dies with ``must be on a branch named differently than "main"``.
    """
    return run(["gh", *args], cwd=cwd)


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd)


def current_branch(cwd: str) -> str:
    proc = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return proc.stdout.strip() if proc.returncode == 0 else ""


_PR_FIELDS = "number,state,mergeable,mergeStateStatus,url,autoMergeRequest"


def pr_status(repo: str, branch: str, *, cwd: str | None = None) -> dict:
    """Return the PR whose head is ``branch`` as a dict, ``{}`` if there is none.

    Parsed with ``json.loads`` rather than piped through ``jq``: a shape change
    then surfaces as a Python error instead of an empty string that every
    caller reads as "no PR".
    """
    proc = gh("pr", "view", branch, "--repo", repo, "--json", _PR_FIELDS, cwd=cwd)
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        # Every caller reads {} as "no such PR", which for a *malformed* reply
        # is a different and wrong conclusion — say so rather than let a `gh`
        # output change read as an absent PR.
        logging.getLogger("ops").error(
            "unparseable `gh pr view %s` output: %r", branch, proc.stdout[:300],
        )
        return {}
    return data


def wait_for_merge(
    repo: str,
    branch: str,
    *,
    cwd: str,
    budget_seconds: float,
    poll_seconds: float,
    log: logging.Logger,
) -> str:
    """Poll ``branch``'s PR until it resolves. Returns its terminal-ish state.

    One of ``MERGED`` / ``CLOSED`` / ``CONFLICTING`` / ``PENDING`` / ``GONE``.
    ``PENDING`` means the budget ran out with the PR still clean — a success
    for an auto-merge-armed PR, which lands on its own once CI finishes.

    Only an explicit ``CONFLICTING`` ends the wait early. Right after a push
    GitHub reports ``mergeable: UNKNOWN`` while it recomputes the merge base,
    and treating that as either answer would be wrong.
    """
    deadline = time.monotonic() + budget_seconds
    while True:
        status = pr_status(repo, branch, cwd=cwd)
        if not status:
            log.error("no PR found for head %s while waiting to merge", branch)
            return "GONE"
        state = str(status.get("state") or "")
        if state in ("MERGED", "CLOSED"):
            log.info("PR for %s is %s", branch, state)
            return state
        if str(status.get("mergeable") or "") == "CONFLICTING":
            log.info("PR for %s is CONFLICTING (main moved under it)", branch)
            return "CONFLICTING"
        if time.monotonic() + poll_seconds >= deadline:
            log.info(
                "merge wait budget exhausted for %s (mergeable=%s, state=%s) — "
                "leaving it armed",
                branch, status.get("mergeable"), status.get("mergeStateStatus"),
            )
            return "PENDING"
        time.sleep(poll_seconds)


def commit_and_open_pr(
    *,
    cwd: str,
    repo: str,
    paths: list[str],
    message: str,
    title: str,
    body: str,
    log: logging.Logger,
    source: str,
    base: str = "main",
    auto_merge: bool = False,
    force_push: bool = False,
) -> str | None:
    """Stage → commit → push → open a PR from the worktree at ``cwd``.

    Returns the PR URL, or ``None`` after notifying the operator if any step
    failed. Every step's return code is checked: the sessions that commit run
    unattended at 02:00-05:00, so an unchecked ``rc`` is a change that quietly
    never lands (stack#2408 was the same bug one step earlier in this chain).

    ``auto_merge`` arms GitHub auto-merge (squash) so the PR lands the moment
    required CI goes green, matching what ``sync-claude-md.yml`` already does
    for the repo-derived half of CLAUDE.md. ``force_push`` re-pushes an
    existing session branch with ``--force-with-lease`` — used when a caller
    regenerates its output on top of a main that moved, in which case
    ``gh pr create`` finds the PR already open and the existing one is reused.
    """

    def _fail(step: str, proc: subprocess.CompletedProcess) -> None:
        detail = (proc.stderr or proc.stdout or "no output captured").strip()[:1500]
        log.error("%s failed (rc=%s): %s", step, proc.returncode, detail)
        notify_fail(
            f"{source}: {step} failed — no PR opened",
            f"`{step}` exited {proc.returncode} in {cwd}. The session's changes were "
            f"NOT proposed; they are stranded on branch `{branch or '?'}`.\n{detail}",
            source,
        )

    branch = current_branch(cwd)
    if branch in ("", "main", "HEAD"):
        # Guard the exact symptom that hid this for six weeks: if the session is
        # somehow not on its own worktree branch, say so instead of asking gh.
        log.error("refusing to open a PR from branch %r in %s", branch, cwd)
        notify_fail(
            f"{source}: not on a session branch — no PR opened",
            f"HEAD in {cwd} resolved to {branch or '<unresolvable>'}; expected the "
            f"session's `auto/*` worktree branch. Refusing to commit or push.",
            source,
        )
        return None

    # --force-with-lease, never bare --force: on a retry we are the only writer
    # of this branch, so the lease holds; if anything else moved it, failing is
    # the right outcome.
    lease = ("--force-with-lease",) if force_push else ()
    push = ("push", *lease, "-u", "origin", "HEAD")
    for step, args in (
        ("git add", ("add", *paths)),
        ("git commit", ("commit", "--no-verify", "-m", message)),
        ("git push", push),
    ):
        proc = git(*args, cwd=cwd)
        if proc.returncode != 0:
            _fail(step, proc)
            return None

    # --head is explicit rather than inferred: with it, `gh` needs no local git
    # context at all, so the PR can't be misaddressed by whatever CWD we inherit.
    proc = gh(
        "pr", "create", "--repo", repo, "--base", base, "--head", branch,
        "--title", title, "--body", body, cwd=cwd,
    )
    if proc.returncode == 0:
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else f"({branch})"
        log.info("opened PR %s", url)
    else:
        # A re-push onto an already-proposed branch is the expected path for
        # regenerating callers, not a failure — but only when a PR really is
        # open for this head. Anything else is still a hard fail.
        existing = pr_status(repo, branch, cwd=cwd)
        if not existing or str(existing.get("state") or "") != "OPEN":
            _fail("gh pr create", proc)
            return None
        url = str(existing.get("url") or f"({branch})")
        log.info("PR %s already open for %s — pushed updated commit", url, branch)

    if auto_merge:
        _arm_auto_merge(repo, branch, url, cwd=cwd, log=log, source=source)
    return url


def _arm_auto_merge(
    repo: str,
    branch: str,
    url: str,
    *,
    cwd: str,
    log: logging.Logger,
    source: str,
) -> None:
    """Enable squash auto-merge on ``branch``'s PR; notify if it won't arm.

    A failure here is a degradation, not a lost change: the PR is open and
    correct, it just now needs a human to press merge. Said out loud rather
    than swallowed — an unwatched PR waiting forever is exactly the silent
    stall of stack#2809.
    """
    proc = gh("pr", "merge", "--auto", "--squash", branch, "--repo", repo, cwd=cwd)
    if proc.returncode == 0:
        log.info("auto-merge (squash) armed on %s", url)
        return
    # `gh` also exits non-zero when the PR was already mergeable and merged on
    # the spot, and when auto-merge was already armed by an earlier attempt.
    # Re-read the truth instead of pattern-matching stderr.
    status = pr_status(repo, branch, cwd=cwd)
    if str(status.get("state") or "") == "MERGED":
        log.info("PR %s merged immediately (no wait needed)", url)
        return
    if status.get("autoMergeRequest"):
        log.info("auto-merge already armed on %s", url)
        return
    detail = (proc.stderr or proc.stdout or "no output captured").strip()[:1500]
    log.error("gh pr merge --auto failed (rc=%s): %s", proc.returncode, detail)
    notify_fail(
        f"{source}: auto-merge not armed — PR needs a manual merge",
        f"{url} is open and correct, but `gh pr merge --auto --squash` exited "
        f"{proc.returncode}, so it will sit unmerged until someone presses the "
        f"button.\n{detail}",
        source,
    )


def notify_fail(title: str, detail: str, source: str) -> None:
    try:
        from brain.operator_notifier import notify_operator

        notify_operator(title, detail, source=source, severity="warning")
    except Exception:  # noqa: BLE001 — notification must never mask the real error
        logging.getLogger("ops").warning("notify_operator failed: %s | %s", title, detail)


def _resolve_log_dir() -> Path:
    """Directory session logs land in. ``OPS_LOG_DIR`` overrides the default.

    Read per call — unlike the import-time ``OPS_*`` model knobs — so the unit
    tests' autouse fixture (tests/unit/scripts/conftest.py) can redirect
    modules that were already imported, however they bound ``get_logger``.
    That redirect is load-bearing: a file in the real dir reads as "a session
    ran" (run-session.sh writes the same ``<name>-<stamp>.log`` shape), and a
    pytest-written test-health log was mistaken for a real session fire during
    an unrelated investigation on 2026-08-15.
    """
    override = os.environ.get("OPS_LOG_DIR", "").strip()
    return Path(override) if override else _LOG_DIR


# Handlers get_logger attached per logger name, so a repeat call replaces its
# own handlers instead of stacking a second file+stream pair (and leaves any
# externally-attached handler — pytest's caplog etc. — alone).
_SESSION_HANDLERS: dict[str, list[logging.Handler]] = {}


def get_logger(name: str) -> logging.Logger:
    log_dir = _resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    # logging.getLogger(name) is process-global: without this sweep, a second
    # get_logger(name) in one process duplicates every record into every
    # earlier call's file. Prod fires one session per process so never saw it;
    # pytest drives several main()s per process and did.
    for stale in _SESSION_HANDLERS.pop(name, []):
        logger.removeHandler(stale)
        stale.close()
    handler = logging.FileHandler(log_dir / f"{name}-{ts}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    stream = logging.StreamHandler()
    logger.addHandler(handler)
    logger.addHandler(stream)
    _SESSION_HANDLERS[name] = [handler, stream]
    return logger


def asyncio_run(coro):
    return asyncio.run(coro)
