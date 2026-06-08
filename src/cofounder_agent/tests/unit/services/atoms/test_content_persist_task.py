"""Unit test: content.persist_task writes the media artifacts (incl. the
ambient audio path) into task_metadata."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms.content_persist_task import run as persist_run


@pytest.mark.asyncio
async def test_persist_includes_media_artifacts():
    captured: dict = {}

    async def _update_task(*, task_id, updates):
        captured.update(updates)

    db = SimpleNamespace(
        pool=MagicMock(),
        update_task=_update_task,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )

    state = {
        "task_id": "t-1",
        "content": "body",
        "title": "Title",
        "database_service": db,
        "podcast_script": "POD",
        "video_scenes": ["a"],
        "short_summary_script": "SHORT",
        "video_shot_list": {"version": 1, "shots": [{"idx": 0}]},
        "video_ambient_audio_path": "/tmp/ambient.wav",
        "podcast_audio_path": "/tmp/podcast_tts.wav",
        "podcast_intro_audio_path": "/tmp/intro.wav",
    }

    # pipeline_versions + log_revision are best-effort; let them no-op.
    import services.pipeline_db as _pdb
    _pdb.PipelineDB = lambda *_a, **_k: SimpleNamespace(upsert_version=AsyncMock())

    result = await persist_run(state)

    meta = captured["task_metadata"]
    assert meta["podcast_script"] == "POD"
    assert meta["video_shot_list"] == {"version": 1, "shots": [{"idx": 0}]}
    assert meta["video_ambient_audio_path"] == "/tmp/ambient.wav"
    assert meta["podcast_audio_path"] == "/tmp/podcast_tts.wav"
    assert meta["podcast_intro_audio_path"] == "/tmp/intro.wav"
    assert result["status"] == "awaiting_approval"
