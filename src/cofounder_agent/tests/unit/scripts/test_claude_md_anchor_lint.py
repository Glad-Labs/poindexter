"""Tests for ``scripts/ci/claude_md_anchor_lint.py`` (#2832).

The lint exists because a rewording can kill the prose anchor a
sync-script count rides on — the number freezes while still reading as
current, silently (the ``app_settings`` bullet did exactly that on
2026-07-26). These tests pin the properties that make the lint
trustworthy:

1. it imports its patterns from the sync scripts (a restated copy would
   drift and green falsely),
2. every anchor matches the real checked-in CLAUDE.md **and README.md**
   (the lint must be green on main as-is),
3. a reworded phrase turns into a non-zero exit naming the dead anchor,
   its file, and its owning script — while a missing CLAUDE.md (public
   mirror) is a clean skip that still checks README, and
4. a checkout with neither file present FAILS rather than reporting
   clean: a check that scanned nothing has not passed.
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

    db_owner = "scripts/sync_claude_md_db_stats.py"
    repo_owner = "scripts/sync-claude-md-stats.py"
    sources = {
        (owner, target, name): pattern.pattern
        for owner, target, name, pattern in LINT.collect_anchor_sources()
    }
    for name, pat in db_sync.COUNT_ANCHORS.items():
        assert sources[(db_owner, "CLAUDE.md", name)] == pat
    assert (
        sources[(db_owner, "CLAUDE.md", "key_numbers_header")]
        == db_sync.HEADER_RE.pattern
    )
    for name, pat in db_sync.README_ANCHORS.items():
        assert sources[(db_owner, "README.md", name)] == pat
    for name, pat in repo_sync.STAT_ANCHORS.items():
        assert sources[(repo_owner, "CLAUDE.md", name)] == pat
    for name, pat in repo_sync.README_ANCHORS.items():
        assert sources[(repo_owner, "README.md", name)] == pat
    # 5 DB counts + header + 5 DB README claims + 3 repo stats + 3 repo README
    assert len(sources) == 17


def test_both_target_files_are_actually_covered():
    """A README anchor set that silently ended up keyed to CLAUDE.md would
    be checked against the wrong text and green forever."""
    targets = {target for _, target, _, _ in LINT.collect_anchor_sources()}
    assert targets == {"CLAUDE.md", "README.md"}


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
    assert "CLAUDE.md" in out
    assert "scripts/sync_claude_md_db_stats.py" in out


def test_dead_readme_anchor_fails_and_names_readme(tmp_path, monkeypatch, capsys):
    """The README half must be enforced too — it is the public front door,
    and its stats had rotted for months before they went on this mechanism."""
    real = LINT.README_MD.read_text(encoding="utf-8")
    # Reword the intro the way a copy edit plausibly would.
    broken = real.replace("live posts and counting", "published posts and counting")
    assert broken != real, (
        "fixture assumption: README.md contains 'live posts and counting'"
    )
    fake = tmp_path / "README.md"
    fake.write_text(broken, encoding="utf-8")
    monkeypatch.setattr(LINT, "README_MD", fake)

    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "live_posts_intro" in out
    assert "README.md" in out


def test_absent_claude_md_still_checks_readme(tmp_path, monkeypatch, capsys):
    """Public mirror: CLAUDE.md is stripped, README.md is not. The lint must
    exit 0 *and* say it still checked the README anchors — skipping both
    would leave the mirror's own front page unguarded."""
    monkeypatch.setattr(LINT, "CLAUDE_MD", tmp_path / "CLAUDE.md")
    assert LINT.main() == 0
    out = capsys.readouterr().out
    assert "skip" in out.lower()
    assert "README.md" in out
    assert "clean" in out


def test_no_target_files_is_a_failure_not_a_pass(tmp_path, monkeypatch, capsys):
    """Scan floor: if a rename or a bad sync filter removes both targets, the
    lint examined nothing — which must never report as clean."""
    monkeypatch.setattr(LINT, "CLAUDE_MD", tmp_path / "CLAUDE.md")
    monkeypatch.setattr(LINT, "README_MD", tmp_path / "README.md")
    assert LINT.main() == 1
    assert "NO TARGET FILE FOUND" in capsys.readouterr().out
