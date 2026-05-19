"""Tests for worker_service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


@pytest.fixture
def service(mock_pool):
    from services.worker_service import WorkerService

    pool, _ = mock_pool
    return WorkerService(pool, worker_type="test")


class TestInit:
    def test_worker_id_format(self, service):
        assert service.worker_id.startswith("test-")
        assert service.worker_type == "test"

    def test_initial_state(self, service):
        assert service._running is False
        assert service._current_task_id is None


class TestCapabilities:
    def test_returns_dict_with_keys(self, service):
        caps = service.capabilities
        assert "hostname" in caps
        assert "platform" in caps
        assert "python" in caps
        assert "ollama_url" in caps
        assert "sdxl" in caps

    def test_caches_capabilities(self, service):
        caps1 = service.capabilities
        caps2 = service.capabilities
        assert caps1 is caps2

    @patch.dict("os.environ", {"SDXL_API_URL": "http://localhost:5000"})
    def test_sdxl_detected_from_env(self):
        from services.worker_service import WorkerService

        pool = MagicMock()
        ws = WorkerService(pool)
        assert ws.capabilities["sdxl"] is True


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_executes_upsert(self, service, mock_pool):
        _, conn = mock_pool
        await service.register()
        conn.execute.assert_called_once()
        query = conn.execute.call_args[0][0]
        assert "INSERT INTO capability_registry" in query
        assert "ON CONFLICT" in query


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_marks_offline(self, service, mock_pool):
        _, conn = mock_pool
        service._running = True
        await service.stop()
        assert service._running is False
        conn.execute.assert_called_once()
        query = conn.execute.call_args[0][0]
        assert "offline" in query

    @pytest.mark.asyncio
    async def test_stop_db_error_is_swallowed(self, service, mock_pool):
        _, conn = mock_pool
        conn.execute.side_effect = RuntimeError("db gone")
        service._running = True
        await service.stop()
        assert service._running is False


class TestSetCurrentTask:
    def test_set_task(self, service):
        service.set_current_task("task-123")
        assert service._current_task_id == "task-123"

    def test_clear_task(self, service):
        service.set_current_task("task-123")
        service.set_current_task(None)
        assert service._current_task_id is None


class TestCollectHealthMetrics:
    @pytest.mark.asyncio
    async def test_returns_dict(self, service):
        service._current_task_id = "t1"
        health = await service._collect_health_metrics()
        assert "timestamp" in health
        assert health["current_task"] == "t1"


class TestHeartbeatQueryShape:
    """Regression pin for the asyncpg IndeterminateDatatypeError bug.

    Per `reference_asyncpg_type_cast_quirks`: `CASE WHEN $N IS NOT NULL`
    raises `asyncpg.exceptions.IndeterminateDatatypeError` unless the
    parameter is explicitly cast (e.g. `$3::text`). The 2026-05-19
    jank-audit finding #1 was caused by this exact pattern silently
    killing every heartbeat UPDATE.
    """

    @pytest.mark.asyncio
    async def test_heartbeat_casts_current_task_param_to_text(
        self, service, mock_pool
    ):
        _, conn = mock_pool

        async def _stop_after_first(_interval):
            service._running = False

        service._running = True
        with patch("services.worker_service.asyncio.sleep", _stop_after_first):
            await service._heartbeat_loop()

        assert conn.execute.called
        sql = conn.execute.call_args[0][0]
        assert "$3::text IS NOT NULL" in sql, (
            "Heartbeat UPDATE must cast $3 (current_task_id) to ::text — "
            "without it asyncpg raises IndeterminateDatatypeError on every "
            "tick and the heartbeat silently dies. See "
            "reference_asyncpg_type_cast_quirks."
        )


class TestStartHeartbeat:
    @pytest.mark.asyncio
    async def test_sets_running_flag(self, service):
        with patch("asyncio.create_task"):
            await service.start_heartbeat()
        assert service._running is True


class TestHeartbeatLoopErrorVisibility:
    """Heartbeat failures MUST log at WARNING, not DEBUG.

    Per feedback_no_silent_defaults: required state changes must fail
    loud. A silent DEBUG-level swallow in a critical loop hides the
    root cause indefinitely (see the 2026-05-19 jank-audit finding #1
    where the worker's last_heartbeat was pinned at register-time for
    ~85 minutes because the heartbeat UPDATE was raising silently).
    """

    @pytest.mark.asyncio
    async def test_heartbeat_failure_logs_warning_not_debug(
        self, service, mock_pool
    ):
        _, conn = mock_pool
        conn.execute.side_effect = RuntimeError("simulated db blip")

        # Run exactly one iteration, then stop the loop. We patch sleep
        # so the test doesn't actually wait 30s, and use it as the
        # cue to flip _running off so the while-loop exits.
        async def _stop_after_first(_interval):
            service._running = False

        service._running = True
        # Spy on the module-level logger so we can assert level + kwargs.
        # caplog is unreliable here because the project uses structlog,
        # which renders exc_info into the message string before the stdlib
        # LogRecord is built — so we check the call directly.
        mock_logger = MagicMock()
        with patch("services.worker_service.logger", mock_logger), patch(
            "services.worker_service.asyncio.sleep", _stop_after_first
        ):
            await service._heartbeat_loop()

        # DEBUG must NOT be the level used for this failure.
        assert not mock_logger.debug.called or all(
            "Heartbeat failed" not in str(c) for c in mock_logger.debug.mock_calls
        ), "Heartbeat failure was logged at DEBUG — this is the bug we're fixing."

        # WARNING must be called with the failure message and exc_info=True.
        assert mock_logger.warning.called, (
            "Heartbeat failure must log at WARNING level "
            "(feedback_no_silent_defaults)."
        )
        call = mock_logger.warning.call_args
        assert "Heartbeat failed" in call.args[0], (
            f"Expected 'Heartbeat failed' in warning message, got: {call.args}"
        )
        assert call.kwargs.get("exc_info") is True, (
            "Heartbeat warning must pass exc_info=True so the actual "
            "exception type/message reaches the operator."
        )

    @pytest.mark.asyncio
    async def test_heartbeat_loop_keeps_running_after_failure(
        self, service, mock_pool
    ):
        """A single DB error must NOT break the loop — it should keep
        retrying on subsequent ticks (until _running flips off)."""
        _, conn = mock_pool
        # First call raises, second call succeeds.
        conn.execute.side_effect = [RuntimeError("transient"), None]

        call_count = {"n": 0}

        async def _sleep_two_iters(_interval):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                service._running = False

        service._running = True
        with patch("services.worker_service.asyncio.sleep", _sleep_two_iters):
            await service._heartbeat_loop()

        # Two execute() calls = the loop survived the first exception and
        # ran a second iteration.
        assert conn.execute.call_count == 2
