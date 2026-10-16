"""Contract tests for ``run-session.sh``'s network-setup step.

On 2026-08-14 the ``test-health`` session died two seconds in:

    remote: Internal Server Error
    fatal: unable to access 'https://github.com/Glad-Labs/poindexter.git/':
           The requested URL returned error: 500

A transient GitHub 5xx on ``git fetch origin``. Under ``set -euo pipefail`` that
aborted the whole run with git's exit 128, and because a systemd unit's state is
a **latch** rather than an event, the unit stayed ``failed`` until the next day's
timer fire — 24h of red on the Grafana panel for a blip that cleared in seconds.

Worse, the run left **no log file**: the ``>>"$LOG"`` redirect used to start at
the ``worktree add`` one line below the fetch, so the only trace was journald.

These tests pin both halves: the bounded retry, and the log-before-first-git-call
ordering.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# A fetch failure that looks exactly like the 2026-08-14 outage. Fails until
# FAKE_GIT_SUCCEED_AFTER attempts have been consumed, then starts succeeding.
# Every non-fetch git subcommand (worktree prune/add/...) is a no-op success.
_FAKE_GIT = """#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "fetch" ]; then
    n=$(cat "$FAKE_GIT_STATE" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$FAKE_GIT_STATE"
    if [ "$n" -gt "${FAKE_GIT_SUCCEED_AFTER:-99}" ]; then exit 0; fi
    echo "remote: Internal Server Error" >&2
    echo "fatal: unable to access 'https://github.com/Glad-Labs/poindexter.git/': The requested URL returned error: 500" >&2
    exit 128
  fi
done
exit 0
"""


def _script() -> Path:
    root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "run-session.sh").exists()
    )
    return root / "scripts" / "linux" / "run-session.sh"


class _Run:
    """Result of driving run-session.sh against the fake git."""

    def __init__(self, proc: subprocess.CompletedProcess, attempts: int, log: str):
        self.proc, self.attempts, self.log = proc, attempts, log


def _run_session(tmp_path: Path, *, succeed_after: int, attempts: int = 3) -> _Run:
    bin_dir, home, repo = tmp_path / "bin", tmp_path / "home", tmp_path / "repo"
    for d in (bin_dir, home, repo):
        d.mkdir(parents=True, exist_ok=True)

    fake_git = bin_dir / "git"
    fake_git.write_text(_FAKE_GIT, encoding="utf-8")
    fake_git.chmod(0o755)
    counter = tmp_path / "attempts"

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "POINDEXTER_REPO": str(repo),
        "FAKE_GIT_STATE": str(counter),
        "FAKE_GIT_SUCCEED_AFTER": str(succeed_after),
        "OPS_GIT_FETCH_ATTEMPTS": str(attempts),
        "OPS_GIT_FETCH_RETRY_SECONDS": "0",   # keep the test instant
        # These tests pin the fetch contract; the boot readiness gate
        # (stack#3033) has its own suite in test_run_session_ready_gate.py.
        "OPS_READY_GATE": "0",
    }
    # test-health is one of the four worktree sessions, so it takes the fetch path.
    proc = subprocess.run(
        ["bash", str(_script()), "test-health"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    logs = sorted((home / ".poindexter" / "logs" / "claude-sessions").glob("*.log"))
    return _Run(
        proc,
        int(counter.read_text().strip()) if counter.exists() else 0,
        logs[0].read_text(encoding="utf-8") if logs else "",
    )


def test_transient_fetch_failure_is_retried_not_fatal(tmp_path):
    """Two 500s then a success must carry the run past setup — the 08-14 case."""
    run = _run_session(tmp_path, succeed_after=2)
    assert run.attempts == 3, "should have retried past the two failures"
    assert "aborting" not in run.log, f"recovered fetch must not abort:\n{run.log}"


def test_fetch_failure_leaves_a_log_file(tmp_path):
    """The regression that hid the outage: a setup failure wrote no log at all."""
    run = _run_session(tmp_path, succeed_after=99)
    assert run.log, "a failed fetch must still leave a session log, not only journald"
    # and the operator must be able to see *why* from that file alone
    assert "Internal Server Error" in run.log
    assert "git fetch origin failed after 3 attempt(s) — aborting" in run.log


def test_exhausted_retries_abort_the_run(tmp_path):
    """Retry is bounded — a real outage must still fail, not hang or run on."""
    run = _run_session(tmp_path, succeed_after=99)
    assert run.proc.returncode != 0
    assert run.attempts == 3


def test_attempt_count_is_env_tunable(tmp_path):
    """OPS_GIT_FETCH_ATTEMPTS follows the OPS_* host-knob convention."""
    run = _run_session(tmp_path, succeed_after=99, attempts=5)
    assert run.attempts == 5
