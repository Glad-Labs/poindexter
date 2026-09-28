"""Probe schedules survive a brain restart (``poindexter/brain/probe_schedule.py``).

Each probe's last-run time lived in a module-level dict that is empty in every
new process, so every brain restart made every probe due at once. 111 restarts
in the 30 days to 2026-09-25 put 111 of ``post_performance``'s 120 pages and all
31 of ``webhook_freshness``'s alerts within 15 minutes of a restart.

A restart here is a fresh ``ProbeSchedule`` reading the same table, which is
what a new brain process has.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.brain import business_probes as bp
from poindexter.brain import health_probes as hp
from poindexter.brain import post_performance_probe as pp
from poindexter.brain import probe_failure_state, probe_schedule
from poindexter.brain.probe_failure_state import ProbeFailureState
from poindexter.brain.probe_schedule import ProbeSchedule
from poindexter.services.topic_sources.knowledge import KnowledgeSource

DAY = 24 * 3600


class _Pool:
    """``brain_knowledge`` for the schedule's two statements, run for real.

    Every other query gets ``answer(sql, args)``, which defaults to no rows.
    """

    def __init__(self, answer: Callable[[str, tuple], Any] | None = None) -> None:
        self.rows: dict[tuple[str, str], tuple[str, str]] = {}
        self._answer = answer or (lambda _sql, _args: None)

    def seed_run(self, name: str, ago: timedelta) -> None:
        ran_at = (datetime.now(UTC) - ago).isoformat()
        self.rows[(f"probe.{name}", "last_run_at")] = (ran_at, "probe_schedule")

    def last_run_value(self, name: str) -> str:
        return self.rows[(f"probe.{name}", "last_run_at")][0]

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if sql == probe_schedule._LOAD_SQL:
            attribute, source = args
            return [
                {"entity": entity, "value": value}
                for (entity, attr), (value, src) in self.rows.items()
                if attr == attribute and src == source
            ]
        return self._answer(sql, args) or []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return self._answer(sql, args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return self._answer(sql, args)

    async def execute(self, sql: str, *args: Any) -> str:
        if sql == probe_schedule._UPSERT_SQL:
            entity, attribute, value, source = args
            self.rows[(entity, attribute)] = (value, source)
        return "OK"


def _db_read(*rows: dict[str, str]) -> MagicMock:
    """A pool whose schedule read returns ``rows``."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=list(rows))
    pool.execute = AsyncMock()
    return pool


def _ago(**delta: float) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).isoformat()


@pytest.fixture(autouse=True)
def restart(monkeypatch) -> Callable[[], None]:
    """Each test starts in a new brain process; calling this starts another."""

    def _restart() -> None:
        monkeypatch.setattr(probe_schedule, "schedule", ProbeSchedule())

    _restart()
    return _restart


@pytest.mark.unit
class TestProbeSchedule:
    async def test_a_new_process_does_not_rerun_a_probe_that_ran_within_its_interval(self):
        pool = _Pool()
        before = ProbeSchedule()
        await before.load(pool)
        await before.mark_run(pool, "post_performance")

        after = ProbeSchedule()
        await after.load(pool)

        assert after.is_due("post_performance", DAY) is False
        assert after.is_due("webhook_freshness", DAY) is True

    async def test_a_run_an_hour_ago_read_from_the_db_is_not_due_for_a_24h_probe(self):
        schedule = ProbeSchedule()
        await schedule.load(
            _db_read({"entity": "probe.post_performance", "value": _ago(hours=1)})
        )

        assert schedule.is_due("post_performance", DAY) is False
        assert schedule.is_due("post_performance", 30 * 60) is True

    async def test_due_again_once_its_interval_has_passed_since_the_persisted_run(self):
        schedule = ProbeSchedule()
        await schedule.load(
            _db_read({"entity": "probe.post_performance", "value": _ago(hours=25)})
        )

        assert schedule.is_due("post_performance", DAY) is True

    async def test_the_table_is_read_once_per_process_not_per_check(self):
        pool = _db_read()
        schedule = ProbeSchedule()

        await schedule.load(pool)
        await schedule.load(pool)
        schedule.is_due("db_ping", 300)
        schedule.is_due("post_performance", DAY)

        pool.fetch.assert_awaited_once()

    async def test_a_failed_read_is_retried_and_until_then_every_probe_is_due(self, caplog):
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=[
            OSError("connection refused"),
            [{"entity": "probe.post_performance", "value": _ago(hours=1)}],
        ])
        schedule = ProbeSchedule()

        with caplog.at_level(logging.WARNING):
            await schedule.load(pool)
        assert "could not read last-run times" in caplog.text
        assert schedule.is_due("post_performance", DAY) is True

        await schedule.load(pool)
        assert schedule.is_due("post_performance", DAY) is False

    async def test_a_late_read_does_not_move_this_process_s_own_run_back(self):
        # The DB is down at startup; db_ping runs anyway and its write fails.
        # When the DB returns, its older persisted run must not make db_ping due.
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=[
            OSError("db down"),
            [{"entity": "probe.db_ping", "value": _ago(hours=2)}],
        ])
        pool.execute = AsyncMock(side_effect=OSError("db down"))
        schedule = ProbeSchedule()

        await schedule.load(pool)
        await schedule.mark_run(pool, "db_ping")
        await schedule.load(pool)

        assert schedule.is_due("db_ping", 300) is False

    async def test_unreadable_rows_are_skipped_and_the_rest_restored(self, caplog):
        schedule = ProbeSchedule()
        with caplog.at_level(logging.WARNING):
            await schedule.load(_db_read(
                {"entity": "probe.webhook_freshness", "value": "yesterday, roughly"},
                {"entity": "not_a_probe", "value": _ago(hours=1)},
                {"entity": "probe.post_performance", "value": _ago(hours=1)},
            ))

        assert schedule.is_due("webhook_freshness", DAY) is True
        assert schedule.is_due("post_performance", DAY) is False
        assert list(schedule.last_run) == ["post_performance"]
        assert caplog.text.count("ignoring unreadable row") == 2

    async def test_a_future_timestamp_delays_a_probe_one_interval_not_forever(self):
        schedule = ProbeSchedule()
        await schedule.load(_db_read(
            {"entity": "probe.post_performance", "value": "2099-01-01T00:00:00+00:00"},
        ))

        assert schedule.last_run["post_performance"] <= time.time()
        assert schedule.is_due("post_performance", DAY) is False

    async def test_a_timestamp_without_a_zone_is_read_as_utc(self):
        naive = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
        schedule = ProbeSchedule()
        await schedule.load(
            _db_read({"entity": "probe.post_performance", "value": naive.isoformat()})
        )

        assert schedule.is_due("post_performance", DAY) is False
        assert schedule.is_due("post_performance", 30 * 60) is True

    async def test_mark_run_writes_a_utc_timestamp_under_the_probe_s_entity(self):
        pool = _Pool()
        before = datetime.now(UTC)

        await ProbeSchedule().mark_run(pool, "silent_alerter")

        value, source = pool.rows[("probe.silent_alerter", "last_run_at")]
        assert source == "probe_schedule"
        written = datetime.fromisoformat(value)
        assert written.utcoffset() == timedelta(0)
        assert before <= written <= datetime.now(UTC)

    async def test_a_failed_write_keeps_the_run_in_this_process(self, caplog):
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=OSError("db down"))
        schedule = ProbeSchedule()

        with caplog.at_level(logging.WARNING):
            await schedule.mark_run(pool, "post_performance")

        assert schedule.is_due("post_performance", DAY) is False
        assert "could not persist post_performance's last run" in caplog.text


def _post_performance_answer(sql: str, _args: tuple) -> Any:
    if "FROM post_performance" in sql:
        return [{"slug": "a-post-nobody-reads", "views_1d": 0, "views_7d": 0, "views_30d": 0}]
    return None


def _stale_subscriber_events(sql: str, _args: tuple) -> Any:
    if "subscriber_events" in sql:
        return datetime.now(UTC) - timedelta(days=30)
    return None


@pytest.mark.unit
class TestRestartDoesNotRerunProbes:
    """Every probe entry point reads the schedule before asking whether it is due."""

    async def test_post_performance_pages_once_across_a_restart(self, restart):
        pool = _Pool(_post_performance_answer)
        notify = AsyncMock()

        first = await pp.probe_post_performance(pool, notify)
        restart()
        second = await pp.probe_post_performance(pool, notify)

        assert first["broken_count"] == 1
        assert second == {"ok": True, "detail": "not due yet"}
        notify.assert_awaited_once()

    async def test_webhook_freshness_alerts_once_across_a_restart(self, restart):
        pool = _Pool(_stale_subscriber_events)
        notify = AsyncMock()

        first = await bp.probe_webhook_freshness(pool, notify)
        restart()
        second = await bp.probe_webhook_freshness(pool, notify)

        assert len(first["alerts"]) == 1
        assert second == {"ok": True, "detail": "not due yet"}
        notify.assert_awaited_once()

    async def test_silent_alerter_is_not_rerun_within_its_interval_after_a_restart(self):
        pool = _Pool()
        pool.seed_run("silent_alerter", ago=timedelta(minutes=10))  # every 60 min
        seeded = pool.last_run_value("silent_alerter")

        result = await bp.probe_silent_alerter(pool, AsyncMock())

        assert result == {"ok": True, "detail": "not due yet"}
        assert pool.last_run_value("silent_alerter") == seeded

    async def test_health_probes_rerun_only_what_their_own_intervals_allow(self, monkeypatch):
        monkeypatch.setattr(probe_failure_state, "state", ProbeFailureState())
        ran: list[str] = []

        def _probe(name: str):
            async def probe(_pool):
                ran.append(name)
                return {"ok": True, "detail": "fine"}
            return probe

        pool = _Pool()
        pool.seed_run("publish_rate", ago=timedelta(hours=1))  # every 6 h
        pool.seed_run("db_ping", ago=timedelta(minutes=10))  # every 5 min
        publish_rate_seeded = pool.last_run_value("publish_rate")
        db_ping_seeded = pool.last_run_value("db_ping")

        with (
            patch.dict(
                hp.PROBES,
                {name: _probe(name) for name in ("publish_rate", "db_ping", "disk_space")},
                clear=True,
            ),
            patch.object(hp, "_alertmanager_healthy", new=AsyncMock(return_value=False)),
        ):
            results = await hp.run_health_probes(pool)

        assert ran == ["db_ping", "disk_space"]
        assert set(results) == {"db_ping", "disk_space"}
        assert pool.last_run_value("publish_rate") == publish_rate_seeded
        assert pool.last_run_value("db_ping") != db_ping_seeded
        assert ("probe.disk_space", "last_run_at") in pool.rows


@pytest.mark.unit
class TestStorageShape:
    async def test_the_knowledge_topic_source_skips_schedule_rows_whatever_their_value(self):
        """``KnowledgeSource`` mines ``brain_knowledge`` for blog topics and
        skips ``probe.`` entities. A schedule row must fall under that skip:
        ``content_gen`` matches its ``%content%`` filter. The value here is
        topic-like on purpose, so only the entity keeps it out, and the same
        value under an ordinary entity (the control) does become a topic."""
        value = "Why local LLM pipelines fail silently and how to catch it"

        async def topics_for(entity: str) -> list[Any]:
            async def fetch(sql: str, *_args: Any) -> list[dict[str, Any]]:
                if "FROM brain_knowledge" in sql and "topic_gap" not in sql:
                    return [{
                        "entity": entity,
                        "attribute": probe_schedule.ATTRIBUTE,
                        "value": value,
                        "updated_at": datetime.now(UTC),
                    }]
                return []

            pool = MagicMock()
            pool.fetch = AsyncMock(side_effect=fetch)
            return await KnowledgeSource().extract(pool, {})

        assert await topics_for(f"{probe_schedule.ENTITY_PREFIX}content_gen") == []
        assert len(await topics_for("content.strategy")) == 1
