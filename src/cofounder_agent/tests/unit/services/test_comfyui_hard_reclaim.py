"""The ComfyUI reclaim rung must MEASURE, not trust the status code
(poindexter#1019).

`POST /free` returning 200 is not evidence it worked. Measured 2026-08-25:
ComfyUI held **20,700 MiB** with an empty queue, `/free` returned 200 and
released **0**, and `docker restart` freed ~20 GB immediately. What squats is
the caching-allocator pool + CUDA context, which only a process exit returns —
the identical lesson poindexter#999 recorded for stable-audio (soft unload
freed 3 MiB, restart freed 10.96 GiB).

That is the fourth recurrence of one shape: **a rung that reports success
without measuring.** These tests pin the measurement and the three fail-safes
that keep a spurious restart from killing a render.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _client(queue_json, post_status=200):
    client = MagicMock()
    qr = MagicMock(status_code=200)
    qr.json = MagicMock(return_value=queue_json)
    client.get = AsyncMock(return_value=qr)
    client.post = AsyncMock(return_value=MagicMock(status_code=post_status))
    return client


def _scheduler():
    import services.gpu_scheduler as gs
    from services.gpu_scheduler import GPUScheduler

    gs._LAST_RESTART_REQUEST.clear()  # per-container cooldown is module state
    return GPUScheduler()


class _SC:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_float(self, key, default=None):
        return self._v.get(key, default)


def _run_ctx(scheduler, client, vram_readings, pool=None, sc=None):
    """Patch the rung's collaborators. vram_readings is consumed in order."""
    readings = list(vram_readings)

    async def _read():
        return readings.pop(0) if readings else None

    return (
        patch.object(scheduler, "_get_http_client", return_value=client),
        patch("services.video_providers.comfyui._resolve_server_url",
              return_value="http://comfyui:8188"),
        patch.object(scheduler, "_render_free_vram_gb", side_effect=_read),
        patch("services.gpu_scheduler._sc", return_value=sc or _SC({
            "vram_reclaim_settle_seconds": 0.0,
            "vram_reclaim_min_freed_gb": 1.0,
            "vram_reclaim_restart_cooldown_minutes": 30.0,
        })),
        patch("services.gpu_scheduler._container_pool", return_value=pool),
        patch("asyncio.sleep", new=AsyncMock()),
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestRestartsWhenFreeDidNothing:
    async def test_the_actual_20gb_squat_queues_a_restart(self):
        """/free returns 200 and frees nothing — the incident, reproduced."""
        s = _scheduler()
        client = _client({"queue_running": [], "queue_pending": []})
        created = AsyncMock(return_value={"id": "req-1"})
        ctxs = _run_ctx(s, client, [11.7, 11.7], pool=MagicMock())
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._unload_comfyui(hard=True)
        created.assert_awaited_once()
        assert created.await_args.args[1] == "poindexter-comfyui"

    async def test_no_restart_when_free_actually_worked(self):
        """11.7 -> 31.5 GB is /free doing its job; bouncing would be pure harm."""
        s = _scheduler()
        client = _client({"queue_running": [], "queue_pending": []})
        created = AsyncMock()
        ctxs = _run_ctx(s, client, [11.7, 31.5], pool=MagicMock())
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._unload_comfyui(hard=True)
        created.assert_not_awaited()

    async def test_soft_unload_never_restarts(self):
        """hard=False callers keep exactly the old best-effort behaviour."""
        s = _scheduler()
        client = _client({"queue_running": [], "queue_pending": []})
        created = AsyncMock()
        ctxs = _run_ctx(s, client, [11.7, 11.7], pool=MagicMock())
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._unload_comfyui(hard=False)
        created.assert_not_awaited()
        client.post.assert_awaited_once()  # /free still fired


@pytest.mark.unit
@pytest.mark.asyncio
class TestFailSafes:
    """A spurious restart kills the render this ladder exists to protect."""

    async def test_unreadable_vram_declines_rather_than_bouncing_blind(self):
        s = _scheduler()
        client = _client({"queue_running": [], "queue_pending": []})
        created = AsyncMock()
        ctxs = _run_ctx(s, client, [None], pool=MagicMock())
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._unload_comfyui(hard=True)
        created.assert_not_awaited()

    async def test_a_render_started_during_the_settle_blocks_the_restart(self):
        """The queue is re-checked AFTER the settle — #3094's posture."""
        s = _scheduler()
        client = MagicMock()
        empty = MagicMock(status_code=200)
        empty.json = MagicMock(return_value={"queue_running": [], "queue_pending": []})
        busy = MagicMock(status_code=200)
        busy.json = MagicMock(return_value={"queue_running": [["p1"]], "queue_pending": []})
        client.get = AsyncMock(side_effect=[empty, busy])
        client.post = AsyncMock(return_value=MagicMock(status_code=200))
        created = AsyncMock()
        ctxs = _run_ctx(s, client, [11.7, 11.7], pool=MagicMock())
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._unload_comfyui(hard=True)
        created.assert_not_awaited()

    async def test_cooldown_stops_a_restart_storm(self):
        """A persistently squatting sidecar must not be bounced every pass."""
        s = _scheduler()
        created = AsyncMock(return_value={"id": "req-1"})
        for _ in range(3):
            client = _client({"queue_running": [], "queue_pending": []})
            ctxs = _run_ctx(s, client, [11.7, 11.7], pool=MagicMock())
            with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], \
                 patch("services.service_restart_requests.create_restart_request", created):
                await s._unload_comfyui(hard=True)
        assert created.await_count == 1, "three passes, one restart"

    async def test_no_pool_degrades_to_soft_instead_of_raising(self):
        """CLI/test paths never bootstrap a container; the lever is
        best-effort and must not blow up the rest of the ladder."""
        s = _scheduler()
        client = _client({"queue_running": [], "queue_pending": []})
        ctxs = _run_ctx(s, client, [11.7, 11.7], pool=None)
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
            await s._unload_comfyui(hard=True)  # must not raise
