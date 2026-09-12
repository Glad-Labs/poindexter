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


def test_extract_refs_only_matches_at_a_token_boundary():
    # `brain/` must not match INSIDE `poindexter/brain/...` — that shorthand is
    # narrative, and matching its tail is how #3657 produced
    # `poindexter/src/cofounder_agent/poindexter/brain/seed_app_settings.json`.
    md = "see `poindexter/brain/seed_app_settings.json` and `brain/alert_sync.py`."
    assert ds.extract_refs(md) == ["brain/alert_sync.py"]


def test_replace_ref_never_rewrites_inside_a_longer_path():
    text = "`poindexter/brain/x.py` and `brain/x.py` and brain/x.py."
    out = ds.replace_ref(text, "brain/x.py", "src/cofounder_agent/poindexter/brain/x.py")
    assert out == (
        "`poindexter/brain/x.py` and `src/cofounder_agent/poindexter/brain/x.py`"
        " and src/cofounder_agent/poindexter/brain/x.py."
    )
    # a ref that is a prefix of a longer file name is left alone too
    assert ds.replace_ref("scripts/foo.py scripts/foo.py.bak", "scripts/foo.py", "x/foo.py") == "x/foo.py scripts/foo.py.bak"
