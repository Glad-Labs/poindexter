"""Unit tests for brain/migration_drift_probe.py.

Focused coverage for the async-safety contract. The brain awaits every probe
sequentially on a single event loop (brain_daemon.py), so
``run_migration_drift_probe``'s blocking seams — ``_fetch_health`` (urllib GET),
``_sync_deploy_checkout`` (git), ``_restart_worker_container`` (docker), and the
``_wait_for_worker_healthy`` poll loop (urllib + ``time.sleep``) — MUST be
offloaded via ``asyncio.to_thread``. A synchronous call on this loop freezes the
whole watchdog for the duration.
"""
from __future__ import annotations

import asyncio
import time
from itertools import pairwise
from unittest.mock import AsyncMock, MagicMock

from brain import migration_drift_probe as md


def _make_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)  # auto_recover -> False
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    return pool


def test_unknown_health_short_circuits():
    """A health response without a readable migrations block returns
    ``unknown`` and never touches the restart/sync/wait seams."""
    pool = _make_pool()
    summary = asyncio.run(
        md.run_migration_drift_probe(
            pool, health_fetcher=lambda: {"_error": "worker down"},
        )
    )
    assert summary["status"] == "unknown"


def test_blocking_health_fetch_does_not_block_event_loop():
    """``_fetch_health`` does a blocking urllib GET; the probe must offload it
    via ``asyncio.to_thread`` so the brain loop stays free while it runs."""
    pool = _make_pool()

    def blocking_health():
        time.sleep(0.4)  # simulate a slow /api/health GET
        return {"_error": "worker down"}

    async def scenario():
        loop = asyncio.get_running_loop()
        stamps: list[float] = []
        running = True

        async def ticker():
            while running:
                stamps.append(loop.time())
                await asyncio.sleep(0.01)

        t = asyncio.create_task(ticker())
        summary = await md.run_migration_drift_probe(
            pool, health_fetcher=blocking_health,
        )
        running = False
        await t
        return summary, stamps

    summary, stamps = asyncio.run(scenario())
    assert summary["status"] == "unknown"
    # A blocking health fetch on the brain loop starves the ticker; an
    # offloaded one lets it keep ticking during the 0.4 s wait.
    assert len(stamps) > 5, "event loop was starved during the blocking health fetch"
    gaps = [b - a for a, b in pairwise(stamps)]
    assert max(gaps) < 0.3, f"event loop blocked ~{max(gaps):.2f}s during health fetch"
