"""Regression tests for the retry loop that could never retry (poindexter#3229).

``_try_image_gen_featured`` wraps ``_render_image_gen`` in a
``for attempt in range(1, attempts + 1)`` loop whose comment states the intent
plainly: "The dominant failure is a WINDOW, not a verdict: image-gen exits and
restarts to hand VRAM back for a video render, or the GPU lock times out under
contention. Both clear in seconds."

Neither was ever retried. ``_render_image_gen`` had no exception handling, so a
read timeout or a wedged lock arrived as an EXCEPTION and flew straight past the
loop into the caller's outer ``except Exception`` — one attempt, no backoff,
hero abandoned. Production evidence, task d297faa0 on 2026-08-15::

    02:17:55 [IMAGE] Style: risograph print style | image-gen prompt: ...
    02:22:58 image-gen featured render failed () — falling back to a Pexels ...

303 seconds is one ``image_render_timeout_seconds``, not two attempts. And the
error rendered as ``()`` because ``str(httpx.ReadTimeout())`` is the empty
string — a warn-level finding that fired for weeks naming no cause.

Seven published posts had shipped with no hero image by the time this was found.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from modules.content.stages.source_featured_image import (
    _try_image_gen_featured,
    describe_exception,
)


def _site_config(**overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "image_gen_render_attempts": 3,
        "image_gen_retry_backoff_seconds": 0,
        "image_render_timeout_seconds": 30,
        "image_negative_prompt": "text, faces",
        "image_gen_server_url": "http://image-gen-server:9836",
        "image_generation_model": "z_image_turbo",
    }
    settings.update(overrides)
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda k, d=None: settings.get(k, d))
    sc.get_int = MagicMock(side_effect=lambda k, d=0: int(settings.get(k, d)))
    sc.get_float = MagicMock(side_effect=lambda k, d=0.0: float(settings.get(k, d)))
    sc.get_bool = MagicMock(side_effect=lambda k, d=False: bool(settings.get(k, d)))
    return sc


async def _run(render_side_effect, **cfg: Any):
    """Drive _try_image_gen_featured with a stubbed renderer."""
    with (
        patch(
            "modules.content.stages.source_featured_image._render_image_gen",
            new=AsyncMock(side_effect=render_side_effect),
        ) as render,
        patch(
            "modules.content.stages.source_featured_image._build_image_gen_prompt",
            new=AsyncMock(return_value="a glowing server rack, flat vector"),
        ),
        patch(
            "modules.content.stages.source_featured_image._upload_featured_to_r2",
            new=AsyncMock(return_value="https://r2.example/img.webp"),
        ),
    ):
        result = await _try_image_gen_featured(
            subject="a stuck task in a queue",
            existing_prompt="",
            task_id="t-1",
            on_style_picked=lambda _s: None,
            style_tracker=MagicMock(recent=MagicMock(return_value=[])),
            site_config=_site_config(**cfg),
            platform=MagicMock(),
        )
    return result, render


class TestTransientFailuresAreRetried:
    @pytest.mark.asyncio
    async def test_transient_failure_then_success_yields_an_image(self):
        """The window closes on attempt 2 — the hero must survive."""
        result, render = await _run(
            [
                (None, {"transient": True, "failure": "ReadTimeout"}),
                ("/tmp/hero.png", {"model": "z_image_turbo", "seed": 7}),
            ],
        )
        assert result is not None
        assert result.url == "https://r2.example/img.webp"
        assert render.await_count == 2

    @pytest.mark.asyncio
    async def test_all_attempts_transient_exhausts_the_budget(self):
        """Three configured attempts must mean three renders, not one."""
        result, render = await _run(
            [(None, {"transient": True, "failure": "ReadTimeout"})] * 3,
        )
        assert result is None
        assert render.await_count == 3


class TestVerdictsStopEarly:
    @pytest.mark.asyncio
    async def test_ocr_gate_rejection_is_not_retried(self):
        """The server already re-rolled its seed budget — retrying re-buys it."""
        result, render = await _run([(None, {"ocr_gate_rejected": True})] * 3)
        assert result is None
        assert render.await_count == 1

    @pytest.mark.asyncio
    async def test_terminal_failure_is_not_retried(self):
        """game_mode holds the GPU for hours; this stage cannot outlast it."""
        result, render = await _run(
            [(None, {"transient": False, "failure": "GpuBusyError: game_mode"})] * 3,
        )
        assert result is None
        assert render.await_count == 1


class TestRenderContainsTransientFailures:
    """``_render_image_gen`` must RETURN these, not raise them past the loop."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ReadTimeout("timed out"),
            httpx.ConnectError("connection refused"),
            httpx.RemoteProtocolError("server disconnected"),
        ],
        ids=["read-timeout", "connect-error", "server-disconnected"],
    )
    async def test_transport_failures_come_back_as_values(self, exc):
        from modules.content.stages.source_featured_image import _render_image_gen

        gpu = MagicMock()
        gpu.lock = MagicMock(return_value=AsyncMock())
        gpu.lock.return_value.__aenter__ = AsyncMock()
        gpu.lock.return_value.__aexit__ = AsyncMock(return_value=False)

        client = AsyncMock()
        client.post = AsyncMock(side_effect=exc)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("services.gpu_scheduler.gpu", gpu),
            patch("httpx.AsyncClient", return_value=client),
        ):
            path, meta = await _render_image_gen(
                "http://image-gen-server:9836", "a prompt", "neg", task_id="t-1",
            )

        assert path is None
        assert meta["transient"] is True
        # The failure must NAME itself — "failed ()" is what hid this bug.
        assert type(exc).__name__ in meta["failure"]

    @pytest.mark.asyncio
    async def test_server_5xx_is_transient(self):
        from modules.content.stages.source_featured_image import _render_image_gen

        gpu = MagicMock()
        gpu.lock = MagicMock(return_value=AsyncMock())
        gpu.lock.return_value.__aenter__ = AsyncMock()
        gpu.lock.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = MagicMock(status_code=503)
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("services.gpu_scheduler.gpu", gpu),
            patch("httpx.AsyncClient", return_value=client),
        ):
            path, meta = await _render_image_gen(
                "http://image-gen-server:9836", "a prompt", "neg",
            )

        assert path is None
        assert meta["transient"] is True
        assert "503" in meta["failure"]


class TestDescribeException:
    def test_empty_message_falls_back_to_the_type(self):
        """str(ReadTimeout()) == "" — the emptiness IS the bug being fixed."""
        assert str(httpx.ReadTimeout("")) == ""
        assert describe_exception(httpx.ReadTimeout("")) == "ReadTimeout"

    def test_message_is_kept_when_there_is_one(self):
        assert describe_exception(RuntimeError("gpu busy")) == "RuntimeError: gpu busy"
