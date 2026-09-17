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
    monkeypatch.setattr(slr, "_comfyui_reserved_gb", AsyncMock(return_value=0.0))
    monkeypatch.setattr(slr.asyncio, "sleep", AsyncMock())
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
            _shot(0), prior_clip=None, work_dir=tmp_path, image_gen_url="",
            site_config=_sc(video_presenter_reclaim_wait_s=0),
            http_client_factory=None, narration_path=str(narration), niche_slug=None,
        )
        assert result.success is False and "GB usable" in (result.error or "")
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
    async def test_it_runs_the_shared_ladder_including_ollama_but_spares_comfyui(self, monkeypatch):
        """ComfyUI is the S2V engine this clip is about to call; restarting it
        out from under the prompt lost the hero clip at 17:02Z (2026-09-16)."""
        calls = {}

        class _Gpu:
            async def reclaim_render_vram(self, *, include_ollama=True, exclude=()):
                calls["include_ollama"] = include_ollama
                calls["exclude"] = tuple(exclude)

        import poindexter.services.gpu_scheduler as gs
        monkeypatch.setattr(gs, "gpu", _Gpu())
        await slr._reclaim_card_for_presenter()
        assert calls == {"include_ollama": True, "exclude": ("comfyui",)}

    @pytest.mark.asyncio
    async def test_a_failed_reclaim_is_not_a_certain_skip(self, monkeypatch):
        class _Gpu:
            async def reclaim_render_vram(self, *, include_ollama=True, exclude=()):
                raise RuntimeError("docker socket gone")

        import poindexter.services.gpu_scheduler as gs
        monkeypatch.setattr(gs, "gpu", _Gpu())
        await slr._reclaim_card_for_presenter()  # must not raise


class TestPresenterHeadroomCountsComfyAndWaits:
    """2026-09-16 21:00:54: the closing presenter shot was refused at
    "0.7 GB free" 0.2 s after the ladder queued sidecar restarts, on a card
    where ComfyUI itself held 16.8 GB of reusable pool. ComfyUI renders the
    clip, so its pool counts; and the restarts land seconds later, so the
    floor waits before deciding."""

    @pytest.mark.asyncio
    async def test_headroom_is_free_plus_comfyui_pool(self, monkeypatch):
        monkeypatch.setattr(slr, "_live_free_vram_gb", AsyncMock(return_value=0.7))
        monkeypatch.setattr(slr, "_comfyui_reserved_gb", AsyncMock(return_value=16.8))
        assert await slr._presenter_headroom_gb(_sc()) == pytest.approx(17.5)

    @pytest.mark.asyncio
    async def test_unknown_free_reading_stays_unknown(self, monkeypatch):
        monkeypatch.setattr(slr, "_live_free_vram_gb", AsyncMock(return_value=None))
        monkeypatch.setattr(slr, "_comfyui_reserved_gb", AsyncMock(return_value=16.8))
        assert await slr._presenter_headroom_gb(_sc()) is None

    @pytest.mark.asyncio
    async def test_waits_until_the_restarts_land(self, monkeypatch):
        readings = iter([2.0, 9.0, 27.5])
        monkeypatch.setattr(slr, "_presenter_headroom_gb", AsyncMock(side_effect=lambda sc: next(readings)))
        slept = []

        async def _sleep(s):
            slept.append(s)

        monkeypatch.setattr(slr.asyncio, "sleep", _sleep)
        got = await slr._wait_for_presenter_headroom(_sc(video_presenter_reclaim_wait_s=60), 26.0)
        assert got == 27.5
        assert slept == [5.0, 5.0]

    @pytest.mark.asyncio
    async def test_gives_up_after_the_budget_and_returns_the_last_reading(self, monkeypatch):
        monkeypatch.setattr(slr, "_presenter_headroom_gb", AsyncMock(return_value=3.0))
        import itertools

        clock = itertools.chain(iter([0.0, 0.0]), itertools.repeat(61.0))
        monkeypatch.setattr(slr.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(slr.asyncio, "sleep", AsyncMock())
        got = await slr._wait_for_presenter_headroom(_sc(video_presenter_reclaim_wait_s=60), 26.0)
        assert got == 3.0

    @pytest.mark.asyncio
    async def test_comfyui_reserved_reads_torch_pool_and_fails_soft(self, monkeypatch):
        class _Resp:
            status_code = 200

            def json(self):
                return {"devices": [{"torch_vram_total": 16.8 * 1024 ** 3, "vram_free": 1}]}

        class _Client:
            def __init__(self, *a, **k): ...
            async def __aenter__(self): return self
            async def __aexit__(self, *e): return False
            async def get(self, url): return _Resp()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        assert await slr._comfyui_reserved_gb(_sc()) == pytest.approx(16.8)

        class _Boom(_Client):
            async def get(self, url): raise RuntimeError("down")

        monkeypatch.setattr(httpx, "AsyncClient", _Boom)
        assert await slr._comfyui_reserved_gb(_sc()) == 0.0


class TestFittedNarrationWindow:
    """2026-09-17 render 671c94b3: 13 shots planned at 209 s over a 284 s
    narration. The assembly stretched every scene 1.36x, so the closing
    presenter scene played at 4:01-4:39 while its speech had been cut at the
    director's planned offset — the words that played at 3:23-3:50. The face
    lip-synced to sentences the viewer had already heard."""

    @pytest.mark.asyncio
    async def test_fitted_window_is_cut_and_spoken_but_the_planned_duration_is_reported(
        self, tmp_path, quiet_gpu, monkeypatch,
    ):
        narration = tmp_path / "narration.mp3"
        narration.write_bytes(b"MP3")

        async def fake_fetch(url, dest, factory):
            dest.write_bytes(b"PNG")
            return str(dest)

        cuts = []

        async def fake_cut(src, dst, *, offset_s, duration_s):
            open(dst, "wb").write(b"WAV")
            cuts.append((offset_s, duration_s))
            return True

        seen = {}

        async def fake_render(**kw):
            seen.update(kw)
            open(kw["output_path"], "wb").write(b"MP4")
            return True, ""

        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fake_fetch)
        monkeypatch.setattr(slr, "_cut_narration_window", fake_cut)
        monkeypatch.setattr(slr, "_render_generative_clip", fake_render)
        shot = _shot(12, duration_s=29.4, narration_offset_s=177.0)
        result = await slr._render_one_shot(
            shot, prior_clip=None, work_dir=tmp_path, image_gen_url="",
            site_config=_sc(), http_client_factory=None, orientation="landscape", post_id="p1",
            narration_path=str(narration), niche_slug="glad-labs",
            presenter_window=(240.4, 39.93),
        )
        assert result.success is True
        assert cuts == [(240.4, 39.93)], "the speech must be cut where the fit puts the scene"
        assert seen["extra_config"]["audio_duration_s"] == 39.93
        assert seen["duration_s"] == 40  # ceil of the fitted window, for the chunk count
        # The assembly fits every rendered duration again; reporting the
        # fitted value would stretch the presenter scene twice.
        assert result.duration_s == 29.4

    @pytest.mark.asyncio
    async def test_no_window_keeps_the_planned_cut(self, tmp_path, quiet_gpu, monkeypatch):
        narration = tmp_path / "narration.mp3"
        narration.write_bytes(b"MP3")

        async def fake_fetch(url, dest, factory):
            dest.write_bytes(b"PNG")
            return str(dest)

        cuts = []

        async def fake_cut(src, dst, *, offset_s, duration_s):
            open(dst, "wb").write(b"WAV")
            cuts.append((offset_s, duration_s))
            return True

        async def fake_render(**kw):
            open(kw["output_path"], "wb").write(b"MP4")
            return True, ""

        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fake_fetch)
        monkeypatch.setattr(slr, "_cut_narration_window", fake_cut)
        monkeypatch.setattr(slr, "_render_generative_clip", fake_render)
        await slr._render_one_shot(
            _shot(3), prior_clip=None, work_dir=tmp_path, image_gen_url="",
            site_config=_sc(), http_client_factory=None, orientation="landscape", post_id="p1",
            narration_path=str(narration), niche_slug="glad-labs",
        )
        assert cuts == [(15.0, 5.0)]


class TestFittedShotWindow:
    """Pure: the window a shot occupies after the narration-fit."""

    def test_gentle_stretch_scales_offset_and_duration(self):
        # The 671c94b3 shape: planned 209.4 s, narration 284.4 s, scale 1.358.
        durs = [8.0, 14.0, 12.0, 18.0, 20.0, 15.0, 16.0, 14.0, 5.8, 22.0, 14.0, 6.2, 29.4]
        assert round(sum(durs), 1) == 194.4
        off, dur = slr._fitted_shot_window(12, durs, 264.0, max_shot_s=60.0)
        scale = 264.0 / 194.4
        assert abs(off - (194.4 - 29.4) * scale) < 0.01
        assert abs(dur - 29.4 * scale) < 0.01

    def test_no_fit_needed_keeps_the_planned_window(self):
        durs = [8.0, 5.0, 7.0]
        assert slr._fitted_shot_window(2, durs, 20.5, max_shot_s=9.0) == (13.0, 7.0)

    def test_first_shot_always_starts_at_zero(self):
        durs = [8.0, 5.0, 7.0]
        off, dur = slr._fitted_shot_window(0, durs, 40.0, max_shot_s=60.0)
        assert off == 0.0 and dur == 16.0

    def test_cycling_regime_uses_the_first_occurrence(self):
        # Pathological: average shot would exceed the ceiling → cap + cycle.
        durs = [2.0, 2.0, 2.0]
        off, dur = slr._fitted_shot_window(1, durs, 60.0, max_shot_s=4.0)
        assert (off, dur) == (4.0, 4.0)

    def test_endcard_carves_its_window_out_of_the_target(self, tmp_path):
        sc = SiteConfig(initial_config={"video_endcard_enabled": "false"})
        target, hold, plan = slr._endcard_fit_target(
            284.4, site_config=sc, caption_path=None, endcard_cta_text="like and subscribe",
            narration_fit_hold_s=1.5,
        )
        assert (target, hold, plan) == (284.4, 1.5, None)


class TestPresenterNegativePrompt:
    """The shared Wan negative punishes stillness (静态 / 静止 / 静止不动的画面) —
    right for a hero illustration, wrong for a person talking to camera, whom
    it pushes into head-bobbing (operator feedback 2026-09-17: "doesn't look
    natural"). The presenter render carries its own negative."""

    def test_default_negative_drops_the_anti_stillness_terms(self):
        from poindexter.services.settings_defaults import DEFAULTS
        from poindexter.services.video_providers.comfyui import _DEFAULT_NEGATIVE

        neg = DEFAULTS["video_presenter_negative_prompt"]
        for term in ("静态", "静止", "静止不动的画面"):
            assert term in _DEFAULT_NEGATIVE and term not in neg, term
        # The quality/anatomy terms the model was trained against stay.
        for term in ("过曝", "画得不好的脸部", "多余的手指", "字幕"):
            assert term in neg, term
        # And the talking-head failure modes are named.
        assert "夸张的表情" in neg and "摇头晃脑" in neg

    def test_setting_reaches_the_speech_provider_config(self):
        sc = _sc(video_presenter_negative_prompt="夸张的表情，摇头晃脑")
        assert slr._presenter_negative_prompt(sc) == {"negative_prompt": "夸张的表情，摇头晃脑"}

    def test_empty_setting_inherits_the_shared_negative(self):
        assert slr._presenter_negative_prompt(_sc(video_presenter_negative_prompt="")) == {}
        assert slr._presenter_negative_prompt(None) == {}

    @pytest.mark.asyncio
    async def test_render_passes_the_negative_in_extra_config(self, tmp_path, quiet_gpu, monkeypatch):
        narration = tmp_path / "narration.mp3"
        narration.write_bytes(b"MP3")

        async def fake_fetch(url, dest, factory):
            dest.write_bytes(b"PNG")
            return str(dest)

        async def fake_cut(src, dst, **kw):
            open(dst, "wb").write(b"WAV")
            return True

        seen = {}

        async def fake_render(**kw):
            seen.update(kw)
            open(kw["output_path"], "wb").write(b"MP4")
            return True, ""

        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fake_fetch)
        monkeypatch.setattr(slr, "_cut_narration_window", fake_cut)
        monkeypatch.setattr(slr, "_render_generative_clip", fake_render)
        await slr._render_one_shot(
            _shot(0), prior_clip=None, work_dir=tmp_path, image_gen_url="",
            site_config=_sc(video_presenter_negative_prompt="摇头晃脑"), http_client_factory=None,
            orientation="landscape", post_id="p1", narration_path=str(narration), niche_slug="glad-labs",
        )
        assert seen["extra_config"]["negative_prompt"] == "摇头晃脑"
        assert seen["extra_config"]["audio_path"].endswith("presenter_0.wav")
