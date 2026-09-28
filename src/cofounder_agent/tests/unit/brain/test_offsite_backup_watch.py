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

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package resolves
# the same way the backup_watcher tests import it.
from poindexter.brain import docker_utils as du
from poindexter.brain import offsite_backup_watch as ow
from tests.unit.brain._restart_fakes import RECENT_UPTIME_SECONDS, restart_stub


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
    restart = restart_stub()
    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool,
            age_fn=AsyncMock(return_value=600.0),  # 10 min < 26h
            restart_fn=restart,
            sleep_fn=AsyncMock(),
        )
    )
    assert summary["ok"] is True
    restart.assert_not_awaited()


def test_stale_triggers_restart_then_recovers():
    pool = _make_pool()
    # First read stale (older than 26h), post-restart read fresh.
    ages = iter([26 * 3600 + 100, 30.0])
    age_fn = AsyncMock(side_effect=lambda: next(ages))
    restart = restart_stub()
    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool, age_fn=age_fn, restart_fn=restart, sleep_fn=AsyncMock(),
        )
    )
    restart.assert_awaited_once_with(ow._CONTAINER, pool=pool)
    assert summary["status"] == "recovered"


def test_fresh_heartbeat_resolves_prior_failed_alert():
    """A fresh heartbeat must also auto-resolve a firing offsite_backup_failed
    row — that alertname is emitted directly by the runner's own emit_alert
    in run.sh on a backup failure, and the runner has no mechanism of its own
    to resolve it. Without this, a fixed backup still shows "firing" on the
    dashboard forever (found during the 2026-07-16 B2 storage-cap incident)."""
    executed: list = []
    pool = _make_pool(firing={"offsite_backup_failed"}, executed=executed)
    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool,
            age_fn=AsyncMock(return_value=600.0),  # fresh
            restart_fn=restart_stub(),
            sleep_fn=AsyncMock(),
        )
    )
    assert summary["status"] == "auto_resolved"
    assert any(
        "alert_events" in q
        and len(a) > 2
        and a[0] == "offsite_backup_failed"
        and a[2] == "resolved"
        for q, a in executed
    )


def test_fresh_heartbeat_with_no_firing_failed_alert_is_a_noop():
    """The new check must not fire (or error) when nothing is firing —
    guards against always inserting a resolved row on every fresh cycle."""
    executed: list = []
    pool = _make_pool(executed=executed)
    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool,
            age_fn=AsyncMock(return_value=600.0),
            restart_fn=restart_stub(),
            sleep_fn=AsyncMock(),
        )
    )
    assert summary["status"] == "fresh"
    assert not any("alert_events" in q for q, _a in executed)


def test_escalate_emits_firing_alert_after_max_retries():
    executed: list = []
    pool = _make_pool(executed=executed)
    restart = restart_stub()
    # Always stale ⇒ burn through 2 retries across 3 cycles, then escalate.
    age_fn = AsyncMock(return_value=26 * 3600 + 100)

    def run():
        return __import__("asyncio").run(
            ow.run_offsite_backup_watch_probe(
                pool, age_fn=age_fn, restart_fn=restart, sleep_fn=AsyncMock(),
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


# ---------------------------------------------------------------------------
# The restart is docker_utils.restart_container (the brain's shared helper)
# ---------------------------------------------------------------------------

_STALE = 26 * 3600 + 100


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
        ow.run_offsite_backup_watch_probe(
            pool, age_fn=age_fn, restart_fn=restart_stub(status=du.RESTART_MISSING),
            sleep_fn=sleep_fn, notify_fn=notify,
        )
    )

    assert summary == {"ok": False, "status": "container_missing", "retries_used": 1}
    sleep_fn.assert_not_awaited()
    assert age_fn.await_count == 1
    notify.assert_not_called()
    events = _audit_events(executed)
    assert "probe.offsite_backup_restart_skipped" in events
    assert "probe.offsite_backup_restart_failed" not in events


def test_a_runner_that_stays_missing_still_escalates_critical():
    """Each missing-container cycle uses a retry, so a runner whose container
    never comes back still ends at the critical offsite_backup_stale alert."""
    executed: list = []
    pool = _make_pool(executed=executed)
    kw = {
        "age_fn": AsyncMock(return_value=_STALE),
        "restart_fn": restart_stub(status=du.RESTART_MISSING),
        "sleep_fn": AsyncMock(),
        "notify_fn": MagicMock(),
    }

    statuses = [
        __import__("asyncio").run(ow.run_offsite_backup_watch_probe(pool, **kw))["status"]
        for _ in range(3)
    ]

    assert statuses == ["container_missing", "container_missing", "escalated"]
    firing = [
        a for q, a in executed if "alert_events" in q and len(a) > 2 and a[2] == "firing"
    ]
    assert firing and firing[0][1] == "critical"


def test_recently_started_container_skips_the_wait_and_notifies_nothing():
    """The runner was restarted moments ago (deploy-sync, compose, the restart
    policy), so docker_utils declined to restart it again: nothing was
    restarted, so there is no retry-delay wait, no re-read and no notify. Not
    a failed restart either."""
    executed: list = []
    pool = _make_pool(executed=executed)
    age_fn = AsyncMock(return_value=_STALE)
    sleep_fn = AsyncMock()
    notify = MagicMock()

    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
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
    assert "probe.offsite_backup_restart_skipped" in events
    assert "probe.offsite_backup_restart_failed" not in events


def test_the_recently_started_audit_row_says_why_and_how_long_it_had_been_up():
    executed: list = []
    pool = _make_pool(executed=executed)

    __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool, age_fn=AsyncMock(return_value=_STALE),
            restart_fn=restart_stub(status=du.RESTART_RECENTLY_STARTED),
            sleep_fn=AsyncMock(), notify_fn=MagicMock(),
        )
    )

    row = next(
        json.loads(a[2]) for q, a in executed
        if "audit_log" in q and a[0] == "probe.offsite_backup_restart_skipped"
    )
    assert row["restart_status"] == du.RESTART_RECENTLY_STARTED
    assert row["uptime_seconds"] == RECENT_UPTIME_SECONDS
    assert row["retries_used"] == 1
    assert "started" in row["detail"] and "not restarted" in row["detail"]


def test_a_runner_that_keeps_restarting_still_escalates_critical():
    """Each guarded cycle uses a retry, as a missing container's does: the
    guard can hold a restart off, but it cannot postpone the critical
    offsite_backup_stale alert for a runner that keeps getting restarted under
    the watch. That alert is this tier's only page."""
    executed: list = []
    pool = _make_pool(executed=executed)
    kw = {
        "age_fn": AsyncMock(return_value=_STALE),
        "restart_fn": restart_stub(status=du.RESTART_RECENTLY_STARTED),
        "sleep_fn": AsyncMock(),
        "notify_fn": MagicMock(),
    }

    statuses = [
        __import__("asyncio").run(ow.run_offsite_backup_watch_probe(pool, **kw))["status"]
        for _ in range(3)
    ]

    assert statuses == [
        "container_recently_started", "container_recently_started", "escalated",
    ]
    firing = [
        a for q, a in executed if "alert_events" in q and len(a) > 2 and a[2] == "firing"
    ]
    assert firing and firing[0][1] == "critical"


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
        ow.run_offsite_backup_watch_probe(
            pool, age_fn=AsyncMock(return_value=_STALE),
            restart_fn=restart_stub(status=status), sleep_fn=AsyncMock(),
            notify_fn=notify,
        )
    )

    assert summary["status"] == "restart_failed"
    assert notify.called is notified
    assert "probe.offsite_backup_restart_failed" in _audit_events(executed)


def test_default_restart_is_the_shared_helper_given_the_pool(monkeypatch):
    """With no ``restart_fn`` the restart goes through
    ``docker_utils.restart_container`` with the pool, so it waits
    ``app_settings.brain_docker_restart_timeout_seconds``."""
    helper = restart_stub()
    monkeypatch.setattr(du, "restart_container", helper)
    pool = _make_pool()
    ages = iter([_STALE, 30.0])

    summary = __import__("asyncio").run(
        ow.run_offsite_backup_watch_probe(
            pool, age_fn=AsyncMock(side_effect=lambda: next(ages)), sleep_fn=AsyncMock(),
        )
    )

    helper.assert_awaited_once_with(ow._CONTAINER, pool=pool)
    assert summary["status"] == "recovered"
