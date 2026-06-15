"""Unit tests for brain/offsite_backup_watch.py (poindexter#386).

The offsite-backup watch is the self-heal-before-paging layer for Tier 2
(off-machine restic). Its freshness source is the ``audit_log`` heartbeat
(``offsite_backup_succeeded``), a creds-free DB read — so unlike
``backup_watcher`` it never needs the restic password. These tests cover the
four states: disabled short-circuit, fresh (no restart), stale → restart →
recover, and stale-past-max-retries → escalate (a firing ``offsite_backup_stale``
alert_events row).

All external I/O (the audit_log age read, ``docker restart``, the per-cycle
sleep, the asyncpg pool) is injected/mocked — nothing really restarts and no
test sleeps for real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package resolves
# the same way the backup_watcher tests import it.
from brain import offsite_backup_watch as ow


def _make_pool(*, setting_values=None, firing=None, executed=None):
    pool = MagicMock()
    settings = {
        ow.ENABLED_KEY: "true",
        ow.MAX_AGE_HOURS_KEY: "26",
        ow.MAX_RETRIES_KEY: "2",
        ow.RETRY_DELAY_KEY: "120",
        **(setting_values or {}),
    }
    firing = firing or set()

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        if "alert_events" in query and args and args[0] in firing:
            return {"status": "firing"}
        return None

    async def _execute(query, *args):
        if executed is not None:
            executed.append((query, args))

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock(side_effect=_execute)
    return pool


@pytest.fixture(autouse=True)
def _reset():
    ow._reset_retry_state()
    yield
    ow._reset_retry_state()


def test_disabled_short_circuits():
    pool = _make_pool(setting_values={ow.ENABLED_KEY: "false"})
    summary = __import__("asyncio").run(ow.run_offsite_backup_watch_probe(pool))
    assert summary["status"] == "disabled"


def test_fresh_heartbeat_is_ok_no_restart():
    pool = _make_pool()
    restart = MagicMock()
    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool,
            age_fn=AsyncMock(return_value=600.0),  # 10 min < 26h
            restart_fn=restart,
            sleep_fn=lambda s: None,
        )
    )
    assert summary["ok"] is True
    restart.assert_not_called()


def test_stale_triggers_restart_then_recovers():
    pool = _make_pool()
    # First read stale (older than 26h), post-restart read fresh.
    ages = iter([26 * 3600 + 100, 30.0])
    age_fn = AsyncMock(side_effect=lambda: next(ages))
    restart = MagicMock(return_value=(True, "Restarted"))
    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool, age_fn=age_fn, restart_fn=restart, sleep_fn=lambda s: None,
        )
    )
    restart.assert_called_once_with(ow._CONTAINER)
    assert summary["status"] == "recovered"


def test_escalate_emits_firing_alert_after_max_retries():
    executed: list = []
    pool = _make_pool(executed=executed)
    restart = MagicMock(return_value=(True, "Restarted"))
    # Always stale ⇒ burn through 2 retries across 3 cycles, then escalate.
    age_fn = AsyncMock(return_value=26 * 3600 + 100)

    def run():
        return __import__("asyncio").run(
            ow.run_offsite_backup_watch_probe(
                pool, age_fn=age_fn, restart_fn=restart, sleep_fn=lambda s: None,
            )
        )

    run()
    run()  # 2 restart attempts
    summary = run()  # 3rd cycle escalates
    assert summary["status"] == "escalated"
    # A firing offsite_backup_stale alert_events row was written. status is a
    # bound param ($3), not literal SQL — assert on the args, not query text.
    assert any(
        "alert_events" in q and len(a) > 2 and a[2] == "firing"
        for q, a in executed
    )
