"""Featured-stage fan-out wiring — the stage composes the service.

The fan-out service never imports the stage (services must not depend on
``modules/content``); the stage renders its own zimage candidate and hands
off. These tests pin that composition: the winner's path replaces the
zimage path before the R2 upload, a fan-out miss changes nothing, and the
whole seam is inert while ``image_fanout_enabled`` is off.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.stages.source_featured_image import _try_image_gen_featured


def _site_config(**overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "image_gen_render_attempts": 1,
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


async def _run(*, render_result, fanout_mock, sc, pool=None):
    with (
        patch(
            "modules.content.stages.source_featured_image._render_image_gen",
            new=AsyncMock(return_value=render_result),
        ),
        patch(
            "modules.content.stages.source_featured_image._build_image_gen_prompt",
            new=AsyncMock(return_value="a glowing server rack, flat vector"),
        ),
        patch(
            "modules.content.stages.source_featured_image._upload_featured_to_r2",
            new=AsyncMock(return_value="https://r2.example/img.webp"),
        ) as upload,
        patch("services.image_fanout.run_featured_fanout", fanout_mock),
    ):
        result = await _try_image_gen_featured(
            subject="a stuck task in a queue",
            existing_prompt="",
            task_id="t-1",
            on_style_picked=lambda _s: None,
            style_tracker=MagicMock(recent=MagicMock(return_value=[])),
            site_config=sc,
            platform=MagicMock(),
            pool=pool,
        )
    return result, upload


class TestFanoutDisabled:
    @pytest.mark.asyncio
    async def test_flag_off_never_calls_the_service(self):
        fanout = AsyncMock()
        result, upload = await _run(
            render_result=("/tmp/z.png", {"model": "z_image_turbo"}),
            fanout_mock=fanout,
            sc=_site_config(),  # image_fanout_enabled absent -> off
        )
        assert result is not None
        fanout.assert_not_awaited()
        assert upload.await_args.args[0] == "/tmp/z.png"


class TestFanoutEnabled:
    @pytest.mark.asyncio
    async def test_winner_path_replaces_zimage_before_upload(self):
        fanout = AsyncMock(return_value=(
            "/tmp/qwen.png",
            {"model": "qwen", "fanout": {"winner": "qwen", "judge_ran": True,
                                         "scores": {"qwen": 90.0}}},
        ))
        pool = MagicMock()
        result, upload = await _run(
            render_result=("/tmp/z.png", {"model": "z_image_turbo"}),
            fanout_mock=fanout,
            sc=_site_config(image_fanout_enabled="true"),
            pool=pool,
        )
        assert result is not None
        assert upload.await_args.args[0] == "/tmp/qwen.png"
        assert result.gen_meta["fanout"]["winner"] == "qwen"
        # The stage's own zimage render + the pool ride into the service.
        kwargs = fanout.await_args.kwargs
        assert kwargs["zimage_path"] == "/tmp/z.png"
        assert kwargs["pool"] is pool

    @pytest.mark.asyncio
    async def test_zimage_miss_can_still_ship_a_fanout_winner(self):
        """The OCR-gate class: z-image blocked, another candidate covers."""
        fanout = AsyncMock(return_value=(
            "/tmp/schnell.png",
            {"model": "schnell", "fanout": {"winner": "schnell",
                                            "judge_ran": True, "scores": {}}},
        ))
        result, upload = await _run(
            render_result=(None, {"ocr_gate_rejected": True}),
            fanout_mock=fanout,
            sc=_site_config(image_fanout_enabled="true"),
        )
        assert result is not None
        assert upload.await_args.args[0] == "/tmp/schnell.png"
        assert fanout.await_args.kwargs["zimage_path"] is None

    @pytest.mark.asyncio
    async def test_fanout_none_preserves_legacy_no_image_path(self):
        fanout = AsyncMock(return_value=None)
        result, upload = await _run(
            render_result=(None, {"transient": True, "failure": "ReadTimeout"}),
            fanout_mock=fanout,
            sc=_site_config(image_fanout_enabled="true"),
        )
        assert result is None
        upload.assert_not_awaited()
