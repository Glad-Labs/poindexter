"""Contract: migration file discovery must exclude __init__.py AND _-prefixed modules.

Regression guard for poindexter#228 (glad-labs-stack#1194, commit b002f365a):
The migration runner glob picked up ``_module_runner.py`` as a migration
file because it only excluded ``__init__.py``. The runner tried to execute it,
found no ``up()``/``run_migration()`` function, and skipped it — leaving
it absent from ``schema_migrations``. The smoke test then failed with:

    FAIL: migrations did not record a schema_migrations row: _module_runner.py

The fix: exclude any file whose name starts with ``_`` (private helpers
must never be executed as migrations). This is the same rule already
applied in ``services/module_runner.py`` line 88; here we verify it
holds in all three discovery paths:

1. ``scripts/ci/migrations_smoke._migration_files()``
2. ``services/migrations.run_migrations()`` — via ``_collect_migration_files``
3. ``services/migrations.get_migration_status()`` — via ``_collect_migration_files``
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _populate(migrations_dir: Path) -> None:
    """Write a migrations directory containing normal + excluded files."""
    (migrations_dir / "__init__.py").write_text("")
    (migrations_dir / "0000_baseline.py").write_text("")
    (migrations_dir / "_module_runner.py").write_text("# private helper — must never be a migration")
    (migrations_dir / "_helpers.py").write_text("# another private helper")
    (migrations_dir / "20260601_000000_seed.py").write_text("")


# ---------------------------------------------------------------------------
# migrations_smoke._migration_files
# ---------------------------------------------------------------------------

def test_migrations_smoke_excludes_underscore_prefix_files(tmp_path, monkeypatch):
    """_migration_files() must skip any file whose name starts with '_'."""
    import scripts.ci.migrations_smoke as sm
    monkeypatch.setattr(sm, "MIGRATIONS_DIR", tmp_path)
    _populate(tmp_path)

    names = [f.name for f in sm._migration_files()]

    assert "_module_runner.py" not in names, (
        "_-prefixed helpers must not be treated as migrations "
        "(regression: poindexter#228)"
    )
    assert "_helpers.py" not in names
    assert "__init__.py" not in names
    assert "0000_baseline.py" in names
    assert "20260601_000000_seed.py" in names


# ---------------------------------------------------------------------------
# services/migrations._collect_migration_files (the extracted helper)
# ---------------------------------------------------------------------------

def test_services_migrations_collect_excludes_underscore_prefix_files(tmp_path):
    """_collect_migration_files() must skip _-prefixed files and __init__.py."""
    from services.migrations import _collect_migration_files  # type: ignore[attr-defined]

    _populate(tmp_path)
    names = [f.name for f in _collect_migration_files(tmp_path)]

    assert "_module_runner.py" not in names
    assert "_helpers.py" not in names
    assert "__init__.py" not in names
    assert "0000_baseline.py" in names
    assert "20260601_000000_seed.py" in names


def test_services_migrations_collect_returns_sorted(tmp_path):
    """_collect_migration_files() must return files in alphabetical order."""
    from services.migrations import _collect_migration_files  # type: ignore[attr-defined]

    (tmp_path / "20260602_000000_b.py").write_text("")
    (tmp_path / "0000_baseline.py").write_text("")
    (tmp_path / "20260601_000000_a.py").write_text("")
    (tmp_path / "__init__.py").write_text("")

    names = [f.name for f in _collect_migration_files(tmp_path)]
    assert names == sorted(names), "migration files must be sorted alphabetically"
