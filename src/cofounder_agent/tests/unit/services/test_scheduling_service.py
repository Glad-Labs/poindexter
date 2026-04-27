"""Unit tests for ``services/scheduling_service`` (Glad-Labs/poindexter#147).

The service writes to ``posts.published_at`` + ``posts.status='scheduled'``
which the existing ``services/scheduled_publisher.py`` background loop
consumes. These tests stub the asyncpg pool so the queue logic is
exercised without a live database.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.scheduling_service import (
    ScheduleResult,
    assign_batch,
    assign_slot,
    clear,
    generate_slots,
    list_scheduled,
    parse_duration,
    parse_quiet_hours,
    parse_when,
    shift,
    show_scheduled,
)
from services.site_config import SiteConfig


# ---------------------------------------------------------------------------
# Mock pool — captures every call site
# ---------------------------------------------------------------------------


class _FakeConn:
    """A tiny asyncpg-conn-shaped stub that records every call."""

    def __init__(self):
        self.queries: list[tuple[str, tuple]] = []
        self._fetch_responses: list[Any] = []
        self._fetchrow_responses: list[Any] = []
        self._execute_responses: list[Any] = []
        # Default transaction context manager.
        self._transaction = MagicMock()
        self._transaction.__aenter__ = AsyncMock(return_value=self)
        self._transaction.__aexit__ = AsyncMock(return_value=False)

    def queue_fetch(self, rows):
        self._fetch_responses.append(rows)

    def queue_fetchrow(self, row):
        self._fetchrow_responses.append(row)

    def queue_execute(self, value=None):
        self._execute_responses.append(value)

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        if self._fetch_responses:
            return self._fetch_responses.pop(0)
        return []

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        if self._fetchrow_responses:
            return self._fetchrow_responses.pop(0)
        return None

    async def execute(self, sql, *args):
        self.queries.append((sql, args))
        if self._execute_responses:
            return self._execute_responses.pop(0)
        return ""

    def transaction(self):
        return self._transaction


class _FakePool:
    """asyncpg-pool-shaped wrapper that hands out the same FakeConn."""

    def __init__(self, conn: _FakeConn | None = None):
        self.conn = conn or _FakeConn()

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self_inner):  # noqa: N805
                return conn

            async def __aexit__(self_inner, *exc):  # noqa: N805
                return False

        return _Ctx()


def _events_emitted(conn: _FakeConn) -> list[dict]:
    """Pull pipeline_events insert calls out of the recorded queries.

    Returns the JSON-decoded payload for each row so tests can assert
    on event_type and payload fields.
    """
    import json

    out: list[dict] = []
    for sql, args in conn.queries:
        if "INSERT INTO pipeline_events" in sql:
            event_type = args[0]
            payload = json.loads(args[1]) if isinstance(args[1], str) else args[1]
            out.append({"event_type": event_type, "payload": payload})
    return out


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_simple_units(self):
        assert parse_duration("30m") == timedelta(minutes=30)
        assert parse_duration("2h") == timedelta(hours=2)
        assert parse_duration("1d") == timedelta(days=1)
        assert parse_duration("45s") == timedelta(seconds=45)

    def test_compound(self):
        assert parse_duration("1h30m") == timedelta(hours=1, minutes=30)
        assert parse_duration("90m") == timedelta(minutes=90)

    def test_passthrough(self):
        td = timedelta(hours=3)
        assert parse_duration(td) is td
        assert parse_duration(120) == timedelta(seconds=120)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_duration("")
        with pytest.raises(ValueError):
            parse_duration("two hours")
        with pytest.raises(ValueError):
            parse_duration("30x")


class TestParseWhen:
    def test_now(self):
        ref = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
        assert parse_when("now", now=ref) == ref

    def test_iso(self):
        got = parse_when("2026-04-28 09:00")
        assert got == datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)

    def test_tomorrow(self):
        ref = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
        assert parse_when("tomorrow 9am", now=ref) == datetime(
            2026, 4, 29, 9, 0, tzinfo=timezone.utc
        )

    def test_next_monday(self):
        # 2026-04-28 is a Tuesday. Next Monday is 2026-05-04.
        ref = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
        got = parse_when("next monday 14:00", now=ref)
        assert got == datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc)

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_when("")
        with pytest.raises(ValueError):
            parse_when("sometime soon")


class TestParseQuietHours:
    def test_empty(self):
        assert parse_quiet_hours("") is None

    def test_normal(self):
        assert parse_quiet_hours("22:00-07:00") == (time(22, 0), time(7, 0))
        assert parse_quiet_hours("09:00-17:00") == (time(9, 0), time(17, 0))

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_quiet_hours("9-17")
        with pytest.raises(ValueError):
            parse_quiet_hours("25:00-07:00")


# ---------------------------------------------------------------------------
# Slot generation
# ---------------------------------------------------------------------------


class TestGenerateSlots:
    def test_basic_30m_interval(self):
        start = datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)
        slots = generate_slots(
            start=start, interval=timedelta(minutes=30), count=20
        )
        assert len(slots) == 20
        assert slots[0] == start
        assert slots[1] == start + timedelta(minutes=30)
        assert slots[-1] == start + timedelta(minutes=30 * 19)

    def test_zero_count(self):
        start = datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)
        assert generate_slots(
            start=start, interval=timedelta(minutes=30), count=0
        ) == []

    def test_zero_interval_rejects(self):
        start = datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            generate_slots(start=start, interval=timedelta(0), count=5)

    def test_quiet_hours_overnight_skip(self):
        # Quiet from 22:00 to 07:00. Start at 21:00 UTC, hourly step.
        start = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
        window = parse_quiet_hours("22:00-07:00")
        slots = generate_slots(
            start=start,
            interval=timedelta(hours=1),
            count=4,
            quiet_hours=window,
        )
        # First slot lands at 21:00 (outside the quiet window).
        assert slots[0] == start
        # Next slot would be 22:00 — pushed to 07:00 next day.
        assert slots[1] == datetime(2026, 4, 29, 7, 0, tzinfo=timezone.utc)
        # Step from 07:00 by 1h = 08:00, then 09:00.
        assert slots[2] == datetime(2026, 4, 29, 8, 0, tzinfo=timezone.utc)
        assert slots[3] == datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc)

    def test_quiet_hours_daytime_skip(self):
        # Quiet from 09:00 to 17:00 (daytime window).
        start = datetime(2026, 4, 28, 8, 0, tzinfo=timezone.utc)
        window = parse_quiet_hours("09:00-17:00")
        slots = generate_slots(
            start=start,
            interval=timedelta(hours=1),
            count=3,
            quiet_hours=window,
        )
        # 08:00 OK, then 09:00 → pushed to 17:00, then 18:00.
        assert slots[0] == datetime(2026, 4, 28, 8, 0, tzinfo=timezone.utc)
        assert slots[1] == datetime(2026, 4, 28, 17, 0, tzinfo=timezone.utc)
        assert slots[2] == datetime(2026, 4, 28, 18, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# assign_slot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAssignSlot:
    async def test_writes_published_at_and_status(self):
        conn = _FakeConn()
        # Existing post row, currently approved with no schedule.
        conn.queue_fetchrow({
            "id": "abc",
            "slug": "example",
            "title": "Example",
            "status": "approved",
            "published_at": None,
        })
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        when = datetime(2026, 4, 30, 9, 0, tzinfo=timezone.utc)
        result = await assign_slot(
            "abc", when, pool=pool, site_config=cfg,
        )

        assert isinstance(result, ScheduleResult)
        assert result.ok is True
        assert result.count == 1
        # UPDATE was issued with the right timestamp.
        update_sql = next(
            (sql for sql, _ in conn.queries if "UPDATE posts" in sql), None,
        )
        assert update_sql is not None
        assert "status = 'scheduled'" in update_sql
        # pipeline_events row written.
        events = _events_emitted(conn)
        assert any(e["event_type"] == "schedule.assigned" for e in events)

    async def test_post_not_found(self):
        conn = _FakeConn()
        # No fetchrow response queued → returns None.
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        when = datetime(2026, 4, 30, 9, 0, tzinfo=timezone.utc)
        result = await assign_slot(
            "missing", when, pool=pool, site_config=cfg,
        )
        assert result.ok is False
        assert "not found" in result.detail

    async def test_already_scheduled_without_force(self):
        conn = _FakeConn()
        future = datetime.now(timezone.utc) + timedelta(days=1)
        conn.queue_fetchrow({
            "id": "abc",
            "slug": "example",
            "title": "Example",
            "status": "scheduled",
            "published_at": future,
        })
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        when = datetime(2026, 4, 30, 9, 0, tzinfo=timezone.utc)
        result = await assign_slot(
            "abc", when, pool=pool, site_config=cfg,
        )
        assert result.ok is False
        assert "already scheduled" in result.detail


# ---------------------------------------------------------------------------
# assign_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAssignBatch:
    async def test_20_post_batch_30m_interval(self):
        conn = _FakeConn()
        # 20 approved-and-unscheduled posts.
        rows = [
            {
                "id": f"post-{i:02d}",
                "slug": f"post-{i:02d}",
                "title": f"Post {i}",
                "status": "approved",
                "published_at": None,
                "created_at": datetime(2026, 4, 27, tzinfo=timezone.utc),
            }
            for i in range(20)
        ]
        conn.queue_fetch(rows)
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await assign_batch(
            count=20,
            interval="30m",
            start="2026-04-28 09:00",
            pool=pool,
            site_config=cfg,
        )
        assert result.ok is True
        assert result.count == 20
        # 20 UPDATE statements were issued (one per post) plus the
        # initial SELECT and the pipeline_events INSERT.
        update_calls = [
            (sql, args) for sql, args in conn.queries
            if "UPDATE posts" in sql
        ]
        assert len(update_calls) == 20
        # Slot times are 30m apart starting at 09:00.
        recorded_times = [args[1] for _, args in update_calls]
        assert recorded_times[0] == datetime(
            2026, 4, 28, 9, 0, tzinfo=timezone.utc
        )
        assert recorded_times[1] == datetime(
            2026, 4, 28, 9, 30, tzinfo=timezone.utc
        )
        assert recorded_times[19] == datetime(
            2026, 4, 28, 9, 0, tzinfo=timezone.utc
        ) + timedelta(minutes=30 * 19)
        # Audit row written.
        events = _events_emitted(conn)
        assert any(
            e["event_type"] == "schedule.batch_assigned" for e in events
        )

    async def test_quiet_hours_skips_overnight(self):
        conn = _FakeConn()
        rows = [
            {
                "id": f"p{i}",
                "slug": f"p{i}",
                "title": f"Post {i}",
                "status": "approved",
                "published_at": None,
                "created_at": datetime(2026, 4, 27, tzinfo=timezone.utc),
            }
            for i in range(4)
        ]
        conn.queue_fetch(rows)
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await assign_batch(
            count=4,
            interval="1h",
            start="2026-04-28 21:00",
            quiet_hours="22:00-07:00",
            pool=pool,
            site_config=cfg,
        )
        assert result.ok is True
        assert result.count == 4

        update_calls = [
            (sql, args) for sql, args in conn.queries
            if "UPDATE posts" in sql
        ]
        recorded_times = [args[1] for _, args in update_calls]
        # First slot 21:00 (just before the quiet window).
        assert recorded_times[0] == datetime(
            2026, 4, 28, 21, 0, tzinfo=timezone.utc
        )
        # Second slot would be 22:00 — pushed to 07:00 next day.
        assert recorded_times[1] == datetime(
            2026, 4, 29, 7, 0, tzinfo=timezone.utc
        )
        assert recorded_times[2] == datetime(
            2026, 4, 29, 8, 0, tzinfo=timezone.utc
        )
        assert recorded_times[3] == datetime(
            2026, 4, 29, 9, 0, tzinfo=timezone.utc
        )

    async def test_empty_queue_is_loud_failure(self):
        conn = _FakeConn()
        conn.queue_fetch([])  # No eligible posts.
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await assign_batch(
            count=10,
            interval="30m",
            start="now",
            pool=pool,
            site_config=cfg,
        )
        assert result.ok is False
        assert "No eligible posts" in result.detail

    async def test_quiet_hours_falls_back_to_site_config(self):
        conn = _FakeConn()
        rows = [
            {
                "id": "p0",
                "slug": "p0",
                "title": "P0",
                "status": "approved",
                "published_at": None,
                "created_at": datetime(2026, 4, 27, tzinfo=timezone.utc),
            },
        ]
        conn.queue_fetch(rows)
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={
            "publish_quiet_hours": "09:00-17:00",
        })

        result = await assign_batch(
            count=1,
            interval="1h",
            start="2026-04-28 10:00",
            pool=pool,
            site_config=cfg,
        )
        assert result.ok is True
        update_calls = [
            args for sql, args in conn.queries if "UPDATE posts" in sql
        ]
        # Quiet 09:00-17:00 → 10:00 pushed to 17:00.
        assert update_calls[0][1] == datetime(
            2026, 4, 28, 17, 0, tzinfo=timezone.utc
        )

    async def test_invalid_ordered_by(self):
        conn = _FakeConn()
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await assign_batch(
            count=1, interval="30m", start="now",
            ordered_by="random_column",
            pool=pool, site_config=cfg,
        )
        assert result.ok is False
        assert "Unsupported ordered_by" in result.detail

    async def test_count_zero_rejects(self):
        conn = _FakeConn()
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})
        result = await assign_batch(
            count=0, interval="30m", start="now",
            pool=pool, site_config=cfg,
        )
        assert result.ok is False


# ---------------------------------------------------------------------------
# list_scheduled / show_scheduled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListAndShow:
    async def test_list_returns_rows(self):
        conn = _FakeConn()
        conn.queue_fetch([
            {
                "post_id": "a",
                "slug": "a",
                "title": "A",
                "published_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
                "status": "scheduled",
            },
        ])
        pool = _FakePool(conn)

        result = await list_scheduled(pool=pool)
        assert result.ok is True
        assert result.count == 1
        assert result.rows[0]["post_id"] == "a"

    async def test_show_missing(self):
        conn = _FakeConn()
        pool = _FakePool(conn)
        result = await show_scheduled("missing", pool=pool)
        assert result.ok is False

    async def test_show_found(self):
        conn = _FakeConn()
        conn.queue_fetchrow({
            "post_id": "abc",
            "slug": "abc",
            "title": "ABC",
            "published_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "status": "scheduled",
            "created_at": datetime(2026, 4, 28, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 4, 28, tzinfo=timezone.utc),
        })
        pool = _FakePool(conn)
        result = await show_scheduled("abc", pool=pool)
        assert result.ok is True
        assert result.count == 1


# ---------------------------------------------------------------------------
# shift / clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestShift:
    async def test_shift_all_future(self):
        conn = _FakeConn()
        conn.queue_fetch([
            {
                "post_id": "a",
                "slug": "a",
                "title": "A",
                "published_at": datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                "status": "scheduled",
            },
            {
                "post_id": "b",
                "slug": "b",
                "title": "B",
                "published_at": datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
                "status": "scheduled",
            },
        ])
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await shift(
            by_delta="1h", post_ids=None, pool=pool, site_config=cfg,
        )
        assert result.ok is True
        assert result.count == 2
        # The UPDATE used the all-future scope (no id list).
        update_calls = [
            (sql, args) for sql, args in conn.queries
            if "UPDATE posts" in sql and "RETURNING" in sql
        ]
        assert len(update_calls) == 1
        events = _events_emitted(conn)
        assert any(e["event_type"] == "schedule.shifted" for e in events)

    async def test_shift_specific(self):
        conn = _FakeConn()
        conn.queue_fetch([
            {
                "post_id": "a",
                "slug": "a",
                "title": "A",
                "published_at": datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                "status": "scheduled",
            },
        ])
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await shift(
            by_delta=timedelta(minutes=15),
            post_ids=["a"],
            pool=pool, site_config=cfg,
        )
        assert result.ok is True
        assert result.count == 1

    async def test_shift_no_match(self):
        conn = _FakeConn()
        conn.queue_fetch([])  # nothing matched
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})
        result = await shift(
            by_delta="1h", pool=pool, site_config=cfg,
        )
        assert result.ok is False


@pytest.mark.asyncio
class TestClear:
    async def test_clear_specific(self):
        conn = _FakeConn()
        conn.queue_fetch([
            {"post_id": "a", "slug": "a", "title": "A", "status": "approved"},
        ])
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await clear(
            post_ids=["a"], pool=pool, site_config=cfg,
        )
        assert result.ok is True
        assert result.count == 1
        # UPDATE was issued setting published_at = NULL.
        update_calls = [
            sql for sql, _ in conn.queries
            if "UPDATE posts" in sql and "published_at = NULL" in sql
        ]
        assert update_calls
        events = _events_emitted(conn)
        assert any(e["event_type"] == "schedule.cleared" for e in events)

    async def test_clear_all(self):
        conn = _FakeConn()
        conn.queue_fetch([
            {"post_id": "a", "slug": "a", "title": "A", "status": "approved"},
            {"post_id": "b", "slug": "b", "title": "B", "status": "approved"},
        ])
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})

        result = await clear(
            post_ids=None, pool=pool, site_config=cfg,
        )
        assert result.ok is True
        assert result.count == 2

    async def test_clear_nothing(self):
        conn = _FakeConn()
        conn.queue_fetch([])
        pool = _FakePool(conn)
        cfg = SiteConfig(initial_config={})
        result = await clear(post_ids=["x"], pool=pool, site_config=cfg)
        assert result.ok is False
