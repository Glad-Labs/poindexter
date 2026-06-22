"""Contract test for the add-``category``-column-to-``pipeline_tasks`` migration.

String-level (no DB): the migration restores a column the canonical baseline
defines but that drifted installs lack — see the migration docstring. Guards
that the DDL stays idempotent and the runner interface is present.
"""

from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = next(
    p / "src" / "cofounder_agent" / "services" / "migrations"
    for p in Path(__file__).resolve().parents
    if (p / "src" / "cofounder_agent" / "services" / "migrations").is_dir()
)


def _migration_text() -> str:
    matches = sorted(MIGRATIONS_DIR.glob("*add_category*pipeline_tasks*.py"))
    assert matches, "expected an add_category…pipeline_tasks migration file"
    return matches[-1].read_text(encoding="utf-8")


def test_adds_category_column_idempotently() -> None:
    text = _migration_text()
    assert "ALTER TABLE pipeline_tasks" in text
    assert "ADD COLUMN IF NOT EXISTS category" in text


def test_runner_interface_present() -> None:
    text = _migration_text()
    assert "async def up(pool)" in text
    assert "async def down(pool)" in text
