"""
Tests for GPU scheduler — async lock serializing Ollama/SDXL access.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
        with pytest.raises(ValueError, match="test error"):
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


# ===========================================================================
# _get_gpu_utilization
# ===========================================================================


class TestGetGpuUtilization:
    """Coverage for the nvidia-smi exporter parsing."""

    @pytest.mark.asyncio
    async def test_parses_utilization_from_prometheus_output(self):
        """The function looks for a 'nvidia_gpu_utilization_percent{...}' line."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()

        prometheus_text = (
            "# HELP nvidia_gpu_utilization_percent GPU utilization\n"
            "# TYPE nvidia_gpu_utilization_percent gauge\n"
            "nvidia_gpu_utilization_percent{gpu=\"0\"} 75.0\n"
            "nvidia_other_metric{x=\"y\"} 100\n"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = prometheus_text

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await scheduler._get_gpu_utilization()
        assert result == 75.0

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await scheduler._get_gpu_utilization()
        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        from unittest.mock import AsyncMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await scheduler._get_gpu_utilization()
        assert result is None

    @pytest.mark.asyncio
    async def test_no_matching_line_returns_none(self):
        """If the metric isn't in the response, returns None."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "some_other_metric{} 50\nyet_another{} 100\n"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await scheduler._get_gpu_utilization()
        assert result is None


# ===========================================================================
# _wait_for_gaming_clear
# ===========================================================================


class TestWaitForGamingClear:
    @pytest.mark.asyncio
    async def test_idle_gpu_proceeds_immediately(self):
        from unittest.mock import AsyncMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._get_gpu_utilization = AsyncMock(return_value=10.0)  # below threshold

        with patch("services.gpu_scheduler.site_config") as mock_sc:
            mock_sc.get_int.side_effect = lambda k, d: d  # use defaults
            await scheduler._wait_for_gaming_clear()
        # Did not enter gaming-detected state
        assert scheduler._gaming_detected is False
        # Only one utilization check happened
        scheduler._get_gpu_utilization.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_utilization_proceeds(self):
        from unittest.mock import AsyncMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._get_gpu_utilization = AsyncMock(return_value=None)

        with patch("services.gpu_scheduler.site_config") as mock_sc:
            mock_sc.get_int.side_effect = lambda k, d: d
            await scheduler._wait_for_gaming_clear()
        # No exception, no gaming flag set
        assert scheduler._gaming_detected is False

    @pytest.mark.asyncio
    async def test_brief_spike_proceeds(self):
        """First check is high, second is low — was just a spike, proceed."""
        from unittest.mock import AsyncMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        # First check: 90% (high), second check: 5% (idle)
        scheduler._get_gpu_utilization = AsyncMock(side_effect=[90.0, 5.0])

        with patch("services.gpu_scheduler.site_config") as mock_sc, \
             patch("asyncio.sleep", new=AsyncMock()):
            mock_sc.get_int.side_effect = lambda k, d: d
            await scheduler._wait_for_gaming_clear()
        # Was just a spike — gaming not flagged
        assert scheduler._gaming_detected is False

    @pytest.mark.asyncio
    async def test_idle_after_previous_gaming_clears_flag(self):
        """If gaming was previously detected and GPU is now idle, the flag clears."""
        import time
        from unittest.mock import AsyncMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._gaming_detected = True
        scheduler._gaming_paused_since = time.monotonic() - 60.0
        scheduler._get_gpu_utilization = AsyncMock(return_value=5.0)

        with patch("services.gpu_scheduler.site_config") as mock_sc:
            mock_sc.get_int.side_effect = lambda k, d: d
            await scheduler._wait_for_gaming_clear()

        assert scheduler._gaming_detected is False
        # Pause duration was added to total
        assert scheduler._total_gaming_paused_s >= 60.0


# ===========================================================================
# prepare_mode
# ===========================================================================


class TestPrepareMode:
    @pytest.mark.asyncio
    async def test_sdxl_mode_unloads_ollama(self):
        from unittest.mock import AsyncMock

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler._unload_sdxl = AsyncMock()

        await scheduler.prepare_mode("sdxl")
        scheduler._unload_ollama_models.assert_awaited_once()
        scheduler._unload_sdxl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ollama_mode_unloads_sdxl(self):
        from unittest.mock import AsyncMock

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler._unload_sdxl = AsyncMock()

        await scheduler.prepare_mode("ollama")
        scheduler._unload_sdxl.assert_awaited_once()
        scheduler._unload_ollama_models.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idle_mode_unloads_both(self):
        from unittest.mock import AsyncMock

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler._unload_sdxl = AsyncMock()

        await scheduler.prepare_mode("idle")
        scheduler._unload_ollama_models.assert_awaited_once()
        scheduler._unload_sdxl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_mode_no_op(self):
        from unittest.mock import AsyncMock

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler._unload_sdxl = AsyncMock()

        await scheduler.prepare_mode("unknown")
        scheduler._unload_ollama_models.assert_not_awaited()
        scheduler._unload_sdxl.assert_not_awaited()


# ===========================================================================
# _unload_sdxl
# ===========================================================================


class TestUnloadSdxl:
    @pytest.mark.asyncio
    async def test_post_to_unload_endpoint(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("services.gpu_scheduler._sc_get", return_value="http://localhost:9836"):
            await scheduler._unload_sdxl()

        mock_client.post.assert_awaited_once()
        url = mock_client.post.await_args.args[0]
        assert "/unload" in url

    @pytest.mark.asyncio
    async def test_server_unavailable_silently_passes(self):
        from unittest.mock import AsyncMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("services.gpu_scheduler._sc_get", return_value="http://localhost:9836"):
            # Should not raise
            await scheduler._unload_sdxl()


# ===========================================================================
# Properties and config helpers
# ===========================================================================


class TestPropertiesAndConfig:
    def test_is_busy_property(self):
        from services.gpu_scheduler import GPUScheduler
        scheduler = GPUScheduler()
        assert scheduler.is_busy is False

    def test_is_gaming_property(self):
        from services.gpu_scheduler import GPUScheduler
        scheduler = GPUScheduler()
        assert scheduler.is_gaming is False
        scheduler._gaming_detected = True
        assert scheduler.is_gaming is True

    def test_status_includes_config(self):
        from unittest.mock import patch

        from services.gpu_scheduler import GPUScheduler
        scheduler = GPUScheduler()
        with patch("services.gpu_scheduler.site_config") as mock_sc:
            mock_sc.get_int.side_effect = lambda k, d: d
            status = scheduler.status
        assert "config" in status
        assert "threshold_percent" in status["config"]
        assert "check_interval_s" in status["config"]
        assert status["busy"] is False
        assert status["gaming_detected"] is False

    def test_cfg_int_defaults_when_site_config_missing(self):
        from unittest.mock import patch

        from services.gpu_scheduler import _cfg_int

        with patch.dict("sys.modules", {"services.site_config": None}):
            result = _cfg_int("any_key", 42)
        # Falls back to default since import fails
        assert result == 42

    def test_cfg_float_defaults_when_site_config_missing(self):
        from unittest.mock import patch

        from services.gpu_scheduler import _cfg_float

        with patch.dict("sys.modules", {"services.site_config": None}):
            result = _cfg_float("any_key", 3.14)
        assert result == 3.14

    def test_cfg_int_uses_site_config_when_available(self):
        from unittest.mock import MagicMock, patch

        from services import gpu_scheduler

        fake_sc = MagicMock()
        fake_sc.get_int = MagicMock(return_value=99)

        with patch.object(gpu_scheduler, "site_config", fake_sc):
            result = gpu_scheduler._cfg_int("threshold", 30)
        assert result == 99
