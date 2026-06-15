"""Integration: app_settings.updated_at is stamped on value change.

Forensics regression from the 2026-06-15 beacon incident — a surgical
``jsonb`` UPDATE to ``operator_url_probe_target_overrides`` changed
``value`` without touching ``updated_at``, so the row read days-stale and
couldn't answer "when did this setting last change?" during triage. A
``BEFORE UPDATE OF value`` trigger now stamps ``updated_at = now()`` (see
migration ``20260615_040000_app_settings_updated_at_trigger``).

``now()`` is frozen for the whole transaction, and ``test_txn`` runs each
test inside one rolled-back transaction — so we seed an explicitly OLD
``updated_at`` and assert the post-update value jumped past it, rather
than comparing two ``now()``-valued reads (which would be identical).
"""

from __future__ import annotations

import datetime as dt

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_OLD = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)


async def test_updated_at_bumped_on_value_change(test_txn) -> None:
    await test_txn.execute(
        """
        INSERT INTO app_settings (key, value, category, updated_at)
        VALUES ('trigger_probe_value_change', 'orig', 'testing', $1)
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = $1
        """,
        _OLD,
    )
    before = await test_txn.fetchval(
        "SELECT updated_at FROM app_settings WHERE key = 'trigger_probe_value_change'"
    )
    assert before == _OLD  # sanity: the seed's old timestamp took

    await test_txn.execute(
        "UPDATE app_settings SET value = 'changed' "
        "WHERE key = 'trigger_probe_value_change'"
    )
    after = await test_txn.fetchval(
        "SELECT updated_at FROM app_settings WHERE key = 'trigger_probe_value_change'"
    )

    assert after > before, (
        f"updated_at must advance when value changes (before={before}, after={after})"
    )


async def test_updated_at_untouched_when_value_not_in_update(test_txn) -> None:
    # Scoped to OF value (mirrors the auto-encrypt trigger): an UPDATE that
    # does not set `value` must NOT bump updated_at, so the timestamp means
    # "when the value last changed", not "when any column changed".
    await test_txn.execute(
        """
        INSERT INTO app_settings (key, value, category, is_active, updated_at)
        VALUES ('trigger_probe_other_col', 'v', 'testing', true, $1)
        ON CONFLICT (key) DO UPDATE
            SET is_active = true, updated_at = $1
        """,
        _OLD,
    )
    await test_txn.execute(
        "UPDATE app_settings SET is_active = false "
        "WHERE key = 'trigger_probe_other_col'"
    )
    after = await test_txn.fetchval(
        "SELECT updated_at FROM app_settings WHERE key = 'trigger_probe_other_col'"
    )
    assert after == _OLD, (
        f"updated_at must NOT change on a non-value UPDATE (got {after})"
    )
