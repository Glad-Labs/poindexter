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

import json
from itertools import pairwise
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package resolves
# the same way the offsite_backup_watch tests import it.
from poindexter.brain import auto_embed_watch as ae
from poindexter.brain import docker_utils as du
from tests.unit.brain._restart_fakes import RECENT_UPTIME_SECONDS, restart_stub


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
    restart = restart_stub()
    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool,
            age_fn=AsyncMock(return_value=600.0),  # 10 min < 6h
            restart_fn=restart,
            sleep_fn=AsyncMock(),
        )
    )
    assert summary["ok"] is True
    restart.assert_not_awaited()


def test_stale_triggers_restart_then_recovers():
    pool = _make_pool()
    # First read stale (older than 6h), post-restart read fresh.
    ages = iter([6 * 3600 + 100, 30.0])
    age_fn = AsyncMock(side_effect=lambda: next(ages))
    restart = restart_stub()
    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool, age_fn=age_fn, restart_fn=restart, sleep_fn=AsyncMock(),
        )
    )
    restart.assert_awaited_once_with(ae._CONTAINER, pool=pool)
    assert summary["status"] == "recovered"


def test_escalate_emits_firing_alert_after_max_retries():
    executed: list = []
    pool = _make_pool(executed=executed)
    restart = restart_stub()
    # Always stale => burn through 2 retries across 3 cycles, then escalate.
    age_fn = AsyncMock(return_value=6 * 3600 + 100)

    def run():
        return __import__("asyncio").run(
            ae.run_auto_embed_watch_probe(
                pool, age_fn=age_fn, restart_fn=restart, sleep_fn=AsyncMock(),
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


def test_retry_sleep_does_not_block_event_loop():
    """The between-retry wait must yield to the loop, not freeze it.

    The brain runs every probe by awaiting it sequentially on a single event
    loop (brain_daemon.py). A blocking ``time.sleep(retry_delay)`` here froze
    the whole watchdog — no other probe, heartbeat, or queue work ran for up to
    ``retry_delay`` seconds. Drive stale -> restart -> wait -> recover with the
    REAL default sleep seam and assert a co-scheduled 10 ms ticker never saw a
    large gap (which would mean the loop was blocked).
    """
    import asyncio

    pool = _make_pool(setting_values={ae.RETRY_DELAY_KEY: "1"})
    ages = iter([6 * 3600 + 100, 30.0])  # stale, then fresh after restart
    age_fn = AsyncMock(side_effect=lambda: next(ages))
    restart = restart_stub()

    async def scenario():
        loop = asyncio.get_running_loop()
        stamps: list[float] = []
        running = True

        async def ticker():
            while running:
                stamps.append(loop.time())
                await asyncio.sleep(0.01)

        t = asyncio.create_task(ticker())
        # No sleep_fn injected: exercise the real default seam.
        summary = await ae.run_auto_embed_watch_probe(
            pool, age_fn=age_fn, restart_fn=restart,
        )
        running = False
        await t
        return summary, stamps

    summary, stamps = asyncio.run(scenario())
    assert summary["status"] == "recovered"
    # A blocking sleep on the brain loop starves the ticker entirely; an
    # awaitable asyncio.sleep lets it tick ~100x during the 1 s wait.
    assert len(stamps) > 5, (
        "event loop was starved during the retry wait — a synchronous sleep is "
        "running on the brain loop"
    )
    gaps = [b - a for a, b in pairwise(stamps)]
    assert max(gaps) < 0.5, (
        f"event loop blocked ~{max(gaps):.2f}s during the retry wait"
    )


def test_escalate_alert_severity_is_warning():
    """auto_embed escalation is `warning` (Discord), not `critical` — stale
    embeddings degrade search/memory but don't block the pipeline or risk data
    loss. This is the distinguishing call vs offsite_backup_watch (critical)."""
    executed: list = []
    pool = _make_pool(executed=executed)
    restart = restart_stub()
    age_fn = AsyncMock(return_value=6 * 3600 + 100)

    def run():
        return __import__("asyncio").run(
            ae.run_auto_embed_watch_probe(
                pool, age_fn=age_fn, restart_fn=restart, sleep_fn=AsyncMock(),
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


# ---------------------------------------------------------------------------
# The restart is docker_utils.restart_container (the brain's shared helper)
# ---------------------------------------------------------------------------

_STALE = 6 * 3600 + 100


def _audit_events(executed: list) -> list[str]:
    return [a[0] for q, a in executed if "audit_log" in q]


def test_missing_container_skips_the_wait_and_notifies_nothing():
    """Mid-recreate the container name is unbound for a second or two, so
    nothing was restarted: no retry-delay wait, no re-read, no notify. Before
    the shared helper this was a failed restart ("No such container")."""
    executed: list = []
    pool = _make_pool(executed=executed)
    age_fn = AsyncMock(return_value=_STALE)
    sleep_fn = AsyncMock()
    notify = MagicMock()

    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool, age_fn=age_fn, restart_fn=restart_stub(status=du.RESTART_MISSING),
            sleep_fn=sleep_fn, notify_fn=notify,
        )
    )

    assert summary == {"ok": False, "status": "container_missing", "retries_used": 1}
    sleep_fn.assert_not_awaited()
    assert age_fn.await_count == 1
    notify.assert_not_called()
    events = _audit_events(executed)
    assert "probe.auto_embed_restart_skipped" in events
    assert "probe.auto_embed_restart_failed" not in events


def test_a_container_that_stays_missing_still_escalates():
    """Each missing-container cycle uses a retry, so an embedder whose
    container never comes back still ends at the firing alert."""
    executed: list = []
    pool = _make_pool(executed=executed)
    kw = {
        "age_fn": AsyncMock(return_value=_STALE),
        "restart_fn": restart_stub(status=du.RESTART_MISSING),
        "sleep_fn": AsyncMock(),
        "notify_fn": MagicMock(),
    }

    statuses = [
        __import__("asyncio").run(ae.run_auto_embed_watch_probe(pool, **kw))["status"]
        for _ in range(3)
    ]

    assert statuses == ["container_missing", "container_missing", "escalated"]
    assert any(
        "alert_events" in q and len(a) > 2 and a[2] == "firing" for q, a in executed
    )


def test_recently_started_container_skips_the_wait_and_notifies_nothing():
    """The embedder was restarted moments ago (deploy-sync, compose, the
    restart policy), so docker_utils declined to restart it again: nothing was
    restarted, so there is no retry-delay wait, no re-read and no notify. Not
    a failed restart either."""
    executed: list = []
    pool = _make_pool(executed=executed)
    age_fn = AsyncMock(return_value=_STALE)
    sleep_fn = AsyncMock()
    notify = MagicMock()

    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool, age_fn=age_fn,
            restart_fn=restart_stub(status=du.RESTART_RECENTLY_STARTED),
            sleep_fn=sleep_fn, notify_fn=notify,
        )
    )

    assert summary == {"ok": False, "status": "container_recently_started", "retries_used": 1}
    sleep_fn.assert_not_awaited()
    assert age_fn.await_count == 1
    notify.assert_not_called()
    events = _audit_events(executed)
    assert "probe.auto_embed_restart_skipped" in events
    assert "probe.auto_embed_restart_failed" not in events


def test_the_recently_started_audit_row_says_why_and_how_long_it_had_been_up():
    executed: list = []
    pool = _make_pool(executed=executed)

    __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool, age_fn=AsyncMock(return_value=_STALE),
            restart_fn=restart_stub(status=du.RESTART_RECENTLY_STARTED),
            sleep_fn=AsyncMock(), notify_fn=MagicMock(),
        )
    )

    row = next(
        json.loads(a[2]) for q, a in executed
        if "audit_log" in q and a[0] == "probe.auto_embed_restart_skipped"
    )
    assert row["restart_status"] == du.RESTART_RECENTLY_STARTED
    assert row["uptime_seconds"] == RECENT_UPTIME_SECONDS
    assert row["retries_used"] == 1
    assert "started" in row["detail"] and "not restarted" in row["detail"]


def test_a_container_that_keeps_restarting_still_escalates():
    """Each guarded cycle uses a retry, as a missing container's does: the
    guard can hold a restart off, but it cannot postpone the escalation for an
    embedder that keeps getting restarted under the watch."""
    executed: list = []
    pool = _make_pool(executed=executed)
    kw = {
        "age_fn": AsyncMock(return_value=_STALE),
        "restart_fn": restart_stub(status=du.RESTART_RECENTLY_STARTED),
        "sleep_fn": AsyncMock(),
        "notify_fn": MagicMock(),
    }

    statuses = [
        __import__("asyncio").run(ae.run_auto_embed_watch_probe(pool, **kw))["status"]
        for _ in range(3)
    ]

    assert statuses == [
        "container_recently_started", "container_recently_started", "escalated",
    ]
    assert any(
        "alert_events" in q and len(a) > 2 and a[2] == "firing" for q, a in executed
    )


@pytest.mark.parametrize(
    ("status", "notified"),
    [
        (du.RESTART_NO_DOCKER_CLI, True),
        (du.RESTART_FAILED, False),
        (du.RESTART_TIMED_OUT, False),
        (du.RESTART_ERROR, False),
    ],
)
def test_only_a_missing_docker_cli_notifies(status, notified):
    executed: list = []
    pool = _make_pool(executed=executed)
    notify = MagicMock()

    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool, age_fn=AsyncMock(return_value=_STALE),
            restart_fn=restart_stub(status=status), sleep_fn=AsyncMock(),
            notify_fn=notify,
        )
    )

    assert summary["status"] == "restart_failed"
    assert notify.called is notified
    assert "probe.auto_embed_restart_failed" in _audit_events(executed)


def test_default_restart_is_the_shared_helper_given_the_pool(monkeypatch):
    """With no ``restart_fn`` the restart goes through
    ``docker_utils.restart_container`` with the pool, so it waits
    ``app_settings.brain_docker_restart_timeout_seconds``."""
    helper = restart_stub()
    monkeypatch.setattr(du, "restart_container", helper)
    pool = _make_pool()
    ages = iter([_STALE, 30.0])

    summary = __import__("asyncio").run(
        ae.run_auto_embed_watch_probe(
            pool, age_fn=AsyncMock(side_effect=lambda: next(ages)), sleep_fn=AsyncMock(),
        )
    )

    helper.assert_awaited_once_with(ae._CONTAINER, pool=pool)
    assert summary["status"] == "recovered"
