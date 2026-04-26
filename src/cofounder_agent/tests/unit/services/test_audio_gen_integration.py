"""Integration tests for the AudioGenProvider opt-in wiring inside
``video_service`` and ``podcast_service`` (Glad-Labs/poindexter#125).

These exercise the helper functions that pull the audio-gen layer in
opportunistically — default-off, fail-soft. They do NOT touch a real
inference server; ``generate_audio`` is patched so the helpers can be
verified in isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.audio_gen_provider import AudioGenResult


def _stub_site_config(mapping: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = mapping or {}
    sc.get.side_effect = lambda k, d="": values.get(k, d)
    return sc


# ---------------------------------------------------------------------------
# video_service._maybe_generate_ambient_bed
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestVideoBedHook:
    async def test_returns_none_when_engine_disabled(self):
        from services.video_service import _maybe_generate_ambient_bed

        sc = _stub_site_config()  # no engine set — default off
        result = await _maybe_generate_ambient_bed(
            post_id="abc", title="Test", site_config=sc,
        )
        assert result is None

    async def test_returns_path_when_provider_succeeds(self, tmp_path):
        from services.video_service import _maybe_generate_ambient_bed

        sc = _stub_site_config({
            "audio_gen_engine": "stable-audio-open-1.0",
            "video_audio_bed_prompt": "warm bed",
        })
        fake = AudioGenResult(
            file_path=str(tmp_path / "abc-bed.wav"),
            duration_s=5.0,
            sample_rate=44100,
            kind="ambient",
            source="stable-audio-open-1.0",
        )
        with patch(
            "services.audio_gen_service.generate_audio",
            new=AsyncMock(return_value=fake),
        ) as gen:
            out = await _maybe_generate_ambient_bed(
                post_id="abc", title="Test", site_config=sc,
            )
        assert out == fake.file_path
        gen.assert_awaited_once()
        kwargs = gen.call_args.kwargs
        # Dispatcher hook gets the prompt + kind + site_config
        assert kwargs["kind"] == "ambient"
        assert kwargs["site_config"] is sc

    async def test_provider_returning_none_yields_none(self):
        from services.video_service import _maybe_generate_ambient_bed

        sc = _stub_site_config({"audio_gen_engine": "stable-audio-open-1.0"})
        with patch(
            "services.audio_gen_service.generate_audio",
            new=AsyncMock(return_value=None),
        ):
            out = await _maybe_generate_ambient_bed(
                post_id="abc", title="Test", site_config=sc,
            )
        assert out is None


# ---------------------------------------------------------------------------
# PodcastService._maybe_generate_stings
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestPodcastStingHook:
    async def test_no_op_when_site_config_missing(self, tmp_path):
        from services.podcast_service import PodcastService

        svc = PodcastService(output_dir=tmp_path, site_config=None)
        # Should not raise — just bail.
        await svc._maybe_generate_stings(post_id="abc", title="x")

    async def test_no_op_when_engine_disabled(self, tmp_path):
        from services.podcast_service import PodcastService

        sc = _stub_site_config()  # default-off
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

        with patch(
            "services.audio_gen_service.generate_audio",
            new=AsyncMock(),
        ) as gen:
            await svc._maybe_generate_stings(post_id="abc", title="x")
        gen.assert_not_called()

    async def test_calls_generate_for_intro_and_outro(self, tmp_path):
        from services.podcast_service import PodcastService

        sc = _stub_site_config({"audio_gen_engine": "stable-audio-open-1.0"})
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

        async def fake_generate(*args, **kwargs):
            return AudioGenResult(
                file_path=str(tmp_path / f"x-{kwargs['kind']}.wav"),
                duration_s=3.0,
                sample_rate=44100,
                kind=kwargs["kind"],
                source="stable-audio-open-1.0",
            )

        with patch(
            "services.audio_gen_service.generate_audio",
            side_effect=fake_generate,
        ) as gen:
            await svc._maybe_generate_stings(post_id="abc", title="My Episode")

        # Two calls — one intro, one outro
        kinds = [call.kwargs["kind"] for call in gen.call_args_list]
        assert sorted(kinds) == ["intro", "outro"]

    async def test_swallows_unexpected_exception(self, tmp_path):
        from services.podcast_service import PodcastService

        sc = _stub_site_config({"audio_gen_engine": "stable-audio-open-1.0"})
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

        with patch(
            "services.audio_gen_service.generate_audio",
            new=AsyncMock(side_effect=RuntimeError("provider blew up")),
        ):
            # Helper must never raise — sting failure shouldn't kill
            # an otherwise-successful podcast generation.
            await svc._maybe_generate_stings(post_id="abc", title="x")
