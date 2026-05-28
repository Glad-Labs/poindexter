"""Tests for ``services/video_renderers/shot_list_renderer.py``.

Glad-Labs/glad-labs-stack#649 PR 2 — the director-driven renderer that
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
        """The SDXL render must POST {'prompt': ..., 'negative_prompt': ...,
        'steps': 4, 'guidance_scale': 1.0} to ``/generate`` — this is the
        correct SDXL server shape (Wan21's wrong-body 422 issue should
        never resurface here)."""
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

    @pytest.mark.asyncio
    async def test_wan21_calls_provider_with_prompt_only(self, tmp_path):
        """The wan21 source must route through ``Wan21Provider.fetch``
        which sends the correct ``{'prompt': ..., 'duration_s': ..., ...}``
        request body to the wan-server. This pins the 422 fix: the body
        MUST NOT contain image_paths/audio_path/ken_burns."""
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

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_fetch):
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

        # The wan21 provider config must carry duration + output_path.
        # It must NOT carry image_paths / audio_path / ken_burns —
        # that was the 2026-05-26 422-causing body.
        assert "output_path" in captured_config
        assert "duration_s" in captured_config
        assert captured_config["duration_s"] <= 6  # Wan2.1 artifacts beyond
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

        from services.video_providers import wan2_1 as wan21_mod

        captured_config: dict = {}

        async def _fake_fetch(self, prompt, config):
            captured_config.update(config)
            with open(config["output_path"], "wb") as f:
                f.write(b"fake")
            return [MagicMock(file_path=config["output_path"])]

        with patch.object(wan21_mod.Wan21Provider, "fetch", _fake_fetch):
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
        from services.video_renderers import shot_list_renderer as renderer_mod

        captured_request = {}

        class _MockCompositor:
            def __init__(self, site_config=None):
                self._sc = site_config

            async def compose(self, request, **kwargs):
                captured_request["scenes"] = list(request.scenes)
                captured_request["soundtrack_path"] = request.soundtrack_path
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
        # Only the first scene carries narration_path so the soundtrack
        # plays as one stream over the concat.
        assert scenes[0].narration_path == audio_path
        assert scenes[1].narration_path is None
        assert scenes[2].narration_path is None
        # The full audio is also passed as the soundtrack.
        assert captured_request["soundtrack_path"] == audio_path
