"""
Tests for GPU scheduler — async lock serializing Ollama/SDXL access.
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from services.gpu_scheduler import GPUScheduler


class TestGPUScheduler:
    """Core GPU scheduler behavior."""

    def setup_method(self):
        self.gpu = GPUScheduler()

    @pytest.mark.asyncio
    async def test_lock_acquires_and_releases(self):
        assert not self.gpu.is_busy
        async with self.gpu.lock("ollama", model="qwen3.5"):
            assert self.gpu.is_busy
            assert self.gpu.status["owner"] == "ollama"
            assert self.gpu.status["model"] == "qwen3.5"
        assert not self.gpu.is_busy

    @pytest.mark.asyncio
    async def test_status_shows_duration(self):
        async with self.gpu.lock("sdxl"):
            status = self.gpu.status
            assert status["busy"] is True
            assert status["owner"] == "sdxl"
            assert status["duration_s"] >= 0

    @pytest.mark.asyncio
    async def test_status_idle(self):
        status = self.gpu.status
        assert status["busy"] is False
        assert status["owner"] is None
        assert status["model"] is None
        assert status["duration_s"] == 0

    @pytest.mark.asyncio
    async def test_serializes_access(self):
        """Two tasks can't hold the lock simultaneously."""
        order = []

        async def task(name, delay):
            async with self.gpu.lock(name):
                order.append(f"{name}_start")
                await asyncio.sleep(delay)
                order.append(f"{name}_end")

        await asyncio.gather(task("first", 0.1), task("second", 0.05))
        # Verify serialization: no interleaving (start/end pairs must be adjacent)
        assert len(order) == 4
        assert order[0].endswith("_start")
        assert order[1].endswith("_end")
        assert order[2].endswith("_start")
        assert order[3].endswith("_end")
        # Same task must start and end before the other starts
        assert order[0].split("_")[0] == order[1].split("_")[0]
        assert order[2].split("_")[0] == order[3].split("_")[0]

    @pytest.mark.asyncio
    async def test_lock_released_on_exception(self):
        with pytest.raises(ValueError):
            async with self.gpu.lock("ollama"):
                raise ValueError("test error")
        assert not self.gpu.is_busy

    @pytest.mark.asyncio
    async def test_sdxl_unloads_ollama_models(self):
        """Acquiring for SDXL should attempt to unload Ollama models."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "qwen3.5:latest"}]}

        with patch.object(self.gpu, "_wait_for_gaming_clear", new=AsyncMock()):
            with patch("services.gpu_scheduler.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
                mock_client_cls.return_value = mock_client

                async with self.gpu.lock("sdxl"):
                    pass

                # Should have called GET /api/ps and POST /api/generate with keep_alive=0
                mock_client.get.assert_called_once()
                mock_client.post.assert_called_once()
                call_args = mock_client.post.call_args
                assert call_args[1]["json"]["keep_alive"] == 0

    @pytest.mark.asyncio
    async def test_ollama_does_not_unload(self):
        """Acquiring for Ollama should NOT call httpx to unload models."""
        with patch.object(self.gpu, "_wait_for_gaming_clear", new=AsyncMock()):
            with patch("services.gpu_scheduler.httpx.AsyncClient") as mock_client_cls:
                async with self.gpu.lock("ollama"):
                    pass
                mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_unload_failure_does_not_block(self):
        """If unloading Ollama fails, SDXL lock still works."""
        with patch("services.gpu_scheduler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client

            async with self.gpu.lock("sdxl"):
                assert self.gpu.is_busy
            assert not self.gpu.is_busy


class TestGPUSchedulerSingleton:
    """Module-level singleton."""

    def test_singleton_exists(self):
        from services.gpu_scheduler import gpu
        assert isinstance(gpu, GPUScheduler)
