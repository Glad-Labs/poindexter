"""Pinning test for the SDXL-degraded → Pexels-fallback path.

Live incident 2026-05-11 17:48 UTC: after a worker restart cascade,
``poindexter-sdxl-server`` lost its connection to ``poindexter-postgres-
local`` and entered DEGRADED state — ``/health`` reported ``"DB read
failed for 'image_generation_model'"`` and ``/generate`` returned HTTP
503 to every caller. Worker correctly fell back to Pexels for all 9
awaiting_approval featured images in batch C.

That fallback is the difference between "graceful degradation" and
"a whole batch loses its art surface." No test pinned it before tonight.

This file locks in the contract: when ``/generate`` returns a non-200
response (timeout, 503, 500, 404), the featured-image stage MUST fall
through to the Pexels fallback rather than crashing or returning a
broken image.

See also:
- Glad-Labs/poindexter#459 (the SDXL HTTP-fetch fix that introduced
  the new code path being tested here)
- ``services/stages/source_featured_image.py::_render_sdxl`` (the
  early-return on ``resp.status_code != 200``)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(status_code: int, content_type: str = "application/json"):
    """An httpx.Response double whose status code we control."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}
    resp.json = MagicMock(return_value={"error": "service degraded"})
    return resp


def _fake_httpx_client_returning(post_resp):
    """AsyncClient context-manager whose ``.post`` returns one response."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=post_resp)
    return client


def _gpu_lock_noop():
    """Stub ``services.gpu_scheduler.gpu.lock`` so the test doesn't
    hit the real GPU scheduler. ``gpu.lock(...)`` is an async context
    manager — yield once + exit cleanly is all the production code
    needs."""
    from contextlib import asynccontextmanager

    class _Stub:
        @asynccontextmanager
        async def lock(self, *_args, **_kwargs):
            yield
    return _Stub()


# ---------------------------------------------------------------------------
# _render_sdxl: non-200 → None
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderSdxlNon200ReturnsNone:
    """The early-return on ``resp.status_code != 200`` is the actual
    fallback trigger. Pin it for every error-shape the SDXL server
    might emit when degraded.
    """

    @pytest.mark.parametrize(
        "status_code,scenario",
        [
            (503, "service degraded — the scenario we saw 2026-05-11 17:48 UTC"),
            (500, "internal server error"),
            (502, "bad gateway (load balancer in front of SDXL)"),
            (504, "gateway timeout"),
            (404, "endpoint not found (deploy / route mismatch)"),
            (401, "auth required"),
            (429, "rate limited"),
        ],
    )
    @pytest.mark.asyncio
    async def test_non_200_response_returns_none(self, status_code, scenario):
        """``_render_sdxl`` returns None on any non-200 — the stage's
        Pexels-fallback branch keys on that None."""
        from services.stages.source_featured_image import _render_sdxl

        post_resp = _fake_response(status_code)
        with patch(
            "services.stages.source_featured_image.httpx.AsyncClient",
            return_value=_fake_httpx_client_returning(post_resp),
        ), patch(
            "services.gpu_scheduler.gpu", _gpu_lock_noop(),
        ):
            result = await _render_sdxl(
                sdxl_url="http://sdxl.example:9836",
                sdxl_prompt="a serene server room",
                negative_prompt="text, words",
                task_id="task-degraded-test",
            )

        assert result is None, (
            f"_render_sdxl must return None on HTTP {status_code} ({scenario}) "
            f"so the featured-image stage falls through to Pexels."
        )


# ---------------------------------------------------------------------------
# Stage-level: SDXL degraded → Pexels image makes it onto the context
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStageFallsBackToPexels:
    """End-to-end through ``SourceFeaturedImageStage.execute``: when
    the SDXL branch returns None, the Pexels branch runs and the
    resulting context carries the Pexels image (not NULL, not a
    crashed pipeline)."""

    @pytest.mark.asyncio
    async def test_sdxl_returns_none_yields_pexels_featured_image(self):
        from services.stages.source_featured_image import SourceFeaturedImageStage

        pexels_image = SimpleNamespace(
            url="https://images.pexels.com/photos/12345/photo.jpg",
            photographer="Test Photographer",
            source="pexels",
            width=940, height=650,
        )

        image_service = SimpleNamespace(
            sdxl_available=True,
            sdxl_initialized=True,
            search_featured_image=AsyncMock(return_value=pexels_image),
        )

        ctx = {
            "topic": "NVMe Gen5 thermal throttling",
            "tags": ["nvme", "thermal"],
            "generate_featured_image": True,
            "task_id": "task-sdxl-degraded",
            "image_service": image_service,
            "site_config": SimpleNamespace(
                get=lambda key, default=None: default,
                get_int=lambda key, default=0: default,
                get_float=lambda key, default=0.0: default,
                get_bool=lambda key, default=False: default,
            ),
        }

        # Force the SDXL branch to return None — simulating the
        # 2026-05-11 17:48 UTC degraded scenario.
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            new=AsyncMock(return_value=None),
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})

        # Stage didn't crash.
        assert result.ok is True
        assert "pexels" in (result.detail or "").lower()

        # Pexels image landed on context.
        updates = result.context_updates
        assert updates["featured_image"] is pexels_image
        assert updates["featured_image_url"] == pexels_image.url
        assert updates["featured_image_photographer"] == "Test Photographer"
        assert updates["featured_image_source"] == "pexels"

        # The stages dict records pexels as the image source — the
        # operator dashboards key on this to render the "image source"
        # badge correctly.
        assert updates["stages"]["3_image_source"] == "pexels"
        assert updates["stages"]["3_featured_image_found"] is True

    @pytest.mark.asyncio
    async def test_sdxl_and_pexels_both_fail_yields_no_image_no_crash(self):
        """If BOTH SDXL and Pexels fail (or return None), the stage
        completes with ``featured_image=None`` and the pipeline keeps
        running. This is the "graceful third-tier degradation" path —
        the post lands without a featured image rather than crashing
        the whole task.
        """
        from services.stages.source_featured_image import SourceFeaturedImageStage

        image_service = SimpleNamespace(
            sdxl_available=True,
            sdxl_initialized=True,
            search_featured_image=AsyncMock(return_value=None),
        )

        ctx = {
            "topic": "test topic",
            "generate_featured_image": True,
            "task_id": "task-both-fail",
            "image_service": image_service,
            "site_config": SimpleNamespace(
                get=lambda key, default=None: default,
                get_int=lambda key, default=0: default,
                get_float=lambda key, default=0.0: default,
                get_bool=lambda key, default=False: default,
            ),
        }

        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            new=AsyncMock(return_value=None),
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})

        # Stage still succeeded (graceful — pipeline continues).
        assert result.ok is True
        # No image, no crash, observable in the stages dict.
        updates = result.context_updates
        assert updates.get("featured_image") is None
        assert updates["stages"]["3_featured_image_found"] is False
