"""``media.package_short`` — adapts graph state to the Short's packaging services.

The package belongs to the Short's upload, is fail-soft (a title or thumbnail
must never cost the Short), and hands everything to ``media.persist``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.modules.content.atoms import media_package_short as atom
from poindexter.services.short_package import ShortPackage
from poindexter.services.video_thumbnail import ThumbnailResult
from tests.unit._nonempty import nonempty

_PACKAGE = "poindexter.services.short_package.compose_short_package"
_THUMB = "poindexter.services.video_thumbnail.compose_short_thumbnail"


def _short(tmp_path):
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")
    return str(video)


@pytest.mark.asyncio
async def test_no_short_means_no_package(tmp_path):
    with patch(_PACKAGE, AsyncMock()) as pkg, patch(_THUMB, AsyncMock()) as thumb:
        out = await atom.run({"task_id": "t1", "short_video_path": str(tmp_path / "missing.mp4")})
    assert out == {"short_package": {}, "short_thumbnail_path": ""}
    pkg.assert_not_awaited()
    thumb.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_package_and_thumbnail_reach_the_state(tmp_path):
    package = ShortPackage(title="RTX 5090 fits 70B at 4-bit", description="d", model="m")
    result = ThumbnailResult(path="/tmp/s.jpg", hook="H", hook_note="the long thumbnail's hook",
                             background="featured_image", size_bytes=9)
    with patch(_PACKAGE, AsyncMock(return_value=package)) as pkg, \
         patch(_THUMB, AsyncMock(return_value=result)) as thumb:
        out = await atom.run({
            "task_id": "t1", "short_video_path": _short(tmp_path), "niche_slug": "glad-labs",
            "short_summary_script": "The narration.", "long_thumbnail_meta": {"hook": "H"},
        })
    assert out["short_package"] == package.as_metadata()
    assert out["short_thumbnail_path"] == "/tmp/s.jpg"
    assert out["short_thumbnail_meta"]["hook"] == "H"
    assert pkg.await_args.kwargs["script"] == "The narration."
    assert thumb.await_args.kwargs["hook_text"] == "H" and thumb.await_args.kwargs["niche_slug"] == "glad-labs"


@pytest.mark.asyncio
async def test_with_no_hook_in_state_the_thumbnail_reads_the_stored_one(tmp_path):
    """``hook_text=None`` (not "") tells the composer to look up the stored hook."""
    with patch(_PACKAGE, AsyncMock(return_value=ShortPackage())), \
         patch(_THUMB, AsyncMock(return_value=None)) as thumb:
        out = await atom.run({"task_id": "t1", "short_video_path": _short(tmp_path)})
    assert thumb.await_args.kwargs["hook_text"] is None
    assert out["short_thumbnail_path"] == ""


@pytest.mark.asyncio
async def test_a_blank_script_in_state_falls_back_to_the_stored_narration(tmp_path):
    with patch(_PACKAGE, AsyncMock(return_value=ShortPackage())) as pkg, \
         patch(_THUMB, AsyncMock(return_value=None)):
        await atom.run({"task_id": "t1", "short_video_path": _short(tmp_path), "short_summary_script": "  "})
    assert pkg.await_args.kwargs["script"] is None


@pytest.mark.asyncio
async def test_one_part_failing_never_costs_the_other_or_the_short(tmp_path):
    result = ThumbnailResult(path="/tmp/s.jpg", hook="", hook_note="", background="brand", size_bytes=1)
    with patch(_PACKAGE, AsyncMock(side_effect=RuntimeError("model gone"))), \
         patch(_THUMB, AsyncMock(return_value=result)):
        out = await atom.run({"task_id": "t1", "short_video_path": _short(tmp_path)})
    assert out["short_package"] == {} and out["short_thumbnail_path"] == "/tmp/s.jpg"
    with patch(_PACKAGE, AsyncMock(return_value=ShortPackage(title="T"))), \
         patch(_THUMB, AsyncMock(side_effect=RuntimeError("chromium gone"))):
        out = await atom.run({"task_id": "t1", "short_video_path": _short(tmp_path)})
    assert out["short_package"]["title"] == "T" and out["short_thumbnail_path"] == ""


def test_the_outputs_are_declared_state_channels():
    """LangGraph drops an undeclared key (#674): every output must be a PipelineState channel."""
    from poindexter.services.template_runner import PipelineState

    for spec in nonempty(atom.ATOM_META.outputs, "ATOM_META.outputs"):
        assert spec.name in PipelineState.__annotations__, spec.name


def test_the_node_sits_between_the_thumbnail_and_media_qa():
    from poindexter.services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF as spec

    edges = {(e["from"], e["to"]) for e in spec["edges"]}
    assert ("render_thumbnail", "package_short") in edges and ("package_short", "media_qa") in edges
    assert {"id": "package_short", "atom": "media.package_short"} in spec["nodes"]
