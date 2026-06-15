"""Unit tests for the media.render_narration atom (poindexter#689).

Pins: long + short narration render from their OWN scripts with their OWN CTAs,
and the long lane falls back to the podcast script when video_long_script is empty.
"""

from __future__ import annotations

import pytest

from modules.content.atoms import media_render_narration


@pytest.mark.asyncio
async def test_renders_long_and_short_with_own_cta(monkeypatch):
    calls = []

    async def _fake_render(*, script, cta_key, site_config, task_id, key):
        calls.append((cta_key, key, script))
        return f"/tmp/{key}.mp3"

    monkeypatch.setattr(
        "modules.content.atoms._narration_render.render_narration", _fake_render
    )
    out = await media_render_narration.run({
        "task_id": "t1",
        "video_long_script": "long vo",
        "short_summary_script": "short vo",
        "site_config": object(),
    })
    assert out["long_narration_audio_path"] == "/tmp/t1_long.mp3"
    assert out["short_narration_audio_path"] == "/tmp/t1_short.mp3"
    cta_by_key = {k: c for (c, k, _s) in calls}
    assert cta_by_key["t1_long"] == "media.cta.video"
    assert cta_by_key["t1_short"] == "media.cta.video_short"


@pytest.mark.asyncio
async def test_long_falls_back_to_podcast_script(monkeypatch):
    seen = {}

    async def _fake_render(*, script, cta_key, **_kw):
        seen[cta_key] = script
        return "/tmp/x.mp3"

    monkeypatch.setattr(
        "modules.content.atoms._narration_render.render_narration", _fake_render
    )
    await media_render_narration.run({
        "task_id": "t1", "video_long_script": "",
        "podcast_script": "podcast body", "short_summary_script": "s",
        "site_config": object(),
    })
    assert seen["media.cta.video"] == "podcast body"


@pytest.mark.asyncio
async def test_empty_scripts_yield_empty_paths(monkeypatch):
    """No scripts → the helper no-ops per lane; both channels are ''."""
    async def _fake_render(*, script, **_kw):
        return "" if not script.strip() else "/tmp/x.mp3"

    monkeypatch.setattr(
        "modules.content.atoms._narration_render.render_narration", _fake_render
    )
    out = await media_render_narration.run({"task_id": "t1", "site_config": object()})
    assert out["long_narration_audio_path"] == ""
    assert out["short_narration_audio_path"] == ""
