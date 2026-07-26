from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock


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
        monkeypatch.setattr(cms.c, "git", lambda *a, **kw: _fake_completed(0, stdout=""))

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
        monkeypatch.setattr(cms.c, "git", lambda *a, **kw: _fake_completed(0, stdout=""))

        rc = cms.main()

        assert rc == 0
        notify_mock.assert_not_called()


class TestMainSurfacesPrFailure:
    """stack#2809: with DB stats refreshed but ``gh pr create`` failing every
    night since 2026-06-10, ``main()`` logged "opened CLAUDE.md sync PR" and
    returned 0 regardless. Six weeks of correct counts were pushed to orphaned
    ``auto/*`` branches and never proposed."""

    def _run(self, tmp_path, monkeypatch, *, pr_url):
        _stub_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(cms.c, "run", lambda *a, **kw: _fake_completed(0, stdout="updated"))
        # A dirty worktree is what puts main() on the commit-and-PR path.
        monkeypatch.setattr(cms.c, "git", lambda *a, **kw: _fake_completed(0, stdout=" M CLAUDE.md"))
        pr_mock = MagicMock(return_value=pr_url)
        monkeypatch.setattr(cms.c, "commit_and_open_pr", pr_mock)
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
        assert kwargs["paths"] == ["CLAUDE.md"]
        assert kwargs["repo"] == cms.REPO
