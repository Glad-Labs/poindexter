"""Contract tests for ``_common.commit_and_open_pr`` (stack#2809).

The four worktree sessions (claude-md-sync / doc-sync / codebase-audit /
test-health) all committed, pushed, then called ``gh pr create`` with **no
cwd** — so ``gh`` inferred its head branch from the shared checkout that
``run-session.sh`` leaves as the process CWD, not from the session's worktree.
Every night it died with ``must be on a branch named differently than "main"``
and every night the session logged success anyway. These tests pin both halves:
the cwd/--head plumbing, and the rc discipline.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import _common as c  # noqa: E402

WT = "/home/op/.poindexter/worktrees/claude-md-sync-2026-07-26-0230"
BRANCH = "auto/claude-md-sync-2026-07-26-0230"


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def calls(monkeypatch):
    """Record every argv `commit_and_open_pr` shells out, with its cwd."""
    recorded: list[tuple[list[str], str | None]] = []

    def fake_run(cmd, *, cwd=None):
        recorded.append((cmd, cwd))
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _done(0, stdout=f"{BRANCH}\n")
        if cmd[0] == "gh":
            return _done(0, stdout="https://github.com/Glad-Labs/poindexter/pull/9999\n")
        return _done(0)

    monkeypatch.setattr(c, "run", fake_run)
    return recorded


@pytest.fixture
def notify(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(c, "notify_fail", mock)
    return mock


def _open_pr(**overrides):
    kwargs = dict(
        cwd=WT,
        repo="Glad-Labs/poindexter",
        paths=["CLAUDE.md"],
        message="docs(CLAUDE.md): sync DB-derived counts (ops)",
        title="docs(CLAUDE.md): sync DB-derived counts (ops)",
        body="Automated DB-count refresh.",
        log=logging.getLogger("test-ops-pr"),
        source="claude_md_sync",
    )
    kwargs.update(overrides)
    return c.commit_and_open_pr(**kwargs)


class TestRunsAgainstTheWorktree:
    """The root cause: `gh` was reading the shared checkout instead of $WT."""

    def test_gh_pr_create_receives_the_worktree_cwd(self, calls, notify):
        assert _open_pr() is not None
        gh_call = next((cmd, cwd) for cmd, cwd in calls if cmd[0] == "gh")
        assert gh_call[1] == WT, "gh pr create must run inside the session worktree"

    def test_gh_pr_create_pins_head_branch_explicitly(self, calls, notify):
        _open_pr()
        argv = next(cmd for cmd, _ in calls if cmd[0] == "gh")
        assert "--head" in argv
        assert argv[argv.index("--head") + 1] == BRANCH

    def test_every_git_step_receives_the_worktree_cwd(self, calls, notify):
        _open_pr()
        assert [cwd for cmd, cwd in calls if cmd[0] == "git"] == [WT] * 4  # rev-parse, add, commit, push

    def test_returns_the_pr_url(self, calls, notify):
        assert _open_pr() == "https://github.com/Glad-Labs/poindexter/pull/9999"


class TestRefusesToActOffASessionBranch:
    @pytest.mark.parametrize("head", ["main", "HEAD", ""])
    def test_bad_head_notifies_and_commits_nothing(self, head, monkeypatch, notify):
        recorded: list[list[str]] = []

        def fake_run(cmd, *, cwd=None):
            recorded.append(cmd)
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return _done(0 if head else 1, stdout=f"{head}\n" if head else "")
            return _done(0)

        monkeypatch.setattr(c, "run", fake_run)

        assert _open_pr() is None
        notify.assert_called_once()
        assert "no PR opened" in notify.call_args[0][0]
        # Nothing beyond the branch probe may run — no commit against main.
        assert [cmd[1] for cmd in recorded] == ["rev-parse"]


class TestEveryStepsReturnCodeIsChecked:
    """feedback_no_silent_defaults: an unattended 02:30 session that swallows an
    rc is a change that never lands behind a green log line."""

    @pytest.mark.parametrize(
        ("failing", "step"),
        [(("git", "add"), "git add"),
         (("git", "commit"), "git commit"),
         (("git", "push"), "git push"),
         (("gh", "pr"), "gh pr create")],
    )
    def test_failure_returns_none_and_notifies(self, failing, step, monkeypatch, notify):
        def fake_run(cmd, *, cwd=None):
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return _done(0, stdout=f"{BRANCH}\n")
            if tuple(cmd[:2]) == failing:
                return _done(1, stderr='must be on a branch named differently than "main"')
            return _done(0)

        monkeypatch.setattr(c, "run", fake_run)

        assert _open_pr() is None
        notify.assert_called_once()
        title, detail, source = notify.call_args[0]
        assert step in title
        assert source == "claude_md_sync"
        assert BRANCH in detail, "operator needs the branch name to recover the stranded work"

    def test_success_does_not_notify(self, calls, notify):
        assert _open_pr() is not None
        notify.assert_not_called()
