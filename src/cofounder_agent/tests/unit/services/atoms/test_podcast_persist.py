"""Unit tests for the ``podcast.persist`` Stage-3 atom (#689 deviation).

The atom is the terminal node of ``podcast_pipeline``: it moves the rendered
narration MP3 out of the temp dir into the durable podcast dir and records a
task-keyed ``media_assets`` row (``type='podcast'``, ``post_id=NULL`` — the post
is resolved later by ``podcast_distribute``).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.atoms import podcast_persist


class _FakePool:
    """Sentinel pool — the atom only forwards it to record_media_asset."""


@pytest.mark.asyncio
async def test_records_podcast_asset_and_moves_file(tmp_path: Path) -> None:
    src = tmp_path / "tmp_render.mp3"
    src.write_bytes(b"ID3fake-audio-bytes")
    durable_dir = tmp_path / "podcast"

    state = {
        "task_id": "task-abc",
        "podcast_audio_path": str(src),
        "pool": _FakePool(),
    }

    with patch.object(podcast_persist, "PODCAST_DIR", durable_dir), patch.object(
        podcast_persist, "record_media_asset", new=AsyncMock(return_value="asset-1")
    ) as rec:
        result = await podcast_persist.run(state)

    assert result == {"media_assets_recorded": ["asset-1"]}

    # File moved out of temp into durable task-keyed location.
    assert not src.exists()
    durable = durable_dir / "task-abc.mp3"
    assert durable.exists()

    # Recorded as a task-keyed podcast asset with no post yet.
    rec.assert_awaited_once()
    kwargs = rec.await_args.kwargs
    assert kwargs["asset_type"] == "podcast"
    assert kwargs["task_id"] == "task-abc"
    assert kwargs["post_id"] is None
    assert kwargs["storage_path"] == str(durable)


@pytest.mark.asyncio
async def test_skips_when_no_audio_path(tmp_path: Path) -> None:
    state = {"task_id": "task-abc", "pool": _FakePool()}
    with patch.object(
        podcast_persist, "record_media_asset", new=AsyncMock()
    ) as rec:
        result = await podcast_persist.run(state)
    assert result == {"media_assets_recorded": []}
    rec.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_no_pool(tmp_path: Path) -> None:
    src = tmp_path / "tmp_render.mp3"
    src.write_bytes(b"audio")
    state = {"task_id": "task-abc", "podcast_audio_path": str(src)}
    result = await podcast_persist.run(state)
    assert result == {"media_assets_recorded": []}


@pytest.mark.asyncio
async def test_requires_task_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await podcast_persist.run({"podcast_audio_path": "x"})
