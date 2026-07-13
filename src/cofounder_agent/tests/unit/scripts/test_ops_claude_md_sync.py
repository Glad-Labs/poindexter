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
