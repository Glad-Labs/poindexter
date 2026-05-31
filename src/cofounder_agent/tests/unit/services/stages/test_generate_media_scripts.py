"""Regression tests for ``services/stages/generate_media_scripts.py``.

Pins the #517-Stage-A fix: a failure while parsing the video-scenes output
(e.g. the #272 ``_normalize_for_speech`` site_config regression) must NOT
discard a podcast_script that was already built — otherwise the downstream
``generate_video_shot_list`` director starves and produces 0 shot lists.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.stages.generate_media_scripts import GenerateMediaScriptsStage


@contextlib.asynccontextmanager
async def _fake_lock(*_a: Any, **_kw: Any):
    yield


def _ctx() -> dict[str, Any]:
    sc = MagicMock()
    sc.get.return_value = "llama3:latest"
    db = SimpleNamespace(pool=MagicMock())
    return {
        "title": "A Real Title",
        "content": "body " * 200,
        "site_config": sc,
        "database_service": db,
        "task_id": "t-mediascripts",
    }


@pytest.mark.asyncio
async def test_podcast_script_preserved_when_scene_parsing_fails():
    """Call 1 builds the script; Call-2 scene parsing raises — the script
    must still flow into context_updates so the director can run."""
    gpu = SimpleNamespace(lock=_fake_lock)
    result_obj = SimpleNamespace(text="PART1\n\nSHORT:\nsummary")

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch(
             "services.podcast_service._build_script_with_llm",
             new=AsyncMock(return_value="A" * 500),
         ), \
         patch(
             "services.llm_providers.dispatcher.dispatch_complete",
             new=AsyncMock(return_value=result_obj),
         ), \
         patch(
             "services.stages.generate_media_scripts._parse_scene_output",
             side_effect=RuntimeError(
                 "podcast_service requires a site_config",
             ),
         ):
        result = await GenerateMediaScriptsStage().execute(_ctx(), {})

    # The scene parse raised, but the already-built script must survive.
    assert result.context_updates.get("podcast_script") == "A" * 500
    assert result.context_updates.get("podcast_script_length") == 500


@pytest.mark.asyncio
async def test_happy_path_propagates_podcast_script_and_scenes():
    """Sanity: when nothing fails, podcast_script + scenes propagate."""
    gpu = SimpleNamespace(lock=_fake_lock)
    result_obj = SimpleNamespace(text="1. a scene\n2. another\n\nSHORT:\nsummary")

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch(
             "services.podcast_service._build_script_with_llm",
             new=AsyncMock(return_value="B" * 500),
         ), \
         patch(
             "services.llm_providers.dispatcher.dispatch_complete",
             new=AsyncMock(return_value=result_obj),
         ):
        result = await GenerateMediaScriptsStage().execute(_ctx(), {})

    assert result.ok is True
    assert result.context_updates["podcast_script"] == "B" * 500
