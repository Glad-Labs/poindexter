"""``app_settings`` is created in two places and seeded from three, and nothing else.

The table has one definition, ``0000_baseline.schema.sql``. The brain daemon is
the only other thing that may create it, because on a compose-first install it
boots before the worker has run a migration (``seed_loader.APP_SETTINGS_DDL``,
pinned to the baseline by ``tests/unit/brain/test_seed_loader_app_settings_ddl.py``).
The rows come from the three sources the value-drift lint compares:
``settings_defaults.DEFAULTS``, ``0000_baseline.seeds.sql`` and
``brain/seed_app_settings.json``.

A shell or SQL script that creates or seeds the table is a fourth copy that no
lint reads. Two sat in the repo until 2026-09-28. An orphaned SQL seed script
had no caller and shipped to the public mirror, and it held the 8-column table
the brain's own DDL was copied from, so the worker's baseline crashed on a
compose-first install (poindexter#1097). ``scripts/bootstrap.sh`` seeded 32 rows
through ``psql`` before any worker or brain existed. Because the first writer
wins, its values shadowed the reference seeds: ``api_base_url`` pointed at port
8000 (the API is on 8002), the writer and critic models were not the reference
pins, and 12 keys retired in #2281 came back. None of that showed in a lint or
a test.

The rule has two halves:

* ``CREATE TABLE app_settings (`` may appear only in the baseline schema, the
  brain's ``seed_loader`` and the tests, which build throwaway copies. Docs may
  show it.
* ``INSERT INTO app_settings`` may appear in a non-Python file only in
  ``0000_baseline.seeds.sql`` and the tests. Python writes settings all over the
  app (the settings service, jobs, the CLI, migrations), so it is not scanned
  for inserts.

The scan reads tracked files through ``git grep``, so a venv or build directory
inside the checkout is never walked. A scan that matched nothing has not
passed, so the real-tree test also requires the known homes to be found.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)

BASELINE_SCHEMA = "src/cofounder_agent/poindexter/services/migrations/0000_baseline.schema.sql"
BASELINE_SEEDS = "src/cofounder_agent/poindexter/services/migrations/0000_baseline.seeds.sql"
BRAIN_SEED_LOADER = "src/cofounder_agent/poindexter/brain/seed_loader.py"
TESTS = "src/cofounder_agent/tests/"

DDL_HOMES = frozenset({BASELINE_SCHEMA, BRAIN_SEED_LOADER})
SEED_HOMES = frozenset({BASELINE_SEEDS})

# POSIX classes, not \s: ``git grep -E`` does not read \s the way Python's re does.
_DDL_PATTERN = r'CREATE[[:space:]]+TABLE[[:space:]]+(IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+)?(public\.)?"?app_settings"?[[:space:]]*\('
_INSERT_PATTERN = r'INSERT[[:space:]]+INTO[[:space:]]+(public\.)?"?app_settings"?([^A-Za-z0-9_]|$)'


def violations(ddl_files: set[str], insert_files: set[str]) -> list[str]:
    """The files that break the rule, given every file matching each pattern."""
    out = [
        f"{path}: creates app_settings, which only the baseline schema and the "
        "brain's seed_loader may do"
        for path in sorted(ddl_files)
        if path not in DDL_HOMES and not path.startswith(TESTS)
    ]
    out += [
        f"{path}: seeds app_settings from a non-Python file, which only "
        "0000_baseline.seeds.sql may do"
        for path in sorted(insert_files)
        if path not in SEED_HOMES and not path.startswith(TESTS) and not path.endswith(".py")
    ]
    return out


def _tracked_matches(pattern: str, *, exclude: tuple[str, ...]) -> set[str]:
    """Tracked files whose text matches ``pattern`` (case-insensitive).

    ``exclude`` entries are suffix globs like ``*.md``. They go in as plain
    ``:(exclude)`` pathspecs on purpose: without the ``glob`` magic ``*`` also
    matches ``/``, so ``*.md`` covers ``docs/superpowers/plans/x.md``. With it,
    only a top-level ``.md`` would be excluded.
    """
    pathspec = [".", *(f":(exclude){glob}" for glob in exclude)]
    result = subprocess.run(
        ["git", "grep", "-l", "-z", "-i", "-E", pattern, "--", *pathspec],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    # git grep exits 1 for "no match" and >1 for a real error.
    assert result.returncode in (0, 1), f"git grep failed: {result.stderr.strip()}"
    return {p for p in result.stdout.split("\0") if p}


def test_the_rule_flags_a_stray_copy_and_spares_the_sanctioned_homes():
    """The rule itself, on synthetic paths: a control that cannot be blinded by
    the state of the repo."""
    assert violations(set(), set()) == []
    # The homes and the tests are fine.
    assert violations(
        {BASELINE_SCHEMA, BRAIN_SEED_LOADER, f"{TESTS}integration/conftest.py"},
        {BASELINE_SEEDS, f"{TESTS}integration_db/test_x.py"},
    ) == []
    # The two files this guard was written for.
    found = violations(
        {"scripts/seed-defaults.sql", "scripts/bootstrap.sh"},
        {"scripts/seed-defaults.sql", "scripts/bootstrap.sh"},
    )
    assert len(found) == 4
    assert all(p in "\n".join(found) for p in ("scripts/seed-defaults.sql", "scripts/bootstrap.sh"))
    # Python inserts settings legitimately; a Python file may not create the table.
    assert violations(set(), {"src/cofounder_agent/poindexter/services/settings_service.py"}) == []
    assert violations({"src/cofounder_agent/poindexter/cli/setup.py"}, set()) != []
    # Another config format is not an escape hatch.
    assert violations(set(), {"infrastructure/local-db/seed.yml"}) != []


def test_no_tracked_file_creates_or_seeds_app_settings_outside_its_homes():
    ddl_files = _tracked_matches(_DDL_PATTERN, exclude=("*.md",))
    insert_files = _tracked_matches(_INSERT_PATTERN, exclude=("*.md", "*.py"))

    # A scan that matched nothing has not passed. Each home carries the text
    # its pattern looks for, so if one is missing the pattern or the scan is
    # broken, not the repo.
    assert DDL_HOMES <= ddl_files, (
        f"the DDL pattern did not find its homes {sorted(DDL_HOMES - ddl_files)}; "
        f"it matched {sorted(ddl_files)}"
    )
    assert SEED_HOMES <= insert_files, (
        f"the INSERT pattern did not find {sorted(SEED_HOMES - insert_files)}; "
        f"it matched {sorted(insert_files)}"
    )

    found = violations(ddl_files, insert_files)
    assert not found, (
        "app_settings is created only by the baseline schema and the brain's "
        "seed_loader, and seeded from a non-Python file only by "
        "0000_baseline.seeds.sql. A second copy is read by no lint, and on a "
        "first-writer-wins table its values shadow the reference seeds "
        "(poindexter#1097). Seed through settings_defaults.py, the brain seed or "
        "the baseline seeds instead:\n  " + "\n  ".join(found)
    )
