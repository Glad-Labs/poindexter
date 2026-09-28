"""Contract tests for the migration-deleted-key reseed guard.

``scripts/ci/settings_seed_drift_lint.py`` fails CI when a migration DELETEs an
``app_settings`` key that a seed source still carries. Every seed source inserts
``ON CONFLICT DO NOTHING``, so the one-shot DELETE loses to the next seeding pass
and the dead key comes back. Sibling to ``test_settings_seed_value_drift_lint``,
which guards value disagreement between the same three sources.

What these pin, beyond the original baseline/brain checks:

* ``settings_defaults.DEFAULTS`` is a seed source like the other two. It used to
  be exempt ("maintained on purpose"), which exempted the one source that
  re-inserts on EVERY boot. The exemption covered no key when it was dropped
  (2026-09-28), but it would have waved through a migration retiring
  ``rate_limit_video_generate_per_ip`` with its ``DEFAULTS`` line left in place.
* A seed source that parses to zero keys fails the lint. With ``DEFAULTS`` now
  load-bearing, a renamed or reshaped dict would otherwise read as "seeds
  nothing" and disarm that half of the check without a sound.
* An ALLOWLIST entry whose key no current migration deletes fails the lint.
  The three entries dropped on 2026-09-28 had outlived their migrations (folded
  away by the Phase F/G squashes) by months.
* A DELETE's key literals are read from its own string literal only. Over the
  raw file text the match ran into the next statement and reported an
  ``UPDATE``'s key as deleted. That was harmless while ``DEFAULTS`` was exempt;
  now it would red CI on a correct rename migration.

Synthetic-tree tests build their own sources in ``tmp_path`` and repoint the
module's path constants, so they assert the *rule* and stay green whatever the
real tree holds. ``test_real_tree_is_clean`` is the one that asserts the tree.
"""

from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_lint_module():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ci").is_dir() and (p / "src").is_dir()
    )
    script = repo_root / "scripts" / "ci" / "settings_seed_drift_lint.py"
    spec = spec_from_file_location("settings_seed_drift_lint_under_test", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()

_ORPHAN_MIGRATION = '''"""Migration: drop an orphan."""
ORPHANED_KEYS = ({keys})


async def up(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])", list(ORPHANED_KEYS)
        )
'''


def _write_tree(
    tmp_path: Path,
    *,
    defaults: tuple[str, ...] = ("live_default",),
    baseline: tuple[str, ...] = ("live_baseline",),
    brain: tuple[str, ...] | None = ("live_brain",),
    migrations: dict[str, str] | None = None,
    defaults_name: str = "DEFAULTS",
) -> None:
    """Write a synthetic set of seed sources + migrations and point LINT at it."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()

    body = "\n".join(f"    {k!r}: 'v'," for k in defaults)
    defaults_py = tmp_path / "settings_defaults.py"
    defaults_py.write_text(
        f'"""synthetic"""\n\n{defaults_name}: dict[str, str] = {{\n{body}\n}}\n',
        encoding="utf-8",
    )

    seeds_sql = migrations_dir / "0000_baseline.seeds.sql"
    seeds_sql.write_text(
        "".join(
            "INSERT INTO app_settings (key, value, category, description, "
            f"is_secret, is_active) VALUES ('{k}', 'v', 'general', '', false, "
            "true) ON CONFLICT (key) DO NOTHING;\n"
            for k in baseline
        ),
        encoding="utf-8",
    )
    # The baseline migration itself is skipped by name; include one so that
    # skip is exercised.
    (migrations_dir / "0000_baseline.py").write_text(
        '"""baseline"""\n# DELETE FROM app_settings WHERE key = \'live_default\';\n',
        encoding="utf-8",
    )

    brain_json = tmp_path / "seed_app_settings.json"
    if brain is not None:
        brain_json.write_text(
            json.dumps({"_meta": {"tier": "free"}, "settings": [{"key": k} for k in brain]}),
            encoding="utf-8",
        )

    for name, text in (migrations or {}).items():
        (migrations_dir / name).write_text(text, encoding="utf-8")

    LINT.MIGRATIONS = migrations_dir
    LINT.BASELINE_SEEDS = seeds_sql
    LINT.DEFAULTS_PY = defaults_py
    LINT.BRAIN_SEED = brain_json


@pytest.fixture(autouse=True)
def _restore_lint_constants():
    """Each test repoints the module's path constants; put the real ones back."""
    saved = {
        name: getattr(LINT, name)
        for name in ("MIGRATIONS", "BASELINE_SEEDS", "DEFAULTS_PY", "BRAIN_SEED", "ALLOWLIST")
    }
    yield
    for name, value in saved.items():
        setattr(LINT, name, value)


def _orphan_migration(*keys: str) -> dict[str, str]:
    quoted = ", ".join(repr(k) for k in keys) + ("," if len(keys) == 1 else "")
    return {"20990101_000000_drop_orphans.py": _ORPHAN_MIGRATION.format(keys=quoted)}


# --- the real tree -----------------------------------------------------------


def test_real_tree_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    assert LINT.main() == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "3 seed source(s)" in out, out


def test_real_tree_reads_every_seed_source() -> None:
    sources = LINT._seed_sources()
    assert set(sources) == {
        "settings_defaults.DEFAULTS",
        "baseline.seeds.sql",
        "brain/seed_app_settings.json",
    }
    # Floors well under the real sizes (~1,550 / 681 / 80): each proves its
    # parser still matches the source's shape, not an exact count.
    floors = {
        "settings_defaults.DEFAULTS": 1000,
        "baseline.seeds.sql": 500,
        "brain/seed_app_settings.json": 50,
    }
    for label, (_, keys) in sources.items():
        assert len(keys) >= floors[label], f"{label} parsed to only {len(keys)} keys"


# --- drift in each source ----------------------------------------------------


def test_deleted_key_still_in_defaults_is_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case the old DEFAULTS exemption hid."""
    _write_tree(
        tmp_path,
        defaults=("live_default", "dead_key"),
        migrations=_orphan_migration("dead_key"),
    )
    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "dead_key" in out
    assert "settings_defaults.DEFAULTS" in out


def test_deleted_key_still_in_baseline_is_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_tree(
        tmp_path,
        baseline=("live_baseline", "dead_key"),
        migrations=_orphan_migration("dead_key"),
    )
    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "dead_key" in out and "baseline.seeds.sql" in out


def test_deleted_key_still_in_brain_seed_is_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_tree(
        tmp_path,
        brain=("live_brain", "dead_key"),
        migrations=_orphan_migration("dead_key"),
    )
    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "dead_key" in out and "brain/seed_app_settings.json" in out


def test_drift_names_every_source_still_seeding_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_tree(
        tmp_path,
        defaults=("live_default", "dead_key"),
        baseline=("live_baseline", "dead_key"),
        migrations=_orphan_migration("dead_key"),
    )
    assert LINT.main() == 1
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "dead_key" in ln)
    assert "settings_defaults.DEFAULTS" in line and "baseline.seeds.sql" in line


def test_sql_literal_delete_is_detected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other deletion shape: a key named in the DELETE's WHERE clause."""
    _write_tree(
        tmp_path,
        defaults=("live_default", "dead_key"),
        migrations={
            "20990101_000000_retire.py": (
                "async def up(pool):\n"
                "    async with pool.acquire() as conn:\n"
                "        await conn.execute(\"DELETE FROM app_settings WHERE key = 'dead_key'\")\n"
            ),
        },
    )
    assert LINT.main() == 1
    assert "dead_key" in capsys.readouterr().out


# --- what counts as a deletion -----------------------------------------------


def test_delete_split_across_adjacent_literals_is_detected() -> None:
    """The ``20260712_203614`` shape: one DELETE written as joined literals."""
    text = (
        "async def up(pool):\n"
        "    async with pool.acquire() as conn:\n"
        "        await conn.execute(\n"
        "            \"DELETE FROM app_settings WHERE key IN (\"\n"
        "            \"'first_dead', \"\n"
        "            \"'second_dead'\"\n"
        "            \")\"\n"
        "        )\n"
    )
    assert LINT._deleted_keys_in(text) == {"first_dead", "second_dead"}


def test_update_after_a_parameterized_delete_is_not_a_deletion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``20260625_120000`` rename shape that used to need an ALLOWLIST entry.

    Over the raw file text, the DELETE's 300-char window ran past the end of
    its own string into the UPDATE and reported ``operator_url_probe_skip_keys``
    as deleted. With ``DEFAULTS`` now a seed source, that would red CI on a
    correct rename migration whose target key is seeded, as it should be.
    """
    rename = '''
async def up(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            old_key,
        )
        await conn.execute(
            """
            UPDATE app_settings
            SET value = replace(value, 'old_server_url', 'new_server_url')
            WHERE key = 'operator_url_probe_skip_keys'
            """
        )
'''
    assert LINT._deleted_keys_in(rename) == set()
    _write_tree(
        tmp_path,
        defaults=("live_default", "operator_url_probe_skip_keys"),
        migrations={"20990101_000000_rename_old_server_url.py": rename},
    )
    assert LINT.main() == 0, capsys.readouterr().out


def test_docstring_describing_a_delete_is_not_a_deletion() -> None:
    text = (
        '"""Follow-up to a migration that ran\n'
        "DELETE FROM app_settings WHERE key = 'still_live_key' by mistake.\n"
        '"""\n'
        "async def up(pool):\n"
        '    """Nothing here runs DELETE FROM app_settings WHERE key = \'other_key\'."""\n'
    )
    assert LINT._deleted_keys_in(text) == set()


def test_annotated_orphan_tuple_is_detected() -> None:
    text = (
        "ORPHANED_KEYS: tuple[str, ...] = ('annotated_dead',)\n"
        "async def up(pool):\n"
        "    async with pool.acquire() as conn:\n"
        "        await conn.execute(\n"
        "            'DELETE FROM app_settings WHERE key = ANY($1::text[])',\n"
        "            list(ORPHANED_KEYS),\n"
        "        )\n"
    )
    assert LINT._deleted_keys_in(text) == {"annotated_dead"}


# --- clean outcomes ----------------------------------------------------------


def test_key_removed_from_every_source_is_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_tree(tmp_path, migrations=_orphan_migration("dead_key"))
    assert LINT.main() == 0
    assert "1 migration-deleted keys" in capsys.readouterr().out


def test_baseline_migration_is_not_read_as_a_deletion(tmp_path: Path) -> None:
    """``0000_baseline.py`` is skipped by name, so its text never counts."""
    _write_tree(tmp_path)  # its baseline.py "deletes" live_default, a DEFAULTS key
    assert LINT.main() == 0


def test_allowlisted_key_is_not_drift(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        defaults=("live_default", "reseeded_key"),
        migrations=_orphan_migration("reseeded_key"),
    )
    LINT.ALLOWLIST = {"reseeded_key": "deleted so seed_all_defaults re-inserts a corrected default"}
    assert LINT.main() == 0


def test_missing_brain_seed_is_tolerated(tmp_path: Path) -> None:
    """The brain seed is optional (an install may not ship it); the rest still run."""
    _write_tree(
        tmp_path,
        brain=None,
        defaults=("live_default", "dead_key"),
        migrations=_orphan_migration("dead_key"),
    )
    assert "brain/seed_app_settings.json" not in LINT._seed_sources()
    assert LINT.main() == 1


# --- guards on the guard -----------------------------------------------------


def test_stale_allowlist_entry_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An entry whose migration is gone would silently excuse the next one."""
    _write_tree(tmp_path)
    LINT.ALLOWLIST = {"long_gone_key": "migration 20260101_000000 over-pruned this"}
    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "STALE ALLOWLIST" in out and "long_gone_key" in out


def test_real_allowlist_has_no_stale_entries() -> None:
    """Direct pin on the shipped ALLOWLIST (``main()`` also enforces it)."""
    deleted: set[str] = set()
    for mig in LINT.MIGRATIONS.glob("*.py"):
        if mig.name != "0000_baseline.py":
            deleted |= LINT._deleted_keys_in(mig.read_text(encoding="utf-8"))
    assert set(LINT.ALLOWLIST) <= deleted, set(LINT.ALLOWLIST) - deleted


def test_defaults_that_parse_to_nothing_fail_loud(tmp_path: Path) -> None:
    """A renamed DEFAULTS dict must not read as "DEFAULTS seeds nothing"."""
    _write_tree(
        tmp_path,
        defaults=("live_default", "dead_key"),
        defaults_name="SEED_DEFAULTS",
        migrations=_orphan_migration("dead_key"),
    )
    with pytest.raises(SystemExit) as exc:
        LINT.main()
    assert exc.value.code == 1


def test_baseline_that_parses_to_nothing_fails_loud(tmp_path: Path) -> None:
    _write_tree(tmp_path, baseline=())
    with pytest.raises(SystemExit) as exc:
        LINT.main()
    assert exc.value.code == 1
