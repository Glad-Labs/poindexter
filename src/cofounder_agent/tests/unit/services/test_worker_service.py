"""Tests for worker_service."""

import json
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


class TestStartHeartbeat:
    @pytest.mark.asyncio
    async def test_sets_running_flag(self, service):
        with patch("asyncio.create_task"):
            await service.start_heartbeat()
        assert service._running is True
