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


# ── Intro/outro sting mix (poindexter#690 finish) ────────────────────────


def _sting_state(tmp_path, *, enabled="true"):
    sting = tmp_path / "sting.wav"
    sting.write_bytes(b"RIFFfake")
    sc = SiteConfig(initial_config={
        "podcast_sting_mix_enabled": enabled,
        "media.cta.podcast": "",
    })
    return {
        "task_id": "t-sting",
        "podcast_script": "Hello world, this is the show.",
        "podcast_intro_audio_path": str(sting),
        "site_config": sc,
    }


@pytest.mark.asyncio
async def test_sting_mix_replaces_path_on_success(tmp_path, monkeypatch) -> None:
    async def fake_narration(**kwargs):
        return "/tmp/dry.mp3"

    async def fake_mix(narration, sting, *, site_config=None, task_id=None):
        assert narration == "/tmp/dry.mp3"
        return "/tmp/mixed.mp3"

    import modules.content.atoms._narration_render as nr
    import services.podcast_sting_mixer as mixer

    monkeypatch.setattr(nr, "render_narration", fake_narration)
    monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
    out = await podcast_render.run(_sting_state(tmp_path))
    assert out == {"podcast_audio_path": "/tmp/mixed.mp3"}


@pytest.mark.asyncio
async def test_sting_mix_failure_ships_dry_and_flags(tmp_path, monkeypatch) -> None:
    async def fake_narration(**kwargs):
        return "/tmp/dry.mp3"

    async def fake_mix(*a, **k):
        return None

    findings = []

    import modules.content.atoms._narration_render as nr
    import services.podcast_sting_mixer as mixer

    monkeypatch.setattr(nr, "render_narration", fake_narration)
    monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
    monkeypatch.setattr(
        podcast_render, "emit_finding",
        lambda **kw: findings.append(kw),
    )
    out = await podcast_render.run(_sting_state(tmp_path))
    assert out == {"podcast_audio_path": "/tmp/dry.mp3"}
    assert findings and findings[0]["kind"] == "podcast_sting_mix_failed"


@pytest.mark.asyncio
async def test_sting_mix_disabled_skips(tmp_path, monkeypatch) -> None:
    async def fake_narration(**kwargs):
        return "/tmp/dry.mp3"

    calls = []

    async def fake_mix(*a, **k):
        calls.append(a)
        return "/tmp/mixed.mp3"

    import modules.content.atoms._narration_render as nr
    import services.podcast_sting_mixer as mixer

    monkeypatch.setattr(nr, "render_narration", fake_narration)
    monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
    out = await podcast_render.run(
        _sting_state(tmp_path, enabled="false"))
    assert out == {"podcast_audio_path": "/tmp/dry.mp3"}
    assert calls == []


@pytest.mark.asyncio
async def test_no_sting_no_mix(monkeypatch) -> None:
    async def fake_narration(**kwargs):
        return "/tmp/dry.mp3"

    import modules.content.atoms._narration_render as nr

    monkeypatch.setattr(nr, "render_narration", fake_narration)
    sc = SiteConfig(initial_config={"media.cta.podcast": ""})
    out = await podcast_render.run({
        "task_id": "t", "podcast_script": "hi", "site_config": sc,
    })
    assert out == {"podcast_audio_path": "/tmp/dry.mp3"}
