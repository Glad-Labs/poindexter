"""``media.render_thumbnail`` — adapts graph state to the thumbnail composer.

The thumbnail belongs to the long upload, is fail-soft (a thumbnail must
never cost the video), and hands its hook + background to ``media.persist``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.modules.content.atoms import media_render_thumbnail as atom
from poindexter.services.video_thumbnail import ThumbnailResult
from tests.unit._nonempty import nonempty

_COMPOSE = "poindexter.services.video_thumbnail.compose_video_thumbnail"


@pytest.mark.asyncio
async def test_no_long_video_means_no_thumbnail(tmp_path):
    with patch(_COMPOSE, AsyncMock()) as compose:
        out = await atom.run({"task_id": "t1", "long_video_path": str(tmp_path / "missing.mp4")})
    assert out == {"long_thumbnail_path": ""}
    compose.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_composed_thumbnail_and_its_meta_reach_the_state(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = ThumbnailResult(path="/tmp/t.jpg", hook="No NCCL", hook_note="", background="featured_image", size_bytes=9)
    with patch(_COMPOSE, AsyncMock(return_value=result)) as compose:
        out = await atom.run({
            "task_id": "t1", "long_video_path": str(video), "niche_slug": "glad-labs",
            "video_shot_list": '{"shots": []}', "video_long_script": "narration",
        })
    assert out["long_thumbnail_path"] == "/tmp/t.jpg"
    assert out["long_thumbnail_meta"] == {"hook": "No NCCL", "hook_note": "", "background": "featured_image", "size_bytes": 9}
    kw = compose.await_args.kwargs
    assert kw["shot_list"] == {"shots": []} and kw["niche_slug"] == "glad-labs" and kw["source_text"] == "narration"


@pytest.mark.asyncio
async def test_a_composer_crash_never_costs_the_video(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with patch(_COMPOSE, AsyncMock(side_effect=RuntimeError("chromium gone"))):
        out = await atom.run({"task_id": "t1", "long_video_path": str(video)})
    assert out == {"long_thumbnail_path": ""}


def test_the_outputs_are_declared_state_channels():
    """LangGraph drops an undeclared key (#674): both outputs must be PipelineState channels."""
    from poindexter.services.template_runner import PipelineState

    for spec in nonempty(atom.ATOM_META.outputs, "ATOM_META.outputs"):
        assert spec.name in PipelineState.__annotations__, spec.name
