from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import doc_sync as ds  # noqa: E402


def test_extract_refs_finds_paths_and_dedups():
    md = "See `src/cofounder_agent/main.py` and docs/operations/foo.md, plus src/cofounder_agent/main.py again."
    refs = ds.extract_refs(md)
    assert "src/cofounder_agent/main.py" in refs
    assert "docs/operations/foo.md" in refs
    assert refs.count("src/cofounder_agent/main.py") == 1


def test_extract_refs_strips_trailing_punctuation():
    assert "scripts/foo.py" in ds.extract_refs("run scripts/foo.py.")


def test_resolve_ref_ok_fix_flag(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "here.py").write_text("x")
    (tmp_path / "moved").mkdir()
    (tmp_path / "moved" / "unique.py").write_text("x")
    assert ds.resolve_ref("src/here.py", tmp_path) == ("ok", None)
    assert ds.resolve_ref("src/unique.py", tmp_path) == ("fix", "moved/unique.py")
    assert ds.resolve_ref("src/nope.py", tmp_path) == ("flag", None)
