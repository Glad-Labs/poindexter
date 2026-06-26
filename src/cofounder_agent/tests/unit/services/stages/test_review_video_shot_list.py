"""ReviewVideoShotListStage — director self-critique pass (Piece 1, spec §3.1).

Mirrors the dispatch-path test harness from test_generate_video_shot_list.py:
both services.gpu_scheduler.gpu and services.prompt_manager.get_prompt_manager
are patched, because the stage acquires the GPU lock and renders a skill prompt.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _valid_list(*, source1: str = "image_kenburns") -> dict:
    return {
        "version": 1,
        "aspect": "16:9",
        "total_duration_s": 10.0,
        "shots": [
            {"idx": 0, "duration_s": 5.0, "intent": "open", "source": "pexels",
             "query": "server room", "narration_offset_s": 0.0},
            {"idx": 1, "duration_s": 5.0, "intent": "close", "source": source1,
             "prompt": "flat vector circuit, deep navy and cyan, faceless",
             "narration_offset_s": 5.0},
        ],
        "director_model": "draft-model",
        "director_prompt_version": "v1.2",
        "director_decided_at": "2026-06-19T00:00:00+00:00",
    }


def _make_db() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    db = MagicMock()
    db.pool = pool
    return db


def _platform(
    *, dispatch_text: str | None = None, model: str = "reviewer", timeout: int = 300,
) -> MagicMock:
    p = MagicMock()
    p.config.get = MagicMock(return_value=model)
    p.config.get_int = MagicMock(return_value=timeout)
    p.dispatch.complete = AsyncMock(return_value=MagicMock(text=dispatch_text))
    return p


@pytest.mark.asyncio
async def test_revised_list_replaces_original() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")  # reviewer promoted a hero shot
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": _platform(dispatch_text=json.dumps(revised)),
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "wan21"


@pytest.mark.asyncio
async def test_failure_keeps_original_non_halting() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    original = _valid_list()  # shot[1].source == "image_kenburns"
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": original,
        "platform": _platform(dispatch_text="I refuse to output JSON."),
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok  # non-halting
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "image_kenburns"


@pytest.mark.asyncio
async def test_skips_when_no_shot_list() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage
    result = await ReviewVideoShotListStage().execute({"task_id": "t"}, {})
    assert result.ok
    assert result.metrics.get("skipped") is True


@pytest.mark.asyncio
async def test_short_list_also_reviewed() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    long_revised = _valid_list(source1="wan21")
    short_revised = _valid_list(source1="wan21")
    short_revised["aspect"] = "9:16"
    platform = _platform()
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text=json.dumps(long_revised)),
        MagicMock(text=json.dumps(short_revised)),
    ])
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "short_summary_script": "short " * 10,
        "video_shot_list": _valid_list(),
        "short_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert platform.dispatch.complete.call_count == 2
    assert result.context_updates["short_shot_list"]["aspect"] == "9:16"


@pytest.mark.asyncio
async def test_review_timeout_read_from_db_setting() -> None:
    """The per-call LLM timeout is read from ``video_director_timeout_seconds``
    and threaded into the dispatch — not hardcoded. Finding: the old hardcoded
    120s timed out the writer-grade reviewer mid shot-list (same bug the director
    had, #1750), leaving the draft list unreviewed on every run."""
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")
    platform = _platform(dispatch_text=json.dumps(revised), timeout=555)
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        await ReviewVideoShotListStage().execute(ctx, {})

    # timeout sourced from the DB setting (not the old hardcoded 120)
    assert platform.config.get_int.call_args.args[0] == "video_director_timeout_seconds"
    _, kwargs = platform.dispatch.complete.call_args
    assert kwargs["timeout_s"] == 555
