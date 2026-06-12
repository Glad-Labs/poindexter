"""Unit tests for the ``podcast.render`` Stage-3 atom (#689 deviation).

Renders the loaded ``podcast_script`` to an MP3 via ``PodcastService.synthesize``
after appending the DB-configurable per-medium CTA outro
(``media.cta.podcast``), and surfaces ``podcast_audio_path``. Fail-soft: a TTS
failure or empty script returns an empty path rather than halting the graph.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from modules.content.atoms import podcast_render
from services.podcast_service import PodcastService
from services.site_config import SiteConfig


@pytest.mark.asyncio
async def test_render_appends_cta_and_returns_path() -> None:
    sc = SiteConfig(
        initial_config={
            "media.cta.podcast": "Rate and review the show.",
            "podcast_name": "Show", "site_domain": "x.io",
        }
    )
    captured: dict[str, Any] = {}

    async def fake_synth(self: Any, script: str, *, output_path: Any = None, key: str = "") -> tuple[str, int]:
        captured["script"] = script
        captured["key"] = key
        return ("/tmp/out.mp3", 33)

    with patch.object(PodcastService, "synthesize", fake_synth):
        result = await podcast_render.run(
            {"task_id": "t1", "podcast_script": "Episode body.", "site_config": sc}
        )

    assert result["podcast_audio_path"] == "/tmp/out.mp3"
    assert "Rate and review the show." in captured["script"]
    assert "Episode body." in captured["script"]
    assert captured["key"] == "t1"


@pytest.mark.asyncio
async def test_render_noop_on_empty_script() -> None:
    sc = SiteConfig(initial_config={})
    result = await podcast_render.run(
        {"task_id": "t1", "podcast_script": "   ", "site_config": sc}
    )
    assert result == {"podcast_audio_path": ""}


@pytest.mark.asyncio
async def test_render_failsoft_when_synthesis_raises() -> None:
    sc = SiteConfig(initial_config={})

    async def boom(self: Any, script: str, *, output_path: Any = None, key: str = "") -> tuple[str, int]:
        raise RuntimeError("tts down")

    with patch.object(PodcastService, "synthesize", boom):
        result = await podcast_render.run(
            {"task_id": "t1", "podcast_script": "Body.", "site_config": sc}
        )
    assert result == {"podcast_audio_path": ""}


@pytest.mark.asyncio
async def test_render_noop_when_no_site_config() -> None:
    result = await podcast_render.run({"task_id": "t1", "podcast_script": "Body."})
    assert result == {"podcast_audio_path": ""}
