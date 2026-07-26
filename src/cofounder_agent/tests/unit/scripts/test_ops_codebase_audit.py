"""Tests for scripts/ops_sessions/codebase_audit.py — the ruff-fix ops session.

This session used to ALSO run bandit and file one GitHub issue per finding.
That is gone: security findings are gated by the bandit ratchet
(``scripts/ci/bandit_lint.py``, tested in ``test_bandit_lint.py``), which blocks
a net-new finding at PR time and files nothing. The issue-filing flow produced
91 issues — every one examined was a false positive (#2594-#2623, closed by
#2644) — and buried the real backlog three pages deep.

The dedup logic these tests used to cover (#2645) went with it: nothing is
filed, so nothing can be re-filed. ``test_session_files_no_github_issues`` is
the guard that keeps the firehose from being reintroduced.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import codebase_audit as ca  # noqa: E402

SESSION_BRANCH = "auto/codebase-audit-2026-07-22-0358"


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str, stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _install_fakes(monkeypatch, *, dirty: bool, gh_spy):
    """Wire the session's shell-outs to fakes. ``dirty`` decides whether
    `git status --porcelain` reports ruff-modified files."""
    monkeypatch.setattr(
        ca.c, "get_logger", lambda _name: logging.getLogger("test-codebase-audit")
    )
    monkeypatch.setattr(ca.c, "run", lambda *a, **k: _FakeProc(0, ""))
    monkeypatch.setattr(ca.c, "gh", gh_spy)

    def fake_git(*args, **_kwargs):
        if args and args[0] == "status":
            return _FakeProc(0, " M src/cofounder_agent/services/foo.py\n" if dirty else "")
        # stack#2809: commit_and_open_pr refuses to commit unless HEAD resolves
        # to the session's own worktree branch, so the fake must report one.
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _FakeProc(0, f"{SESSION_BRANCH}\n")
        return _FakeProc(0, "")

    monkeypatch.setattr(ca.c, "git", fake_git)


class TestNoBanditIssueFiling:
    """The whole point of the ratchet flip — this session must file nothing."""

    def test_session_files_no_github_issues(self, monkeypatch):
        # dirty=True on purpose: a clean tree makes main() skip gh entirely, so
        # the assertion below could never fire and the test would pass
        # vacuously. A dirty tree drives the one gh path that DOES run (pr
        # create), proving `issue create` is absent rather than just unreached.
        seen: list[tuple] = []

        def boom_gh(*args, **_kwargs):
            seen.append(args)
            if args[:2] == ("issue", "create"):
                raise AssertionError(
                    "codebase_audit must not file GitHub issues — the bandit "
                    "ratchet (scripts/ci/bandit_lint.py) is the gate now. "
                    "Re-adding issue-per-finding reintroduces the 91-issue "
                    "firehose that buried the real backlog."
                )
            return _FakeProc(0, "")

        _install_fakes(monkeypatch, dirty=True, gh_spy=boom_gh)
        assert ca.main() == 0
        assert seen, "guard is vacuous unless main() actually reached gh"
        assert all(call[:2] != ("issue", "create") for call in seen)

    def test_session_does_not_invoke_bandit(self, monkeypatch):
        commands: list[list[str]] = []

        def spy_run(cmd, *, cwd=None):
            commands.append(cmd)
            return _FakeProc(0, "")

        monkeypatch.setattr(
            ca.c, "get_logger", lambda _name: logging.getLogger("test-codebase-audit")
        )
        monkeypatch.setattr(ca.c, "run", spy_run)
        monkeypatch.setattr(ca.c, "gh", lambda *_a, **_k: _FakeProc(0, ""))
        monkeypatch.setattr(ca.c, "git", lambda *a, **k: _FakeProc(0, ""))

        assert ca.main() == 0
        flat = " ".join(part for cmd in commands for part in cmd)
        assert "bandit" not in flat, "bandit belongs in the CI ratchet, not this session"

    def test_module_exposes_no_issue_filing_helpers(self):
        """The bandit-filing surface is gone, not merely unreferenced."""
        for gone in ("bandit_issue_body", "finding_dedup_key", "_has_existing_issue", "BANDIT_TARGETS"):
            assert not hasattr(ca, gone), f"{gone} should have been removed with the bandit flow"


class TestRuffSweep:
    def test_runs_ruff_fix_over_the_declared_targets(self, monkeypatch):
        commands: list[list[str]] = []

        def spy_run(cmd, *, cwd=None):
            commands.append(cmd)
            return _FakeProc(0, "")

        monkeypatch.setattr(
            ca.c, "get_logger", lambda _name: logging.getLogger("test-codebase-audit")
        )
        monkeypatch.setattr(ca.c, "run", spy_run)
        monkeypatch.setattr(ca.c, "gh", lambda *_a, **_k: _FakeProc(0, ""))
        monkeypatch.setattr(ca.c, "git", lambda *a, **k: _FakeProc(0, ""))

        assert ca.main() == 0
        (ruff_cmd,) = [c_ for c_ in commands if "ruff" in c_]
        assert "--fix" in ruff_cmd
        assert "F401,F841" in ruff_cmd
        for target in ca.RUFF_TARGETS:
            assert target in ruff_cmd

    def test_opens_a_pr_when_ruff_changed_files(self, monkeypatch):
        gh_calls: list[tuple] = []
        _install_fakes(
            monkeypatch, dirty=True, gh_spy=lambda *a, **_k: (gh_calls.append(a), _FakeProc(0, ""))[1]
        )
        assert ca.main() == 0
        assert any(call[:2] == ("pr", "create") for call in gh_calls), "a dirty tree should open a lint PR"

    def test_opens_no_pr_when_tree_is_clean(self, monkeypatch):
        gh_calls: list[tuple] = []
        _install_fakes(
            monkeypatch, dirty=False, gh_spy=lambda *a, **_k: (gh_calls.append(a), _FakeProc(0, ""))[1]
        )
        assert ca.main() == 0
        assert not any(call[:2] == ("pr", "create") for call in gh_calls)
