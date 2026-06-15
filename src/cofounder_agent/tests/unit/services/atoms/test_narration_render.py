"""Unit tests for the shared narration-TTS helper (poindexter#689).

Pins the contract media.render_narration + podcast.render both depend on:
empty script / no site_config → "" (fail-soft), CTA appended before synth, and
a TTS exception never raises.
"""

from __future__ import annotations

import pytest

from modules.content.atoms import _narration_render


class _SC:
    """Minimal SiteConfig stand-in."""

    def __init__(self, d: dict) -> None:
        self._d = d

    def get(self, k: str, default=None):
        return self._d.get(k, default)


@pytest.mark.asyncio
async def test_empty_script_returns_empty():
    out = await _narration_render.render_narration(
        script="  ", cta_key="media.cta.video", site_config=_SC({}),
        task_id="t1", key="t1_long",
    )
    assert out == ""


@pytest.mark.asyncio
async def test_no_site_config_returns_empty():
    out = await _narration_render.render_narration(
        script="hello", cta_key="media.cta.video", site_config=None,
        task_id="t1", key="t1_long",
    )
    assert out == ""


@pytest.mark.asyncio
async def test_appends_cta_and_synthesizes(monkeypatch):
    seen = {}

    class _PS:
        def __init__(self, *, site_config):
            pass

        async def synthesize(self, text, *, key):
            seen["text"], seen["key"] = text, key
            return "/tmp/out.mp3", 12.0

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Body.", cta_key="media.cta.video",
        site_config=_SC({"media.cta.video": "Like and subscribe."}),
        task_id="t1", key="t1_long",
    )
    assert out == "/tmp/out.mp3"
    assert seen["text"].endswith("Like and subscribe.")
    assert seen["key"] == "t1_long"


@pytest.mark.asyncio
async def test_no_cta_synthesizes_bare_script(monkeypatch):
    seen = {}

    class _PS:
        def __init__(self, *, site_config):
            pass

        async def synthesize(self, text, *, key):
            seen["text"] = text
            return "/tmp/out.mp3", 1.0

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Body only.", cta_key="media.cta.video",
        site_config=_SC({}), task_id="t1", key="t1_long",
    )
    assert out == "/tmp/out.mp3"
    assert seen["text"] == "Body only."


@pytest.mark.asyncio
async def test_tts_exception_is_failsoft(monkeypatch):
    class _PS:
        def __init__(self, *, site_config):
            pass

        async def synthesize(self, text, *, key):
            raise RuntimeError("speaches down")

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Body.", cta_key="media.cta.video",
        site_config=_SC({}), task_id="t1", key="t1_long",
    )
    assert out == ""
