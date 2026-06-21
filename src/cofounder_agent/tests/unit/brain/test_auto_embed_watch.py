"""Unit tests for brain/auto_embed_watch.py.

The auto-embed watch is the self-heal-before-paging layer for the embedder
sidecar. Its freshness source is the ``audit_log`` heartbeat
(``auto_embed_succeeded``) that scripts/auto-embed.py stamps each run — a
creds-free DB read, no Ollama/embedding access. These tests cover the four
states: disabled short-circuit, fresh (no restart), stale -> restart ->
recover, and stale-past-max-retries -> escalate (a firing ``auto_embed_stale``
alert_events row), plus the warning-not-critical severity choice.

All external I/O (the audit_log age read, ``docker restart``, the per-cycle
sleep, the asyncpg pool) is injected/mocked — nothing really restarts and no
test sleeps for real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package resolves
# the same way the offsite_backup_watch tests import it.
from brain import auto_embed_watch as ae


def _make_pool(*, setting_values=None, firing=None, executed=None):
    pool = MagicMock()
    settings = {
        ae.ENABLED_KEY: "true",
        ae.MAX_AGE_HOURS_KEY: "6",
        ae.MAX_RETRIES_KEY: "2",
        ae.RETRY_DELAY_KEY: "120",
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
    ae._reset_retry_state()
    yield
    ae._reset_retry_state()


def test_disabled_short_circuits():
    pool = _make_pool(setting_values={ae.ENABLED_KEY: "false"})
    summary = __import__("asyncio").run(ae.run_auto_embed_watch_probe(pool))
    assert summary["status"] == "disabled"


def test_fresh_heartbeat_is_ok_no_restart():
    pool = _make_pool()
    restart = MagicMock()
    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool,
            age_fn=AsyncMock(return_value=600.0),  # 10 min < 6h
            restart_fn=restart,
            sleep_fn=lambda s: None,
        )
    )
    assert summary["ok"] is True
    restart.assert_not_called()


def test_stale_triggers_restart_then_recovers():
    pool = _make_pool()
    # First read stale (older than 6h), post-restart read fresh.
    ages = iter([6 * 3600 + 100, 30.0])
    age_fn = AsyncMock(side_effect=lambda: next(ages))
    restart = MagicMock(return_value=(True, "Restarted"))
    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool, age_fn=age_fn, restart_fn=restart, sleep_fn=lambda s: None,
        )
    )
    restart.assert_called_once_with(ae._CONTAINER)
    assert summary["status"] == "recovered"


def test_escalate_emits_firing_alert_after_max_retries():
    executed: list = []
    pool = _make_pool(executed=executed)
    restart = MagicMock(return_value=(True, "Restarted"))
    # Always stale => burn through 2 retries across 3 cycles, then escalate.
    age_fn = AsyncMock(return_value=6 * 3600 + 100)

    def run():
        return __import__("asyncio").run(
            ae.run_auto_embed_watch_probe(
                pool, age_fn=age_fn, restart_fn=restart, sleep_fn=lambda s: None,
            )
        )

    run()
    run()  # 2 restart attempts
    summary = run()  # 3rd cycle escalates
    assert summary["status"] == "escalated"
    # A firing auto_embed_stale alert_events row was written. status is a bound
    # param ($3), not literal SQL — assert on the args, not query text.
    assert any(
        "alert_events" in q and len(a) > 2 and a[2] == "firing"
        for q, a in executed
    )


def test_escalate_alert_severity_is_warning():
    """auto_embed escalation is `warning` (Discord), not `critical` — stale
    embeddings degrade search/memory but don't block the pipeline or risk data
    loss. This is the distinguishing call vs offsite_backup_watch (critical)."""
    executed: list = []
    pool = _make_pool(executed=executed)
    restart = MagicMock(return_value=(True, "Restarted"))
    age_fn = AsyncMock(return_value=6 * 3600 + 100)

    def run():
        return __import__("asyncio").run(
            ae.run_auto_embed_watch_probe(
                pool, age_fn=age_fn, restart_fn=restart, sleep_fn=lambda s: None,
            )
        )

    run()
    run()
    run()  # escalate on the 3rd cycle
    # The firing alert_events INSERT binds severity=$2, status=$3.
    firing = [
        a for q, a in executed
        if "alert_events" in q and len(a) > 2 and a[2] == "firing"
    ]
    assert firing, "expected a firing alert_events insert"
    assert firing[0][1] == "warning"
