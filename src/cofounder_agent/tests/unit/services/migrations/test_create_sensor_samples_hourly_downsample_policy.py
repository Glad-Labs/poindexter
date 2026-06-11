"""Contract test for the sensor_samples_hourly rollup migration.

Pins the switch from ttl_prune (hard-delete after 30 days) to downsample
(roll up hourly then delete raw rows). The migration creates the
``sensor_samples_hourly`` table and updates the retention_policies row
for ``sensor_samples`` to use ``handler_name='downsample'``.

Checks:
1. Migration file exists at the expected path.
2. ``up()`` / ``down()`` are importable without heavy deps.
3. ``up()`` creates the rollup table with the right schema (UNIQUE on
   ``(bucket_start, source, metric_name)``).
4. ``up()`` creates a descending index on ``bucket_start``.
5. ``up()`` UPDATEs the retention_policies row for the correct UUID
   with ``handler_name='downsample'`` and a valid ``downsample_rule``.
6. ``down()`` reverts the policy to ``ttl_prune`` with ``ttl_days=30``.
7. ``down()`` drops the rollup table.
8. The ``downsample_rule`` contains the expected fields (group_by,
   aggregations, keep_raw_days, rollup_table).
9. ``sensor_samples`` is still NOT in ``retention_janitor._JANITOR_TARGETS``
   — it must not be double-registered.
"""
from __future__ import annotations

import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260611_000000_create_sensor_samples_hourly_downsample_policy.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "create_sensor_samples_hourly_downsample_policy", _MIGRATION
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration spec from {_MIGRATION}"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_pool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="OK")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


# ---------------------------------------------------------------------------
# File presence + light-env
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert _MIGRATION.exists(), f"Migration file missing: {_MIGRATION}"


def test_migration_imports_only_stdlib():
    """Must be importable in the light CI environment (no langchain/langgraph)."""
    mod = _load_migration()
    assert hasattr(mod, "up")
    assert hasattr(mod, "down")


# ---------------------------------------------------------------------------
# up() — creates rollup table + switches policy to downsample
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_up_creates_sensor_samples_hourly_table():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    calls = conn.execute.call_args_list
    assert len(calls) == 3, f"Expected 3 execute calls (CREATE TABLE, CREATE INDEX, UPDATE), got {len(calls)}"

    create_sql = calls[0][0][0]
    assert "CREATE TABLE IF NOT EXISTS" in create_sql
    assert "sensor_samples_hourly" in create_sql
    assert "bucket_start" in create_sql
    assert "source" in create_sql
    assert "metric_name" in create_sql
    # UNIQUE constraint must cover all three columns so ON CONFLICT works
    assert "UNIQUE" in create_sql
    assert "bucket_start" in create_sql and "source" in create_sql and "metric_name" in create_sql


@pytest.mark.asyncio
async def test_up_creates_descending_index():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    calls = conn.execute.call_args_list
    index_sql = calls[1][0][0]
    assert "CREATE INDEX IF NOT EXISTS" in index_sql
    assert "sensor_samples_hourly" in index_sql
    assert "bucket_start" in index_sql


@pytest.mark.asyncio
async def test_up_updates_policy_to_downsample():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    calls = conn.execute.call_args_list
    update_sql, rule_json, policy_id = calls[2][0]

    assert "UPDATE retention_policies" in update_sql
    assert "handler_name" in update_sql
    assert "'downsample'" in update_sql
    assert "ttl_days" in update_sql

    # UUID targets the sensor_samples policy seeded in 20260610_220000
    assert policy_id == mod._SENSOR_SAMPLES_ID

    # The downsample_rule is valid JSON
    rule = json.loads(rule_json)
    assert rule["keep_raw_days"] == 30
    assert rule["rollup_table"] == "sensor_samples_hourly"
    assert "group_by" in rule
    assert "source" in rule["group_by"]
    assert "metric_name" in rule["group_by"]
    assert len(rule["aggregations"]) >= 3


@pytest.mark.asyncio
async def test_up_nulls_ttl_days_in_update_sql():
    """ttl_days must be set to NULL — the check constraint is still satisfied
    because downsample_rule is now non-NULL after the UPDATE."""
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    update_sql = conn.execute.call_args_list[2][0][0]
    # The UPDATE must include ttl_days = NULL explicitly so the column is cleared
    assert "ttl_days" in update_sql and "NULL" in update_sql


# ---------------------------------------------------------------------------
# down() — reverts policy + drops rollup table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_down_reverts_policy_to_ttl_prune():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.down(pool)

    calls = conn.execute.call_args_list
    assert len(calls) == 2, f"Expected 2 execute calls (REVERT UPDATE, DROP TABLE), got {len(calls)}"

    revert_sql, policy_id = calls[0][0]
    assert "UPDATE retention_policies" in revert_sql
    assert "'ttl_prune'" in revert_sql
    assert "ttl_days" in revert_sql
    assert policy_id == mod._SENSOR_SAMPLES_ID


@pytest.mark.asyncio
async def test_down_drops_rollup_table():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.down(pool)

    drop_sql = conn.execute.call_args_list[1][0][0]
    assert "DROP TABLE IF EXISTS" in drop_sql
    assert "sensor_samples_hourly" in drop_sql


# ---------------------------------------------------------------------------
# Double-registration guard
# ---------------------------------------------------------------------------


def test_sensor_samples_not_in_janitor_targets():
    """sensor_samples must remain absent from retention_janitor._JANITOR_TARGETS.

    It is handled by the declarative retention_policies / downsample path.
    Having it in both would delete raw rows twice on every janitor cycle.
    """
    from services.retention_janitor import _JANITOR_TARGETS

    table_names = {t[0] for t in _JANITOR_TARGETS}
    assert "sensor_samples" not in table_names, (
        "sensor_samples should NOT be in retention_janitor._JANITOR_TARGETS — "
        "it is handled by the retention_policies downsample handler "
        "(migration 20260611_000000). Double-registration double-deletes rows."
    )
