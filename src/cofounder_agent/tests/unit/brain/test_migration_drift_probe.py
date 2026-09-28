"""Unit tests for brain/migration_drift_probe.py.

Focused coverage for the async-safety contract. The brain awaits every probe
sequentially on a single event loop (brain_daemon.py), so
``run_migration_drift_probe``'s blocking seams — ``_fetch_health`` (urllib GET),
``_sync_deploy_checkout`` (git) and the ``_wait_for_worker_healthy`` poll loop
(urllib + ``time.sleep``) — MUST be offloaded via ``asyncio.to_thread``. A
synchronous call on this loop freezes the whole watchdog for the duration. The
worker restart is ``docker_utils.restart_container``, which offloads its own
docker calls (tests/unit/brain/test_docker_utils_restart.py).
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from unittest.mock import AsyncMock, MagicMock

from poindexter.brain import docker_utils as du
from poindexter.brain import migration_drift_probe as md


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


def _migrations_health(pending: int) -> dict:
    return {
        "status": "healthy",
        "components": {
            "migrations": {"pending": pending, "applied": 5, "latest_applied": "x.py"},
        },
    }


def test_default_restart_is_the_shared_helper_with_the_pool(monkeypatch):
    """With no ``restart_fn`` injected the worker restart goes through
    ``docker_utils.restart_container`` and is handed the pool, so it waits
    ``app_settings.brain_docker_restart_timeout_seconds``. The probe used to
    hardcode 30 s, under the worker's 75 s stop grace, and page a restart that
    dockerd went on to finish."""
    helper = AsyncMock(return_value=du.ContainerRestart(
        md.WORKER_CONTAINER, du.RESTART_OK, f"restarted {md.WORKER_CONTAINER}", 90,
    ))
    monkeypatch.setattr(du, "restart_container", helper)
    settings = {
        md.AUTO_RECOVER_SETTING_KEY: "true",
        md.DEFER_WHILE_INFLIGHT_SETTING_KEY: "false",
    }

    async def fetchval(_query, *args):
        return settings.get(args[0]) if args else 0

    pool = _make_pool()
    pool.fetchval = AsyncMock(side_effect=fetchval)

    summary = asyncio.run(
        md.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            wait_fn=lambda: (True, _migrations_health(0)),
            health_fetcher=lambda: _migrations_health(1),
        )
    )

    helper.assert_awaited_once_with(md.WORKER_CONTAINER, pool=pool)
    assert summary["status"] == "recovered"


def test_a_worker_that_started_moments_ago_is_not_restarted_and_nothing_pages(monkeypatch):
    """Through the real ``docker_utils`` helper, with only ``subprocess.run``
    faked underneath. The worker's ``State.StartedAt`` is 30 s ago (deploy-sync,
    another brain path's heal or compose restarted it), so ``docker restart``
    never runs: a second restart would kill it mid-boot, and applying
    migrations is what that boot does. No critical page and no health wait."""
    started = datetime.now(UTC) - timedelta(seconds=30)
    stamp = started.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=f"running {stamp}\n", stderr=""))
    monkeypatch.setattr(du.subprocess, "run", run)
    settings = {
        md.AUTO_RECOVER_SETTING_KEY: "true",
        md.DEFER_WHILE_INFLIGHT_SETTING_KEY: "false",
    }

    async def fetchval(_query, *args):
        return settings.get(args[0]) if args else 0

    pool = _make_pool()
    pool.fetchval = AsyncMock(side_effect=fetchval)
    notify = MagicMock()
    wait = MagicMock(return_value=(True, _migrations_health(0)))

    summary = asyncio.run(
        md.run_migration_drift_probe(
            pool,
            notify_fn=notify,
            wait_fn=wait,
            health_fetcher=lambda: _migrations_health(1),
        )
    )

    assert summary["status"] == "recover_worker_recently_started"
    assert [c.args[0][1] for c in run.call_args_list] == ["inspect"], "no `docker restart`"
    notify.assert_not_called()
    wait.assert_not_called()
