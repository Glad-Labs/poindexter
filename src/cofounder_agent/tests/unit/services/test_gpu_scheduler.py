"""
Tests for GPU scheduler — async lock serializing Ollama/SDXL access.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gpu_scheduler import GPU_ADVISORY_LOCK_KEY, GPUScheduler


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
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._get_gpu_utilization = AsyncMock(return_value=10.0)  # below threshold

        mock_sc = MagicMock()
        mock_sc.get_int.side_effect = lambda k, d: d  # use defaults
        with patch("services.gpu_scheduler._sc", return_value=mock_sc):
            await scheduler._wait_for_gaming_clear()
        # Did not enter gaming-detected state
        assert scheduler._gaming_detected is False
        # Only one utilization check happened
        scheduler._get_gpu_utilization.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_utilization_proceeds(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._get_gpu_utilization = AsyncMock(return_value=None)

        mock_sc = MagicMock()
        mock_sc.get_int.side_effect = lambda k, d: d
        with patch("services.gpu_scheduler._sc", return_value=mock_sc):
            await scheduler._wait_for_gaming_clear()
        # No exception, no gaming flag set
        assert scheduler._gaming_detected is False

    @pytest.mark.asyncio
    async def test_brief_spike_proceeds(self):
        """First check is high, second is low — was just a spike, proceed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        # First check: 90% (high), second check: 5% (idle)
        scheduler._get_gpu_utilization = AsyncMock(side_effect=[90.0, 5.0])

        mock_sc = MagicMock()
        mock_sc.get_int.side_effect = lambda k, d: d
        with patch("services.gpu_scheduler._sc", return_value=mock_sc), \
             patch("asyncio.sleep", new=AsyncMock()):
            await scheduler._wait_for_gaming_clear()
        # Was just a spike — gaming not flagged
        assert scheduler._gaming_detected is False

    @pytest.mark.asyncio
    async def test_idle_after_previous_gaming_clears_flag(self):
        """If gaming was previously detected and GPU is now idle, the flag clears."""
        import time
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._gaming_detected = True
        scheduler._gaming_paused_since = time.monotonic() - 60.0
        scheduler._get_gpu_utilization = AsyncMock(return_value=5.0)

        mock_sc = MagicMock()
        mock_sc.get_int.side_effect = lambda k, d: d
        with patch("services.gpu_scheduler._sc", return_value=mock_sc):
            await scheduler._wait_for_gaming_clear()

        assert scheduler._gaming_detected is False
        # Pause duration was added to total
        assert scheduler._total_gaming_paused_s >= 60.0

    @pytest.mark.asyncio
    async def test_skips_detection_when_pipeline_holds_lock(self):
        """poindexter#579 regression guard.

        When _current_owner is set the pipeline holds the GPU lock — any
        high utilization is ours (Ollama inference), not a game.  The
        guard must return immediately without querying GPU utilization.
        """
        from unittest.mock import AsyncMock

        from services.gpu_scheduler import GPUScheduler

        scheduler = GPUScheduler()
        scheduler._current_owner = "ollama"  # simulate lock held
        scheduler._get_gpu_utilization = AsyncMock(return_value=95.0)  # would be gaming

        await scheduler._wait_for_gaming_clear()

        # Never queried GPU — returned before any IO
        scheduler._get_gpu_utilization.assert_not_awaited()
        # Gaming flag must not have been set by a false positive
        assert scheduler._gaming_detected is False


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
        from unittest.mock import MagicMock, patch

        from services.gpu_scheduler import GPUScheduler
        scheduler = GPUScheduler()
        mock_sc = MagicMock()
        mock_sc.get_int.side_effect = lambda k, d: d
        with patch("services.gpu_scheduler._sc", return_value=mock_sc):
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

        with patch.object(gpu_scheduler, "_sc", return_value=fake_sc):
            result = gpu_scheduler._cfg_int("threshold", 30)
        assert result == 99

    # ---------------------------------------------------------------
    # poindexter#485 fail-loud sweep — bare `except: return default`
    # in `_cfg_int` / `_cfg_float` previously masked SiteConfig
    # failures as "using defaults". These tests pin the new contract:
    # still falls back to the default (scheduler must keep running),
    # but emits a warning + a finding row so the operator sees it.
    # ---------------------------------------------------------------

    def test_cfg_int_emits_finding_when_site_config_raises(self):
        from unittest.mock import MagicMock, patch

        from services import gpu_scheduler

        fake_sc = MagicMock()
        fake_sc.get_int = MagicMock(side_effect=RuntimeError("db pool exhausted"))

        with patch.object(gpu_scheduler, "_sc", return_value=fake_sc), \
             patch("utils.findings.emit_finding") as mock_emit:
            result = gpu_scheduler._cfg_int("threshold", 42)

        # Still falls back to default — scheduler can't crash on config-read failure.
        assert result == 42
        # But operator now sees the outage via a finding row.
        assert mock_emit.called, "emit_finding must fire on SiteConfig.get_int failure"
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["source"] == "gpu_scheduler.cfg_fetch"
        assert call_kwargs["kind"] == "site_config_read_failed"
        assert call_kwargs["severity"] == "warning"
        assert "threshold" in call_kwargs["title"]
        assert call_kwargs["dedup_key"] == "gpu_scheduler_cfg_int_threshold"

    def test_cfg_float_emits_finding_when_site_config_raises(self):
        from unittest.mock import MagicMock, patch

        from services import gpu_scheduler

        fake_sc = MagicMock()
        fake_sc.get_float = MagicMock(side_effect=RuntimeError("connection refused"))

        with patch.object(gpu_scheduler, "_sc", return_value=fake_sc), \
             patch("utils.findings.emit_finding") as mock_emit:
            result = gpu_scheduler._cfg_float("electricity_rate_kwh_usd", 0.12)

        assert result == 0.12
        assert mock_emit.called, "emit_finding must fire on SiteConfig.get_float failure"
        assert mock_emit.call_args.kwargs["dedup_key"] == "gpu_scheduler_cfg_float_electricity_rate_kwh_usd"

    def test_cfg_int_swallows_emit_finding_failure(self):
        """Observability path must never gate the scheduler.

        If ``emit_finding`` itself raises (audit_log uninitialised,
        DB pool exhausted, etc.), ``_cfg_int`` must still return the
        default. The scheduler lock lifecycle is more important than
        the warning getting logged.
        """
        from unittest.mock import MagicMock, patch

        from services import gpu_scheduler

        fake_sc = MagicMock()
        fake_sc.get_int = MagicMock(side_effect=RuntimeError("simulated"))

        with patch.object(gpu_scheduler, "_sc", return_value=fake_sc), \
             patch("utils.findings.emit_finding",
                   side_effect=RuntimeError("audit_log not ready")):
            result = gpu_scheduler._cfg_int("threshold", 30)

        assert result == 30  # scheduler keeps running even when observability breaks


# ===========================================================================
# Cross-process pg_advisory_lock (poindexter#731)
# ===========================================================================


def _make_mock_pg_conn(*, lock_raise=None):
    """Return a mock asyncpg connection with advisory-lock calls instrumented."""
    conn = AsyncMock()
    if lock_raise:
        conn.execute.side_effect = lock_raise
    else:
        conn.execute.return_value = None  # pg_advisory_lock returns void
    conn.close = AsyncMock()
    return conn


def _patch_pg(dsn: str | None = "postgresql://test/db", conn=None):
    """Return a context-manager stack that injects mock asyncpg + resolve_database_url.

    Because asyncpg and brain.bootstrap are imported INLINE inside
    _acquire_pg_advisory_lock, we patch sys.modules so the ``import``
    statements inside the function see our mocks.
    """

    mock_asyncpg = MagicMock()
    if conn is not None:
        mock_asyncpg.connect = AsyncMock(return_value=conn)
    else:
        mock_asyncpg.connect = AsyncMock(side_effect=OSError("no conn"))

    mock_brain_bootstrap = MagicMock()
    mock_brain_bootstrap.resolve_database_url = MagicMock(return_value=dsn)

    return patch.dict(
        "sys.modules",
        {
            "asyncpg": mock_asyncpg,
            "brain": MagicMock(),
            "brain.bootstrap": mock_brain_bootstrap,
        },
    ), mock_asyncpg, mock_brain_bootstrap


class TestPgAdvisoryLock:
    """poindexter#731 — cross-process GPU lock via pg_advisory_lock."""

    @pytest.mark.asyncio
    async def test_acquire_calls_pg_advisory_lock_with_correct_key(self):
        """_acquire_pg_advisory_lock opens a dedicated conn and calls pg_advisory_lock."""
        mock_conn = _make_mock_pg_conn()
        ctx, mock_asyncpg, _ = _patch_pg(conn=mock_conn)

        with ctx:
            scheduler = GPUScheduler()
            await scheduler._acquire_pg_advisory_lock()

        mock_asyncpg.connect.assert_awaited_once_with("postgresql://test/db")
        mock_conn.execute.assert_awaited_once_with(
            "SELECT pg_advisory_lock($1)", GPU_ADVISORY_LOCK_KEY
        )
        assert scheduler._pg_lock_conn is mock_conn

    @pytest.mark.asyncio
    async def test_release_calls_pg_advisory_unlock_and_closes_conn(self):
        """_release_pg_advisory_lock calls pg_advisory_unlock then closes the conn."""
        mock_conn = _make_mock_pg_conn()
        scheduler = GPUScheduler()
        scheduler._pg_lock_conn = mock_conn

        await scheduler._release_pg_advisory_lock()

        mock_conn.execute.assert_awaited_once_with(
            "SELECT pg_advisory_unlock($1)", GPU_ADVISORY_LOCK_KEY
        )
        mock_conn.close.assert_awaited_once()
        assert scheduler._pg_lock_conn is None

    @pytest.mark.asyncio
    async def test_release_is_idempotent_when_no_conn_held(self):
        """_release_pg_advisory_lock is a no-op when no connection is held."""
        scheduler = GPUScheduler()
        assert scheduler._pg_lock_conn is None
        # Must not raise
        await scheduler._release_pg_advisory_lock()

    @pytest.mark.asyncio
    async def test_acquire_falls_back_gracefully_when_dsn_missing(self):
        """When database_url is None, pg lock is skipped; scheduler stays functional."""
        ctx, _, _ = _patch_pg(dsn=None, conn=_make_mock_pg_conn())
        with ctx:
            scheduler = GPUScheduler()
            await scheduler._acquire_pg_advisory_lock()

        # No connection was stored — graceful fallback to process-local lock
        assert scheduler._pg_lock_conn is None

    @pytest.mark.asyncio
    async def test_acquire_falls_back_gracefully_when_pg_connect_fails(self):
        """If asyncpg.connect raises, pg lock is skipped; no connection leaked."""
        ctx, _, _ = _patch_pg(conn=None)  # conn=None → connect raises OSError
        with ctx:
            scheduler = GPUScheduler()
            await scheduler._acquire_pg_advisory_lock()

        assert scheduler._pg_lock_conn is None

    @pytest.mark.asyncio
    async def test_lock_context_manager_acquires_and_releases_pg_lock(self):
        """The lock() context manager calls both acquire and release helpers."""
        scheduler = GPUScheduler()
        scheduler._acquire_pg_advisory_lock = AsyncMock()
        scheduler._release_pg_advisory_lock = AsyncMock()
        scheduler._wait_for_gaming_clear = AsyncMock()
        scheduler._unload_ollama_models = AsyncMock()

        async with scheduler.lock("ollama", model="glm4"):
            scheduler._acquire_pg_advisory_lock.assert_awaited_once()
            scheduler._release_pg_advisory_lock.assert_not_awaited()

        scheduler._release_pg_advisory_lock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_releases_pg_lock_on_exception(self):
        """pg advisory lock is released even when the body raises."""
        scheduler = GPUScheduler()
        scheduler._acquire_pg_advisory_lock = AsyncMock()
        scheduler._release_pg_advisory_lock = AsyncMock()
        scheduler._wait_for_gaming_clear = AsyncMock()
        scheduler._unload_ollama_models = AsyncMock()

        with pytest.raises(ValueError, match="body error"):
            async with scheduler.lock("sdxl"):
                raise ValueError("body error")

        scheduler._release_pg_advisory_lock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_two_concurrent_acquires_serialize_via_pg_lock(self):
        """Two concurrent lock() calls execute sequentially (poindexter#731 core scenario).

        This test simulates the cross-process race by verifying that the
        in-process asyncio.Lock (which the pg_advisory_lock wraps) correctly
        serializes concurrent callers.  The pg_advisory_lock helpers are mocked
        so that we don't need a real Postgres connection, but the asyncio.Lock
        ordering guarantees hold for the pg_advisory_lock layer as well —
        pg_advisory_lock is a blocking call that returns only after the previous
        holder calls pg_advisory_unlock.
        """
        acquire_calls: list[str] = []
        release_calls: list[str] = []

        scheduler = GPUScheduler()
        scheduler._wait_for_gaming_clear = AsyncMock()
        scheduler._unload_ollama_models = AsyncMock()

        async def _fake_acquire(self_inner=None):
            acquire_calls.append("pg_acquire")

        async def _fake_release(self_inner=None):
            release_calls.append("pg_release")

        scheduler._acquire_pg_advisory_lock = _fake_acquire
        scheduler._release_pg_advisory_lock = _fake_release

        order: list[str] = []

        async def holder(name: str, hold_seconds: float) -> None:
            async with scheduler.lock(name):
                order.append(f"{name}_start")
                await asyncio.sleep(hold_seconds)
                order.append(f"{name}_end")

        await asyncio.gather(
            holder("worker", 0.05),
            holder("prefect", 0.01),
        )

        # Verify the two holders never interleaved
        assert len(order) == 4
        first, second = order[0].split("_")[0], order[2].split("_")[0]
        assert order[0] == f"{first}_start"
        assert order[1] == f"{first}_end"
        assert order[2] == f"{second}_start"
        assert order[3] == f"{second}_end"

        # pg acquire and release must each have been called twice (once per holder)
        assert acquire_calls.count("pg_acquire") == 2
        assert release_calls.count("pg_release") == 2

    @pytest.mark.asyncio
    async def test_status_includes_pg_lock_observability(self):
        """status dict exposes pg_advisory_lock_held and pg_advisory_lock_key."""
        scheduler = GPUScheduler()
        status = scheduler.status
        assert "pg_advisory_lock_held" in status
        assert status["pg_advisory_lock_held"] is False
        assert status["pg_advisory_lock_key"] == GPU_ADVISORY_LOCK_KEY

    @pytest.mark.asyncio
    async def test_aclose_closes_pg_conn_if_held(self):
        """aclose() closes any held pg advisory lock connection."""
        mock_conn = _make_mock_pg_conn()
        scheduler = GPUScheduler()
        scheduler._pg_lock_conn = mock_conn

        await scheduler.aclose()

        mock_conn.close.assert_awaited_once()
        assert scheduler._pg_lock_conn is None

    def test_gpu_advisory_lock_key_is_int64(self):
        """GPU_ADVISORY_LOCK_KEY must fit in int64 for pg_advisory_lock."""
        # pg_advisory_lock accepts int8 (int64): -9223372036854775808 .. 9223372036854775807
        assert isinstance(GPU_ADVISORY_LOCK_KEY, int)
        assert -(2**63) <= GPU_ADVISORY_LOCK_KEY <= (2**63 - 1)
