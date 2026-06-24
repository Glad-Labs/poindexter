"""Tests for ``services/video_renderers/shot_list_renderer.py``.

Glad-Labs/poindexter#649 PR 2 — the director-driven renderer that
turns a ``VideoShotList`` into an MP4 by composing per-shot clips and
running them through ``FFmpegLocalCompositor``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.video_shot_list import Shot, VideoShotList
from services.video_renderers.shot_list_renderer import (
    ShotListRenderResult,
    _render_one_shot,
    render_shot_list,
)


def _build_shot_list(shots: list[Shot]) -> VideoShotList:
    """Convenience: wrap shots in a valid VideoShotList."""
    total = sum(s.duration_s for s in shots)
    return VideoShotList(
        version=1,
        total_duration_s=total,
        shots=shots,
        director_model="ollama/test-model",
        director_prompt_version="v1",
        director_decided_at=datetime.now(timezone.utc),
    )


class TestRenderOneShot:
    """Per-source shot rendering dispatches to the right backend."""

    @pytest.mark.asyncio
    async def test_sdxl_kenburns_calls_sdxl_with_correct_body(self, tmp_path):
        """The SDXL render must POST {'prompt': ..., 'negative_prompt': ...}
        to ``/generate`` and OMIT steps / guidance_scale so the server's
        per-model registry drives them (z_image_turbo wants 9 / CFG0;
        hardcoding SDXL-Turbo's 4 / 1.0 blew the render past the timeout).
        Wan21's wrong-body 422 issue should never resurface here."""
        shot = Shot(
            idx=0,
            duration_s=5.0,
            intent="opening shot",
            source="sdxl_kenburns",
            prompt="a clean modern desk with a monitor",
            kenburns_zoom=(1.0, 1.2),
            narration_offset_s=0.0,
        )

        # Mock the SDXL response with image bytes content-type.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = b"fake-png-bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        def _factory(*args, **kwargs):
            return mock_client

        result = await _render_one_shot(
            shot,
            prior_clip=None,
            work_dir=tmp_path,
            sdxl_url="http://sdxl:9836",
            site_config=None,
            http_client_factory=_factory,
        )

        assert result.success is True
        assert result.clip_path is not None
        assert result.clip_path.endswith(".png")

        # Verify the SDXL POST body shape — must include 'prompt' and
        # 'negative_prompt', NOT image_paths / audio_path / ken_burns.
        call_kwargs = mock_client.post.call_args.kwargs
        body = call_kwargs.get("json")
        assert body is not None
        assert body["prompt"] == "a clean modern desk with a monitor"
        assert "negative_prompt" in body
        assert "image_paths" not in body
        assert "audio_path" not in body
        assert "ken_burns" not in body
        # Params omitted — server's per-model registry drives them (z_image_turbo).
        assert "steps" not in body
        assert "guidance_scale" not in body

    @pytest.mark.asyncio
    async def test_sdxl_render_timeout_comes_from_site_config(self, tmp_path):
        """The SDXL render timeout is read from image_render_timeout_seconds so a
        cold Z-Image load (~133s) survives — not a hardcoded 60s cap."""
        from services.site_config import SiteConfig

        shot = Shot(
            idx=0,
            duration_s=5.0,
            intent="opening shot",
            source="sdxl_kenburns",
            prompt="a clean modern desk",
            kenburns_zoom=(1.0, 1.2),
            narration_offset_s=0.0,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = b"fake-png-bytes"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        captured: dict = {}

        def _factory(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return mock_client

        sc = SiteConfig(initial_config={"image_render_timeout_seconds": "234"})
        await _render_one_shot(
            shot,
            prior_clip=None,
            work_dir=tmp_path,
            sdxl_url="http://sdxl:9836",
            site_config=sc,
            http_client_factory=_factory,
        )
        # httpx.Timeout(read) reflects the configured value, not the 60s default.
        assert captured["timeout"] is not None
        assert captured["timeout"].read == 234.0

    @pytest.mark.asyncio
    async def test_wan21_calls_provider_with_image_path(self, tmp_path):
        """A hero shot (wan21/generative) renders its SDXL still first, then
        routes through ``Wan21Provider.fetch`` with that still as ``image_path``
        (i2v conditioning). This pins the 422 fix too: the body MUST NOT carry
        the old slideshow fields image_paths/audio_path/ken_burns."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(
            idx=0,
            duration_s=5.0,
            intent="dynamic transition",
            source="wan21",
            prompt="abstract glowing data particles",
            narration_offset_s=0.0,
        )

        # Spy on Wan21Provider.fetch — capture the config dict so we
        # can assert no slideshow fields leaked in.
        from services.video_providers import wan2_1 as wan21_mod

        captured_config: dict = {}

        async def _fake_fetch(self, prompt, config):
            captured_config.update(config)
            # Materialise an output file the way the real provider would.
            output = config.get("output_path", "")
            if output:
                with open(output, "wb") as f:
                    f.write(b"\x00\x00\x00\x18ftypisom" + b"fake_mp4")
            return [MagicMock(file_path=output)]

        async def _fake_sdxl(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_fetch), \
                patch.object(mod, "_render_sdxl_image", _fake_sdxl):
            result = await _render_one_shot(
                shot,
                prior_clip=None,
                work_dir=tmp_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                http_client_factory=AsyncMock,
            )

        assert result.success is True
        assert result.clip_path is not None
        assert result.clip_path.endswith(".mp4")

        # The provider config carries duration + output_path + the i2v init
        # image. It must NOT carry image_paths / audio_path / ken_burns —
        # that was the 2026-05-26 422-causing body.
        assert "output_path" in captured_config
        assert "duration_s" in captured_config
        assert captured_config["duration_s"] <= 6  # Wan2.1 artifacts beyond
        assert captured_config["image_path"].endswith(".png")  # i2v init still
        assert "image_paths" not in captured_config
        assert "audio_path" not in captured_config
        assert "ken_burns" not in captured_config

    @pytest.mark.asyncio
    async def test_wan21_caps_duration_at_six_seconds(self, tmp_path):
        """Director-specified durations beyond 6s get capped — Wan2.1
        1.3B artifacts beyond that mark show seams."""
        shot = Shot(
            idx=0,
            duration_s=10.0,
            intent="long shot",
            source="wan21",
            prompt="prompt text",
            narration_offset_s=0.0,
        )

        import services.video_renderers.shot_list_renderer as mod
        from services.video_providers import wan2_1 as wan21_mod

        captured_config: dict = {}

        async def _fake_fetch(self, prompt, config):
            captured_config.update(config)
            with open(config["output_path"], "wb") as f:
                f.write(b"fake")
            return [MagicMock(file_path=config["output_path"])]

        async def _fake_sdxl(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_fetch), \
                patch.object(mod, "_render_sdxl_image", _fake_sdxl):
            await _render_one_shot(
                shot,
                prior_clip=None,
                work_dir=tmp_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                http_client_factory=AsyncMock,
            )

        assert captured_config["duration_s"] == 6

    @pytest.mark.asyncio
    async def test_generative_animates_sdxl_still(self, tmp_path):
        """Piece 4: a ``generative`` hero shot renders the stylized SDXL still
        first, then animates it into a clip — the success path returns the
        .mp4."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(idx=0, duration_s=5.0, intent="hero", source="generative",
                    prompt="neon GPU die, cyberpunk", narration_offset_s=0.0)

        async def _fake_sdxl(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        async def _fake_clip(*, prompt, output_path, image_path, duration_s, site_config):
            assert image_path and image_path.endswith(".png")  # the i2v init still
            with open(output_path, "wb") as f:
                f.write(b"MP4")
            return True

        with patch.object(mod, "_render_sdxl_image", _fake_sdxl), \
                patch.object(mod, "_render_generative_clip", _fake_clip):
            result = await _render_one_shot(
                shot, prior_clip=None, work_dir=tmp_path, sdxl_url="http://x",
                site_config=None, http_client_factory=AsyncMock)

        assert result.success is True
        assert result.clip_path.endswith(".mp4")

    @pytest.mark.asyncio
    async def test_generative_falls_back_to_still_on_clip_miss(self, tmp_path):
        """When i2v produces no clip, fall back to the SDXL still (the
        compositor Ken-Burns it) and emit a ``hero_render_fallback`` finding —
        NOT a holdover of the prior clip."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(idx=1, duration_s=5.0, intent="hero", source="generative",
                    prompt="neon GPU die", narration_offset_s=0.0)
        findings: list[dict] = []

        async def _fake_sdxl(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        async def _fake_clip(**kw):
            return False  # i2v miss

        with patch.object(mod, "_render_sdxl_image", _fake_sdxl), \
                patch.object(mod, "_render_generative_clip", _fake_clip), \
                patch.object(mod, "emit_finding",
                             lambda **kw: findings.append(kw)):
            result = await _render_one_shot(
                shot, prior_clip="/prior/clip.mp4", work_dir=tmp_path,
                sdxl_url="http://x", site_config=None,
                http_client_factory=AsyncMock, post_id="post-1")

        assert result.success is True
        assert result.clip_path.endswith(".png")  # the still, not the prior clip
        assert "/prior/clip.mp4" not in (result.clip_path or "")
        assert any(f.get("kind") == "hero_render_fallback" for f in findings)

    @pytest.mark.asyncio
    async def test_generative_still_render_failure_is_hard_fail(self, tmp_path):
        """If even the SDXL still can't render, there's nothing to fall back
        to — the shot fails (the render pass drops it)."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(idx=0, duration_s=5.0, intent="hero", source="generative",
                    prompt="neon GPU die", narration_offset_s=0.0)

        async def _fake_sdxl(*, prompt, output_path, **kw):
            return False

        with patch.object(mod, "_render_sdxl_image", _fake_sdxl):
            result = await _render_one_shot(
                shot, prior_clip=None, work_dir=tmp_path, sdxl_url="http://x",
                site_config=None, http_client_factory=AsyncMock)

        assert result.success is False

    def test_generative_is_regenerable(self):
        import services.video_renderers.shot_list_renderer as mod
        assert "generative" in mod._REGENERABLE_SOURCES

    @pytest.mark.asyncio
    async def test_holdover_carries_prior_clip(self, tmp_path):
        """Holdover shots reuse the prior clip path — they're pure
        transitions, no asset to fetch."""
        shot = Shot(
            idx=2,
            duration_s=1.0,
            intent="hold on the prior shot",
            source="holdover",
            narration_offset_s=10.0,
        )

        prior = str(tmp_path / "shot_01.png")
        with open(prior, "wb") as f:
            f.write(b"existing image bytes")

        result = await _render_one_shot(
            shot,
            prior_clip=prior,
            work_dir=tmp_path,
            sdxl_url="http://sdxl:9836",
            site_config=None,
            http_client_factory=AsyncMock,
        )

        assert result.success is True
        assert result.clip_path == prior

    @pytest.mark.asyncio
    async def test_holdover_at_idx_zero_fails(self, tmp_path):
        """Holdover as the FIRST shot has nothing to carry — fail
        rather than produce silently broken output."""
        shot = Shot(
            idx=0,
            duration_s=1.0,
            intent="bad: holdover with no prior",
            source="holdover",
            narration_offset_s=0.0,
        )

        result = await _render_one_shot(
            shot,
            prior_clip=None,
            work_dir=tmp_path,
            sdxl_url="http://sdxl:9836",
            site_config=None,
            http_client_factory=AsyncMock,
        )

        assert result.success is False
        assert result.error is not None
        assert "prior clip" in result.error


class TestRenderShotList:
    """Full-shot-list render — exercises the concat pipeline."""

    @pytest.mark.asyncio
    async def test_three_shot_pipeline_composes_via_ffmpeg(self, tmp_path):
        """A 3-shot list (sdxl_kenburns + pexels + wan21) renders each
        shot then calls ``FFmpegLocalCompositor.compose`` with the
        per-shot scenes. Pins the seam between per-shot rendering and
        the concat pipeline."""
        shots = [
            Shot(
                idx=0,
                duration_s=3.0,
                intent="opening still",
                source="sdxl_kenburns",
                prompt="modern server room with cool blue lighting",
                kenburns_zoom=(1.0, 1.15),
                narration_offset_s=0.0,
            ),
            Shot(
                idx=1,
                duration_s=2.0,
                intent="real footage",
                source="pexels",
                query="server hardware",
                narration_offset_s=3.0,
            ),
            Shot(
                idx=2,
                duration_s=4.0,
                intent="dynamic clip",
                source="wan21",
                prompt="flowing data particles abstract",
                narration_offset_s=5.0,
            ),
        ]
        shot_list = _build_shot_list(shots)
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "final.mp4")

        # Mock SDXL responses (covers sdxl_kenburns + pexels fallback).
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = b"fake-png-bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        def _factory(*args, **kwargs):
            return mock_client

        # Mock Wan21Provider.fetch.
        from services.video_providers import wan2_1 as wan21_mod

        async def _fake_wan_fetch(self, prompt, config):
            with open(config["output_path"], "wb") as f:
                f.write(b"fake wan mp4")
            return [MagicMock(file_path=config["output_path"])]

        # Mock the compositor.

        captured_request = {}

        class _MockCompositor:
            def __init__(self, site_config=None):
                self._sc = site_config

            async def compose(self, request, **kwargs):
                captured_request["scenes"] = list(request.scenes)
                captured_request["soundtrack_path"] = request.soundtrack_path
                captured_request["narration_track_path"] = (
                    request.narration_track_path
                )
                captured_request["output_path"] = request.output_path
                with open(request.output_path, "wb") as f:
                    f.write(b"fake composed mp4 bytes")
                return MagicMock(
                    success=True,
                    output_path=request.output_path,
                    file_size_bytes=len(b"fake composed mp4 bytes"),
                    duration_s=9.0,
                )

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_wan_fetch), \
             patch(
                "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                _MockCompositor,
             ):
            result = await render_shot_list(
                post_id="post-test",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                pool=None,
                http_client_factory=_factory,
            )

        assert isinstance(result, ShotListRenderResult)
        assert result.success is True
        assert result.shots_rendered == 3
        assert result.shots_total == 3
        assert result.output_path == output_path

        # The compositor must have been called with 3 scenes in order.
        assert len(captured_request["scenes"]) == 3
        scenes = captured_request["scenes"]
        assert scenes[0].duration_s == 3.0
        assert scenes[1].duration_s == 2.0
        assert scenes[2].duration_s == 4.0
        # ALL scenes are silent — the narration is laid over the whole
        # concat via narration_track_path, not bound to scene 0 (binding
        # to scene 0 truncated the voiceover at the first transition;
        # #media-render-fixes).
        assert scenes[0].narration_path is None
        assert scenes[1].narration_path is None
        assert scenes[2].narration_path is None
        # The full-length narration rides narration_track_path.
        assert captured_request["narration_track_path"] == audio_path
        # The soundtrack is the AMBIENT bed (None here, since no ambient
        # was passed) — NOT the narration. Passing narration as the
        # soundtrack double-used it. #679 fix.
        assert captured_request["soundtrack_path"] is None


class TestRenderShotListAspectAndAmbient:
    """Gaps A + B (#679): aspect-profile dimensions and the ambient bed.

    These exercise the new ``width`` / ``height`` / ``ambient_path``
    kwargs on ``render_shot_list``. They mock the compositor and capture
    the ``CompositionRequest`` so we can assert the dims + soundtrack.
    """

    def _single_sdxl_shot_list(self):
        shots = [
            Shot(
                idx=0,
                duration_s=3.0,
                intent="opening still",
                source="sdxl",
                prompt="a clean abstract gradient backdrop",
                narration_offset_s=0.0,
            ),
        ]
        return _build_shot_list(shots)

    def _sdxl_client_factory(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = b"fake-png-bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        def _factory(*args, **kwargs):
            return mock_client

        return _factory

    def _capturing_compositor(self, captured: dict):
        class _MockCompositor:
            def __init__(self, site_config=None):
                self._sc = site_config

            async def compose(self, request, **kwargs):
                captured["width"] = request.width
                captured["height"] = request.height
                captured["soundtrack_path"] = request.soundtrack_path
                captured["caption_track_path"] = request.caption_track_path
                with open(request.output_path, "wb") as f:
                    f.write(b"fake composed mp4 bytes")
                return MagicMock(
                    success=True,
                    output_path=request.output_path,
                    file_size_bytes=len(b"fake composed mp4 bytes"),
                    duration_s=3.0,
                )

        return _MockCompositor

    @pytest.mark.asyncio
    async def test_short_dims_produce_9_16_composition_request(self, tmp_path):
        """Passing width=1080, height=1920 (the 9:16 short profile) sets
        those dims on the CompositionRequest — Gap A. The renderer must
        not hardcode 1920x1080 anymore."""
        shot_list = self._single_sdxl_shot_list()
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "short.mp4")

        captured: dict = {}
        with patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            self._capturing_compositor(captured),
        ):
            result = await render_shot_list(
                post_id="post-short",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._sdxl_client_factory(),
                width=1080,
                height=1920,
            )

        assert result.success is True
        assert captured["width"] == 1080
        assert captured["height"] == 1920

    @pytest.mark.asyncio
    async def test_default_dims_remain_16_9(self, tmp_path):
        """No width/height args → backcompat 1920x1080 (16:9 long-form)."""
        shot_list = self._single_sdxl_shot_list()
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "long.mp4")

        captured: dict = {}
        with patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            self._capturing_compositor(captured),
        ):
            await render_shot_list(
                post_id="post-long",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._sdxl_client_factory(),
            )

        assert captured["width"] == 1920
        assert captured["height"] == 1080

    @pytest.mark.asyncio
    async def test_ambient_path_sets_soundtrack(self, tmp_path):
        """Passing ambient_path routes it to soundtrack_path — the ambient
        bed channel (#679) is now consumed, and narration is no longer
        double-used as the soundtrack."""
        shot_list = self._single_sdxl_shot_list()
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "with_amb.mp4")

        captured: dict = {}
        with patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            self._capturing_compositor(captured),
        ):
            await render_shot_list(
                post_id="post-amb",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._sdxl_client_factory(),
                ambient_path="/x/amb.wav",
            )

        assert captured["soundtrack_path"] == "/x/amb.wav"

    @pytest.mark.asyncio
    async def test_no_ambient_leaves_soundtrack_none(self, tmp_path):
        """No ambient_path → soundtrack_path is None (clean narration,
        no -18dB second copy of the voice over the whole video)."""
        shot_list = self._single_sdxl_shot_list()
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "no_amb.mp4")

        captured: dict = {}
        with patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            self._capturing_compositor(captured),
        ):
            await render_shot_list(
                post_id="post-noamb",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._sdxl_client_factory(),
            )

        assert captured["soundtrack_path"] is None

    @pytest.mark.asyncio
    async def test_caption_path_sets_caption_track(self, tmp_path):
        """Passing caption_path routes it to caption_track_path on the
        CompositionRequest so the compositor burns the captions into the
        video (#676 Plan 5)."""
        shot_list = self._single_sdxl_shot_list()
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "with_caps.mp4")

        captured: dict = {}
        with patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            self._capturing_compositor(captured),
        ):
            await render_shot_list(
                post_id="post-caps",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._sdxl_client_factory(),
                caption_path="/x/c.srt",
            )

        assert captured["caption_track_path"] == "/x/c.srt"

    @pytest.mark.asyncio
    async def test_no_caption_path_leaves_caption_track_none(self, tmp_path):
        """No caption_path → caption_track_path is None (backcompat: the
        existing video_service.py caller renders without captions)."""
        shot_list = self._single_sdxl_shot_list()
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "no_caps.mp4")

        captured: dict = {}
        with patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            self._capturing_compositor(captured),
        ):
            await render_shot_list(
                post_id="post-nocaps",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._sdxl_client_factory(),
            )

        assert captured["caption_track_path"] is None


class TestPexelsSource:
    """#media-render-fixes: pexels shots fetch a REAL stock photo and NEVER
    fall back to SDXL — AI-generated humans (six-fingered hands) are a hard
    brand no-no. On any Pexels miss the renderer holds over the prior clip.
    """

    def _pexels_shot(self, *, idx=0, offset=0.0):
        return Shot(
            idx=idx,
            duration_s=4.0,
            intent="a real developer at a desk",
            source="pexels",
            query="developer working at desk",
            narration_offset_s=offset,
        )

    @pytest.mark.asyncio
    async def test_pexels_fetches_real_photo_not_sdxl(self, tmp_path):
        """A configured key + a Pexels hit writes a .jpg and never touches
        SDXL."""
        # Download client returns jpeg bytes.
        mock_resp = MagicMock()
        mock_resp.content = b"\xff\xd8\xff\xe0_fake_jpeg_bytes"
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        def _factory(*args, **kwargs):
            return mock_client

        from services.image_providers import pexels as pexels_mod

        async def _fake_fetch(self, query, config):
            return [MagicMock(url="https://images.pexels.com/photos/x.jpg")]

        sdxl_spy = AsyncMock()
        with patch.object(pexels_mod.PexelsProvider, "fetch", _fake_fetch), \
             patch(
                "services.video_renderers.shot_list_renderer._render_sdxl_image",
                sdxl_spy,
             ):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                http_client_factory=_factory,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
            )

        assert result.success is True
        assert result.clip_path is not None
        assert result.clip_path.endswith(".jpg")
        sdxl_spy.assert_not_called()  # the whole point: no AI human

    @pytest.mark.asyncio
    async def test_pexels_miss_holds_over_prior_never_sdxl(self, tmp_path):
        """No key (or no result) → hold over the prior clip; SDXL is never
        invoked to fake the human."""
        prior = str(tmp_path / "shot_00.png")
        with open(prior, "wb") as f:
            f.write(b"prior image bytes")

        sdxl_spy = AsyncMock()
        with patch(
            "services.video_renderers.shot_list_renderer._render_sdxl_image",
            sdxl_spy,
        ):
            result = await _render_one_shot(
                self._pexels_shot(idx=1, offset=4.0),
                prior_clip=prior,
                work_dir=tmp_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                http_client_factory=AsyncMock,
                pexels_key="",  # no key → Pexels miss
                orientation="landscape",
            )

        assert result.success is True
        assert result.clip_path == prior  # held over
        sdxl_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_pexels_miss_at_idx0_fails_never_sdxl(self, tmp_path):
        """A Pexels miss as the FIRST shot has nothing to hold over — fail
        (the shot drops out) rather than SDXL-faking a person."""
        sdxl_spy = AsyncMock()
        with patch(
            "services.video_renderers.shot_list_renderer._render_sdxl_image",
            sdxl_spy,
        ):
            result = await _render_one_shot(
                self._pexels_shot(idx=0),
                prior_clip=None,
                work_dir=tmp_path,
                sdxl_url="http://sdxl:9836",
                site_config=None,
                http_client_factory=AsyncMock,
                pexels_key="",
                orientation="landscape",
            )

        assert result.success is False
        assert "pexels" in (result.error or "")
        sdxl_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_shot_list_threads_secret_key_and_aspect(self, tmp_path):
        """render_shot_list loads pexels_api_key via get_secret and derives
        orientation from the shot list's aspect, threading both to the
        pexels render."""
        shots = [self._pexels_shot(idx=0)]
        shot_list = VideoShotList(
            version=1,
            aspect="9:16",
            total_duration_s=4.0,
            shots=shots,
            director_model="ollama/test-model",
            director_prompt_version="v1",
            director_decided_at=datetime.now(timezone.utc),
        )
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "short.mp4")

        captured: dict = {}

        async def _fake_pexels(
            *, query, output_path, api_key, orientation, http_client_factory,
        ):
            captured["api_key"] = api_key
            captured["orientation"] = orientation
            with open(output_path, "wb") as f:
                f.write(b"jpg")
            return True

        class _SC:
            def get(self, k, d=None):
                return d

            def get_int(self, k, d=0):
                return d

            async def get_secret(self, k, d=None):
                return "SEKRIT" if k == "pexels_api_key" else d

        class _MockCompositor:
            def __init__(self, site_config=None):
                pass

            async def compose(self, request, **kwargs):
                with open(request.output_path, "wb") as f:
                    f.write(b"mp4")
                return MagicMock(
                    success=True,
                    output_path=request.output_path,
                    file_size_bytes=3,
                    duration_s=4.0,
                )

        with patch(
            "services.video_renderers.shot_list_renderer._render_pexels_image",
            _fake_pexels,
        ), patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            _MockCompositor,
        ):
            result = await render_shot_list(
                post_id="p",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                sdxl_url="http://sdxl:9836",
                site_config=_SC(),
                pool=None,
                http_client_factory=AsyncMock,
            )

        assert result.success is True
        assert captured["api_key"] == "SEKRIT"
        assert captured["orientation"] == "portrait"  # 9:16 → portrait


class _QASC:
    """site_config stub for the render-check loop tests.

    Sync ``.get`` for the ``video_shot_qa_*`` tunables + ``qa_vision_model``,
    async ``.get_secret`` for the pexels key ``render_shot_list`` fetches.
    """

    def __init__(self, **over):
        self._cfg = {
            "video_shot_qa_enabled": "true",
            "video_shot_qa_threshold": "60",
            "video_shot_qa_max_retries": "2",
            "qa_vision_model": "qwen3-vl:30b",
        }
        self._cfg.update(over)

    def get(self, k, d=None):
        return self._cfg.get(k, d)

    def get_int(self, k, d=0):
        try:
            return int(self._cfg.get(k, d))
        except (TypeError, ValueError):
            return d

    async def get_secret(self, k, d=None):
        return d


class TestRenderCheckLoop:
    """Per-shot vision-QA verify-and-repair loop (video-quality Piece 2, §3.2)."""

    def _sdxl_factory(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "image/png"}
        resp.content = b"fake-png-bytes"
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=resp)
        return lambda *a, **k: client

    def _mock_compositor(self):
        class _C:
            def __init__(self, site_config=None):
                pass

            async def compose(self, request, **kw):
                with open(request.output_path, "wb") as f:
                    f.write(b"mp4")
                return MagicMock(
                    success=True, output_path=request.output_path,
                    file_size_bytes=3, duration_s=request.scenes[0].duration_s,
                )
        return _C

    @pytest.mark.asyncio
    async def test_accept_above_threshold_no_regen(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="sdxl",
                      prompt="a cyan abstract circuit", narration_offset_s=0.0)]
        scorer = AsyncMock(return_value=ShotQAResult(score=90.0, reason="great"))
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                sdxl_url="http://sdxl:9836", site_config=_QASC(),
                http_client_factory=self._sdxl_factory())
        assert result.success is True
        assert scorer.await_count == 1  # scored once, accepted, no regen

    @pytest.mark.asyncio
    async def test_regenerate_then_fallback_emits_finding(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="sdxl",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="beat", source="sdxl",
                      prompt="teal mesh", narration_offset_s=3.0)]
        # shot 0 passes (90, gives a prior clip); shot 1 fails every attempt.
        scorer = AsyncMock(side_effect=[ShotQAResult(90.0), ShotQAResult(20.0),
                                        ShotQAResult(25.0), ShotQAResult(30.0)])
        findings = []
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "emit_finding", lambda **kw: findings.append(kw)), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                sdxl_url="http://sdxl:9836", site_config=_QASC(),
                http_client_factory=self._sdxl_factory())
        assert result.success is True
        # shot 0: 1 score; shot 1: 1 initial + 2 regens = 3 → 4 total.
        assert scorer.await_count == 4
        assert any(f["kind"] == "shot_quality_fallback" for f in findings)

    @pytest.mark.asyncio
    async def test_qa_disabled_when_site_config_none(self, tmp_path):
        """site_config=None ⇒ QA never runs (backcompat for the existing suite)."""
        import services.video_renderers.shot_list_renderer as mod
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="sdxl",
                      prompt="cyan grid", narration_offset_s=0.0)]
        scorer = AsyncMock()
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                sdxl_url="http://sdxl:9836", site_config=None,
                http_client_factory=self._sdxl_factory())
        assert result.success is True
        scorer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_via_flag(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="sdxl",
                      prompt="cyan grid", narration_offset_s=0.0)]
        scorer = AsyncMock()
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                sdxl_url="http://sdxl:9836",
                site_config=_QASC(video_shot_qa_enabled="false"),
                http_client_factory=self._sdxl_factory())
        scorer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pexels_not_regenerated(self, tmp_path):
        """Pexels is deterministic — a low score falls back without re-fetching."""
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="sdxl",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="person", source="pexels",
                      query="developer at desk", narration_offset_s=3.0)]
        scorer = AsyncMock(side_effect=[ShotQAResult(90.0), ShotQAResult(10.0)])
        pexels = AsyncMock(return_value=True)
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "_render_pexels_image", pexels), \
             patch.object(mod, "emit_finding", lambda **kw: None), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                sdxl_url="http://sdxl:9836", site_config=_QASC(),
                http_client_factory=self._sdxl_factory())
        # pexels shot scored once (10 < 60) but NOT re-fetched (deterministic).
        assert pexels.await_count == 1
        assert scorer.await_count == 2  # sdxl(1) + pexels(1), no regen

    @pytest.mark.asyncio
    async def test_fallback_finding_shape_is_dashboard_ready(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="sdxl",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="beat", source="sdxl",
                      prompt="teal mesh", narration_offset_s=3.0)]
        scorer = AsyncMock(side_effect=[ShotQAResult(90.0)] + [ShotQAResult(15.0)] * 3)
        captured = []
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "emit_finding", lambda **kw: captured.append(kw)), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            await render_shot_list(
                post_id="post-xyz", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                sdxl_url="http://sdxl:9836", site_config=_QASC(),
                http_client_factory=self._sdxl_factory())
        f = next(f for f in captured if f["kind"] == "shot_quality_fallback")
        assert f["source"] == "shot_list_renderer"
        assert f["severity"] == "warn"
        assert f["dedup_key"] == "shot_quality_fallback:post-xyz:1"
        assert f["extra"]["score"] == 15.0

    @pytest.mark.asyncio
    async def test_all_renders_precede_any_vision_call(self, tmp_path):
        """Anti-thrash invariant: with QA on, EVERY shot is rendered before
        ANY vision score runs — so SDXL stays resident for the whole render
        pass instead of being evicted by an interleaved Ollama call per shot.

        On the old interleaved loop the timeline was
        ``[render0, score0, render1, score1, ...]`` and this assert fails; the
        two-pass renderer produces ``[render0, render1, ..., score0, score1]``.
        """
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_list_renderer import ShotRenderResult
        from services.video_renderers.shot_vision_qa import ShotQAResult

        # Distinct sources keep the ≤2-consecutive-same-source streak guard
        # happy; all three are fresh-render sources, so each is scored once.
        shots = [
            Shot(idx=0, duration_s=3.0, intent="open", source="sdxl",
                 prompt="cyan circuit", narration_offset_s=0.0),
            Shot(idx=1, duration_s=3.0, intent="beat", source="wan21",
                 prompt="teal mesh in motion", narration_offset_s=3.0),
            Shot(idx=2, duration_s=3.0, intent="close", source="sdxl",
                 prompt="gold grid", narration_offset_s=6.0),
        ]
        timeline: list[tuple[str, int]] = []

        async def _fake_render(shot, *, prior_clip, **kwargs):
            timeline.append(("render", shot.idx))
            clip = str(tmp_path / f"shot_{shot.idx:02d}.png")
            with open(clip, "wb") as fh:
                fh.write(b"png")
            return ShotRenderResult(
                idx=shot.idx, source=shot.source, success=True,
                clip_path=clip, duration_s=shot.duration_s,
            )

        async def _fake_score(*, frame_path, shot, **kwargs):
            timeline.append(("score", shot.idx))
            return ShotQAResult(score=90.0, reason="great")

        with patch.object(mod, "_render_one_shot", _fake_render), \
             patch.object(mod, "score_shot_frame", _fake_score), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                sdxl_url="http://sdxl:9836", site_config=_QASC(),
                http_client_factory=self._sdxl_factory())

        assert result.success is True
        # All three passed (90 ≥ 60) → each scored exactly once, no repair.
        assert [k for k, _ in timeline].count("render") == 3
        assert [k for k, _ in timeline].count("score") == 3
        render_positions = [i for i, (k, _) in enumerate(timeline) if k == "render"]
        score_positions = [i for i, (k, _) in enumerate(timeline) if k == "score"]
        assert max(render_positions) < min(score_positions), (
            f"renders must all precede scores (anti-thrash); timeline={timeline}"
        )


# ---------------------------------------------------------------------------
# Piece 4 — hero-shot budget cap (video_hero_shots_max)
# ---------------------------------------------------------------------------


def test_cap_hero_shots_downgrades_excess_to_kenburns():
    """Past ``max_hero`` generative/wan21 shots, the rest downgrade to
    sdxl_kenburns (same prompt) so the director over-asking can't blow the
    GPU budget (spec §3.3). Non-hero shots are untouched, order preserved."""
    import services.video_renderers.shot_list_renderer as mod

    def _gen(i):
        return Shot(idx=i, duration_s=4.0, intent="hero", source="generative",
                    prompt="neon die", narration_offset_s=float(i) * 4.0)

    shots = [
        _gen(0),
        _gen(1),
        Shot(idx=2, duration_s=4.0, intent="b-roll", source="pexels",
             query="data center", narration_offset_s=8.0),
        _gen(3),
        _gen(4),
    ]
    out = mod._cap_hero_shots(shots, 2)

    assert [s.source for s in out] == [
        "generative", "generative", "pexels", "sdxl_kenburns", "sdxl_kenburns",
    ]
    # Downgraded shots keep their prompt (sdxl_kenburns needs one too).
    assert out[3].prompt == "neon die"
    assert out[4].prompt == "neon die"
    # Idx order preserved.
    assert [s.idx for s in out] == [0, 1, 2, 3, 4]


def test_cap_hero_shots_noop_under_budget():
    """Two generative shots under a cap of 3 are left as-is."""
    import services.video_renderers.shot_list_renderer as mod

    shots = [
        Shot(idx=0, duration_s=4.0, intent="hero", source="generative",
             prompt="a", narration_offset_s=0.0),
        Shot(idx=1, duration_s=4.0, intent="hero", source="wan21",
             prompt="b", narration_offset_s=4.0),
    ]
    out = mod._cap_hero_shots(shots, 3)
    assert [s.source for s in out] == ["generative", "wan21"]
