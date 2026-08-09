"""Contract tests for the self-landing half of ``_common``: ``pr_status``,
``wait_for_merge``, and ``commit_and_open_pr``'s ``auto_merge`` / ``force_push``.

Companion to ``test_ops_common_pr.py``, which pins the cwd/``--head``/rc
plumbing (stack#2809). This file pins what happens *after* the PR exists: a
session that opens a correct PR nobody merges has the same net effect as one
that opened none. Motivating failure is PR #3126 — DB-count sync, every
required check green, permanently unmergeable because the repo-derived CI sync
landed adjacent bullets 47 minutes later.

Both files stub ``_common.run``, the lowest seam, so the real ``git``/``gh``
argv builders are exercised rather than mocked away.
"""
from __future__ import annotations

import json
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

REPO = "Glad-Labs/glad-labs-stack"
WT = "/home/op/.poindexter/worktrees/claude-md-sync-2026-08-08-0230"
BRANCH = "auto/claude-md-sync-2026-08-08-0230"
CREATED_URL = "https://github.com/Glad-Labs/glad-labs-stack/pull/9999"
EXISTING_URL = "https://github.com/Glad-Labs/glad-labs-stack/pull/3126"
LOG = logging.getLogger("test-ops-autoland")


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _pr(**overrides) -> str:
    payload = {
        "number": 3126,
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "BLOCKED",
        "url": EXISTING_URL,
        "autoMergeRequest": None,
    }
    payload.update(overrides)
    return json.dumps(payload)


class _Shell:
    """Routes every argv the helpers shell out, recording each one.

    ``views`` is consumed one entry per ``gh pr view``, the last repeating — so
    a test can walk a PR through UNKNOWN → MERGEABLE → MERGED the way GitHub
    actually reports it.
    """

    def __init__(self, *, create_rc: int = 0, merge_rc: int = 0, views: list[str] | None = None):
        self.create_rc = create_rc
        self.merge_rc = merge_rc
        self.views = list(views) if views else [_pr()]
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *, cwd=None):
        self.calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _done(0, stdout=f"{BRANCH}\n")
        if cmd[:3] == ["gh", "pr", "create"]:
            if self.create_rc:
                return _done(self.create_rc, stderr="a pull request for branch already exists")
            return _done(0, stdout=f"{CREATED_URL}\n")
        if cmd[:3] == ["gh", "pr", "merge"]:
            if self.merge_rc:
                return _done(self.merge_rc, stderr="auto-merge is not enabled for this repository")
            return _done(0)
        if cmd[:3] == ["gh", "pr", "view"]:
            view = self.views.pop(0) if len(self.views) > 1 else self.views[0]
            return _done(0, stdout=view) if view else _done(1, stderr="no pull requests found")
        return _done(0)

    def install(self, monkeypatch) -> _Shell:
        monkeypatch.setattr(c, "run", self)
        return self

    def argv(self, *prefix: str) -> list[str]:
        return next(cmd for cmd in self.calls if cmd[: len(prefix)] == list(prefix))

    def count(self, *prefix: str) -> int:
        return sum(1 for cmd in self.calls if cmd[: len(prefix)] == list(prefix))


@pytest.fixture
def notify(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(c, "notify_fail", mock)
    return mock


def _open_pr(**overrides):
    kwargs = dict(
        cwd=WT, repo=REPO, paths=["CLAUDE.md"],
        message="docs(CLAUDE.md): sync DB-derived counts (ops)",
        title="docs(CLAUDE.md): sync DB-derived counts (ops)",
        body="Automated DB-count refresh.",
        log=LOG, source="claude_md_sync",
    )
    kwargs.update(overrides)
    return c.commit_and_open_pr(**kwargs)


class TestPrStatus:
    def test_parses_the_pr_json(self, monkeypatch):
        _Shell().install(monkeypatch)
        assert c.pr_status(REPO, BRANCH)["number"] == 3126

    def test_runs_inside_the_worktree(self, monkeypatch):
        """Same cwd discipline as the rest of the chain — see the CWD trap."""
        shell = _Shell().install(monkeypatch)
        c.pr_status(REPO, BRANCH, cwd=WT)
        assert shell.argv("gh", "pr", "view")[3] == BRANCH

    @pytest.mark.parametrize("view", ["", "not json at all", "[]"])
    def test_degrades_to_empty_rather_than_raising(self, monkeypatch, view):
        _Shell(views=[view]).install(monkeypatch)
        assert c.pr_status(REPO, BRANCH) == {}

    def test_malformed_output_is_logged_not_silently_absent(self, monkeypatch, caplog):
        """``{}`` means "no such PR" to every caller. For a *malformed* reply
        that conclusion is wrong, so it has to be said out loud."""
        _Shell(views=["}{ not json"]).install(monkeypatch)
        with caplog.at_level(logging.ERROR, logger="ops"):
            assert c.pr_status(REPO, BRANCH) == {}
        assert "unparseable" in caplog.text


class TestWaitForMerge:
    def _wait(self, monkeypatch, views, *, budget=60.0, poll=0.0):
        _Shell(views=views).install(monkeypatch)
        return c.wait_for_merge(
            REPO, BRANCH, cwd=WT, budget_seconds=budget, poll_seconds=poll, log=LOG,
        )

    def test_merged(self, monkeypatch):
        assert self._wait(monkeypatch, [_pr(state="MERGED")]) == "MERGED"

    def test_closed(self, monkeypatch):
        assert self._wait(monkeypatch, [_pr(state="CLOSED")]) == "CLOSED"

    def test_conflicting(self, monkeypatch):
        assert self._wait(monkeypatch, [_pr(mergeable="CONFLICTING")]) == "CONFLICTING"

    def test_vanished_pr_is_gone(self, monkeypatch):
        assert self._wait(monkeypatch, [""]) == "GONE"

    def test_unknown_mergeable_keeps_polling(self, monkeypatch):
        """GitHub reports mergeable=UNKNOWN for a beat after every push while it
        recomputes the merge base. Reading it as CONFLICTING would trigger a
        pointless regenerate; reading it as clean would call a conflict fine."""
        outcome = self._wait(monkeypatch, [
            _pr(mergeable="UNKNOWN"), _pr(mergeable="UNKNOWN"), _pr(state="MERGED"),
        ])
        assert outcome == "MERGED"

    def test_budget_expiry_on_a_clean_pr_is_pending(self, monkeypatch):
        """PENDING is a success shape: clean + armed, GitHub lands it later."""
        assert self._wait(monkeypatch, [_pr()], budget=0.0, poll=30.0) == "PENDING"

    def test_budget_is_not_overshot_by_a_final_sleep(self, monkeypatch):
        """The last poll must not sleep past the deadline before giving up."""
        shell = _Shell(views=[_pr()]).install(monkeypatch)
        c.wait_for_merge(
            REPO, BRANCH, cwd=WT, budget_seconds=0.0, poll_seconds=999.0, log=LOG,
        )
        assert shell.count("gh", "pr", "view") == 1


class TestForcePush:
    def test_default_push_is_not_forced(self, monkeypatch, notify):
        shell = _Shell().install(monkeypatch)
        _open_pr()
        assert "--force-with-lease" not in shell.argv("git", "push")

    def test_force_push_uses_a_lease_never_bare_force(self, monkeypatch, notify):
        """A bare --force would silently overwrite whatever else moved the
        branch; the lease turns that into a loud failure instead."""
        shell = _Shell().install(monkeypatch)
        _open_pr(force_push=True)
        argv = shell.argv("git", "push")
        assert "--force-with-lease" in argv
        assert "--force" not in argv


class TestReusesAnAlreadyOpenPr:
    """A regenerating caller re-pushes a branch that already has a PR, so
    ``gh pr create`` failing with "already exists" is the expected path — but
    only when a PR really is open for that head."""

    def test_existing_open_pr_is_reused(self, monkeypatch, notify):
        _Shell(create_rc=1, views=[_pr(state="OPEN")]).install(monkeypatch)
        assert _open_pr(force_push=True) == EXISTING_URL
        notify.assert_not_called()

    def test_create_failure_with_no_pr_at_all_still_fails_loud(self, monkeypatch, notify):
        _Shell(create_rc=1, views=[""]).install(monkeypatch)
        assert _open_pr() is None
        notify.assert_called_once()
        assert "gh pr create" in notify.call_args[0][0]

    def test_create_failure_against_a_closed_pr_still_fails_loud(self, monkeypatch, notify):
        """Reusing a CLOSED PR would strand the push behind a dead review."""
        _Shell(create_rc=1, views=[_pr(state="CLOSED")]).install(monkeypatch)
        assert _open_pr() is None
        notify.assert_called_once()


class TestAutoMerge:
    def test_not_armed_unless_asked(self, monkeypatch, notify):
        shell = _Shell().install(monkeypatch)
        _open_pr()
        assert shell.count("gh", "pr", "merge") == 0

    def test_arms_squash_auto_merge_on_the_session_branch(self, monkeypatch, notify):
        shell = _Shell().install(monkeypatch)
        assert _open_pr(auto_merge=True) == CREATED_URL
        argv = shell.argv("gh", "pr", "merge")
        assert "--auto" in argv and "--squash" in argv
        assert BRANCH in argv, "must arm this session's PR, not whatever gh infers"
        notify.assert_not_called()

    def test_failure_notifies_but_keeps_the_pr(self, monkeypatch, notify):
        """The PR is open and correct — it just needs a human now. Dropping the
        URL would discard a good change over a merge-button problem."""
        _Shell(merge_rc=1, views=[_pr()]).install(monkeypatch)
        assert _open_pr(auto_merge=True) == CREATED_URL
        notify.assert_called_once()
        assert "manual merge" in notify.call_args[0][0]

    def test_already_armed_is_not_reported_as_a_failure(self, monkeypatch, notify):
        """gh exits non-zero when auto-merge is already enabled, so a retry
        re-arming its own PR must not page the operator."""
        _Shell(
            merge_rc=1, views=[_pr(autoMergeRequest={"mergeMethod": "SQUASH"})],
        ).install(monkeypatch)
        _open_pr(auto_merge=True)
        notify.assert_not_called()

    def test_immediate_merge_is_not_reported_as_a_failure(self, monkeypatch, notify):
        """A fully-green PR merges on the spot, and gh exits non-zero saying so."""
        _Shell(merge_rc=1, views=[_pr(state="MERGED")]).install(monkeypatch)
        _open_pr(auto_merge=True)
        notify.assert_not_called()
