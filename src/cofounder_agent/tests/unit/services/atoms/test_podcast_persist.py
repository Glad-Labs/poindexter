"""Unit tests for the ``podcast.persist`` Stage-3 atom (#689 deviation).

The atom is the terminal node of ``podcast_pipeline``: it moves the rendered
narration MP3 out of the temp dir into the durable podcast dir and records a
task-keyed ``media_assets`` row (``type='podcast'``, ``post_id=NULL`` — the post
is resolved later by ``podcast_distribute``).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_no_audio_path_emits_no_finding(tmp_path: Path) -> None:
    """The legitimate no-op stays silent — the render atom already emitted."""
    state = {"task_id": "task-abc", "pool": _FakePool()}
    mock_emit = MagicMock()
    with patch.object(podcast_persist, "record_media_asset", new=AsyncMock()), \
            patch.object(podcast_persist, "emit_finding", mock_emit):
        await podcast_persist.run(state)
    mock_emit.assert_not_called()


@pytest.mark.asyncio
async def test_move_failure_emits_finding(tmp_path: Path) -> None:
    """A failed move is a DEFECT, not a no-op (issue #877).

    The TTS already succeeded and the audio exists — only the move to durable
    storage failed. Returning the same empty list as "no narration produced"
    leaves no media_asset row, so media_reconciliation re-dispatches and
    re-pays the whole TTS render against the identical disk failure. Same
    shape as #874: a logger.warning is visible to a human, but the return
    value stays success-shaped so the watchdog can't branch on it.
    """
    src = tmp_path / "tmp_render.mp3"
    src.write_bytes(b"ID3fake-audio-bytes")
    state = {
        "task_id": "task-abc",
        "podcast_audio_path": str(src),
        "pool": _FakePool(),
    }
    mock_emit = MagicMock()
    rec = AsyncMock()
    with patch.object(podcast_persist, "PODCAST_DIR", tmp_path / "podcast"), \
            patch.object(podcast_persist, "record_media_asset", new=rec), \
            patch.object(podcast_persist, "emit_finding", mock_emit), \
            patch.object(
                podcast_persist.shutil, "move",
                side_effect=OSError("disk full"),
            ):
        result = await podcast_persist.run(state)

    assert result == {"media_assets_recorded": []}
    rec.assert_not_awaited()
    assert mock_emit.call_count == 1
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["kind"] == "podcast_persist_failed"
    assert kwargs["severity"] == "warn"
    assert kwargs["extra"]["task_id"] == "task-abc"
    assert kwargs["dedup_key"] == "podcast_persist_failed:task-abc"


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


@pytest.mark.asyncio
async def test_stamps_post_id_when_state_carries_one(tmp_path: Path) -> None:
    """Architect plan runs LOAD a pre-existing post (state post_id arrives
    via run params) — the pipeline_task_id resolver in podcast_distribute
    never matches those tasks, so the asset must be stamped at persist or
    it stays invisible to the media approval surface (live operator
    report) and becomes orphan-reaper bait."""
    src = tmp_path / "tmp_render.mp3"
    src.write_bytes(b"ID3fake-audio-bytes")

    state = {
        "task_id": "task-plan",
        "podcast_audio_path": str(src),
        "post_id": "78f8a6cc-f18d-4ba8-b57e-8a09f0f85d8c",
        "pool": _FakePool(),
    }

    with patch.object(podcast_persist, "PODCAST_DIR", tmp_path / "p"), patch.object(
        podcast_persist, "record_media_asset", new=AsyncMock(return_value="a-2")
    ) as rec:
        result = await podcast_persist.run(state)

    assert result == {"media_assets_recorded": ["a-2"]}
    assert rec.await_args.kwargs["post_id"] == (
        "78f8a6cc-f18d-4ba8-b57e-8a09f0f85d8c"
    )


@pytest.mark.asyncio
async def test_blank_post_id_stays_none(tmp_path: Path) -> None:
    src = tmp_path / "tmp_render.mp3"
    src.write_bytes(b"ID3fake-audio-bytes")
    state = {
        "task_id": "task-c",
        "podcast_audio_path": str(src),
        "post_id": "   ",
        "pool": _FakePool(),
    }
    with patch.object(podcast_persist, "PODCAST_DIR", tmp_path / "p"), patch.object(
        podcast_persist, "record_media_asset", new=AsyncMock(return_value="a-3")
    ) as rec:
        await podcast_persist.run(state)
    assert rec.await_args.kwargs["post_id"] is None
