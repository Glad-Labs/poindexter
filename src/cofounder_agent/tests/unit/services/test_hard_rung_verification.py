"""Every hard rung must MEASURE its unload, not trust its own report
(poindexter#1019, extended to wan / image-gen / stable-audio).

#1019 fixed ComfyUI and asked whether the other three needed the same. They
do — but their success signal is INVERTED, which is exactly why this needed
measuring rather than reasoning by analogy:

- ComfyUI answers `/free` with 200 whether or not it freed anything.
- wan / image-gen / stable-audio `os._exit(0)` on a real hard unload, which
  fires BEFORE uvicorn flushes — so a **reset connection is the reclaim
  working**, and a clean 200 carrying `status: nothing_to_reclaim` is a
  legitimate decline below the reserved-pool floor.

A naive "measure VRAM, restart if unchanged" would therefore bounce a healthy
service for correctly declining. That case is the first test below.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.gpu_scheduler as gs
from services.gpu_scheduler import GPUScheduler


class _SC:
    def __init__(self, v=None):
        self._v = v or {
            "vram_reclaim_settle_seconds": 0.0,
            "vram_reclaim_min_freed_gb": 1.0,
            "vram_reclaim_restart_cooldown_minutes": 30.0,
        }

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_float(self, key, default=None):
        return self._v.get(key, default)


def _sched():
    gs._LAST_RESTART_REQUEST.clear()
    return GPUScheduler()


def _resp(status=200, body=None):
    r = MagicMock(status_code=status)
    r.json = MagicMock(return_value=body if body is not None else {})
    r.text = str(body or "")
    return r


@pytest.mark.unit
class TestDeclineDetection:
    """For a self-exit sidecar, a 200 AT ALL means it did not exit.

    Only `unloaded` / `exiting` say it acted. Every other status is the server
    deliberately declining, and restarting on a decline bounces a healthy —
    often actively rendering — service.
    """

    @pytest.mark.parametrize("status", [
        "nothing_to_reclaim",         # below the reserved-pool floor
        "busy_generation_in_flight",  # a render is running (#992, #1024)
        "already_unloaded",           # nothing left to drop
        "degraded",                   # stable-audio degraded state
    ])
    def test_every_decline_status_is_recognised(self, status):
        assert GPUScheduler._declined_hard_unload(_resp(200, {"status": status})) is True

    def test_busy_specifically_must_not_restart(self):
        """The regression this guards: wan and stable-audio ALREADY return
        busy_generation_in_flight. A verifier that only knew
        nothing_to_reclaim would measure no VRAM change and restart the
        service MID-RENDER — exactly the failure #3094 fixed."""
        assert GPUScheduler._declined_hard_unload(
            _resp(200, {"status": "busy_generation_in_flight", "inflight": 1}),
        ) is True

    @pytest.mark.parametrize("status", ["unloaded", "exiting"])
    def test_acting_statuses_are_not_declines(self, status):
        assert GPUScheduler._declined_hard_unload(
            _resp(200, {"status": status, "freed_mb": 10952}),
        ) is False

    def test_unknown_200_status_is_treated_as_a_decline(self):
        """Asymmetry on purpose: a spurious restart costs GPU-minutes and a
        piece's re-dispatch cap; a missed one just leaves VRAM squatting until
        the next ladder pass, which retries."""
        assert GPUScheduler._declined_hard_unload(_resp(200, {"status": "who_knows"})) is True

    def test_unparseable_200_is_treated_as_a_decline(self):
        r = MagicMock(status_code=200)
        r.json = MagicMock(side_effect=ValueError("not json"))
        assert GPUScheduler._declined_hard_unload(r) is True

    def test_non_200_falls_through_to_the_measurement(self):
        """An error is not a decline — let the VRAM evidence decide."""
        assert GPUScheduler._declined_hard_unload(_resp(503, {})) is False


@pytest.mark.unit
@pytest.mark.asyncio
class TestVerifierRespectsTheDecline:
    async def test_a_declining_service_is_never_restarted(self):
        """The mistake a naive version would make: bouncing a healthy sidecar
        that correctly reported it had nothing to free."""
        s = _sched()
        created = AsyncMock()
        with patch("services.gpu_scheduler._sc", return_value=_SC()), \
             patch("services.gpu_scheduler._container_pool", return_value=MagicMock()), \
             patch.object(s, "_render_free_vram_gb", AsyncMock(return_value=11.7)), \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._verify_reclaim_or_restart(
                service="wan", container="poindexter-wan-server",
                before_gb=11.7, declined=True,
            )
        created.assert_not_awaited()

    async def test_a_self_exit_that_did_not_free_gets_restarted(self):
        """Connection reset means the exit fired — but if VRAM never came
        back, the exit did NOT actually happen."""
        s = _sched()
        created = AsyncMock(return_value={"id": "r1"})
        with patch("services.gpu_scheduler._sc", return_value=_SC()), \
             patch("services.gpu_scheduler._container_pool", return_value=MagicMock()), \
             patch.object(s, "_render_free_vram_gb", AsyncMock(return_value=11.7)), \
             patch("asyncio.sleep", new=AsyncMock()), \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._verify_reclaim_or_restart(
                service="stable-audio", container="poindexter-stable-audio",
                before_gb=11.7, declined=False,
            )
        created.assert_awaited_once()
        assert created.await_args.args[1] == "poindexter-stable-audio"

    async def test_a_working_unload_is_left_alone(self):
        s = _sched()
        created = AsyncMock()
        with patch("services.gpu_scheduler._sc", return_value=_SC()), \
             patch("services.gpu_scheduler._container_pool", return_value=MagicMock()), \
             patch.object(s, "_render_free_vram_gb", AsyncMock(return_value=22.7)), \
             patch("asyncio.sleep", new=AsyncMock()), \
             patch("services.service_restart_requests.create_restart_request", created):
            await s._verify_reclaim_or_restart(
                service="wan", container="poindexter-wan-server",
                before_gb=11.7, declined=False,
            )
        created.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
class TestCooldownIsPerContainer:
    async def test_one_squatting_service_does_not_mute_another(self):
        """A single global cooldown would let wan's restart suppress
        stable-audio's — the ladder evicts several services per pass."""
        s = _sched()
        created = AsyncMock(return_value={"id": "r"})
        with patch("services.gpu_scheduler._sc", return_value=_SC()), \
             patch("services.gpu_scheduler._container_pool", return_value=MagicMock()), \
             patch.object(s, "_render_free_vram_gb", AsyncMock(return_value=11.7)), \
             patch("asyncio.sleep", new=AsyncMock()), \
             patch("services.service_restart_requests.create_restart_request", created):
            for svc, ctr in (
                ("wan", "poindexter-wan-server"),
                ("stable-audio", "poindexter-stable-audio"),
                ("image-gen", "poindexter-image-gen-server"),
            ):
                await s._verify_reclaim_or_restart(
                    service=svc, container=ctr, before_gb=11.7, declined=False,
                )
        assert created.await_count == 3
        assert {c.args[1] for c in created.await_args_list} == {
            "poindexter-wan-server",
            "poindexter-stable-audio",
            "poindexter-image-gen-server",
        }

    async def test_the_same_container_is_still_rate_limited(self):
        s = _sched()
        created = AsyncMock(return_value={"id": "r"})
        with patch("services.gpu_scheduler._sc", return_value=_SC()), \
             patch("services.gpu_scheduler._container_pool", return_value=MagicMock()), \
             patch.object(s, "_render_free_vram_gb", AsyncMock(return_value=11.7)), \
             patch("asyncio.sleep", new=AsyncMock()), \
             patch("services.service_restart_requests.create_restart_request", created):
            for _ in range(3):
                await s._verify_reclaim_or_restart(
                    service="wan", container="poindexter-wan-server",
                    before_gb=11.7, declined=False,
                )
        assert created.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestEveryHardRungIsWired:
    """A rung missing from the ladder is invisible precisely when it matters
    (poindexter#999) — so assert all four actually call the verifier."""

    @pytest.mark.parametrize("method,url_patch,container", [
        ("_unload_wan", "services.video_providers.wan2_1._resolve_server_url", "poindexter-wan-server"),
        ("_unload_stable_audio", "services.audio_gen_providers.stable_audio_open._resolve_server_url", "poindexter-stable-audio"),
    ])
    async def test_self_exit_rungs_verify(self, method, url_patch, container):
        s = _sched()
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(200, {"status": "unloaded"}))
        seen = {}

        async def _verify(**kw):
            seen.update(kw)

        with patch.object(s, "_get_http_client", return_value=client), \
             patch(url_patch, return_value="http://svc:9000"), \
             patch.object(s, "_render_free_vram_gb", AsyncMock(return_value=11.7)), \
             patch.object(s, "_verify_reclaim_or_restart", _verify):
            await getattr(s, method)(hard=True)
        assert seen.get("container") == container
        assert seen.get("declined") is False

    async def test_image_gen_verifies(self):
        s = _sched()
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(200, {"status": "unloaded"}))
        seen = {}

        async def _verify(**kw):
            seen.update(kw)

        with patch.object(s, "_get_http_client", return_value=client), \
             patch("services.gpu_scheduler._sc_get", return_value="http://img:9836"), \
             patch.object(s, "_render_free_vram_gb", AsyncMock(return_value=11.7)), \
             patch.object(s, "_verify_reclaim_or_restart", _verify):
            await s._unload_image_gen(hard=True)
        assert seen.get("container") == "poindexter-image-gen-server"

    async def test_soft_unload_never_verifies(self):
        """hard=False callers keep exactly the old behaviour."""
        s = _sched()
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(200, {"status": "unloaded"}))
        verify = AsyncMock()
        with patch.object(s, "_get_http_client", return_value=client), \
             patch("services.video_providers.wan2_1._resolve_server_url", return_value="http://w:9840"), \
             patch.object(s, "_verify_reclaim_or_restart", verify):
            await s._unload_wan(hard=False)
        verify.assert_not_awaited()
