"""Contract test for the post-preview_gate restamp migration.

String-level: the migration re-stamps active graph_defs (the #755 contract
fingerprints) that the v6-director and preview_gate reseeds un-stamped by
writing the raw spec. Guards the import-guard (smoke-safe) and runner interface.
"""

from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = next(
    p / "src" / "cofounder_agent" / "services" / "migrations"
    for p in Path(__file__).resolve().parents
    if (p / "src" / "cofounder_agent" / "services" / "migrations").is_dir()
)


def _migration_text() -> str:
    matches = sorted(MIGRATIONS_DIR.glob("*restamp_active_graph_defs_after_preview_gate*.py"))
    assert matches, "expected a restamp_active_graph_defs_after_preview_gate migration file"
    return matches[-1].read_text(encoding="utf-8")


def test_restamps_active_graph_defs() -> None:
    text = _migration_text()
    assert "stamp_graph_def" in text
    assert "WHERE active = true" in text
    assert "UPDATE pipeline_templates" in text


def test_import_guarded_for_smoke() -> None:
    text = _migration_text()
    # discovery + stamping imports must be inside a try/except so the
    # migrations-smoke env (no atom registry) skips instead of crashing.
    assert "try:" in text
    assert "from services.atom_registry import discover" in text
    assert "skipping" in text.lower()


def test_runner_interface_present() -> None:
    text = _migration_text()
    assert "async def up(pool)" in text
    assert "async def down(pool)" in text
