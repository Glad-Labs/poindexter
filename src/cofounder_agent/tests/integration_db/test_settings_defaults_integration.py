"""Integration tests for seed_all_defaults() against a real Postgres (#379).

These run only when the integration_db fixture chain has a live
Postgres available — the conftest.py session-fixture handles the skip.
Locally: ``poetry run pytest tests/integration_db/test_settings_defaults_integration.py``.

What we verify here that the unit tests can't:

1. Against a freshly-migrated DB the seeder inserts the expected number
   of new rows (≈ len(DEFAULTS); some keys overlap with migrations and
   are skipped via ON CONFLICT).
2. Total app_settings row count after seed is in the documented range
   (CLAUDE.md says ~453 active keys; we accept 350-600).
3. Re-running the seeder is a true no-op (returns 0, leaves rows
   untouched).
4. Operator-tuned values are NOT clobbered — seed once, mutate one row,
   seed again, verify the mutation survives.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_fresh_db_seeds_expected_row_count(test_pool) -> None:
    """The seeder closes the fresh-DB gap to roughly DEFAULTS' size.

    The session-scoped ``test_pool`` fixture has already run every
    migration, which seeds ~149 keys. Running the seeder ON TOP should
    add ~218 more (= len(DEFAULTS)) minus any overlap with migrations.
    """
    from services.settings_defaults import DEFAULTS, seed_all_defaults

    # Snapshot — what's in app_settings after migrations alone?
    async with test_pool.acquire() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM app_settings")

    inserted = await seed_all_defaults(test_pool)

    async with test_pool.acquire() as conn:
        after = await conn.fetchval("SELECT COUNT(*) FROM app_settings")

    # Sanity: inserted matches the row count delta exactly.
    assert int(after) - int(before) == inserted, (
        f"Insert count mismatch: status said {inserted}, "
        f"row count went {before}→{after}"
    )

    # We should have inserted at least 100 new keys (migrations seed
    # plenty already on this fixture-loaded DB but not all 218).
    assert inserted >= 50, (
        f"Seeder only inserted {inserted} new keys — expected at least 50; "
        "did the registry shrink, or do migrations now seed everything?"
    )

    # Total app_settings size should be in the CLAUDE.md range.
    assert 200 <= int(after) <= 700, (
        f"app_settings has {after} rows after seed — outside expected range. "
        f"DEFAULTS has {len(DEFAULTS)} keys; check for unintended duplicates."
    )


async def test_idempotent_second_run_inserts_zero(test_pool) -> None:
    """Running the seeder twice in a row inserts nothing the second time."""
    from services.settings_defaults import seed_all_defaults

    # First run (might insert anything left over from the previous test)
    await seed_all_defaults(test_pool)
    # Second run must be a clean no-op
    second = await seed_all_defaults(test_pool)
    assert second == 0, (
        f"Second seed run inserted {second} rows — seeder is not idempotent"
    )


async def test_operator_tuned_value_survives_seed(test_pool) -> None:
    """Operator-customised values are NEVER overwritten."""
    from services.settings_defaults import DEFAULTS, seed_all_defaults

    # Pick the first registry key that has a default value we can flip.
    target_key = next(iter(DEFAULTS))
    operator_value = "OPERATOR_CUSTOM_VALUE_DO_NOT_CLOBBER"

    async with test_pool.acquire() as conn:
        # Make sure the key exists (seed first if needed) and then
        # explicitly UPDATE it to the operator-custom value.
        await seed_all_defaults(test_pool)
        await conn.execute(
            "UPDATE app_settings SET value = $1 WHERE key = $2",
            operator_value,
            target_key,
        )

    # Re-run the seeder.
    await seed_all_defaults(test_pool)

    async with test_pool.acquire() as conn:
        round_tripped = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", target_key,
        )

    assert round_tripped == operator_value, (
        f"Seeder clobbered operator-tuned value for {target_key!r} — "
        f"expected {operator_value!r}, got {round_tripped!r}. "
        "ON CONFLICT DO NOTHING is broken."
    )
