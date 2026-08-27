"""
Unit tests for services/audit_log.py

Covers:
- AuditLogger.log: inserts audit row via pool.execute
- AuditLogger.log: swallows exceptions (never crashes caller)
- AuditLogger.log: serializes details dict to JSON
- AuditLogger.query: builds SQL with no filters
- AuditLogger.query: builds SQL with all filters
- AuditLogger.query: returns list of dicts from rows
- init_global_audit_logger: sets global singleton
- get_audit_logger: returns None before init, returns logger after init
- audit_log_bg: silently drops event when no global logger
- audit_log_bg: schedules background task when logger exists
- _handle_audit_task_exception: logs warning on failed task
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.audit_log import (
    AuditLogger,
    _handle_audit_task_exception,
    _is_loud,
    audit_log_bg,
    get_audit_logger,
    init_global_audit_logger,
    reset_global_audit_logger,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool():
    """Create a mock asyncpg Pool."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# AuditLogger.log
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLoggerLog:
    @pytest.mark.asyncio
    async def test_inserts_audit_row(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("task_created", "content_router", {"topic": "AI"}, task_id="t1")

        pool.execute.assert_awaited_once()
        call_args = pool.execute.call_args[0]
        assert call_args[0] == AuditLogger.INSERT_SQL
        assert call_args[1] == "task_created"
        assert call_args[2] == "content_router"
        assert call_args[3] == "t1"
        # details should be a JSON string
        parsed = json.loads(call_args[4])
        assert parsed == {"topic": "AI"}
        assert call_args[5] == "info"

    @pytest.mark.asyncio
    async def test_default_severity_is_info(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src")
        call_args = pool.execute.call_args[0]
        assert call_args[5] == "info"

    @pytest.mark.asyncio
    async def test_custom_severity(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src", severity="error")
        call_args = pool.execute.call_args[0]
        assert call_args[5] == "error"

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        audit = AuditLogger(pool)

        # Should not raise
        await audit.log("evt", "src", {"detail": "val"}, task_id="t1")

    @pytest.mark.asyncio
    async def test_none_details_becomes_empty_json(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src")
        call_args = pool.execute.call_args[0]
        parsed = json.loads(call_args[4])
        assert parsed == {}

    @pytest.mark.asyncio
    async def test_none_task_id_passed_through(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src")
        call_args = pool.execute.call_args[0]
        assert call_args[3] is None


# ---------------------------------------------------------------------------
# Non-finite floats in details (NaN / ±inf) — the ragas_score row loss.
# json.dumps defaults to allow_nan=True and emits the literal ``NaN``, which
# Postgres's ::jsonb cast rejects (InvalidTextRepresentationError: Token "NaN"
# is invalid) — losing the whole row. Loki showed 40+ such drops from
# event=ragas_score source=ragas_eval across 2026-06-18→06-28. The logger must
# replace non-finite floats with null so the row still lands.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNonFiniteDetails:
    @pytest.mark.asyncio
    async def test_nan_in_details_still_lands_row_as_null(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log(
            "ragas_score", "ragas_eval",
            {"score": 0.7415, "answer_relevancy": float("nan"), "topic": "t"},
        )

        pool.execute.assert_awaited_once()
        raw = pool.execute.call_args[0][4]
        # Postgres jsonb rejects the NaN literal — it must not appear.
        # (json.loads would happily re-parse it, so check the raw string.)
        assert "NaN" not in raw
        parsed = json.loads(raw)
        assert parsed["score"] == 0.7415
        assert parsed["answer_relevancy"] is None
        assert parsed["topic"] == "t"

    @pytest.mark.asyncio
    async def test_inf_and_nested_non_finite_sanitized(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log(
            "evt", "src",
            {
                "up": float("inf"),
                "down": float("-inf"),
                "nested": {"bad": float("nan"), "ok": 1.5},
                "listed": [float("nan"), 2.0, {"deep": float("inf")}],
            },
        )

        pool.execute.assert_awaited_once()
        raw = pool.execute.call_args[0][4]
        assert "NaN" not in raw
        assert "Infinity" not in raw
        parsed = json.loads(raw)
        assert parsed["up"] is None
        assert parsed["down"] is None
        assert parsed["nested"] == {"bad": None, "ok": 1.5}
        assert parsed["listed"] == [None, 2.0, {"deep": None}]

    @pytest.mark.asyncio
    async def test_finite_details_are_not_rewritten(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src", {"score": 0.9999, "count": 3, "flag": True})

        parsed = json.loads(pool.execute.call_args[0][4])
        assert parsed == {"score": 0.9999, "count": 3, "flag": True}


# ---------------------------------------------------------------------------
# AuditLogger.query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLoggerQuery:
    @pytest.mark.asyncio
    async def test_query_no_filters(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        audit = AuditLogger(pool)

        result = await audit.query()

        assert result == []
        pool.fetch.assert_awaited_once()
        sql = pool.fetch.call_args[0][0]
        assert "WHERE" not in sql
        assert "ORDER BY timestamp DESC" in sql

    @pytest.mark.asyncio
    async def test_query_with_all_filters(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        audit = AuditLogger(pool)
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)

        await audit.query(
            event_type="task_created",
            source="router",
            task_id="t1",
            severity="info",
            since=since,
            limit=50,
        )

        sql = pool.fetch.call_args[0][0]
        assert "event_type = $1" in sql
        assert "source = $2" in sql
        assert "task_id = $3" in sql
        assert "severity = $4" in sql
        assert "timestamp >= $5" in sql

    @pytest.mark.asyncio
    async def test_query_returns_dicts(self):
        pool = _make_pool()
        row = MagicMock()
        row.__iter__ = MagicMock(return_value=iter([("event_type", "test")]))
        row.items = MagicMock(return_value=[("event_type", "test")])
        row.keys = MagicMock(return_value=["event_type"])

        # dict(record) in asyncpg returns a plain dict
        mock_record = {"event_type": "test", "source": "src"}
        pool.fetch = AsyncMock(return_value=[mock_record])
        audit = AuditLogger(pool)

        result = await audit.query()
        assert len(result) == 1
        assert result[0]["event_type"] == "test"


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGlobalAuditLogger:
    def test_get_audit_logger_returns_none_before_init(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            mod._global_audit_logger = None
            assert get_audit_logger() is None
        finally:
            mod._global_audit_logger = original

    def test_init_sets_global(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            pool = _make_pool()
            logger = init_global_audit_logger(pool)
            assert isinstance(logger, AuditLogger)
            assert get_audit_logger() is logger
        finally:
            mod._global_audit_logger = original

    def test_audit_log_bg_drops_when_no_logger(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            mod._global_audit_logger = None
            # Should not raise
            audit_log_bg("evt", "src")
        finally:
            mod._global_audit_logger = original

    @pytest.mark.asyncio
    async def test_audit_log_bg_schedules_task(self):
        import services.audit_log as mod
        original = mod._global_audit_logger

        pool = _make_pool()
        init_global_audit_logger(pool)

        try:
            audit_log_bg("evt", "src", {"key": "val"}, task_id="t1")
            # Allow the scheduled task to run
            await asyncio.sleep(0.05)
            pool.execute.assert_awaited_once()
        finally:
            mod._global_audit_logger = original

    def test_init_quiet_logs_at_debug_not_info(self, caplog):
        import logging

        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            with caplog.at_level(logging.DEBUG, logger="services.audit_log"):
                init_global_audit_logger(_make_pool(), quiet=True)
            infos = [r for r in caplog.records if r.levelno >= logging.INFO]
            assert not infos, "quiet init must not log at info (CLI stderr noise)"
        finally:
            mod._global_audit_logger = original

    def test_reset_clears_matching_pool(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            pool = _make_pool()
            init_global_audit_logger(pool)
            assert reset_global_audit_logger(pool) is True
            assert get_audit_logger() is None
        finally:
            mod._global_audit_logger = original

    def test_reset_leaves_non_matching_pool(self):
        """A teardown seam (close_cli_pool) must not clobber a logger some
        other context re-initialised with its own pool."""
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            owner_pool = _make_pool()
            logger = init_global_audit_logger(owner_pool)
            assert reset_global_audit_logger(_make_pool()) is False
            assert get_audit_logger() is logger
        finally:
            mod._global_audit_logger = original

    def test_reset_none_is_unconditional(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            init_global_audit_logger(_make_pool())
            assert reset_global_audit_logger() is True
            assert get_audit_logger() is None
            # And a second reset on an empty global is a no-op False.
            assert reset_global_audit_logger() is False
        finally:
            mod._global_audit_logger = original


# ---------------------------------------------------------------------------
# _handle_audit_task_exception
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleAuditTaskException:
    def test_cancelled_task_does_nothing(self):
        task = MagicMock()
        task.cancelled.return_value = True
        # Should not raise
        _handle_audit_task_exception(task)

    def test_failed_task_logs_warning(self):
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        # Should not raise
        _handle_audit_task_exception(task)

    def test_successful_task_does_nothing(self):
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None
        # Should not raise
        _handle_audit_task_exception(task)


# ---------------------------------------------------------------------------
# #303 — warn/critical findings must NOT be silently dropped on a DB blip.
# The audit_log row IS the signal findings_alert_router reads, so a dropped
# write would kill the downstream alert.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoudOnDroppedFindings:
    def test_is_loud_severity_classification(self):
        assert _is_loud("critical") is True
        assert _is_loud("warn") is True
        assert _is_loud("warning") is True
        assert _is_loud("CRITICAL") is True
        assert _is_loud("info") is False
        assert _is_loud("") is False
        assert _is_loud(None) is False

    @pytest.mark.asyncio
    async def test_critical_write_failure_pages_operator_out_of_band(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        audit = AuditLogger(pool)

        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(),
        ) as notify:
            await audit.log(
                "finding", "jwt_blocklist_service",
                {"title": "table missing"}, severity="critical",
            )

        notify.assert_awaited_once()
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_warn_write_failure_does_not_page(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        audit = AuditLogger(pool)

        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(),
        ) as notify:
            await audit.log("finding", "src", {"k": "v"}, severity="warn")

        notify.assert_not_called()  # warn escalates to error log, not a page

    @pytest.mark.asyncio
    async def test_info_write_failure_stays_quiet(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        audit = AuditLogger(pool)

        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(),
        ) as notify:
            await audit.log("evt", "src", {"k": "v"}, severity="info")

        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_out_of_band_page_failure_never_raises(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        audit = AuditLogger(pool)

        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(side_effect=RuntimeError("telegram down too")),
        ):
            # Both the audit write AND the out-of-band page fail — must not raise.
            await audit.log("finding", "src", {"title": "x"}, severity="critical")

    def test_audit_log_bg_loud_when_dropped_with_no_logger(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            mod._global_audit_logger = None
            with patch.object(mod, "logger") as log:
                audit_log_bg("finding", "src", severity="critical")
            log.error.assert_called_once()  # critical drop is LOUD
        finally:
            mod._global_audit_logger = original

    def test_audit_log_bg_quiet_when_info_dropped_with_no_logger(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            mod._global_audit_logger = None
            with patch.object(mod, "logger") as log:
                audit_log_bg("evt", "src", severity="info")
            log.error.assert_not_called()
            log.debug.assert_called_once()
        finally:
            mod._global_audit_logger = original


# ---------------------------------------------------------------------------
# GlitchTip #863 root cause A — a fire-and-forget audit write scheduled right
# before a short-lived pool's teardown (e.g. the spend-throttle engage finding
# in a Prefect flow subprocess that builds+closes its own pool per run) raced
# ``local_pool.close()`` and died with ``InterfaceError('pool is closing')`` —
# losing a warn finding, the exact loss the #303 loud-drop path is meant to make
# impossible. ``audit_log_bg`` now registers each background write, and
# ``drain_pending_writes`` lets an owner flush them BEFORE closing the pool.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDrainPendingWrites:
    @pytest.mark.asyncio
    async def test_audit_log_bg_registers_then_discards_task(self):
        import services.audit_log as mod

        original = mod._global_audit_logger
        pool = _make_pool()
        init_global_audit_logger(pool)
        try:
            mod._pending_writes.clear()
            assert len(mod._pending_writes) == 0

            audit_log_bg("finding", "spend_throttle", {"k": "v"}, severity="warn")
            # Registered while in-flight so an owner can drain it.
            assert len(mod._pending_writes) == 1

            # Once the write completes, the done-callback discards it (no leak).
            await asyncio.sleep(0.05)
            assert len(mod._pending_writes) == 0
        finally:
            mod._global_audit_logger = original
            mod._pending_writes.clear()

    @pytest.mark.asyncio
    async def test_drain_awaits_inflight_write_before_returning(self):
        import services.audit_log as mod

        original = mod._global_audit_logger
        ran: list = []

        async def execute(*args, **kwargs):
            # Yield once so the task is genuinely in-flight (scheduled, not run),
            # then record that the write landed.
            await asyncio.sleep(0)
            ran.append(args)

        pool = AsyncMock()
        pool.execute = execute
        init_global_audit_logger(pool)
        try:
            mod._pending_writes.clear()
            audit_log_bg("finding", "spend_throttle", {"k": "v"}, severity="warn")
            # loop.create_task schedules but does not run synchronously — the
            # write has NOT happened yet. This is the window where the old code
            # closed the pool and lost the finding.
            assert ran == []

            await mod.drain_pending_writes()

            # drain awaited the in-flight write to completion → finding persisted.
            assert len(ran) == 1
        finally:
            mod._global_audit_logger = original
            mod._pending_writes.clear()

    @pytest.mark.asyncio
    async def test_drain_is_noop_when_nothing_pending(self):
        import services.audit_log as mod

        mod._pending_writes.clear()
        # Must return promptly and never raise when there is nothing to flush.
        await mod.drain_pending_writes()

    @pytest.mark.asyncio
    async def test_drain_is_bounded_by_timeout(self):
        import services.audit_log as mod

        original = mod._global_audit_logger

        async def hangs(*args, **kwargs):
            await asyncio.sleep(3600)  # never completes within the test

        pool = AsyncMock()
        pool.execute = hangs
        init_global_audit_logger(pool)
        try:
            mod._pending_writes.clear()
            audit_log_bg("finding", "spend_throttle", {"k": "v"}, severity="warn")
            # A hung write must not hang teardown: drain returns after its own
            # timeout (the outer wait_for guards against a drain that ignores it).
            await asyncio.wait_for(mod.drain_pending_writes(timeout=0.05), timeout=2.0)
        finally:
            mod._global_audit_logger = original
            for t in list(mod._pending_writes):
                t.cancel()
            mod._pending_writes.clear()
