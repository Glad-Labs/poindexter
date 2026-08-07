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


@pytest.fixture(autouse=True)
def _neutralize_wan_ready_wait():
    """The hero phase now polls wan /health before animating (#899
    reliability half). Unpatched, every hero-phase test would attempt real
    HTTP for up to the 90s budget — same hermeticity contract as the wan
    unload fixture below; tests asserting the wait re-patch locally."""
    from unittest.mock import AsyncMock as _AsyncMock

    with patch(
        "services.video_renderers.shot_list_renderer._wait_wan_ready",
        _AsyncMock(return_value=True),
    ):
        yield


@pytest.fixture(autouse=True)
def _neutralize_wan_unload():
    """Every _render_pass now fires a best-effort wan hard-unload before its
    still phase (`_clear_wan_for_stills`, poindexter#966 between-lanes half).
    Left unpatched, each render-path test would attempt a REAL HTTP POST to
    the wan-server URL — absorbed by the best-effort except, so tests stay
    green but non-hermetic (the known silent-test-network-hazard shape, and
    non-deterministic on the real-network CI runner). Tests asserting the
    unload re-patch locally; the innermost patch wins."""
    from unittest.mock import AsyncMock as _AsyncMock

    with patch("services.gpu_scheduler.gpu._unload_wan", _AsyncMock()):
        yield


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
    async def test_image_kenburns_calls_image_gen_with_correct_body(self, tmp_path):
        """The image-gen render must POST {'prompt': ..., 'negative_prompt': ...}
        to ``/generate`` and OMIT steps / guidance_scale so the server's
        per-model registry drives them (z_image_turbo wants 9 / CFG0;
        hardcoding image-gen model settings blew the render past the timeout).
        Wan21's wrong-body 422 issue should never resurface here."""
        shot = Shot(
            idx=0,
            duration_s=5.0,
            intent="opening shot",
            source="image_kenburns",
            prompt="a clean modern desk with a monitor",
            kenburns_zoom=(1.0, 1.2),
            narration_offset_s=0.0,
        )

        # Mock the image-gen response with image bytes content-type.
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
            image_gen_url="http://image-gen:9836",
            site_config=None,
            http_client_factory=_factory,
        )

        assert result.success is True
        assert result.clip_path is not None
        assert result.clip_path.endswith(".png")

        # Verify the image-gen POST body shape — must include 'prompt' and
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
    async def test_image_gen_render_timeout_comes_from_site_config(self, tmp_path):
        """The image-gen render timeout is read from image_render_timeout_seconds so a
        cold Z-Image load (~133s) survives — not a hardcoded 60s cap."""
        from services.site_config import SiteConfig

        shot = Shot(
            idx=0,
            duration_s=5.0,
            intent="opening shot",
            source="image_kenburns",
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
            image_gen_url="http://image-gen:9836",
            site_config=sc,
            http_client_factory=_factory,
        )
        # httpx.Timeout(read) reflects the configured value, not the 60s default.
        assert captured["timeout"] is not None
        assert captured["timeout"].read == 234.0

    @pytest.mark.asyncio
    async def test_wan21_calls_provider_with_image_path(self, tmp_path):
        """A hero shot (wan21/generative) renders its image-gen still first, then
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

        async def _fake_image_gen(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_fetch), \
                patch.object(mod, "_render_image_gen_image", _fake_image_gen):
            result = await _render_one_shot(
                shot,
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
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
        assert captured_config["duration_s"] <= 5  # wan-server 121-frame cap
        assert captured_config["image_path"].endswith(".png")  # i2v init still
        # Hero geometry rides the config — TI2V-5B's 720P@24 working range
        # (landscape lane, no site_config → code defaults).
        assert captured_config["width"] == 1280
        assert captured_config["height"] == 704
        assert captured_config["fps"] == 24
        assert "image_paths" not in captured_config
        assert "audio_path" not in captured_config
        assert "ken_burns" not in captured_config

    @pytest.mark.asyncio
    async def test_wan21_caps_duration_at_five_seconds(self, tmp_path):
        """Director-specified durations beyond 5s get capped — the
        wan-server truncates at 121 frames (~5s @ 24fps), so a longer ask
        comes back short and loop-fills with a visible jump cut."""
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

        async def _fake_image_gen(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_fetch), \
                patch.object(mod, "_render_image_gen_image", _fake_image_gen):
            await _render_one_shot(
                shot,
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=AsyncMock,
            )

        assert captured_config["duration_s"] == 5

    @pytest.mark.asyncio
    async def test_generative_animates_image_gen_still(self, tmp_path):
        """Piece 4: a ``generative`` hero shot renders the stylized image-gen still
        first, then animates it into a clip — the success path returns the
        .mp4."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(idx=0, duration_s=5.0, intent="hero", source="generative",
                    prompt="neon GPU die, cyberpunk", narration_offset_s=0.0)

        async def _fake_image_gen(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        async def _fake_clip(*, prompt, output_path, image_path, duration_s,
                             site_config, **kw):
            assert image_path and image_path.endswith(".png")  # the i2v init still
            with open(output_path, "wb") as f:
                f.write(b"MP4")
            return True, ""

        with patch.object(mod, "_render_image_gen_image", _fake_image_gen), \
                patch.object(mod, "_render_generative_clip", _fake_clip):
            result = await _render_one_shot(
                shot, prior_clip=None, work_dir=tmp_path, image_gen_url="http://x",
                site_config=None, http_client_factory=AsyncMock)

        assert result.success is True
        assert result.clip_path.endswith(".mp4")

    @pytest.mark.asyncio
    async def test_generative_falls_back_to_still_on_clip_miss(self, tmp_path):
        """When i2v produces no clip, fall back to the image-gen still (the
        compositor Ken-Burns it) and emit a ``hero_render_fallback`` finding —
        NOT a holdover of the prior clip."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(idx=1, duration_s=5.0, intent="hero", source="generative",
                    prompt="neon GPU die", narration_offset_s=0.0)
        findings: list[dict] = []

        async def _fake_image_gen(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        async def _fake_clip(**kw):
            return False, "i2v miss"

        with patch.object(mod, "_render_image_gen_image", _fake_image_gen), \
                patch.object(mod, "_render_generative_clip", _fake_clip), \
                patch.object(mod, "emit_finding",
                             lambda **kw: findings.append(kw)):
            result = await _render_one_shot(
                shot, prior_clip="/prior/clip.mp4", work_dir=tmp_path,
                image_gen_url="http://x", site_config=None,
                http_client_factory=AsyncMock, post_id="post-1")

        assert result.success is True
        assert result.clip_path.endswith(".png")  # the still, not the prior clip
        assert "/prior/clip.mp4" not in (result.clip_path or "")
        assert any(f.get("kind") == "hero_render_fallback" for f in findings)

    @pytest.mark.asyncio
    async def test_generative_fallback_finding_carries_failure_reason(self, tmp_path):
        """The hero_render_fallback finding must carry WHY the i2v render
        missed (timeout / unreachable / empty result) — not just that it
        did. Without this, diagnosing a miss means grepping wan-server logs
        that may already be gone (e.g. after the container recycled)."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(idx=1, duration_s=5.0, intent="hero", source="generative",
                    prompt="neon GPU die", narration_offset_s=0.0)
        findings: list[dict] = []

        async def _fake_image_gen(*, prompt, output_path, **kw):
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        async def _fake_clip(**kw):
            return False, "TimeoutError: wan-server /generate exceeded 900s"

        with patch.object(mod, "_render_image_gen_image", _fake_image_gen), \
                patch.object(mod, "_render_generative_clip", _fake_clip), \
                patch.object(mod, "emit_finding",
                             lambda **kw: findings.append(kw)):
            await _render_one_shot(
                shot, prior_clip="/prior/clip.mp4", work_dir=tmp_path,
                image_gen_url="http://x", site_config=None,
                http_client_factory=AsyncMock, post_id="post-1")

        f = next(f for f in findings if f["kind"] == "hero_render_fallback")
        assert "TimeoutError" in f["body"]
        assert f["extra"]["reason"] == "TimeoutError: wan-server /generate exceeded 900s"

    @pytest.mark.asyncio
    async def test_generative_still_render_failure_is_hard_fail(self, tmp_path):
        """If even the image-gen still can't render, there's nothing to fall back
        to — the shot fails (the render pass drops it)."""
        import services.video_renderers.shot_list_renderer as mod

        shot = Shot(idx=0, duration_s=5.0, intent="hero", source="generative",
                    prompt="neon GPU die", narration_offset_s=0.0)

        async def _fake_image_gen(*, prompt, output_path, **kw):
            return False

        with patch.object(mod, "_render_image_gen_image", _fake_image_gen):
            result = await _render_one_shot(
                shot, prior_clip=None, work_dir=tmp_path, image_gen_url="http://x",
                site_config=None, http_client_factory=AsyncMock)

        assert result.success is False

    def test_generative_is_regenerable(self):
        import services.video_renderers.shot_list_renderer as mod
        assert "generative" in mod._REGENERABLE_SOURCES

    def test_pexels_is_regenerable(self):
        """Pexels gets a real second chance on a QA miss (next search
        result), not an immediate holdover — see the fix rationale on
        ``test_pexels_regenerated_with_different_candidate_on_miss``."""
        import services.video_renderers.shot_list_renderer as mod
        assert "pexels" in mod._REGENERABLE_SOURCES

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
            image_gen_url="http://image-gen:9836",
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
            image_gen_url="http://image-gen:9836",
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
        """A 3-shot list (image_kenburns + pexels + wan21) renders each
        shot then calls ``FFmpegLocalCompositor.compose`` with the
        per-shot scenes. Pins the seam between per-shot rendering and
        the concat pipeline."""
        shots = [
            Shot(
                idx=0,
                duration_s=3.0,
                intent="opening still",
                source="image_kenburns",
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

        # Mock image-gen responses (covers image_kenburns + pexels fallback).
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
                image_gen_url="http://image-gen:9836",
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

    def _single_image_gen_shot_list(self):
        shots = [
            Shot(
                idx=0,
                duration_s=3.0,
                intent="opening still",
                source="image_gen",
                prompt="a clean abstract gradient backdrop",
                narration_offset_s=0.0,
            ),
        ]
        return _build_shot_list(shots)

    def _image_gen_client_factory(self):
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
        shot_list = self._single_image_gen_shot_list()
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
                image_gen_url="http://image-gen:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._image_gen_client_factory(),
                width=1080,
                height=1920,
            )

        assert result.success is True
        assert captured["width"] == 1080
        assert captured["height"] == 1920

    @pytest.mark.asyncio
    async def test_default_dims_remain_16_9(self, tmp_path):
        """No width/height args → backcompat 1920x1080 (16:9 long-form)."""
        shot_list = self._single_image_gen_shot_list()
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
                image_gen_url="http://image-gen:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._image_gen_client_factory(),
            )

        assert captured["width"] == 1920
        assert captured["height"] == 1080

    @pytest.mark.asyncio
    async def test_ambient_path_sets_soundtrack(self, tmp_path):
        """Passing ambient_path routes it to soundtrack_path — the ambient
        bed channel (#679) is now consumed, and narration is no longer
        double-used as the soundtrack."""
        shot_list = self._single_image_gen_shot_list()
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
                image_gen_url="http://image-gen:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._image_gen_client_factory(),
                ambient_path="/x/amb.wav",
            )

        assert captured["soundtrack_path"] == "/x/amb.wav"

    @pytest.mark.asyncio
    async def test_no_ambient_leaves_soundtrack_none(self, tmp_path):
        """No ambient_path → soundtrack_path is None (clean narration,
        no -18dB second copy of the voice over the whole video)."""
        shot_list = self._single_image_gen_shot_list()
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
                image_gen_url="http://image-gen:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._image_gen_client_factory(),
            )

        assert captured["soundtrack_path"] is None

    @pytest.mark.asyncio
    async def test_caption_path_sets_caption_track(self, tmp_path):
        """Passing caption_path routes it to caption_track_path on the
        CompositionRequest so the compositor burns the captions into the
        video (#676 Plan 5)."""
        shot_list = self._single_image_gen_shot_list()
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
                image_gen_url="http://image-gen:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._image_gen_client_factory(),
                caption_path="/x/c.srt",
            )

        assert captured["caption_track_path"] == "/x/c.srt"

    @pytest.mark.asyncio
    async def test_no_caption_path_leaves_caption_track_none(self, tmp_path):
        """No caption_path → caption_track_path is None (backcompat: the
        existing video_service.py caller renders without captions)."""
        shot_list = self._single_image_gen_shot_list()
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
                image_gen_url="http://image-gen:9836",
                site_config=None,
                pool=None,
                http_client_factory=self._image_gen_client_factory(),
            )

        assert captured["caption_track_path"] is None


class TestPexelsSource:
    """#media-render-fixes: pexels shots fetch REAL Pexels footage and NEVER
    fall back to image-gen — AI-generated humans (six-fingered hands) are a
    hard brand no-no. A genuine VIDEO clip is tried first (the director
    prompt has always told the LLM "pexels" shots are real footage/video —
    this closes the gap where the renderer only ever delivered a still); a
    real STILL PHOTO is the fallback when no video matches; holding over the
    prior clip is the last resort on a total miss.
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

    def _no_video_match(self):
        """Patch context: the Pexels VIDEO search returns nothing, so the
        pexels branch falls through to the photo path — isolates the
        pre-existing photo-path tests from the new video-first step."""
        from services.image_providers import pexels_video as pexels_video_mod
        return patch.object(
            pexels_video_mod.PexelsVideoProvider, "fetch",
            AsyncMock(return_value=[]),
        )

    @pytest.mark.asyncio
    async def test_pexels_video_used_when_available(self, tmp_path):
        """A configured key + a Pexels VIDEO hit writes an .mp4 and never
        touches the photo path or image-gen — real motion beats a static
        photo when Pexels actually has footage for the query."""
        mock_resp = MagicMock()
        mock_resp.content = b"fake-mp4-bytes"
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        def _factory(*args, **kwargs):
            return mock_client

        from services.image_providers import pexels_video as pexels_video_mod

        async def _fake_video_fetch(self, query, config):
            return [MagicMock(file_url="https://videos.pexels.com/x/hd.mp4")]

        photo_spy = AsyncMock()
        image_gen_spy = AsyncMock()
        with patch.object(pexels_video_mod.PexelsVideoProvider, "fetch",
                           _fake_video_fetch), \
             patch(
                "services.video_renderers.shot_list_renderer._render_pexels_image",
                photo_spy,
             ), \
             patch(
                "services.video_renderers.shot_list_renderer._render_image_gen_image",
                image_gen_spy,
             ):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=_factory,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
            )

        assert result.success is True
        assert result.clip_path is not None
        assert result.clip_path.endswith(".mp4")
        photo_spy.assert_not_called()
        image_gen_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_pexels_video_disabled_via_flag_skips_straight_to_photo(self, tmp_path):
        """video_pexels_video_enabled=false skips the video attempt entirely
        — goes straight to the photo path. Deliberately does NOT patch the
        video provider at all: if the gate ever regresses, this becomes a
        slow/flaky real-network test instead of silently passing, which is
        the point (photo_spy succeeding alone doesn't prove video was never
        tried)."""
        photo_spy = AsyncMock(return_value=True)

        class _SC:
            def get(self, k, d=None):
                return "false" if k == "video_pexels_video_enabled" else d

        with patch(
            "services.video_renderers.shot_list_renderer._render_pexels_image",
            photo_spy,
        ):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=_SC(),
                http_client_factory=AsyncMock,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
            )

        assert result.success is True
        photo_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pexels_video_download_failure_falls_back_to_photo(self, tmp_path):
        """The video SEARCH finds a candidate but the DOWNLOAD fails (dead
        link, network blip) — falls through to the real-photo path rather
        than holding over or image-gen-faking the subject."""
        from services.image_providers import pexels_video as pexels_video_mod

        async def _fake_video_fetch(self, query, config):
            return [MagicMock(file_url="https://videos.pexels.com/x/hd.mp4")]

        async def _failing_get(url, **kw):
            raise ConnectionError("simulated download failure")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = _failing_get

        def _factory(*args, **kwargs):
            return mock_client

        photo_spy = AsyncMock(return_value=True)
        with patch.object(pexels_video_mod.PexelsVideoProvider, "fetch",
                           _fake_video_fetch), \
             patch(
                "services.video_renderers.shot_list_renderer._render_pexels_image",
                photo_spy,
             ):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=_factory,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
            )

        assert result.success is True
        photo_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pexels_fetches_real_photo_not_image_gen(self, tmp_path):
        """No video match (the common case for niche/abstract queries) →
        a Pexels PHOTO hit writes a .jpg and never touches image-gen."""
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

        image_gen_spy = AsyncMock()
        with self._no_video_match(), \
             patch.object(pexels_mod.PexelsProvider, "fetch", _fake_fetch), \
             patch(
                "services.video_renderers.shot_list_renderer._render_image_gen_image",
                image_gen_spy,
             ):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=_factory,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
            )

        assert result.success is True
        assert result.clip_path is not None
        assert result.clip_path.endswith(".jpg")
        image_gen_spy.assert_not_called()  # the whole point: no AI human

    @pytest.mark.asyncio
    async def test_pexels_miss_holds_over_prior_never_image_gen(self, tmp_path):
        """No key (or no result) → hold over the prior clip; image-gen is never
        invoked to fake the human."""
        prior = str(tmp_path / "shot_00.png")
        with open(prior, "wb") as f:
            f.write(b"prior image bytes")

        image_gen_spy = AsyncMock()
        with patch(
            "services.video_renderers.shot_list_renderer._render_image_gen_image",
            image_gen_spy,
        ):
            result = await _render_one_shot(
                self._pexels_shot(idx=1, offset=4.0),
                prior_clip=prior,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=AsyncMock,
                pexels_key="",  # no key → Pexels miss
                orientation="landscape",
            )

        assert result.success is True
        assert result.clip_path == prior  # held over
        image_gen_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_pexels_miss_at_idx0_fails_never_image_gen(self, tmp_path):
        """A Pexels miss as the FIRST shot has nothing to hold over — fail
        (the shot drops out) rather than image-gen-faking a person."""
        image_gen_spy = AsyncMock()
        with patch(
            "services.video_renderers.shot_list_renderer._render_image_gen_image",
            image_gen_spy,
        ):
            result = await _render_one_shot(
                self._pexels_shot(idx=0),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=AsyncMock,
                pexels_key="",
                orientation="landscape",
            )

        assert result.success is False
        assert "pexels" in (result.error or "")
        image_gen_spy.assert_not_called()

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
            candidate_index=0,
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

        with self._no_video_match(), patch(
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
                image_gen_url="http://image-gen:9836",
                site_config=_SC(),
                pool=None,
                http_client_factory=AsyncMock,
            )

        assert result.success is True
        assert captured["api_key"] == "SEKRIT"
        assert captured["orientation"] == "portrait"  # 9:16 → portrait

    @pytest.mark.asyncio
    async def test_pexels_first_attempt_fetches_single_candidate(self, tmp_path):
        """The common case (no QA miss) must not over-fetch — attempt=0 (the
        default) asks Pexels for exactly one result, same as before this
        change. Regen diversity only costs extra API calls when actually
        needed."""
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

        captured_config: dict = {}

        async def _fake_fetch(self, query, config):
            captured_config.update(config)
            return [MagicMock(url="https://images.pexels.com/photos/1.jpg")]

        with self._no_video_match(), \
             patch.object(pexels_mod.PexelsProvider, "fetch", _fake_fetch):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=_factory,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
            )

        assert result.success is True
        assert captured_config["per_page"] == 1

    @pytest.mark.asyncio
    async def test_pexels_regen_attempt_fetches_next_ranked_result(self, tmp_path):
        """A repair-pass regen (attempt=1) must fetch a candidate list long
        enough to include a second photo, and pick THAT one — not the same
        top result the first attempt already failed QA on."""
        mock_resp = MagicMock()
        mock_resp.content = b"\xff\xd8\xff\xe0_fake_jpeg_bytes"
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        downloaded_urls: list[str] = []

        async def _tracking_get(url, **kw):
            downloaded_urls.append(url)
            return mock_resp
        mock_client.get = _tracking_get

        def _factory(*args, **kwargs):
            return mock_client

        from services.image_providers import pexels as pexels_mod

        captured_config: dict = {}
        fetched_urls = [
            "https://images.pexels.com/photos/1.jpg",
            "https://images.pexels.com/photos/2.jpg",
        ]

        async def _fake_fetch(self, query, config):
            captured_config.update(config)
            return [MagicMock(url=u) for u in fetched_urls[: config["per_page"]]]

        with self._no_video_match(), \
             patch.object(pexels_mod.PexelsProvider, "fetch", _fake_fetch):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=_factory,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
                attempt=1,
            )

        assert result.success is True
        assert captured_config["per_page"] == 2
        assert downloaded_urls == [fetched_urls[1]]  # picked the SECOND result

    @pytest.mark.asyncio
    async def test_pexels_regen_clamps_to_available_results(self, tmp_path):
        """If Pexels has fewer results than the attempt index wants (a niche
        query), clamp to the last available result instead of an
        IndexError — a thin result set must degrade, never crash."""
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
            # A niche query — only one result exists, regardless of per_page.
            return [MagicMock(url="https://images.pexels.com/photos/only.jpg")]

        with self._no_video_match(), \
             patch.object(pexels_mod.PexelsProvider, "fetch", _fake_fetch):
            result = await _render_one_shot(
                self._pexels_shot(),
                prior_clip=None,
                work_dir=tmp_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                http_client_factory=_factory,
                pexels_key="PEXELS-KEY",
                orientation="landscape",
                attempt=2,
            )

        assert result.success is True  # no IndexError — reuses the only result


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

    def _image_gen_factory(self):
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
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="a cyan abstract circuit", narration_offset_s=0.0)]
        scorer = AsyncMock(return_value=ShotQAResult(score=90.0, reason="great"))
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen:9836", site_config=_QASC(),
                http_client_factory=self._image_gen_factory())
        assert result.success is True
        assert scorer.await_count == 1  # scored once, accepted, no regen

    @pytest.mark.asyncio
    async def test_regenerate_then_fallback_emits_finding(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="beat", source="image_gen",
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
                image_gen_url="http://image-gen:9836", site_config=_QASC(),
                http_client_factory=self._image_gen_factory())
        assert result.success is True
        # shot 0: 1 score; shot 1: 1 initial + 2 regens = 3 → 4 total.
        assert scorer.await_count == 4
        assert any(f["kind"] == "shot_quality_fallback" for f in findings)

    @pytest.mark.asyncio
    async def test_qa_disabled_when_site_config_none(self, tmp_path):
        """site_config=None ⇒ QA never runs (backcompat for the existing suite)."""
        import services.video_renderers.shot_list_renderer as mod
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0)]
        scorer = AsyncMock()
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen:9836", site_config=None,
                http_client_factory=self._image_gen_factory())
        assert result.success is True
        scorer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_via_flag(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0)]
        scorer = AsyncMock()
        with patch.object(mod, "score_shot_frame", scorer), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen:9836",
                site_config=_QASC(video_shot_qa_enabled="false"),
                http_client_factory=self._image_gen_factory())
        scorer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pexels_regenerated_with_different_candidate_on_miss(self, tmp_path):
        """A pexels shot that misses vision-QA gets ONE more real chance — a
        fresh fetch for the NEXT-ranked search result, not an immediate
        holdover. Pexels can't reseed like a diffusion model, but it CAN ask
        for the next result, which is a genuinely different photo (evidenced
        fix: 8/8 shot_quality_fallback findings in 30 days of production were
        pexels shots, 0 on regenerable sources — pexels had no retry path)."""
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="person", source="pexels",
                      query="developer at desk", narration_offset_s=3.0)]
        # image_gen shot passes; pexels shot fails first, passes on the regen.
        scorer = AsyncMock(side_effect=[
            ShotQAResult(90.0), ShotQAResult(10.0), ShotQAResult(75.0),
        ])
        candidates_requested: list[int] = []

        async def _fake_pexels(*, query, output_path, api_key, orientation,
                                http_client_factory, candidate_index=0):
            candidates_requested.append(candidate_index)
            with open(output_path, "wb") as f:
                f.write(f"candidate-{candidate_index}".encode())
            return True

        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "_render_pexels_image", _fake_pexels), \
             patch.object(mod, "emit_finding", lambda **kw: None), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen:9836", site_config=_QASC(),
                http_client_factory=self._image_gen_factory())
        assert result.success is True
        # pexels fetched TWICE — the initial candidate (index 0), then a
        # genuinely different one (index 1) after the QA miss, not a repeat.
        assert candidates_requested == [0, 1]
        assert scorer.await_count == 3  # image_gen(1) + pexels initial(1) + pexels regen(1)

    @pytest.mark.asyncio
    async def test_fallback_finding_shape_is_dashboard_ready(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="beat", source="image_gen",
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
                image_gen_url="http://image-gen:9836", site_config=_QASC(),
                http_client_factory=self._image_gen_factory())
        f = next(f for f in captured if f["kind"] == "shot_quality_fallback")
        assert f["source"] == "shot_list_renderer"
        assert f["severity"] == "warn"
        assert f["dedup_key"] == "shot_quality_fallback:post-xyz:1"
        assert f["extra"]["score"] == 15.0

    @pytest.mark.asyncio
    async def test_unscored_shot_stamps_outcome_and_emits_finding(self, tmp_path):
        """A shot the scorer could NOT score (score=None: model/infra down) is
        accepted fail-open, but the audit row says qa_outcome='unscored' and a
        vision_scorer_unavailable finding fires — the no-op must be visible,
        not a bare NULL qa_score (which also means 'reused' or 'QA off')."""
        import json as _json

        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=3.0, intent="beat", source="image_gen",
                      prompt="teal mesh", narration_offset_s=3.0)]
        scorer = AsyncMock(side_effect=[
            ShotQAResult(90.0),
            ShotQAResult(score=None, reason="vision call failed"),
        ])
        pool = AsyncMock()
        captured = []
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "emit_finding", lambda **kw: captured.append(kw)), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="post-abc", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen:9836", site_config=_QASC(),
                pool=pool, http_client_factory=self._image_gen_factory())
        assert result.success is True  # fail-open: the shot still ships
        audits = [_json.loads(c.args[2]) for c in pool.execute.await_args_list]
        unscored = [a for a in audits if a["qa_outcome"] == "unscored"]
        assert len(unscored) == 1
        assert unscored[0]["shot_idx"] == 1
        assert unscored[0]["qa_score"] is None
        f = next(f for f in captured if f["kind"] == "vision_scorer_unavailable")
        assert f["severity"] == "warn"
        assert f["dedup_key"] == "vision_scorer_unavailable:shot_list_renderer:post-abc"
        assert f["extra"]["unscored"] == 1
        assert f["extra"]["shots_total"] == 2
        assert "vision call failed" in f["body"]

    @pytest.mark.asyncio
    async def test_reused_shot_stamps_skipped_reused_no_finding(self, tmp_path):
        """Holdover shots reuse an already-vetted prior clip — they're skipped
        by design, stamped qa_outcome='skipped_reused', and never counted as
        scorer no-ops (no vision_scorer_unavailable finding)."""
        import json as _json

        import services.video_renderers.shot_list_renderer as mod
        from services.video_renderers.shot_vision_qa import ShotQAResult
        shots = [Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                      prompt="cyan grid", narration_offset_s=0.0),
                 Shot(idx=1, duration_s=2.0, intent="carry", source="holdover",
                      narration_offset_s=3.0)]
        scorer = AsyncMock(return_value=ShotQAResult(90.0))
        pool = AsyncMock()
        captured = []
        with patch.object(mod, "score_shot_frame", scorer), \
             patch.object(mod, "emit_finding", lambda **kw: captured.append(kw)), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="post-def", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen:9836", site_config=_QASC(),
                pool=pool, http_client_factory=self._image_gen_factory())
        assert result.success is True
        assert scorer.await_count == 1  # only the fresh image_gen shot scored
        audits = [_json.loads(c.args[2]) for c in pool.execute.await_args_list]
        by_idx = {a["shot_idx"]: a for a in audits}
        assert by_idx[1]["qa_outcome"] == "skipped_reused"
        assert not [f for f in captured if f["kind"] == "vision_scorer_unavailable"]

    @pytest.mark.asyncio
    async def test_all_renders_precede_any_vision_call(self, tmp_path):
        """Anti-thrash invariant: with QA on, EVERY shot is rendered before
        ANY vision score runs — so image-gen stays resident for the whole render
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
            Shot(idx=0, duration_s=3.0, intent="open", source="image_gen",
                 prompt="cyan circuit", narration_offset_s=0.0),
            Shot(idx=1, duration_s=3.0, intent="beat", source="wan21",
                 prompt="teal mesh in motion", narration_offset_s=3.0),
            Shot(idx=2, duration_s=3.0, intent="close", source="image_gen",
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

        # poindexter#966: heroes route through the split still/animate helpers,
        # not _render_one_shot — record both halves as "render" so the
        # anti-thrash invariant (every render precedes every score) keeps
        # covering the whole two-phase pass.
        async def _fake_hero_still(shot, **kwargs):
            timeline.append(("render", shot.idx))
            still = str(tmp_path / f"shot_{shot.idx:02d}.png")
            with open(still, "wb") as fh:
                fh.write(b"png")
            return ShotRenderResult(
                idx=shot.idx, source=shot.source, success=True,
                clip_path=still, duration_s=shot.duration_s,
            )

        async def _fake_animate(shot, *, still_path, **kwargs):
            timeline.append(("render", shot.idx))
            clip = str(tmp_path / f"shot_{shot.idx:02d}.mp4")
            with open(clip, "wb") as fh:
                fh.write(b"mp4")
            return ShotRenderResult(
                idx=shot.idx, source=shot.source, success=True,
                clip_path=clip, duration_s=shot.duration_s,
            )

        with patch.object(mod, "_render_one_shot", _fake_render), \
             patch.object(mod, "_render_hero_still", _fake_hero_still), \
             patch.object(mod, "_animate_hero", _fake_animate), \
             patch.object(mod, "score_shot_frame", _fake_score), \
             patch("services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
                   self._mock_compositor()):
            result = await render_shot_list(
                post_id="p", shot_list=_build_shot_list(shots),
                audio_path=str(tmp_path / "a.mp3"), output_path=str(tmp_path / "o.mp4"),
                image_gen_url="http://image-gen:9836", site_config=_QASC(),
                http_client_factory=self._image_gen_factory())

        assert result.success is True
        # All three passed (90 ≥ 60) → each scored exactly once, no repair.
        # 4 render events: idx0 + idx2 stills, and the hero's still + animate.
        assert [k for k, _ in timeline].count("render") == 4
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
    image_kenburns (same prompt) so the director over-asking can't blow the
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
        "generative", "generative", "pexels", "image_kenburns", "image_kenburns",
    ]
    # Downgraded shots keep their prompt (image_kenburns needs one too).
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


class TestRenderBrandCard:
    """The guaranteed rung-3 card — pure PIL, no network, cannot fail."""

    def test_card_writes_valid_png_at_requested_dims(self, tmp_path):
        from PIL import Image

        from services.video_renderers.shot_list_renderer import _render_brand_card

        out = str(tmp_path / "card.png")
        ok = _render_brand_card(
            output_path=out, width=1920, height=1080, wordmark="Glad Labs"
        )

        assert ok is True
        with Image.open(out) as im:
            assert im.size == (1920, 1080)
            assert im.mode == "RGB"

    def test_card_renders_solid_field_when_wordmark_empty(self, tmp_path):
        from PIL import Image

        from services.video_renderers.shot_list_renderer import _render_brand_card

        out = str(tmp_path / "card_blank.png")
        ok = _render_brand_card(
            output_path=out, width=1080, height=1920, wordmark=""
        )

        assert ok is True
        with Image.open(out) as im:
            assert im.size == (1080, 1920)

    def test_card_survives_font_load_failure(self, tmp_path, monkeypatch):
        """Even if every font path fails, the solid navy field still saves."""
        from PIL import Image, ImageFont

        from services.video_renderers import shot_list_renderer as slr

        def _boom(*a, **k):
            raise OSError("no fonts")

        monkeypatch.setattr(ImageFont, "truetype", _boom)
        monkeypatch.setattr(ImageFont, "load_default", _boom)

        out = str(tmp_path / "card_nofont.png")
        ok = slr._render_brand_card(
            output_path=out, width=640, height=360, wordmark="X"
        )

        assert ok is True
        with Image.open(out) as im:
            assert im.size == (640, 360)

    def test_wordmark_font_scales_without_linux_truetype_paths(self, monkeypatch):
        """On a host WITHOUT the Linux DejaVu paths (Windows/macOS operator or a
        fresh OSS install), the wordmark must still scale via
        load_default(size=…) — not collapse to the ~10px bitmap default that
        ignores size. Force truetype to miss and assert the rendered text height
        tracks the requested size."""
        from PIL import Image, ImageDraw, ImageFont

        from services.video_renderers import shot_list_renderer as slr

        # Simulate a host with no DejaVu font FILES on disk: fail truetype for a
        # path string (the on-disk lookups) but let a file-like buffer through,
        # so Pillow's own load_default(size=…) — which builds its bundled font
        # from a BytesIO — still works. This is the Windows/macOS/OSS-install
        # shape the fix targets, reproduced on any CI platform.
        real_truetype = ImageFont.truetype

        def _no_font_files(font=None, *a, **k):
            if isinstance(font, str):
                raise OSError("no dejavu font files on this host")
            return real_truetype(font, *a, **k)

        monkeypatch.setattr(ImageFont, "truetype", _no_font_files)
        draw = ImageDraw.Draw(Image.new("RGB", (1920, 1080)))

        small = draw.textbbox((0, 0), "Glad Labs", font=slr._load_card_font(24))
        large = draw.textbbox((0, 0), "Glad Labs", font=slr._load_card_font(106))
        small_h = small[3] - small[1]
        large_h = large[3] - large[1]

        # The 106px font must render markedly taller than the 24px one, and far
        # above the ~11px bitmap floor that would prove the size was ignored.
        assert large_h > small_h * 2
        assert large_h > 40


class TestRenderPassHeroOrdering:
    """poindexter#966: the render pass is two VRAM-coherent phases — every
    image-gen call (non-hero shots AND hero init stills) completes before ANY
    wan animation, so the hero path's image-gen hard-unload can no longer
    poison the rest of the pass (e68e4fe8 substituted the identical 4 shots
    on two consecutive renders because a mid-list hero evicted image-gen)."""

    def _mixed_shots(self):
        return [
            Shot(idx=0, duration_s=5.0, intent="open", source="image_gen",
                 prompt="abstract gradient one", narration_offset_s=0.0),
            Shot(idx=1, duration_s=5.0, intent="hero a", source="generative",
                 prompt="hero subject one", motion="slow push-in",
                 narration_offset_s=5.0),
            Shot(idx=2, duration_s=5.0, intent="mid", source="image_kenburns",
                 prompt="abstract gradient two", kenburns_zoom=(1.0, 1.1),
                 narration_offset_s=10.0),
            Shot(idx=3, duration_s=5.0, intent="hero b", source="generative",
                 prompt="hero subject two", motion="gentle parallax",
                 narration_offset_s=15.0),
            Shot(idx=4, duration_s=5.0, intent="close", source="holdover",
                 narration_offset_s=20.0),
        ]

    @pytest.mark.asyncio
    async def test_all_stills_render_before_any_wan_animation(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        calls: list[str] = []

        async def fake_still(*, prompt, output_path, **kwargs):
            calls.append(f"image_gen:{prompt}")
            with open(output_path, "wb") as fh:
                fh.write(b"png")
            return True

        async def fake_clip(*, prompt, output_path, image_path, **kwargs):
            calls.append(f"wan:{image_path}")
            with open(output_path, "wb") as fh:
                fh.write(b"mp4")
            return True, ""

        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=None, http_client_factory=None, pexels_key="",
            orientation="landscape", post_id="p-order",
        )
        with patch.object(slr, "_render_image_gen_image", fake_still), \
             patch.object(slr, "_render_generative_clip", fake_clip):
            states = await slr._render_pass(
                self._mixed_shots(), render_kwargs=render_kwargs,
            )

        # Phase property: every image-gen call precedes every wan call.
        first_wan = next(i for i, c in enumerate(calls) if c.startswith("wan:"))
        assert all(not c.startswith("image_gen:") for c in calls[first_wan:]), calls
        # 4 image-gen renders (idx 0 + idx 2 stills, idx 1 + idx 3 hero
        # init stills — idx 4 is a holdover) then 2 wan animations.
        assert sum(c.startswith("image_gen:") for c in calls) == 4
        assert sum(c.startswith("wan:") for c in calls) == 2

        # Results keyed to the right shots: heroes end as .mp4 clips.
        by_idx = {st.shot.idx: st for st in states}
        assert by_idx[1].result.success and by_idx[1].result.clip_path.endswith(".mp4")
        assert by_idx[3].result.success and by_idx[3].result.clip_path.endswith(".mp4")
        # The trailing holdover re-pointed from the hero's interim still to
        # the finished clip (pre-split semantics preserved).
        assert by_idx[4].is_reused
        assert by_idx[4].result.clip_path == by_idx[3].result.clip_path

    @pytest.mark.asyncio
    async def test_failed_hero_animation_falls_back_to_its_still(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        async def fake_still(*, prompt, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"png")
            return True

        async def fake_clip(*, prompt, output_path, image_path, **kwargs):
            return False, "wan OOM"

        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=None, http_client_factory=None, pexels_key="",
            orientation="landscape", post_id="p-fb",
        )
        shots = [s for s in self._mixed_shots() if s.idx in (0, 1)]
        with patch.object(slr, "_render_image_gen_image", fake_still), \
             patch.object(slr, "_render_generative_clip", fake_clip), \
             patch.object(slr, "_emit_hero_fallback_finding") as fb:
            states = await slr._render_pass(shots, render_kwargs=render_kwargs)

        hero = next(st for st in states if st.shot.idx == 1)
        assert hero.result.success
        assert hero.result.clip_path.endswith(".png")  # Ken-Burns still fallback
        fb.assert_called_once()

    @pytest.mark.asyncio
    async def test_render_pass_clears_wan_before_stills(self, tmp_path):
        """Between-lanes half of poindexter#966: the prior lane's hero phase
        leaves wan resident, so the pass hard-unloads wan before its still
        phase whenever the list has image-gen-family work (1d10e119's long
        rendered clean while its short substituted 5/8 against resident wan)."""
        from services.gpu_scheduler import gpu as real_gpu
        from services.video_renderers import shot_list_renderer as slr

        async def fake_still(*, prompt, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"png")
            return True

        wan_unload = AsyncMock()
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=None, http_client_factory=None, pexels_key="",
            orientation="landscape", post_id="p-wanclear",
        )
        shots = [s for s in self._mixed_shots() if s.idx == 0]  # image_gen only
        with patch.object(slr, "_render_image_gen_image", fake_still), \
             patch.object(real_gpu, "_unload_wan", wan_unload):
            await slr._render_pass(shots, render_kwargs=render_kwargs)

        wan_unload.assert_awaited_once_with(hard=True)

    @pytest.mark.asyncio
    async def test_render_pass_skips_wan_clear_without_image_gen_work(self, tmp_path):
        """An all-pexels/holdover list needs no image-gen, so evicting wan
        would be a pointless cold-reload tax on the next hero."""
        from services.gpu_scheduler import gpu as real_gpu
        from services.video_renderers import shot_list_renderer as slr

        wan_unload = AsyncMock()
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=None, http_client_factory=None, pexels_key="",
            orientation="landscape", post_id="p-nowan",
        )
        shots = [
            Shot(idx=0, duration_s=5.0, intent="open", source="pexels",
                 query="server room", narration_offset_s=0.0),
        ]

        async def fake_pexels_video(**kwargs):
            return False

        async def fake_pexels_image(*, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"jpg")
            return True

        with patch.object(slr, "_render_pexels_video", fake_pexels_video), \
             patch.object(slr, "_render_pexels_image", fake_pexels_image), \
             patch.object(real_gpu, "_unload_wan", wan_unload):
            await slr._render_pass(shots, render_kwargs=render_kwargs)

        wan_unload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_hero_still_reaches_backfill_ladder(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        async def fake_still(*, prompt, output_path, **kwargs):
            return False

        async def fake_clip(*args, **kwargs):
            raise AssertionError("wan must not be called when the still failed")

        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=None, http_client_factory=None, pexels_key="",
            orientation="landscape", post_id="p-nostill",
        )
        shots = [s for s in self._mixed_shots() if s.idx == 1]
        with patch.object(slr, "_render_image_gen_image", fake_still), \
             patch.object(slr, "_render_generative_clip", fake_clip):
            states = await slr._render_pass(shots, render_kwargs=render_kwargs)

        assert states[0].result.success is False
        assert "still render failed" in (states[0].result.error or "")


class TestBackfillPass:
    """A failed shot is filled with a card, not dropped."""

    def _failed_state(self, idx=0, source="image_gen"):
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import (
            ShotRenderResult,
            _ShotState,
        )

        shot = Shot(
            idx=idx, duration_s=6.0, intent="establish topic", source=source,
            prompt="isometric 3d, empty server hall", narration_offset_s=0.0,
        )
        res = ShotRenderResult(
            idx=idx, source=source, success=False,
            error="image-gen render returned no image",
        )
        return _ShotState(shot=shot, result=res, is_reused=False)

    @pytest.mark.asyncio
    async def test_failed_shot_filled_with_card(self, tmp_path):
        from services.video_renderers.shot_list_renderer import _backfill_pass

        st = self._failed_state()
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="", site_config=None,
            http_client_factory=None, pexels_key="", orientation="landscape",
            post_id="p1",
        )

        await _backfill_pass(
            [st], render_kwargs=render_kwargs, site_config=None,
            post_id="p1", width=1920, height=1080, wordmark="Glad Labs",
        )

        assert st.result.success is True
        assert st.result.clip_path.endswith("_card.png")
        assert st.rung == "card"
        assert st.result.duration_s == 6.0  # timeline slot preserved

    @pytest.mark.asyncio
    async def test_card_disabled_leaves_shot_dropped(self, tmp_path):
        from unittest.mock import MagicMock

        from services.video_renderers.shot_list_renderer import _backfill_pass

        sc = MagicMock()
        sc.get.return_value = "false"  # video_fallback_card_enabled=false
        st = self._failed_state()
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="", site_config=sc,
            http_client_factory=None, pexels_key="", orientation="landscape",
            post_id="p1",
        )

        await _backfill_pass(
            [st], render_kwargs=render_kwargs, site_config=sc,
            post_id="p1", width=1920, height=1080, wordmark="Glad Labs",
        )

        assert st.result.success is False
        assert st.rung == "dropped"

    @pytest.mark.asyncio
    async def test_successful_shot_untouched(self, tmp_path):
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import (
            ShotRenderResult,
            _backfill_pass,
            _ShotState,
        )

        shot = Shot(
            idx=1, duration_s=5.0, intent="x", source="pexels",
            query="server room", narration_offset_s=6.0,
        )
        st = _ShotState(
            shot=shot,
            result=ShotRenderResult(
                idx=1, source="pexels", success=True,
                clip_path="/tmp/real.mp4", duration_s=5.0,
            ),
            is_reused=False,
        )
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="", site_config=None,
            http_client_factory=None, pexels_key="", orientation="landscape",
            post_id="p1",
        )

        await _backfill_pass(
            [st], render_kwargs=render_kwargs, site_config=None,
            post_id="p1", width=1920, height=1080, wordmark="Glad Labs",
        )

        assert st.result.clip_path == "/tmp/real.mp4"
        assert st.rung == "primary"


class TestCrossFamilySubstitute:
    """Rung 2: an image-gen-family failure retries as real Pexels footage;
    a pexels failure never routes to image-gen (no-AI-humans policy)."""

    def test_query_strips_style_modifier(self):
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import (
            _pexels_query_from_shot,
        )

        shot = Shot(
            idx=0, duration_s=6.0, intent="establish the data center",
            source="image_kenburns",
            prompt="flat vector illustration, empty server hall, cyan palette",
            kenburns_zoom=(1.0, 1.2),
            narration_offset_s=0.0,
        )
        assert _pexels_query_from_shot(shot) == "empty server hall"

    def test_query_falls_back_to_intent_when_prompt_has_no_subject(self):
        """A prompt that is only a style modifier yields no subject → intent."""
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import (
            _pexels_query_from_shot,
        )

        shot = Shot(
            idx=0, duration_s=6.0, intent="city skyline at night",
            source="image_gen", prompt="cyberpunk neon", narration_offset_s=0.0,
        )
        assert _pexels_query_from_shot(shot) == "city skyline at night"

    @pytest.mark.asyncio
    async def test_image_gen_failure_substitutes_pexels_video(
        self, tmp_path, monkeypatch
    ):
        from services.video_renderers import shot_list_renderer as slr

        async def _fake_video(*, output_path, **kwargs):
            with open(output_path, "wb") as f:
                f.write(b"mp4")
            return True

        monkeypatch.setattr(slr, "_render_pexels_video", _fake_video)

        st = TestBackfillPass()._failed_state(source="image_gen")
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="", site_config=None,
            http_client_factory=None, pexels_key="KEY",
            orientation="landscape", post_id="p1",
        )

        await slr._backfill_pass(
            [st], render_kwargs=render_kwargs, site_config=None,
            post_id="p1", width=1920, height=1080, wordmark="GL",
        )

        assert st.rung == "substitute"
        assert st.result.success is True
        assert st.result.clip_path.endswith("_sub.mp4")

    @pytest.mark.asyncio
    async def test_pexels_source_never_substitutes_to_image_gen(self, tmp_path):
        """A missed pexels shot must go straight to the card, never image-gen."""
        from schemas.video_shot_list import Shot
        from services.video_renderers.shot_list_renderer import (
            ShotRenderResult,
            _backfill_pass,
            _ShotState,
        )

        shot = Shot(
            idx=0, duration_s=5.0, intent="developer at desk",
            source="pexels", query="developer typing", narration_offset_s=0.0,
        )
        st = _ShotState(
            shot=shot,
            result=ShotRenderResult(
                idx=0, source="pexels", success=False,
                error="pexels miss at idx=0",
            ),
            is_reused=False,
        )
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="", site_config=None,
            http_client_factory=None, pexels_key="KEY",
            orientation="landscape", post_id="p1",
        )

        await _backfill_pass(
            [st], render_kwargs=render_kwargs, site_config=None,
            post_id="p1", width=1920, height=1080, wordmark="GL",
        )

        assert st.rung == "card"  # NOT substitute — pexels never routes to image-gen


class TestFitSceneDurations:
    """Pure narration-fit layout: rescale director shots to span the voiceover."""

    def test_fits_already_is_noop(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [2.0, 5.0, 6.0, 5.0]
        # narration (17s) within tolerance of total (18s) → keep director durations
        out = _fit_scene_durations(durs, 17.0, max_shot_s=9.0)
        assert out == [(0, 2.0), (1, 5.0), (2, 6.0), (3, 5.0)]

    def test_video_longer_than_narration_is_noop(self):
        # Without shortfall_hold_s (the pre-compression contract every legacy
        # caller keeps) a shorter narration never triggers a re-layout.
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [5.0, 5.0, 5.0]
        out = _fit_scene_durations(durs, 8.0, max_shot_s=9.0)  # narration shorter
        assert out == [(0, 5.0), (1, 5.0), (2, 5.0)]

    def test_compression_scales_down_to_narration_plus_hold(self):
        # The 2026-07 silent-tail shape: a ~300s director plan (estimated off
        # the podcast script) over a ~175s narration shipped ~2 minutes of
        # silent footage. With the hold set, the fit compresses proportionally
        # to narration + hold and preserves the director's pacing ratios.
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [15.0, 25.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 20.0]  # 300s
        out = _fit_scene_durations(
            durs, 175.0, max_shot_s=30.0, min_shot_s=2.0, shortfall_hold_s=3.0,
        )
        total = sum(d for _, d in out)
        assert abs(total - 178.0) < 0.05          # narration + hold
        assert len(out) == len(durs)              # no shot dropped, no cycling
        # Pacing preserved: the 15s hook stays exactly half a 30s hold.
        assert abs(out[0][1] * 2 - out[2][1]) < 0.01
        assert all(d >= 2.0 for _, d in out)      # floor holds

    def test_compression_noop_within_hold(self):
        # A deliberate outro beat (shortfall ≤ hold + tolerance) never churns
        # a re-layout — the video may outlive the voice by the hold.
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [5.0, 5.0, 5.0]  # 15s
        out = _fit_scene_durations(
            durs, 12.0, max_shot_s=9.0, min_shot_s=2.0, shortfall_hold_s=3.0,
        )  # shortfall 3.0 ≤ hold(3) + tolerance(1)
        assert out == [(0, 5.0), (1, 5.0), (2, 5.0)]

    def test_compression_respects_min_shot_floor(self):
        # A pathological over-plan can't compress a shot into a subliminal
        # flicker: every shot floors at min_shot_s (the total then runs
        # modestly over the target — bounded, and strictly better than the
        # minutes-long silent tail it replaces).
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [2.0, 30.0]
        out = _fit_scene_durations(
            durs, 3.0, max_shot_s=30.0, min_shot_s=2.0, shortfall_hold_s=0.0,
        )
        assert out[0][1] == 2.0                   # floored, not 0.19
        assert all(d >= 2.0 for _, d in out)

    def test_proportional_rescale_spans_narration_and_preserves_pacing(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [2.0, 5.0, 6.0, 5.0, 6.0, 6.0, 7.0, 8.0]  # 45s, avg 5.625
        out = _fit_scene_durations(durs, 54.0, max_shot_s=9.0)  # scale 1.2, gentle
        # Gentle regime = pure proportional rescale: exact span, one pass, the
        # director's pacing preserved. The 9s ceiling is a cycle-trigger, so the
        # longest shot may sit modestly over it here (9.6) rather than cycle.
        assert out == [
            (0, 2.4), (1, 6.0), (2, 7.2), (3, 6.0),
            (4, 7.2), (5, 7.2), (6, 8.4), (7, 9.6),
        ]
        assert abs(sum(d for _, d in out) - 54.0) < 0.05

    def test_large_scale_caps_per_shot_and_cycles(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        durs = [2.0, 5.0, 6.0, 5.0, 6.0, 6.0, 7.0, 8.0]  # 45s
        out = _fit_scene_durations(durs, 158.0, max_shot_s=9.0)  # scale 3.5
        total = sum(d for _, d in out)
        assert abs(total - 158.0) < 0.05
        assert all(d <= 9.0 + 1e-6 for _, d in out)   # ceiling holds
        assert len(out) > 8                            # cycled past the 8 shots
        assert out[8][0] == 0                          # wrapped back to shot 0

    def test_scene_count_guard_bounds_pathological_fill(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        out = _fit_scene_durations([1.0], 10_000.0, max_shot_s=1.0, max_scenes=50)
        assert len(out) == 50

    def test_empty_or_zero_total_returns_originals(self):
        from services.video_renderers.shot_list_renderer import _fit_scene_durations
        assert _fit_scene_durations([], 30.0, max_shot_s=9.0) == []
        assert _fit_scene_durations([0.0, 0.0], 30.0, max_shot_s=9.0) == [(0, 0.0), (1, 0.0)]


class TestRenderShotListNarrationFit:
    """Part 1 (#867 + the 2026-07-31 silent-tail fix):
    ``render_shot_list(narration_fit=True)`` lays out the scenes to span the
    ACTUAL narration (probed via ffprobe) — stretching so the compositor never
    clones the final frame over an overhang (the frozen tail), and, with
    ``narration_fit_hold_s`` set, compressing so the visuals never outlive the
    voice by minutes (the silent tail). Off by default for the legacy
    ``video_service`` caller."""

    def _image_gen_shots(self, durations: list[float]) -> VideoShotList:
        # Alternate image_kenburns/image_gen (both render via the mocked
        # image-gen path) so the schema's "no >2 consecutive same-source" rule
        # never trips regardless of shot count.
        shots = [
            Shot(
                idx=i,
                duration_s=d,
                intent=f"still {i}",
                source="image_kenburns" if i % 2 == 0 else "image_gen",
                prompt=f"a clean abstract gradient backdrop variant {i}",
                kenburns_zoom=(1.0, 1.15) if i % 2 == 0 else None,
                narration_offset_s=float(sum(durations[:i])),
            )
            for i, d in enumerate(durations)
        ]
        return _build_shot_list(shots)

    async def _render_capture(self, tmp_path, monkeypatch, *, durations, probe_s, fit_kwargs):
        """Render a shot list through a mocked image-gen + compositor, returning
        ``(result, captured_scene_durations, probe_calls)``. ``probe_s`` is the
        stubbed narration duration; ``probe_calls`` counts ffprobe consultations
        so we can assert the OFF path never probes."""
        from services.video_renderers import shot_list_renderer as slr

        shot_list = self._image_gen_shots(durations)
        audio_path = str(tmp_path / "narration.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")
        output_path = str(tmp_path / "final.mp4")

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

        captured: dict = {}

        class _MockCompositor:
            def __init__(self, site_config=None):
                self._sc = site_config

            async def compose(self, request, **kwargs):
                captured["scene_durs"] = [s.duration_s for s in request.scenes]
                with open(request.output_path, "wb") as fh:
                    fh.write(b"fake composed mp4 bytes")
                return MagicMock(
                    success=True,
                    output_path=request.output_path,
                    file_size_bytes=len(b"fake composed mp4 bytes"),
                    duration_s=probe_s,
                )

        probe_calls = {"n": 0}

        async def _fake_probe(path, *, ffprobe="ffprobe"):
            probe_calls["n"] += 1
            return probe_s

        monkeypatch.setattr(slr, "_probe_duration_s", _fake_probe, raising=False)

        with patch(
            "services.media_compositors.ffmpeg_local.FFmpegLocalCompositor",
            _MockCompositor,
        ):
            result = await slr.render_shot_list(
                post_id="post-fit",
                shot_list=shot_list,
                audio_path=audio_path,
                output_path=output_path,
                image_gen_url="http://image-gen:9836",
                site_config=None,
                pool=None,
                http_client_factory=_factory,
                **fit_kwargs,
            )
        return result, captured.get("scene_durs", []), probe_calls["n"]

    @pytest.mark.asyncio
    async def test_narration_fit_rescales_scenes_to_narration(self, tmp_path, monkeypatch):
        """3×15s = 45s of shots, narration 54s → scenes laid out to span ~54s."""
        result, scene_durs, probe_calls = await self._render_capture(
            tmp_path,
            monkeypatch,
            durations=[15.0, 15.0, 15.0],
            probe_s=54.0,
            fit_kwargs={"narration_fit": True, "narration_fit_max_shot_s": 20.0},
        )
        assert result.success is True
        assert probe_calls == 1
        assert abs(sum(scene_durs) - 54.0) < 0.1

    @pytest.mark.asyncio
    async def test_narration_fit_compresses_over_planned_scenes(self, tmp_path, monkeypatch):
        """45s of shots over a 30s narration + 3s hold → scenes span ~33s
        (the silent-tail fix: the visuals no longer outlive the voice)."""
        result, scene_durs, probe_calls = await self._render_capture(
            tmp_path,
            monkeypatch,
            durations=[15.0, 15.0, 15.0],
            probe_s=30.0,
            fit_kwargs={
                "narration_fit": True,
                "narration_fit_max_shot_s": 30.0,
                "narration_fit_min_shot_s": 2.0,
                "narration_fit_hold_s": 3.0,
            },
        )
        assert result.success is True
        assert probe_calls == 1
        assert abs(sum(scene_durs) - 33.0) < 0.1

    @pytest.mark.asyncio
    async def test_narration_fit_off_keeps_director_durations(self, tmp_path, monkeypatch):
        """Default (legacy video_service caller): scenes keep the director's
        45s total; no probe. (Both media atoms opt IN since the silent-tail
        fix — this pins the renderer's own default, not the atom wiring.)"""
        result, scene_durs, probe_calls = await self._render_capture(
            tmp_path,
            monkeypatch,
            durations=[15.0, 15.0, 15.0],
            probe_s=54.0,
            fit_kwargs={},  # narration_fit defaults False
        )
        assert result.success is True
        assert probe_calls == 0
        assert scene_durs == [15.0, 15.0, 15.0]


class _HeroSC:
    """Minimal SiteConfig stub for the hero geometry / motion tests."""

    def __init__(self, cfg: dict | None = None):
        self._cfg = cfg or {}

    def get(self, k, d=None):
        return self._cfg.get(k, d)

    def get_int(self, k, d=0):
        return int(self._cfg.get(k, d))


class TestHeroRenderDims:
    """_hero_render_dims — TI2V-5B geometry resolution + portrait swap."""

    def test_defaults_landscape(self):
        import services.video_renderers.shot_list_renderer as mod
        assert mod._hero_render_dims("landscape", None) == (1280, 704, 24)

    def test_portrait_swaps_width_height(self):
        """The 9:16 short lane gets a vertical hero clip — before this,
        shorts letterboxed a landscape clip into a 1080×1920 canvas."""
        import services.video_renderers.shot_list_renderer as mod
        assert mod._hero_render_dims("portrait", None) == (704, 1280, 24)

    def test_settings_override(self):
        import services.video_renderers.shot_list_renderer as mod
        sc = _HeroSC({
            "video_hero_width": "832",
            "video_hero_height": "480",
            "video_hero_fps": "16",
        })
        assert mod._hero_render_dims("landscape", sc) == (832, 480, 16)
        assert mod._hero_render_dims("portrait", sc) == (480, 832, 16)


class TestComposeHeroWanPrompt:
    """_compose_hero_wan_prompt — the i2v prompt = still description + motion."""

    def test_directors_motion_rides_the_prompt(self):
        import services.video_renderers.shot_list_renderer as mod
        out = mod._compose_hero_wan_prompt(
            "flat vector illustration, data river",
            "slow push-in; particles drift upward",
            None,
        )
        assert out == (
            "flat vector illustration, data river. "
            "Camera and motion: slow push-in; particles drift upward"
        )

    def test_missing_motion_gets_default_direction(self):
        """A frozen pre-motion-field list still gets motion language —
        re-sending only the still's description animates nothing."""
        import services.video_renderers.shot_list_renderer as mod
        out = mod._compose_hero_wan_prompt("neon GPU die", None, None)
        assert out.startswith("neon GPU die. Camera and motion: ")
        assert mod._HERO_MOTION_FALLBACK in out

    def test_default_direction_is_operator_tunable(self):
        import services.video_renderers.shot_list_renderer as mod
        sc = _HeroSC({"video_hero_motion_default": "gentle ripple only"})
        out = mod._compose_hero_wan_prompt("neon GPU die", "", sc)
        assert out == "neon GPU die. Camera and motion: gentle ripple only"

    def test_blank_still_prompt_returns_direction_alone(self):
        import services.video_renderers.shot_list_renderer as mod
        assert mod._compose_hero_wan_prompt("", "drift", None) == "drift"


class TestHeroMotionThreading:
    """The composed prompt + lane geometry reach Wan21Provider.fetch."""

    @pytest.mark.asyncio
    async def test_provider_receives_motion_composed_prompt(self, tmp_path):
        import services.video_renderers.shot_list_renderer as mod
        from services.video_providers import wan2_1 as wan21_mod

        shot = Shot(
            idx=0, duration_s=5.0, intent="hero", source="generative",
            prompt="cinematic illustration, data canyon",
            motion="slow dolly forward as streams flow",
            narration_offset_s=0.0,
        )
        captured: dict = {}

        async def _fake_fetch(self, prompt, config):
            captured["prompt"] = prompt
            captured.update(config)
            with open(config["output_path"], "wb") as f:
                f.write(b"MP4")
            return [MagicMock(file_path=config["output_path"])]

        async def _fake_image_gen(*, prompt, output_path, **kw):
            captured["still_prompt"] = prompt
            captured["still_dims"] = (kw.get("width"), kw.get("height"))
            with open(output_path, "wb") as f:
                f.write(b"\x89PNG")
            return True

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_fetch), \
                patch.object(mod, "_render_image_gen_image", _fake_image_gen):
            result = await _render_one_shot(
                shot, prior_clip=None, work_dir=tmp_path,
                image_gen_url="http://x", site_config=None,
                http_client_factory=AsyncMock, orientation="portrait")

        assert result.success is True
        # The still keeps the raw image prompt; the wan call gets the
        # motion-composed one.
        assert captured["still_prompt"] == "cinematic illustration, data canyon"
        assert captured["prompt"] == (
            "cinematic illustration, data canyon. "
            "Camera and motion: slow dolly forward as streams flow"
        )
        # Portrait lane: the still AND the clip are requested vertical.
        assert captured["still_dims"] == (704, 1280)
        assert (captured["width"], captured["height"]) == (704, 1280)
        assert captured["fps"] == 24


class TestHeroReliability:
    """#899 reliability half: wan ready-wait before the hero phase + the
    frame-delta motion gate — 44 still-fallbacks in one week traced mostly to
    animate calls racing a cold-booting wan-server, and dead-motion clips
    sailing past single-frame vision QA."""

    def _hero_shot(self):
        return Shot(idx=0, duration_s=5.0, intent="hero", source="generative",
                    prompt="hero subject", motion="slow push", narration_offset_s=0.0)

    @pytest.mark.asyncio
    async def test_hero_phase_waits_for_wan(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        async def fake_still(*, prompt, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"png")
            return True

        async def fake_clip(*, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"mp4")
            return True, ""

        wait_mock = AsyncMock(return_value=True)
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=None, http_client_factory=None, pexels_key="",
            orientation="landscape", post_id="p-wait",
        )
        with patch.object(slr, "_render_image_gen_image", fake_still), \
             patch.object(slr, "_render_generative_clip", fake_clip), \
             patch.object(slr, "_wait_wan_ready", wait_mock), \
             patch.object(slr, "_clip_has_motion", AsyncMock(return_value=True)):
            await slr._render_pass([self._hero_shot()], render_kwargs=render_kwargs)
        wait_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_heroes_no_wait(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        async def fake_still(*, prompt, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"png")
            return True

        wait_mock = AsyncMock(return_value=True)
        shots = [Shot(idx=0, duration_s=5.0, intent="still", source="image_gen",
                      prompt="backdrop", narration_offset_s=0.0)]
        render_kwargs = dict(
            work_dir=tmp_path, image_gen_url="http://image-gen:9836",
            site_config=None, http_client_factory=None, pexels_key="",
            orientation="landscape", post_id="p-nowait",
        )
        with patch.object(slr, "_render_image_gen_image", fake_still), \
             patch.object(slr, "_wait_wan_ready", wait_mock):
            await slr._render_pass(shots, render_kwargs=render_kwargs)
        wait_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dead_motion_clip_falls_back_to_still_with_finding(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        still = tmp_path / "shot_00.png"
        still.write_bytes(b"png")

        async def fake_clip(*, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"mp4")
            return True, ""

        with patch.object(slr, "_render_generative_clip", fake_clip), \
             patch.object(slr, "_clip_has_motion", AsyncMock(return_value=False)), \
             patch.object(slr, "emit_finding") as fb:
            result = await slr._animate_hero(
                self._hero_shot(), still_path=str(still),
                site_config=None, orientation="landscape", post_id="p-dead",
            )
        assert result.success
        assert result.clip_path == str(still)  # fell back to its own still
        kinds = [c.kwargs.get("kind") for c in fb.call_args_list]
        assert "hero_motion_dead" in kinds

    @pytest.mark.asyncio
    async def test_live_motion_clip_kept(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        still = tmp_path / "shot_00.png"
        still.write_bytes(b"png")

        async def fake_clip(*, output_path, **kwargs):
            with open(output_path, "wb") as fh:
                fh.write(b"mp4")
            return True, ""

        with patch.object(slr, "_render_generative_clip", fake_clip), \
             patch.object(slr, "_clip_has_motion", AsyncMock(return_value=True)):
            result = await slr._animate_hero(
                self._hero_shot(), still_path=str(still),
                site_config=None, orientation="landscape", post_id="p-live",
            )
        assert result.clip_path.endswith(".mp4")

    @pytest.mark.asyncio
    async def test_motion_check_fails_open_on_probe_error(self, tmp_path):
        from services.video_renderers import shot_list_renderer as slr

        with patch.object(slr, "_probe_duration_s", AsyncMock(return_value=None)):
            assert await slr._clip_has_motion(
                "/nonexistent.mp4", min_delta=2.0, work_dir=tmp_path,
            ) is True
