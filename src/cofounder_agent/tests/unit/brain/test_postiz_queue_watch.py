"""Unit tests for brain/postiz_queue_watch.py.

The Postiz queue watch is the self-heal-before-paging layer for the known
Temporal-restart wedge: Postiz accepts a post group (our social_post_drafts
rows read 'posted') but its internal queue never publishes. Detection is
Postiz-side via ``GET /public/v1/posts``; the heal is ``docker restart
poindexter-postiz``.

States covered: disabled short-circuit, unconfigured (no ``postiz_api_key``)
no-op, clean queue (no restart + auto-resolve of a firing alert), wedged ->
restart -> recover, wedged-past-max-retries -> escalate (a firing
``postiz_queue_wedged`` alert_events row at warning severity), API-unreachable
treated as wedged, and the restart-failure surface.

All external I/O (the Postiz API read, ``docker restart``, the per-cycle
sleep, the asyncpg pool) is injected/mocked — nothing really restarts and no
test sleeps for real.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package resolves
# the same way the auto_embed_watch tests import it.
from poindexter.brain import docker_utils as du
from poindexter.brain import postiz_queue_watch as pz
from tests.unit.brain._restart_fakes import restart_stub


def _make_pool(*, setting_values=None, api_key="pz-key", firing=None, executed=None):
    """Pool mock serving app_settings (fetchval + secret_reader's fetchrow),
    alert_events lookups, and recording executes."""
    pool = MagicMock()
    settings = {
        pz.ENABLED_KEY: "true",
        pz.OVERDUE_MINUTES_KEY: "30",
        pz.MAX_RETRIES_KEY: "2",
        pz.RETRY_DELAY_KEY: "180",
        pz.API_URL_KEY: "http://postiz:3000",
        pz.API_KEY_KEY: api_key,
        **(setting_values or {}),
    }
    firing = firing or set()

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        # secret_reader.read_app_setting path (postiz_api_key is plaintext in
        # tests, so no pgp decrypt round-trip is exercised).
        if "app_settings" in query and args:
            val = settings.get(args[0])
            return None if val is None else {"value": val, "is_secret": False}
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


def _clean():
    return {"overdue": 0, "sample": []}


def _wedged(n=2):
    return {
        "overdue": n,
        "sample": [
            {"id": "cmq123", "state": "QUEUE", "platform": "x"}
            for _ in range(min(n, 5))
        ],
    }


@pytest.fixture(autouse=True)
def _reset():
    pz._reset_retry_state()
    yield
    pz._reset_retry_state()


def test_disabled_short_circuits():
    pool = _make_pool(setting_values={pz.ENABLED_KEY: "false"})
    summary = asyncio.run(pz.run_postiz_queue_watch_probe(pool))
    assert summary["status"] == "disabled"


def test_unconfigured_key_is_a_noop():
    """No postiz_api_key => never restart, never page, never hit the API."""
    pool = _make_pool(api_key="")
    restart = restart_stub()
    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool,
            check_fn=AsyncMock(side_effect=AssertionError("must not be called")),
            restart_fn=restart,
            sleep_fn=AsyncMock(),
        )
    )
    assert summary["ok"] is True
    assert summary["status"] == "unconfigured"
    restart.assert_not_awaited()


def test_clean_queue_no_restart():
    pool = _make_pool()
    restart = restart_stub()
    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool,
            check_fn=AsyncMock(return_value=_clean()),
            restart_fn=restart,
            sleep_fn=AsyncMock(),
        )
    )
    assert summary == {"ok": True, "status": "clean", "overdue": 0, "retries_used": 0}
    restart.assert_not_awaited()


def test_clean_queue_auto_resolves_firing_alert():
    executed = []
    pool = _make_pool(firing={pz._ALERTNAME}, executed=executed)
    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool,
            check_fn=AsyncMock(return_value=_clean()),
            restart_fn=restart_stub(),
            sleep_fn=AsyncMock(),
        )
    )
    assert summary["status"] == "auto_resolved"
    resolved = [
        (q, a) for q, a in executed if "alert_events" in q and "resolved" in a
    ]
    assert resolved, "expected a resolved alert_events insert"


def test_wedged_restart_recovers():
    pool = _make_pool()
    restart = restart_stub()
    check = AsyncMock(side_effect=[_wedged(), _clean()])
    slept = []
    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool, check_fn=check, restart_fn=restart,
            sleep_fn=AsyncMock(side_effect=slept.append),
        )
    )
    assert summary["ok"] is True
    assert summary["status"] == "recovered"
    restart.assert_awaited_once_with("poindexter-postiz", pool=pool)
    assert slept == [180.0]


def test_api_unreachable_counts_as_wedged():
    """A dead/hung container can't serve the API — same restart heals it."""
    pool = _make_pool()
    restart = restart_stub()
    check = AsyncMock(side_effect=[None, _clean()])
    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool, check_fn=check, restart_fn=restart, sleep_fn=AsyncMock(),
        )
    )
    assert summary["status"] == "recovered"
    restart.assert_awaited_once()


def test_escalates_with_warning_alert_after_max_retries():
    executed = []
    pool = _make_pool(executed=executed)
    restart = restart_stub()

    # Two full wedged cycles burn the retry budget (max_retries=2)...
    for _ in range(2):
        summary = asyncio.run(
            pz.run_postiz_queue_watch_probe(
                pool,
                check_fn=AsyncMock(return_value=_wedged()),
                restart_fn=restart,
                sleep_fn=AsyncMock(),
            )
        )
        assert summary["status"] == "retry_failed"

    # ...the third escalates without another restart.
    restart.reset_mock()
    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool,
            check_fn=AsyncMock(return_value=_wedged()),
            restart_fn=restart,
            sleep_fn=AsyncMock(),
        )
    )
    assert summary["ok"] is False
    assert summary["status"] == "escalated"
    restart.assert_not_awaited()

    firing = [
        (q, a)
        for q, a in executed
        if "alert_events" in q and pz._ALERTNAME in a and "firing" in a
    ]
    assert firing, "expected a firing postiz_queue_wedged alert_events insert"
    # Warning, not critical — distribution is delayed, not lost.
    assert any("warning" in a for _, a in firing)


def test_restart_failure_is_surfaced_not_swallowed():
    executed = []
    pool = _make_pool(executed=executed)
    restart = restart_stub(status=du.RESTART_NO_DOCKER_CLI)
    notify = MagicMock()
    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool,
            check_fn=AsyncMock(return_value=_wedged()),
            restart_fn=restart,
            sleep_fn=AsyncMock(),
            notify_fn=notify,
        )
    )
    assert summary["ok"] is False
    assert summary["status"] == "restart_failed"
    notify.assert_called_once()
    audits = [
        (q, a)
        for q, a in executed
        if "audit_log" in q and "probe.postiz_queue_restart_failed" in a
    ]
    assert audits


def test_publish_date_parser_handles_postiz_iso_z():
    dt = pz._parse_publish_date("2026-06-29T13:36:00.000Z")
    assert dt is not None and dt.tzinfo is not None
    assert pz._parse_publish_date("not-a-date") is None
    assert pz._parse_publish_date(None) is None


def test_terminal_error_posts_are_not_wedge_signals():
    """ERROR is a terminal platform rejection (e.g. X 402 credits depleted) —
    a container restart can never heal it, so it must not count as wedged.
    Counting it did exactly that from 2026-08-21 to 08-26: a permanently
    firing postiz_queue_wedged alert, an escalate audit row every 5 minutes,
    and pointless Postiz restarts. Only restart-healable QUEUE counts."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    stale = (datetime.now(UTC) - timedelta(hours=3)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    posts = [
        {"id": "err1", "state": "ERROR", "publishDate": stale,
         "integration": {"providerIdentifier": "x"}},
        {"id": "q1", "state": "QUEUE", "publishDate": stale,
         "integration": {"providerIdentifier": "bluesky"}},
        {"id": "pub1", "state": "PUBLISHED", "publishDate": stale,
         "integration": {"providerIdentifier": "bluesky"}},
    ]
    overdue = pz._overdue_from_posts(posts, cutoff)
    assert [p["id"] for p in overdue] == ["q1"]


def test_escalate_detail_includes_sample_json():
    """The firing alert's description should name the stuck posts."""
    executed = []
    pool = _make_pool(executed=executed)
    pz._retry_count = 2  # budget already burned
    asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool,
            check_fn=AsyncMock(return_value=_wedged(1)),
            restart_fn=restart_stub(),
            sleep_fn=AsyncMock(),
        )
    )
    firing = [a for q, a in executed if "alert_events" in q and "firing" in a]
    assert firing
    annotations = json.loads(firing[0][4])
    assert "cmq123" in annotations["description"]


# ---------------------------------------------------------------------------
# The restart is docker_utils.restart_container (the brain's shared helper)
# ---------------------------------------------------------------------------


def _audit_events(executed: list) -> list[str]:
    return [a[0] for q, a in executed if "audit_log" in q]


def test_missing_container_skips_the_wait_and_notifies_nothing():
    """Mid-recreate the container name is unbound for a second or two, so
    nothing was restarted: no retry-delay wait, no re-check, no notify. Before
    the shared helper this was a failed restart ("No such container")."""
    executed: list = []
    pool = _make_pool(executed=executed)
    check = AsyncMock(return_value=None)  # API unreachable counts as wedged
    sleep_fn = AsyncMock()
    notify = MagicMock()

    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool, check_fn=check, restart_fn=restart_stub(status=du.RESTART_MISSING),
            sleep_fn=sleep_fn, notify_fn=notify,
        )
    )

    assert summary == {"ok": False, "status": "container_missing", "retries_used": 1}
    sleep_fn.assert_not_awaited()
    assert check.await_count == 1
    notify.assert_not_called()
    events = _audit_events(executed)
    assert "probe.postiz_queue_restart_skipped" in events
    assert "probe.postiz_queue_restart_failed" not in events


def test_a_postiz_that_stays_missing_still_escalates():
    """Each missing-container cycle uses a retry, so a Postiz whose container
    never comes back (its API unreachable, which counts as wedged) still ends
    at the firing postiz_queue_wedged alert."""
    executed: list = []
    pool = _make_pool(executed=executed)
    kw = {
        "check_fn": AsyncMock(return_value=None),
        "restart_fn": restart_stub(status=du.RESTART_MISSING),
        "sleep_fn": AsyncMock(),
        "notify_fn": MagicMock(),
    }

    statuses = [
        asyncio.run(pz.run_postiz_queue_watch_probe(pool, **kw))["status"]
        for _ in range(3)
    ]

    assert statuses == ["container_missing", "container_missing", "escalated"]
    assert any(
        "alert_events" in q and pz._ALERTNAME in a and "firing" in a for q, a in executed
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

    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool, check_fn=AsyncMock(return_value=_wedged()),
            restart_fn=restart_stub(status=status), sleep_fn=AsyncMock(),
            notify_fn=notify,
        )
    )

    assert summary["status"] == "restart_failed"
    assert notify.called is notified
    assert "probe.postiz_queue_restart_failed" in _audit_events(executed)


def test_default_restart_is_the_shared_helper_given_the_pool(monkeypatch):
    """With no ``restart_fn`` the restart goes through
    ``docker_utils.restart_container`` with the pool, so it waits
    ``app_settings.brain_docker_restart_timeout_seconds`` rather than the
    60 s this probe used to hardcode."""
    helper = restart_stub()
    monkeypatch.setattr(du, "restart_container", helper)
    pool = _make_pool()

    summary = asyncio.run(
        pz.run_postiz_queue_watch_probe(
            pool, check_fn=AsyncMock(side_effect=[_wedged(), _clean()]),
            sleep_fn=AsyncMock(),
        )
    )

    helper.assert_awaited_once_with(pz._CONTAINER, pool=pool)
    assert summary["status"] == "recovered"
