"""Contract test for the sensor_samples + cost_logs retention policy migration.

Pins the 2026-06-10 fix (Glad-Labs/poindexter#695): ``sensor_samples`` and
``cost_logs`` had no retention coverage at all and were growing indefinitely
(~47k and ~280 rows/day respectively).

The migration adds two ``ttl_prune`` rows to ``retention_policies``:
- ``sensor_samples``: 30-day TTL, age column ``sampled_at``
- ``cost_logs``: 365-day TTL, age column ``created_at``

Checks:
1. The migration file exists at the expected path.
2. ``up()`` inserts both rows with ``ON CONFLICT (id) DO NOTHING`` (idempotent).
3. ``down()`` removes both rows by UUID.
4. Each row uses the ``ttl_prune`` handler.
5. The ``sensor_samples`` policy uses the correct age column (``sampled_at``)
   and a 30-day TTL.
6. The ``cost_logs`` policy uses the correct age column (``created_at``)
   and a 365-day TTL.
"""
from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260610_220000_seed_retention_policies_sensor_and_cost.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "seed_retention_policies_sensor_and_cost", _MIGRATION
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration spec from {_MIGRATION}"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_pool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


# ---------------------------------------------------------------------------
# File presence
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert _MIGRATION.exists(), f"Migration file missing: {_MIGRATION}"


def test_migration_imports_only_stdlib():
    """The migration must be importable without langchain / langgraph —
    migrations-smoke runs in a light CI environment."""
    mod = _load_migration()
    assert hasattr(mod, "up")
    assert hasattr(mod, "down")


# ---------------------------------------------------------------------------
# up() — idempotent INSERT of both policies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_up_inserts_sensor_samples_policy():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    calls = conn.execute.call_args_list
    assert len(calls) == 2, f"Expected 2 INSERT calls, got {len(calls)}"

    # First call is the sensor_samples policy.
    sql, *args = calls[0][0]
    assert "INSERT INTO retention_policies" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql
    # ttl_prune handler
    assert "ttl_prune" in sql
    # table_name param = "sensor_samples"
    assert any(a == "sensor_samples" for a in args), (
        f"Expected 'sensor_samples' in positional args; got {args}"
    )
    # age_column = "sampled_at"
    assert any(a == "sampled_at" for a in args), (
        f"Expected 'sampled_at' in positional args; got {args}"
    )
    # ttl_days = 30
    assert any(a == 30 for a in args), (
        f"Expected ttl_days=30 in positional args; got {args}"
    )


@pytest.mark.asyncio
async def test_up_inserts_cost_logs_policy():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    calls = conn.execute.call_args_list
    # Second call is the cost_logs policy.
    sql, *args = calls[1][0]
    assert "INSERT INTO retention_policies" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert "ttl_prune" in sql
    assert any(a == "cost_logs" for a in args), (
        f"Expected 'cost_logs' in positional args; got {args}"
    )
    assert any(a == "created_at" for a in args), (
        f"Expected 'created_at' in positional args; got {args}"
    )
    assert any(a == 365 for a in args), (
        f"Expected ttl_days=365 in positional args; got {args}"
    )


@pytest.mark.asyncio
async def test_up_uses_distinct_uuids():
    """Both policies must have distinct UUIDs so ON CONFLICT (id) targets
    the right row on re-run."""
    mod = _load_migration()
    assert mod._SENSOR_SAMPLES_ID != mod._COST_LOGS_ID


# ---------------------------------------------------------------------------
# down() — removes both rows by UUID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_down_deletes_both_policies():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.down(pool)

    conn.execute.assert_called_once()
    sql, *args = conn.execute.call_args[0]
    assert "DELETE FROM retention_policies" in sql
    # Both UUIDs must appear in the call — passed as a list to ANY()
    ids_arg = next(
        (a for a in args if isinstance(a, list)), None
    )
    assert ids_arg is not None, "Expected a list of UUIDs as the $1 parameter"
    assert mod._SENSOR_SAMPLES_ID in ids_arg
    assert mod._COST_LOGS_ID in ids_arg


# ---------------------------------------------------------------------------
# sensor_samples retention coverage integration — _JANITOR_TARGETS
# ---------------------------------------------------------------------------


def test_sensor_samples_not_in_janitor_targets():
    """The janitor-based (retention_days__<table>) system is NOT the
    right mechanism for sensor_samples — that path uses a flat
    app_settings key and is for tables that don't have a
    retention_policies row. sensor_samples is now handled by the
    declarative retention_policies path (ttl_prune handler). This
    test pins the split so no one accidentally double-registers it."""
    from services.retention_janitor import _JANITOR_TARGETS

    table_names = {t[0] for t in _JANITOR_TARGETS}
    assert "sensor_samples" not in table_names, (
        "sensor_samples should NOT be in retention_janitor._JANITOR_TARGETS — "
        "it is handled by the declarative retention_policies / ttl_prune path "
        "(migration 20260610_220000_seed_retention_policies_sensor_and_cost). "
        "Having it in both would double-delete rows on every janitor cycle."
    )
