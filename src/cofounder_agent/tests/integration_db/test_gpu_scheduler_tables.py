"""Integration: the GPU-scheduler observability tables exist after migrations
and behave as the P0 layer expects (poindexter#914, plan Task A1).

Runs against the fully-migrated throwaway DB (the ``schema_loaded`` chain
behind ``test_txn``), proving migration
``20260726_181025_add_gpu_scheduler_observability_tables`` yields the schema
``services/gpu_lease_stats.py`` + ``services/gpu_queue_mirror.py`` write to.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_tables_exist(test_txn) -> None:
    stats = await test_txn.fetchval("SELECT to_regclass('gpu_lease_stats')")
    queue = await test_txn.fetchval("SELECT to_regclass('gpu_queue')")
    assert stats is not None, "gpu_lease_stats missing after migrations"
    assert queue is not None, "gpu_queue missing after migrations"


async def test_lease_stats_upsert_folds_in_place(test_txn) -> None:
    """The (owner, phase) PK makes the writer's ON CONFLICT upsert update in
    place — the exact SQL services/gpu_lease_stats.py issues."""
    upsert = """
        INSERT INTO gpu_lease_stats
            (owner, phase, samples, ewma_ms, p50_ms, p90_ms, q_state, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now())
        ON CONFLICT (owner, phase) DO UPDATE SET
            samples = EXCLUDED.samples,
            ewma_ms = EXCLUDED.ewma_ms,
            p50_ms = EXCLUDED.p50_ms,
            p90_ms = EXCLUDED.p90_ms,
            q_state = EXCLUDED.q_state,
            updated_at = now()
    """
    await test_txn.execute(
        upsert, "ollama", "writer", 1, 100.0, 100.0, 100.0, json.dumps({"warmup": [100.0]})
    )
    await test_txn.execute(
        upsert, "ollama", "writer", 2, 150.0, 125.0, 190.0, json.dumps({"warmup": [100.0, 200.0]})
    )
    rows = await test_txn.fetch(
        "SELECT samples, ewma_ms FROM gpu_lease_stats WHERE owner='ollama' AND phase='writer'"
    )
    assert len(rows) == 1, "PK upsert must update in place, not duplicate"
    assert rows[0]["samples"] == 2
    assert rows[0]["ewma_ms"] == 150.0


async def test_queue_round_trip_and_reap_horizon(test_txn) -> None:
    """Insert → visible with waiting_s → delete; and the orphan-reap predicate
    only touches rows past the horizon (a live waiter is never reaped)."""
    row_id = await test_txn.fetchval(
        "INSERT INTO gpu_queue (pid, owner, model, phase, priority) "
        "VALUES (4242, 'ollama', 'gemma', 'writer', 'pipeline') RETURNING id"
    )
    got = await test_txn.fetchrow(
        "SELECT pid, owner, EXTRACT(EPOCH FROM (now() - enqueued_at)) AS waiting_s "
        "FROM gpu_queue WHERE id = $1",
        row_id,
    )
    assert got["pid"] == 4242 and got["owner"] == "ollama"
    assert float(got["waiting_s"]) >= 0.0

    # A fresh row survives the reap predicate (mirror's _ORPHAN_HORIZON_S=1200).
    reaped = await test_txn.execute(
        "DELETE FROM gpu_queue WHERE enqueued_at < now() - ($1::int * interval '1 second')",
        1200,
    )
    assert reaped == "DELETE 0"
    still = await test_txn.fetchval("SELECT count(*) FROM gpu_queue WHERE id = $1", row_id)
    assert still == 1

    # A backdated row IS reaped.
    await test_txn.execute(
        "UPDATE gpu_queue SET enqueued_at = now() - interval '2 hours' WHERE id = $1",
        row_id,
    )
    reaped = await test_txn.execute(
        "DELETE FROM gpu_queue WHERE enqueued_at < now() - ($1::int * interval '1 second')",
        1200,
    )
    assert reaped == "DELETE 1"
