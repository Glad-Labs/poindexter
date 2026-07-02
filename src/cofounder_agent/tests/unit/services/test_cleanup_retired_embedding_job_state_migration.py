"""Tests for migration 20260702_054050_cleanup_retired_embedding_job_state.

The 2026-06-24 embedding-retention consolidation retired three scheduler
jobs but left their ``job_run_state`` rows (which the metrics exporter
re-publishes as freshness gauges every scrape — ghost "stale job" series
on System Health) and their orphaned ``plugin.job.*`` app_settings rows.
This migration deletes both; these tests pin the exact target set so a
future rename doesn't silently widen or narrow the deletion.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

MIGRATION = "services.migrations.20260702_054050_cleanup_retired_embedding_job_state"

RETIRED = {
    "prune_orphan_embeddings",
    "prune_stale_embeddings",
    "collapse_old_embeddings",
}


def _load():
    return importlib.import_module(MIGRATION)


class _FakePool:
    def __init__(self):
        self.conn = AsyncMock()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class TestCleanupRetiredEmbeddingJobState:
    def test_runner_interface(self):
        mod = _load()
        assert callable(mod.up)
        assert callable(mod.down)

    @pytest.mark.asyncio
    async def test_up_deletes_exactly_the_retired_job_state(self):
        mod = _load()
        pool = _FakePool()

        await mod.up(pool)

        job_state_deletes = set()
        settings_deletes = set()
        for call in pool.conn.execute.await_args_list:
            sql, param = call.args
            if "job_run_state" in sql:
                job_state_deletes.add(param)
            elif "app_settings" in sql:
                settings_deletes.add(param)
            else:  # pragma: no cover — a new table target must be reviewed
                raise AssertionError(f"unexpected statement: {sql}")

        assert job_state_deletes == RETIRED
        assert settings_deletes == {f"plugin.job.{name}" for name in RETIRED}

    @pytest.mark.asyncio
    async def test_down_is_a_noop(self):
        mod = _load()
        pool = _FakePool()

        await mod.down(pool)

        pool.conn.execute.assert_not_awaited()
