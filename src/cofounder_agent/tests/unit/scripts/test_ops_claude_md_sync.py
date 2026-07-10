from __future__ import annotations

import sys
from pathlib import Path


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
