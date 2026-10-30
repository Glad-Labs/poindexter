"""media.generate_ambient_bed: the news Short's music bed node."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.modules.content.atoms import media_generate_ambient_bed as atom

_BED = "poindexter.modules.content.atoms.media_generate_ambient_bed.ambient_bed_or_nothing"


@pytest.mark.asyncio
async def test_the_bed_path_flows_to_the_render():
    with patch(_BED, new=AsyncMock(return_value="/data/video/t9_ambient.wav")) as bed:
        out = await atom.run({"task_id": "t9", "title": "RTX 5090 shipment goes missing"})
    assert out == {"video_ambient_audio_path": "/data/video/t9_ambient.wav"}
    kw = bed.await_args.kwargs
    assert kw["output_stem"] == "t9_ambient"
    assert kw["stage"] == "media.generate_ambient_bed"
    assert bed.await_args.args[0] == "RTX 5090 shipment goes missing"


@pytest.mark.asyncio
async def test_scene_text_is_preferred_as_the_mood():
    with patch(_BED, new=AsyncMock(return_value="/b.wav")) as bed:
        await atom.run({
            "task_id": "t9", "title": "headline",
            "video_scenes": ["a cinematic wide shot of a server room at night"],
        })
    assert bed.await_args.args[0] == "a cinematic wide shot of a server room at night"


@pytest.mark.asyncio
async def test_no_bed_leaves_the_channel_unset():
    """The render then mixes nothing, exactly as before this node existed."""
    with patch(_BED, new=AsyncMock(return_value="")):
        assert await atom.run({"task_id": "t9", "title": "x"}) == {}


@pytest.mark.asyncio
async def test_a_bed_already_set_is_kept():
    with patch(_BED, new=AsyncMock()) as bed:
        out = await atom.run({"task_id": "t9", "video_ambient_audio_path": "/kept.wav"})
    assert out == {} and bed.await_count == 0


@pytest.mark.asyncio
async def test_no_task_id_means_no_bed():
    with patch(_BED, new=AsyncMock()) as bed:
        assert await atom.run({"title": "x"}) == {}
    assert bed.await_count == 0
