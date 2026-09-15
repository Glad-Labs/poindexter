"""Presenter shots — the niche's persona speaks a shot's narration window.

Covers the per-video cap/downgrade, the render branch's ladder (portrait,
narration cut, VRAM floor, provider call with the speech config), and the
fallback finding, with every GPU/network/ffmpeg step patched out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr


def _shot(idx=0, source="presenter", **over):
    base = dict(idx=idx, duration_s=5.0, intent="opening address", source=source, narration_offset_s=float(idx) * 5.0)
    if source == "pexels":
        base["query"] = "server room"
    elif source in ("image_kenburns", "generative"):
        base["prompt"] = "abstract data"
    base.update(over)
    return Shot(**base)


def _sc(**extra):
    base = {
        "media_default_persona": "presenter",
        "persona.presenter.display_name": "Ada",
        "persona.presenter.portrait_url": "https://cdn/personas/presenter.png",
        "persona.presenter.style_policy": "photoreal",
        "persona.presenter.enabled": "true",
        "persona.presenter.render_prompt_suffix": "calm and warm",
        "media_human_subjects": "allow",
        "media_style_policy": "any",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


class TestCap:
    def test_keeps_up_to_the_cap_and_downgrades_the_rest_with_a_prompt(self):
        shots = [_shot(0), _shot(1, "pexels"), _shot(2, prompt="warm"), _shot(3)]
        out = slr._cap_presenter_shots(shots, 2, available=True)
        assert [s.source for s in out] == ["presenter", "pexels", "presenter", "image_kenburns"]
        assert out[3].prompt == "opening address"  # intent becomes the still's prompt
        assert out[2].prompt == "warm"

    def test_unavailable_persona_downgrades_every_presenter_shot(self):
        out = slr._cap_presenter_shots([_shot(0), _shot(1)], 2, available=False)
        assert [s.source for s in out] == ["image_kenburns", "image_kenburns"]

    def test_negative_cap_keeps_all(self):
        out = slr._cap_presenter_shots([_shot(0), _shot(1), _shot(2)], -1, available=True)
        assert all(s.source == "presenter" for s in out)


class TestComposePrompt:
    def test_template_persona_suffix_and_note_are_joined(self):
        from poindexter.services.persona_service import get_persona
        persona = get_persona(_sc(), "presenter")
        text = slr._compose_presenter_prompt(_shot(0, prompt="lean in slightly"), persona, _sc())
        assert text.startswith("Ada speaks directly to the camera")
        assert "calm and warm" in text and text.endswith("lean in slightly.")

    def test_operator_template_wins(self):
        from poindexter.services.persona_service import get_persona
        sc = _sc(video_presenter_render_prompt="{display_name} at a desk, talking")
        persona = get_persona(sc, "presenter")
        assert slr._compose_presenter_prompt(_shot(0), persona, sc).startswith("Ada at a desk, talking")


@pytest.fixture
def quiet_gpu(monkeypatch):
    monkeypatch.setattr(slr, "_reclaim_card_for_presenter", AsyncMock())
    monkeypatch.setattr(slr, "_live_free_vram_gb", AsyncMock(return_value=30.0))
    monkeypatch.setattr(slr, "emit_finding", lambda **kw: None)


class TestRenderPresenterClip:
    @pytest.mark.asyncio
    async def test_happy_path_calls_the_speech_provider_with_the_narration_window(self, tmp_path, quiet_gpu, monkeypatch):
        narration = tmp_path / "narration.mp3"
        narration.write_bytes(b"MP3")

        async def fake_fetch(url, dest, factory):
            dest.write_bytes(b"PNG")
            return str(dest)

        async def fake_cut(src, dst, *, offset_s, duration_s):
            open(dst, "wb").write(b"WAV")
            fake_cut.calls.append((src, offset_s, duration_s))
            return True
        fake_cut.calls = []

        seen = {}

        async def fake_render(**kw):
            seen.update(kw)
            open(kw["output_path"], "wb").write(b"MP4")
            return True, ""

        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fake_fetch)
        monkeypatch.setattr(slr, "_cut_narration_window", fake_cut)
        monkeypatch.setattr(slr, "_render_generative_clip", fake_render)
        shot = _shot(2, prompt="lean in")
        result = await slr._render_one_shot(
            shot, prior_clip=None, work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=_sc(), http_client_factory=None, orientation="landscape", post_id="p1",
            narration_path=str(narration), niche_slug="glad-labs",
        )
        assert result.success is True and result.source == "presenter"
        assert result.clip_path.endswith("presenter_2.mp4")
        assert fake_cut.calls == [(str(narration), 10.0, 5.0)]
        assert seen["provider_override"] == "comfyui"
        assert seen["extra_config"]["audio_path"].endswith("presenter_2.wav")
        assert seen["extra_config"]["audio_duration_s"] == 5.0
        assert seen["image_path"].endswith("presenter_presenter.png")
        assert seen["prompt"].startswith("Ada speaks directly to the camera")

    @pytest.mark.asyncio
    async def test_no_persona_fails_soft_with_a_finding(self, tmp_path, monkeypatch):
        findings = []
        monkeypatch.setattr(slr, "emit_finding", lambda **kw: findings.append(kw))
        result = await slr._render_one_shot(
            _shot(0), prior_clip=None, work_dir=tmp_path, image_gen_url="", site_config=SiteConfig(initial_config={}),
            http_client_factory=None, narration_path=None, niche_slug=None,
        )
        assert result.success is False and "persona" in (result.error or "")
        assert findings and findings[0]["kind"] == "presenter_render_fallback"

    @pytest.mark.asyncio
    async def test_missing_narration_fails_soft(self, tmp_path, quiet_gpu):
        result = await slr._render_one_shot(
            _shot(0), prior_clip=None, work_dir=tmp_path, image_gen_url="", site_config=_sc(),
            http_client_factory=None, narration_path=str(tmp_path / "nope.mp3"), niche_slug=None,
        )
        assert result.success is False and "narration" in (result.error or "")

    @pytest.mark.asyncio
    async def test_low_vram_refuses_to_start(self, tmp_path, quiet_gpu, monkeypatch):
        narration = tmp_path / "n.mp3"
        narration.write_bytes(b"MP3")

        async def fake_fetch(url, dest, factory):
            dest.write_bytes(b"PNG")
            return str(dest)

        async def fake_cut(src, dst, **kw):
            open(dst, "wb").write(b"WAV")
            return True
        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fake_fetch)
        monkeypatch.setattr(slr, "_cut_narration_window", fake_cut)
        monkeypatch.setattr(slr, "_live_free_vram_gb", AsyncMock(return_value=12.0))
        render = AsyncMock(return_value=(True, ""))
        monkeypatch.setattr(slr, "_render_generative_clip", render)
        result = await slr._render_one_shot(
            _shot(0), prior_clip=None, work_dir=tmp_path, image_gen_url="", site_config=_sc(),
            http_client_factory=None, narration_path=str(narration), niche_slug=None,
        )
        assert result.success is False and "GB free" in (result.error or "")
        render.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_provider_failure_becomes_a_fallback(self, tmp_path, quiet_gpu, monkeypatch):
        narration = tmp_path / "n.mp3"
        narration.write_bytes(b"MP3")

        async def fake_fetch(url, dest, factory):
            dest.write_bytes(b"PNG")
            return str(dest)

        async def fake_cut(src, dst, **kw):
            open(dst, "wb").write(b"WAV")
            return True
        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fake_fetch)
        monkeypatch.setattr(slr, "_cut_narration_window", fake_cut)
        monkeypatch.setattr(slr, "_render_generative_clip", AsyncMock(return_value=(False, "comfyui rejected the workflow: no audio encoder")))
        result = await slr._render_one_shot(
            _shot(0), prior_clip=None, work_dir=tmp_path, image_gen_url="", site_config=_sc(),
            http_client_factory=None, narration_path=str(narration), niche_slug=None,
        )
        assert result.success is False and "audio encoder" in (result.error or "")


class TestPortraitFetch:
    @pytest.mark.asyncio
    async def test_local_path_is_copied(self, tmp_path):
        src = tmp_path / "face.png"
        src.write_bytes(b"PNG")
        out = await slr._fetch_presenter_portrait(str(src), tmp_path / "dest.png", None)
        assert out and (tmp_path / "dest.png").read_bytes() == b"PNG"

    @pytest.mark.asyncio
    async def test_http_download_uses_the_client_factory(self, tmp_path):
        class _Resp:
            status_code = 200
            content = b"PNGBYTES"

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return _Resp()

        out = await slr._fetch_presenter_portrait("https://cdn/p.png", tmp_path / "d.png", lambda **kw: _Client())
        assert out and (tmp_path / "d.png").read_bytes() == b"PNGBYTES"

    @pytest.mark.asyncio
    async def test_missing_local_file_is_none(self, tmp_path):
        assert await slr._fetch_presenter_portrait(str(tmp_path / "nope.png"), tmp_path / "d.png", None) is None


class TestPresenterReclaimsTheWholeCard:
    """2026-09-15: the gate cleared image-gen + Ollama and then measured. The
    card still held stable-audio (~10 GB from the ambient bed), speaches and
    chatterbox, so free VRAM never reached the 26 GB floor and the presenter
    shot could not start — the memory was reclaimable the whole time."""

    @pytest.mark.asyncio
    async def test_it_runs_the_shared_ladder_including_ollama(self, monkeypatch):
        calls = {}

        class _Gpu:
            async def reclaim_render_vram(self, *, include_ollama=True):
                calls["include_ollama"] = include_ollama

        import poindexter.services.gpu_scheduler as gs
        monkeypatch.setattr(gs, "gpu", _Gpu())
        await slr._reclaim_card_for_presenter()
        assert calls == {"include_ollama": True}

    @pytest.mark.asyncio
    async def test_a_failed_reclaim_is_not_a_certain_skip(self, monkeypatch):
        class _Gpu:
            async def reclaim_render_vram(self, *, include_ollama=True):
                raise RuntimeError("docker socket gone")

        import poindexter.services.gpu_scheduler as gs
        monkeypatch.setattr(gs, "gpu", _Gpu())
        await slr._reclaim_card_for_presenter()  # must not raise
