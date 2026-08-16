"""Tests for ``scripts/ci/claude_md_anchor_lint.py`` (#2832).

The lint exists because a CLAUDE.md rewording can kill the prose anchor a
sync-script count rides on — the number freezes while still reading as
current, silently (the ``app_settings`` bullet did exactly that on
2026-07-26). These tests pin the three properties that make the lint
trustworthy:

1. it imports its patterns from the sync scripts (a restated copy would
   drift and green falsely),
2. every anchor matches the real checked-in CLAUDE.md (the lint must be
   green on main as-is), and
3. a reworded phrase turns into a non-zero exit naming the dead anchor
   and its owning script — while a missing CLAUDE.md (public mirror) is
   a clean skip.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load(script_rel: str, name: str):
    script = _repo_root() / script_rel
    spec = spec_from_file_location(name, script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LINT = _load("scripts/ci/claude_md_anchor_lint.py", "claude_md_anchor_lint")


def test_patterns_are_imported_from_the_sync_scripts():
    """No restated copies: the lint's pattern strings must be the sync
    scripts' own COUNT_ANCHORS / STAT_ANCHORS / HEADER_RE objects."""
    db_sync = _load(
        "scripts/sync_claude_md_db_stats.py", "sync_claude_md_db_stats_t"
    )
    repo_sync = _load("scripts/sync-claude-md-stats.py", "sync_claude_md_stats_t")

    sources = {
        (owner, name): pattern.pattern
        for owner, name, pattern in LINT.collect_anchor_sources()
    }
    for name, pat in db_sync.COUNT_ANCHORS.items():
        assert sources[("scripts/sync_claude_md_db_stats.py", name)] == pat
    assert (
        sources[("scripts/sync_claude_md_db_stats.py", "key_numbers_header")]
        == db_sync.HEADER_RE.pattern
    )
    for name, pat in repo_sync.STAT_ANCHORS.items():
        assert sources[("scripts/sync-claude-md-stats.py", name)] == pat
    # 5 DB counts + header + 3 repo stats
    assert len(sources) == 9


def test_green_against_the_checked_in_claude_md(capsys):
    """Acceptance: all anchors currently match — the lint lands green."""
    if not LINT.CLAUDE_MD.is_file():
        pytest.skip("CLAUDE.md absent (public mirror checkout)")
    assert LINT.main() == 0
    assert "clean" in capsys.readouterr().out


def test_dead_anchor_fails_naming_owner_and_anchor(tmp_path, monkeypatch, capsys):
    """Rewording an anchored phrase must fail, naming what died and where."""
    if not LINT.CLAUDE_MD.is_file():
        pytest.skip("CLAUDE.md absent (public mirror checkout)")
    real = LINT.CLAUDE_MD.read_text(encoding="utf-8")
    # The exact #2820 failure shape: reword the app_settings bullet so the
    # literal "app_settings keys" segment the anchor requires is gone.
    broken = real.replace("app_settings keys", "app-settings keys")
    assert broken != real, (
        "fixture assumption: CLAUDE.md contains 'app_settings keys'"
    )
    fake = tmp_path / "CLAUDE.md"
    fake.write_text(broken, encoding="utf-8")
    monkeypatch.setattr(LINT, "CLAUDE_MD", fake)

    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "app_settings" in out
    assert "scripts/sync_claude_md_db_stats.py" in out


def test_absent_claude_md_is_a_clean_skip(tmp_path, monkeypatch, capsys):
    """Public mirror: CLAUDE.md is stripped, the lint is not — must exit 0."""
    monkeypatch.setattr(LINT, "CLAUDE_MD", tmp_path / "CLAUDE.md")
    assert LINT.main() == 0
    assert "skip" in capsys.readouterr().out.lower()
