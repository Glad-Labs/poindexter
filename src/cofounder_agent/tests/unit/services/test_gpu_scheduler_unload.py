"""Tests for the cooperative sidecar-unload protocol.

Glad-Labs/poindexter#160 — supersedes the run_video_pipeline_sample.py
workaround from #144 (commit b83706f3). These tests cover the worker-
side ``GPUScheduler.request_sidecar_unload`` plus its integration with
``gpu.lock()`` driven by ``gpu_competing_sidecars_for_<phase>``.

Out of scope here:

* Sidecar-side ``/unload`` correctness — wan-server.py + sdxl-server.py
  ship their own behavior; these tests stub the HTTP calls.
* Real GPU contention. The integration test
  (test_cooperative_unload_protocol.py) covers a live wan-server +
  /health VRAM probe when a real sidecar is reachable.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.gpu_scheduler import GPUScheduler
from services.site_config import SiteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_async_client(post_responses: list[Any]) -> Any:
    """Build an httpx.AsyncClient mock whose .post() returns each value
    in ``post_responses`` in turn (or raises if the value is an Exception).

    Returns the client mock so callers can assert call counts/args.
    """
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    iter_responses = iter(post_responses)

    async def _post(*args: Any, **kwargs: Any) -> Any:
        try:
            value = next(iter_responses)
        except StopIteration:
            raise AssertionError("client.post called more times than expected")
        if isinstance(value, BaseException):
            raise value
        return value

    client.post = AsyncMock(side_effect=_post)
    return client


def _ok_response(json_payload: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json = MagicMock(return_value=json_payload or {"status": "unloaded"})
    resp.text = ""
    return resp


def _error_response(status_code: int = 500, body: str = "boom") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(side_effect=ValueError("not json"))
    resp.text = body
    return resp


def _scheduler_with(
    overrides: dict[str, Any] | None = None,
) -> GPUScheduler:
    sc = SiteConfig(
        initial_config={
            "wan_server_url": "http://localhost:9840",
            "sdxl_server_url": "http://localhost:9836",
            **(overrides or {}),
        },
    )
    return GPUScheduler(site_config=sc)


# ---------------------------------------------------------------------------
# request_sidecar_unload — direct method tests
# ---------------------------------------------------------------------------


class TestRequestSidecarUnloadHappyPath:
    @pytest.mark.asyncio
    async def test_two_sidecars_both_ok(self) -> None:
        """Both sidecars return 200 — both flagged ok, URLs set."""
        scheduler = _scheduler_with()

        client = _mock_async_client(
            [
                _ok_response({"unloaded": True, "vram_freed_mb": 14000}),
                _ok_response({"status": "unloaded", "vram_used_mb": 0}),
            ],
        )
        with patch("httpx.AsyncClient", return_value=client):
            results = await scheduler.request_sidecar_unload(["wan", "sdxl"])

        assert set(results.keys()) == {"wan", "sdxl"}
        for name in ("wan", "sdxl"):
            assert results[name]["ok"] is True
            assert results[name]["status_code"] == 200
            assert results[name]["error"] is None
            assert results[name]["url"]
        # Sidecar that surfaced vram_freed_mb propagates the int
        assert results["wan"]["vram_freed_mb"] == 14000
        # Sidecar that only surfaced vram_used_mb still gives us a number
        assert results["sdxl"]["vram_freed_mb"] == 0

    @pytest.mark.asyncio
    async def test_empty_list_is_noop(self) -> None:
        scheduler = _scheduler_with()
        # No mock needed — should never reach httpx
        with patch("httpx.AsyncClient") as client_cls:
            results = await scheduler.request_sidecar_unload([])
        assert results == {}
        client_cls.assert_not_called()


class TestRequestSidecarUnloadFailureModes:
    @pytest.mark.asyncio
    async def test_timeout_records_failure_does_not_raise(self) -> None:
        """A sidecar that hangs records ok=False with the timeout error."""
        scheduler = _scheduler_with()

        # Simulate a real httpx timeout
        client = _mock_async_client(
            [httpx.ReadTimeout("read timeout")],
        )
        with patch("httpx.AsyncClient", return_value=client):
            results = await scheduler.request_sidecar_unload(
                ["wan"], timeout_s=1.0,
            )
        assert results["wan"]["ok"] is False
        assert results["wan"]["status_code"] is None
        assert "ReadTimeout" in (results["wan"]["error"] or "")

    @pytest.mark.asyncio
    async def test_connect_refused_records_failure(self) -> None:
        scheduler = _scheduler_with()
        client = _mock_async_client(
            [httpx.ConnectError("connection refused")],
        )
        with patch("httpx.AsyncClient", return_value=client):
            results = await scheduler.request_sidecar_unload(["sdxl"])
        assert results["sdxl"]["ok"] is False
        assert "ConnectError" in (results["sdxl"]["error"] or "")

    @pytest.mark.asyncio
    async def test_http_5xx_records_failure(self) -> None:
        scheduler = _scheduler_with()
        client = _mock_async_client([_error_response(503, "degraded")])
        with patch("httpx.AsyncClient", return_value=client):
            results = await scheduler.request_sidecar_unload(["wan"])
        assert results["wan"]["ok"] is False
        assert results["wan"]["status_code"] == 503
        assert "503" in (results["wan"]["error"] or "")

    @pytest.mark.asyncio
    async def test_unknown_sidecar_records_failure(self) -> None:
        scheduler = _scheduler_with()
        # No httpx mock needed: unknown name is short-circuited
        with patch("httpx.AsyncClient") as client_cls:
            results = await scheduler.request_sidecar_unload(["mystery"])
        assert results["mystery"]["ok"] is False
        assert results["mystery"]["error"] == "unknown sidecar"
        client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_failure_independent_results(self) -> None:
        """One sidecar succeeds, the other times out — results recorded
        independently. Order is not asserted because the calls run in
        parallel; we check both names regardless of which response went
        to which URL.
        """
        scheduler = _scheduler_with()

        # We can't deterministically pin which httpx call goes first when
        # they run via asyncio.gather, so use side_effect that picks based
        # on the URL passed to .post().
        async def _post(url: str, *args: Any, **kwargs: Any) -> Any:
            if "9840" in url:  # wan
                return _ok_response()
            raise httpx.ReadTimeout("sdxl hung")

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=_post)

        with patch("httpx.AsyncClient", return_value=client):
            results = await scheduler.request_sidecar_unload(
                ["wan", "sdxl"],
            )

        assert results["wan"]["ok"] is True
        assert results["sdxl"]["ok"] is False
        assert "ReadTimeout" in (results["sdxl"]["error"] or "")


# ---------------------------------------------------------------------------
# Failure-policy integration — gpu.lock() drives the protocol
# ---------------------------------------------------------------------------


class TestCooperativeUnloadFailurePolicy:
    @pytest.mark.asyncio
    async def test_proceed_policy_continues_lock_claim(self) -> None:
        """Default failure_policy=proceed: lock is acquired even when
        a sidecar fails to unload. The body of the ``async with`` runs."""
        scheduler = _scheduler_with(
            {
                "gpu_competing_sidecars_for_featured_image": "wan",
                "gpu_unload_failure_policy": "proceed",
            },
        )
        # Stub away gaming-detection + ollama-unload helpers so the test
        # only exercises the new cooperative-unload path.
        scheduler._wait_for_gaming_clear = AsyncMock()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler.request_sidecar_unload = AsyncMock(
            return_value={
                "wan": {
                    "ok": False, "status_code": None, "url": "http://x",
                    "elapsed_s": 0.5, "error": "ConnectError: refused",
                    "vram_freed_mb": None,
                },
            },
        )

        ran = False
        async with scheduler.lock(
            "sdxl", phase="featured_image", task_id="t1",
        ):
            ran = True
            assert scheduler.is_busy
        assert ran, "lock body must execute under proceed policy"
        assert not scheduler.is_busy
        scheduler.request_sidecar_unload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_abort_policy_raises_before_lock_held(self) -> None:
        """failure_policy=abort: lock is NOT acquired; RuntimeError
        propagates and self.is_busy stays False."""
        scheduler = _scheduler_with(
            {
                "gpu_competing_sidecars_for_featured_image": "wan",
                "gpu_unload_failure_policy": "abort",
            },
        )
        scheduler._wait_for_gaming_clear = AsyncMock()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler.request_sidecar_unload = AsyncMock(
            return_value={
                "wan": {
                    "ok": False, "status_code": None, "url": "http://x",
                    "elapsed_s": 0.5, "error": "ConnectError: refused",
                    "vram_freed_mb": None,
                },
            },
        )

        with pytest.raises(RuntimeError, match="cooperative unload failed"):
            async with scheduler.lock(
                "sdxl", phase="featured_image", task_id="t1",
            ):
                # Should NEVER execute — abort raises before yield.
                pytest.fail("lock body should not run under abort policy")
        assert not scheduler.is_busy

    @pytest.mark.asyncio
    async def test_no_competing_sidecars_skips_protocol(self) -> None:
        """When the per-phase setting is unset, the protocol is a no-op
        (backwards compat with pre-#160)."""
        scheduler = _scheduler_with()
        scheduler._wait_for_gaming_clear = AsyncMock()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler.request_sidecar_unload = AsyncMock()

        async with scheduler.lock("ollama", phase="generate_content"):
            pass
        scheduler.request_sidecar_unload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_succeed_proceeds_silently(self) -> None:
        scheduler = _scheduler_with(
            {"gpu_competing_sidecars_for_video": "wan,sdxl"},
        )
        scheduler._wait_for_gaming_clear = AsyncMock()
        scheduler._unload_ollama_models = AsyncMock()
        scheduler.request_sidecar_unload = AsyncMock(
            return_value={
                "wan": {"ok": True, "status_code": 200, "url": "u",
                        "elapsed_s": 0.1, "error": None,
                        "vram_freed_mb": 1000},
                "sdxl": {"ok": True, "status_code": 200, "url": "u",
                         "elapsed_s": 0.1, "error": None,
                         "vram_freed_mb": 1000},
            },
        )

        ran = False
        async with scheduler.lock("ollama", phase="video"):
            ran = True
        assert ran
        scheduler.request_sidecar_unload.assert_awaited_once()
        kwargs = scheduler.request_sidecar_unload.await_args.kwargs
        assert sorted(kwargs.get("sidecars") or []) == ["sdxl", "wan"]


# ---------------------------------------------------------------------------
# Sidecar URL resolution
# ---------------------------------------------------------------------------


class TestResolveSidecarUrl:
    def test_flat_key_wins(self) -> None:
        scheduler = _scheduler_with(
            {"wan_server_url": "http://wan.example:9840"},
        )
        assert scheduler._resolve_sidecar_url("wan") == "http://wan.example:9840"

    def test_falls_back_to_plugin_namespace_for_wan(self) -> None:
        sc = SiteConfig(
            initial_config={
                "plugin.video_provider.wan2.1-1.3b.server_url":
                    "http://wan-plugin:9840",
            },
        )
        scheduler = GPUScheduler(site_config=sc)
        assert (
            scheduler._resolve_sidecar_url("wan")
            == "http://wan-plugin:9840"
        )

    def test_unknown_sidecar_returns_empty(self) -> None:
        scheduler = _scheduler_with()
        assert scheduler._resolve_sidecar_url("mystery") == ""

    def test_no_site_config_uses_module_default(self) -> None:
        from services.bootstrap_defaults import DEFAULT_WAN_URL
        scheduler = GPUScheduler(site_config=None)
        assert scheduler._resolve_sidecar_url("wan") == DEFAULT_WAN_URL


# ---------------------------------------------------------------------------
# _resolve_competing_sidecars — list-of-strings parsing
# ---------------------------------------------------------------------------


class TestResolveCompetingSidecars:
    def test_returns_empty_when_unset(self) -> None:
        scheduler = _scheduler_with()
        assert scheduler._resolve_competing_sidecars("featured_image") == []

    def test_parses_comma_separated(self) -> None:
        scheduler = _scheduler_with(
            {"gpu_competing_sidecars_for_featured_image": "wan, sdxl"},
        )
        assert sorted(
            scheduler._resolve_competing_sidecars("featured_image"),
        ) == ["sdxl", "wan"]

    def test_lowercases_for_consistency(self) -> None:
        scheduler = _scheduler_with(
            {"gpu_competing_sidecars_for_featured_image": "WAN,Sdxl"},
        )
        assert sorted(
            scheduler._resolve_competing_sidecars("featured_image"),
        ) == ["sdxl", "wan"]

    def test_no_site_config_returns_empty(self) -> None:
        scheduler = GPUScheduler(site_config=None)
        assert scheduler._resolve_competing_sidecars("any") == []
