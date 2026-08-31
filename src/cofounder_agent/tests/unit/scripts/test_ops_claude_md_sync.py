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
import claude_md_sync as cms  # noqa: E402


def test_extract_migration_clause_reads_docstring():
    src = '"""Drop pipeline_tasks.category column.\n\nMore detail.\n"""\n\ndef up(): ...\n'
    assert cms.extract_migration_clause(src) == "Drop pipeline_tasks.category column."


def test_extract_migration_clause_no_docstring_returns_empty():
    assert cms.extract_migration_clause("def up(): ...\n") == ""


def test_newest_migration_picks_latest_timestamp(tmp_path):
    (tmp_path / "20260601_010101_a.py").write_text("x")
    (tmp_path / "20260622_200222_b.py").write_text("x")
    (tmp_path / "0000_baseline.py").write_text("x")
    assert cms.newest_migration(tmp_path).name == "20260622_200222_b.py"


def _fake_completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


SESSION_BRANCH = "auto/claude-md-sync-2026-08-08-0230"


def _stub_repo(tmp_path: Path, monkeypatch) -> None:
    """Minimal fake repo tree: CLAUDE.md + an empty migrations dir, so
    ``main()`` runs its full body without touching the real repo."""
    (tmp_path / "CLAUDE.md").write_text("hello\n", encoding="utf-8")
    (tmp_path / "src" / "cofounder_agent" / "services" / "migrations").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    # main() gates the c.run(...) call on stats.exists() — must be a real
    # (stub) file for the mocked c.run to actually be exercised. Content is
    # never read, since c.run itself is monkeypatched below.
    (tmp_path / "scripts" / "sync_claude_md_db_stats.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(cms, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cms.c, "get_logger", lambda name: logging.getLogger(f"test-{name}"))
    # On a session branch by default, and no leftover PRs to reap.
    monkeypatch.setattr(cms.c, "current_branch", lambda cwd: SESSION_BRANCH)
    monkeypatch.setattr(cms, "reap_superseded_prs", lambda *a, **kw: [])


def _git_stub(*, dirty: bool):
    """Stand in for every git call main() makes.

    ``git status --porcelain CLAUDE.md`` is the one whose stdout decides whether
    the generator changed anything; the rest only need rc=0.
    """

    def _git(*args, **kwargs):
        if args and args[0] == "status":
            return _fake_completed(0, stdout=" M CLAUDE.md" if dirty else "")
        return _fake_completed(0)

    return _git


class TestMainSurfacesDbStatsFailure:
    """stack#2408: ``main()`` used to discard ``c.run(...)``'s result entirely
    for the DB-stats step — a crash (e.g. DB unreachable) read identically to
    "already in sync", silently freezing CLAUDE.md's DB-derived counts."""

    def test_nonzero_exit_notifies_and_returns_failure(self, tmp_path, monkeypatch):
        _stub_repo(tmp_path, monkeypatch)
        run_mock = MagicMock(return_value=_fake_completed(2, stderr="no database URL resolved"))
        notify_mock = MagicMock()
        monkeypatch.setattr(cms.c, "run", run_mock)
        monkeypatch.setattr(cms.c, "notify_fail", notify_mock)
        monkeypatch.setattr(cms.c, "git", _git_stub(dirty=False))

        rc = cms.main()

        assert rc != 0
        notify_mock.assert_called_once()
        title, detail, source = notify_mock.call_args[0]
        assert "DB stats" in title
        assert "2" in detail
        assert source == "claude_md_sync"

    def test_zero_exit_does_not_notify_for_db_stats(self, tmp_path, monkeypatch):
        _stub_repo(tmp_path, monkeypatch)
        run_mock = MagicMock(
            return_value=_fake_completed(0, stdout="CLAUDE.md DB stats are in sync (no changes).")
        )
        notify_mock = MagicMock()
        monkeypatch.setattr(cms.c, "run", run_mock)
        monkeypatch.setattr(cms.c, "notify_fail", notify_mock)
        monkeypatch.setattr(cms.c, "git", _git_stub(dirty=False))

        rc = cms.main()

        assert rc == 0
        notify_mock.assert_not_called()


class TestMainSurfacesPrFailure:
    """stack#2809: with DB stats refreshed but ``gh pr create`` failing every
    night since 2026-06-10, ``main()`` logged "opened CLAUDE.md sync PR" and
    returned 0 regardless. Six weeks of correct counts were pushed to orphaned
    ``auto/*`` branches and never proposed."""

    def _run(self, tmp_path, monkeypatch, *, pr_url, outcome="MERGED"):
        _stub_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(cms.c, "run", lambda *a, **kw: _fake_completed(0, stdout="updated"))
        # A dirty CLAUDE.md is what puts main() on the commit-and-PR path.
        monkeypatch.setattr(cms.c, "git", _git_stub(dirty=True))
        monkeypatch.setattr(cms.c, "notify_fail", MagicMock())
        pr_mock = MagicMock(return_value=pr_url)
        monkeypatch.setattr(cms.c, "commit_and_open_pr", pr_mock)
        monkeypatch.setattr(cms.c, "wait_for_merge", MagicMock(return_value=outcome))
        return cms.main(), pr_mock

    def test_pr_failure_is_a_nonzero_exit(self, tmp_path, monkeypatch):
        rc, pr_mock = self._run(tmp_path, monkeypatch, pr_url=None)
        assert rc != 0, "a session that opened no PR must not exit green"
        pr_mock.assert_called_once()

    def test_pr_success_exits_zero(self, tmp_path, monkeypatch):
        rc, _ = self._run(tmp_path, monkeypatch, pr_url="https://github.com/x/y/pull/1")
        assert rc == 0

    def test_pr_targets_the_repo_root_worktree(self, tmp_path, monkeypatch):
        _, pr_mock = self._run(tmp_path, monkeypatch, pr_url="https://github.com/x/y/pull/1")
        kwargs = pr_mock.call_args.kwargs
        assert kwargs["cwd"] == str(tmp_path)
        # README.md rides the same DB probe (live/total posts, pipeline runs,
        # app_settings), so an un-staged README means the marketing stats the
        # public mirror shows go stale behind a green session.
        assert kwargs["paths"] == ["CLAUDE.md", "README.md"]
        assert kwargs["repo"] == cms.REPO


class TestPrIsArmedToMergeItself:
    """The session's whole point is landing unattended. An unarmed PR is the
    stack#2809 stall in a new costume: correct counts, nobody merging them."""

    def test_auto_merge_is_requested(self, tmp_path, monkeypatch):
        _stub_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(cms.c, "run", lambda *a, **kw: _fake_completed(0, stdout="updated"))
        monkeypatch.setattr(cms.c, "git", _git_stub(dirty=True))
        pr_mock = MagicMock(return_value="https://github.com/x/y/pull/1")
        monkeypatch.setattr(cms.c, "commit_and_open_pr", pr_mock)
        monkeypatch.setattr(cms.c, "wait_for_merge", MagicMock(return_value="MERGED"))

        assert cms.main() == 0
        assert pr_mock.call_args.kwargs["auto_merge"] is True

    def test_budget_expiry_on_a_clean_pr_is_success_not_failure(self, tmp_path, monkeypatch):
        """PENDING means clean + armed — GitHub lands it when CI finishes."""
        _stub_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(cms.c, "run", lambda *a, **kw: _fake_completed(0, stdout="updated"))
        monkeypatch.setattr(cms.c, "git", _git_stub(dirty=True))
        notify_mock = MagicMock()
        monkeypatch.setattr(cms.c, "notify_fail", notify_mock)
        monkeypatch.setattr(
            cms.c, "commit_and_open_pr", MagicMock(return_value="https://x/pull/1"),
        )
        monkeypatch.setattr(cms.c, "wait_for_merge", MagicMock(return_value="PENDING"))

        assert cms.main() == 0
        notify_mock.assert_not_called()


class TestConflictIsResolvedByRegeneration:
    """PR #3126: the repo-derived CI sync (sync-claude-md.yml) rewrites bullets
    immediately adjacent to these in the Key Numbers list, so a main that moves
    under an open PR conflicts every time — with CI already green. CLAUDE.md is
    generated output, so the fix is to regenerate on fresh main, not rebase."""

    def _run_with_outcomes(self, tmp_path, monkeypatch, outcomes):
        _stub_repo(tmp_path, monkeypatch)
        git_mock = MagicMock(side_effect=_git_stub(dirty=True))
        monkeypatch.setattr(cms.c, "git", git_mock)
        monkeypatch.setattr(cms.c, "run", lambda *a, **kw: _fake_completed(0, stdout="updated"))
        monkeypatch.setattr(cms.c, "notify_fail", MagicMock())
        pr_mock = MagicMock(return_value="https://github.com/x/y/pull/1")
        monkeypatch.setattr(cms.c, "commit_and_open_pr", pr_mock)
        monkeypatch.setattr(cms.c, "wait_for_merge", MagicMock(side_effect=outcomes))
        return cms.main(), pr_mock, git_mock

    def test_conflict_retries_by_regenerating_on_fresh_main(self, tmp_path, monkeypatch):
        rc, pr_mock, git_mock = self._run_with_outcomes(
            tmp_path, monkeypatch, ["CONFLICTING", "MERGED"],
        )

        assert rc == 0
        assert pr_mock.call_count == 2, "the second attempt must re-push a regenerated file"
        # Attempt 2 force-pushes: the branch is already proposed, and the new
        # commit is a replacement rather than a descendant.
        assert pr_mock.call_args_list[0].kwargs["force_push"] is False
        assert pr_mock.call_args_list[1].kwargs["force_push"] is True
        # Each attempt re-points the branch at a freshly fetched origin/main.
        checkouts = [
            call.args for call in git_mock.call_args_list if call.args[:1] == ("checkout",)
        ]
        assert len(checkouts) == 2
        for args in checkouts:
            assert "-B" in args and "origin/main" in args
            assert "--force" in args, "must move the branch even from a dirty tree"

    def test_never_attempts_a_textual_rebase(self, tmp_path, monkeypatch):
        _, _, git_mock = self._run_with_outcomes(
            tmp_path, monkeypatch, ["CONFLICTING", "MERGED"],
        )
        subcommands = {call.args[0] for call in git_mock.call_args_list if call.args}
        assert "rebase" not in subcommands
        assert "merge" not in subcommands

    def test_gives_up_loudly_after_max_attempts(self, tmp_path, monkeypatch):
        _stub_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(cms.c, "git", _git_stub(dirty=True))
        monkeypatch.setattr(cms.c, "run", lambda *a, **kw: _fake_completed(0, stdout="updated"))
        notify_mock = MagicMock()
        monkeypatch.setattr(cms.c, "notify_fail", notify_mock)
        monkeypatch.setattr(
            cms.c, "commit_and_open_pr", MagicMock(return_value="https://x/pull/1"),
        )
        monkeypatch.setattr(cms.c, "wait_for_merge", MagicMock(return_value="CONFLICTING"))

        rc = cms.main()

        assert rc != 0
        assert notify_mock.call_count == 1
        assert "converge" in notify_mock.call_args[0][0]

    def test_own_pr_landing_mid_wait_is_a_clean_exit(self, tmp_path, monkeypatch):
        """Attempt 2 rebases and finds nothing to change — our own PR merged
        between polls, so main already carries the counts."""
        _stub_repo(tmp_path, monkeypatch)
        dirty = iter([True, False])
        monkeypatch.setattr(
            cms.c, "git",
            lambda *a, **kw: _fake_completed(
                0, stdout=(" M CLAUDE.md" if next(dirty) else "")
            ) if a[:1] == ("status",) else _fake_completed(0),
        )
        monkeypatch.setattr(cms.c, "run", lambda *a, **kw: _fake_completed(0, stdout="updated"))
        notify_mock = MagicMock()
        monkeypatch.setattr(cms.c, "notify_fail", notify_mock)
        monkeypatch.setattr(
            cms.c, "commit_and_open_pr", MagicMock(return_value="https://x/pull/1"),
        )
        monkeypatch.setattr(cms.c, "wait_for_merge", MagicMock(return_value="CONFLICTING"))

        assert cms.main() == 0
        notify_mock.assert_not_called()


class TestRefusesToMoveTheWrongBranch:
    """The session now force-moves its own branch pointer, so the "am I on a
    session branch?" guard has to fire before the first checkout, not after."""

    @pytest.mark.parametrize("branch", ["main", "HEAD", ""])
    def test_aborts_before_touching_git(self, tmp_path, monkeypatch, branch):
        _stub_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(cms.c, "current_branch", lambda cwd: branch)
        git_mock = MagicMock(side_effect=_git_stub(dirty=False))
        monkeypatch.setattr(cms.c, "git", git_mock)
        notify_mock = MagicMock()
        monkeypatch.setattr(cms.c, "notify_fail", notify_mock)
        run_mock = MagicMock()
        monkeypatch.setattr(cms.c, "run", run_mock)

        rc = cms.main()

        assert rc != 0
        # Must not regenerate, and must not move any branch, before the guard.
        run_mock.assert_not_called()
        subcommands = {call.args[0] for call in git_mock.call_args_list if call.args}
        assert "checkout" not in subcommands
        notify_mock.assert_called_once()


class TestOwnBranchRegexCannotMatchTheCiSyncBranch:
    """Two different jobs share the ``auto/claude-md-sync-*`` namespace: this
    session (``-<date>-<HHMM>``) and .github/workflows/sync-claude-md.yml
    (``-<date>``). The reaper closes superseded PRs, so matching the CI job's
    branch would mean auto-closing the other sync's PR every night."""

    @pytest.mark.parametrize("branch", [
        "auto/claude-md-sync-2026-08-08-0230",
        "auto/claude-md-sync-2026-01-01-0000",
    ])
    def test_matches_own_session_branches(self, branch):
        assert cms.OWN_BRANCH_RE.match(branch)

    @pytest.mark.parametrize("branch", [
        "auto/claude-md-sync-2026-08-08",          # the CI sync's daily branch
        "auto/claude-md-sync-2026-08-08-0230-x",
        "auto/doc-sync-2026-08-08-0500",
        "auto/claude-md-sync",
        "claude/some-human-branch",
    ])
    def test_rejects_everything_else(self, branch):
        assert cms.OWN_BRANCH_RE.match(branch) is None


class TestReapSupersededPrs:
    def _listing(self, rows):
        return _fake_completed(0, stdout=json.dumps(rows))

    def test_closes_own_stale_prs_and_keeps_current(self, monkeypatch):
        calls: list[tuple] = []

        def _gh(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("pr", "list"):
                return self._listing([
                    {"number": 1, "headRefName": "auto/claude-md-sync-2026-08-07-0230"},
                    {"number": 2, "headRefName": SESSION_BRANCH},
                    {"number": 3, "headRefName": "auto/claude-md-sync-2026-08-08"},
                    {"number": 4, "headRefName": "claude/human-work"},
                ])
            return _fake_completed(0)

        monkeypatch.setattr(cms.c, "gh", _gh)
        closed = cms.reap_superseded_prs(
            SESSION_BRANCH, cwd="/tmp", log=logging.getLogger("test-reap"),
        )

        assert closed == [1], "only this session's older PR is superseded"
        closed_numbers = [a[2] for a in calls if a[:2] == ("pr", "close")]
        assert closed_numbers == ["1"]

    def test_unparseable_listing_is_a_skip_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(cms.c, "gh", lambda *a, **kw: _fake_completed(0, stdout="not json"))
        assert cms.reap_superseded_prs(
            SESSION_BRANCH, cwd="/tmp", log=logging.getLogger("test-reap"),
        ) == []

    def test_listing_failure_is_a_skip_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(cms.c, "gh", lambda *a, **kw: _fake_completed(1, stderr="gh boom"))
        assert cms.reap_superseded_prs(
            SESSION_BRANCH, cwd="/tmp", log=logging.getLogger("test-reap"),
        ) == []

    def test_listing_is_paged_past_ghs_default_30(self, monkeypatch):
        seen: list[tuple] = []
        monkeypatch.setattr(
            cms.c, "gh",
            lambda *a, **kw: (seen.append(a), self._listing([]))[1],
        )
        cms.reap_superseded_prs(
            SESSION_BRANCH, cwd="/tmp", log=logging.getLogger("test-reap"),
        )
        assert "--limit" in seen[0], "gh pr list silently truncates at 30 without it"
