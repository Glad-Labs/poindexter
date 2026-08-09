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
MODEL_TRIAGE = os.environ.get("OPS_OLLAMA_MODEL_TRIAGE", "llama3.2:3b")
MODEL_TESTFIX = os.environ.get("OPS_OLLAMA_MODEL_TESTFIX", "qwen2.5-coder:7b")

_LOG_DIR = Path.home() / ".poindexter" / "logs" / "claude-sessions"


class OllamaUnavailable(RuntimeError):
    """Raised when the local Ollama endpoint cannot be reached."""


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
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    body: dict = {"model": model, "messages": messages, "stream": False}
    if as_json:
        body["format"] = "json"
    try:
        resp = httpx.post(f"{OPS_OLLAMA_URL}/api/chat", json=body, timeout=timeout)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise OllamaUnavailable(f"{OPS_OLLAMA_URL}: {exc}") from exc
    return parse_ollama_content(resp.json())


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


def get_logger(name: str) -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    handler = logging.FileHandler(_LOG_DIR / f"{name}-{ts}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def asyncio_run(coro):
    return asyncio.run(coro)
