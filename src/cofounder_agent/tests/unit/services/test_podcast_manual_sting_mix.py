"""Intro/outro sting on the MANUAL-regenerate path (``generate_episode``).

The sting used to be mixed only by the Stage-3 ``podcast.render`` atom, so
regenerating an episode from the console / API silently produced a dry cut —
the same parity gap ``_append_podcast_cta`` closed for the per-medium CTA.

Two invariants earn their keep here:

* the mix runs BEFORE ``_record_episode_asset``, because that stamps
  ``media_assets.duration_ms`` / ``file_size_bytes`` straight off the returned
  result and the mixed cut is longer and larger than the dry one;
* every failure mode keeps the dry episode and still reports success — losing
  polish must never lose the episode.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from services.podcast_service import EpisodeResult, PodcastService


def _sc(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "podcast_tts_engine": "edge_tts",
        "podcast_name": "Test Pod",
        "site_domain": "test.example",
        "media.cta.podcast": "",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return SimpleNamespace(
        get=lambda k, d="": base.get(k, d if d is not None else ""),
        get_int=lambda _k, d=0: d,
        get_float=lambda _k, d=0.0: d,
        get_bool=lambda _k, d=False: d,
        get_secret=AsyncMock(return_value=""),
        _pool=None,
    )


def _dry_render(marker: bytes = b"dry-mp3"):
    """Stand-in for the TTS pass — writes a dry episode at output_path."""
    async def _gen(script: str, voice: str, output_path: Path) -> EpisodeResult:
        output_path.write_bytes(marker * 500)
        return EpisodeResult(
            success=True,
            file_path=str(output_path),
            duration_seconds=300,
            file_size_bytes=output_path.stat().st_size,
        )
    return _gen


async def _run(svc: PodcastService, **kw: Any) -> EpisodeResult:
    return await svc.generate_episode(
        "post-1", "The Title", "Body content long enough to matter." * 20, **kw,
    )


@pytest.mark.unit
class TestManualRegenerateMixesSting:
    @pytest.mark.asyncio
    async def test_curated_theme_is_mixed_in(self, tmp_path, monkeypatch):
        theme = tmp_path / "theme.wav"
        theme.write_bytes(b"RIFFfake")
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(podcast_sting_file_path=str(theme)),
        )
        seen: dict[str, Any] = {}

        async def fake_mix(narration, sting, *, site_config=None, task_id=None):
            seen["narration"] = narration
            seen["sting"] = sting
            out = tmp_path / "mixed.mp3"
            out.write_bytes(b"MIXED" * 900)
            return str(out)

        async def fake_probe(path):
            return 311.5

        import services.podcast_sting_mixer as mixer
        monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
        monkeypatch.setattr(mixer, "probe_duration_s", fake_probe)

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.success
        # The curated theme is resolved with NO per-episode snapshot: this path
        # has no Stage-1 metadata to inherit one from.
        assert seen["sting"] == str(theme)
        episode = svc.get_episode_path("post-1")
        assert seen["narration"] == str(episode)
        # The mixed bytes replaced the dry episode in place.
        assert episode.read_bytes().startswith(b"MIXED")
        assert result.file_path == str(episode)

    @pytest.mark.asyncio
    async def test_result_carries_the_mixed_duration_and_size(
        self, tmp_path, monkeypatch,
    ):
        """_record_episode_asset stamps media_assets straight off these."""
        theme = tmp_path / "theme.wav"
        theme.write_bytes(b"RIFFfake")
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(podcast_sting_file_path=str(theme)),
        )

        async def fake_mix(*a, **k):
            out = tmp_path / "mixed.mp3"
            out.write_bytes(b"M" * 99_999)
            return str(out)

        async def fake_probe(path):
            return 311.5

        import services.podcast_sting_mixer as mixer
        monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
        monkeypatch.setattr(mixer, "probe_duration_s", fake_probe)

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.duration_seconds == 311      # probed, not the 300 estimate
        assert result.file_size_bytes == 99_999    # mixed size, not the dry one

    @pytest.mark.asyncio
    async def test_mix_precedes_the_media_assets_record(self, tmp_path, monkeypatch):
        """Ordering is the whole point — a row recorded before the mix would
        carry the dry cut's duration forever."""
        theme = tmp_path / "theme.wav"
        theme.write_bytes(b"RIFFfake")
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(podcast_sting_file_path=str(theme)),
        )
        order: list[str] = []

        async def fake_mix(*a, **k):
            order.append("mix")
            out = tmp_path / "mixed.mp3"
            out.write_bytes(b"M" * 4096)
            return str(out)

        async def fake_probe(path):
            return 311.5

        async def fake_record(**kwargs):
            order.append("record")
            order.append(f"duration={kwargs['duration_ms']}")
            return "asset-uuid"

        import services.podcast_sting_mixer as mixer
        monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
        monkeypatch.setattr(mixer, "probe_duration_s", fake_probe)

        with patch.object(svc, "_generate_with_voice", _dry_render()), \
             patch("services.media_asset_recorder.record_media_asset", fake_record):
            await _run(svc)

        assert order[:2] == ["mix", "record"]
        # 311.5s probed → EpisodeResult.duration_seconds is an int, so 311s.
        # The point is that it is the MIXED duration, not the 300s dry estimate.
        assert "duration=311000" in order


@pytest.mark.unit
class TestFailSoft:
    @pytest.mark.asyncio
    async def test_no_theme_configured_ships_dry_without_a_finding(
        self, tmp_path, monkeypatch,
    ):
        """OSS default — nothing pinned, so silence is correct, not a downgrade."""
        svc = PodcastService(output_dir=tmp_path, site_config=_sc())
        findings: list[dict[str, Any]] = []
        import utils.findings as uf
        monkeypatch.setattr(uf, "emit_finding", lambda **kw: findings.append(kw))

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.success
        assert svc.get_episode_path("post-1").read_bytes().startswith(b"dry-mp3")
        assert findings == []

    @pytest.mark.asyncio
    async def test_missing_theme_file_flags_the_dry_cut(self, tmp_path, monkeypatch):
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(podcast_sting_file_path=str(tmp_path / "typo.wav")),
        )
        findings: list[dict[str, Any]] = []
        import utils.findings as uf
        monkeypatch.setattr(uf, "emit_finding", lambda **kw: findings.append(kw))

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.success
        assert findings and findings[0]["kind"] == "podcast_sting_missing"
        assert "typo.wav" in findings[0]["body"]

    @pytest.mark.asyncio
    async def test_mix_failure_keeps_the_dry_episode_and_flags(
        self, tmp_path, monkeypatch,
    ):
        theme = tmp_path / "theme.wav"
        theme.write_bytes(b"RIFFfake")
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(podcast_sting_file_path=str(theme)),
        )
        findings: list[dict[str, Any]] = []

        async def fake_mix(*a, **k):
            return None

        import services.podcast_sting_mixer as mixer
        import utils.findings as uf
        monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
        monkeypatch.setattr(uf, "emit_finding", lambda **kw: findings.append(kw))

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.success
        assert result.duration_seconds == 300
        assert svc.get_episode_path("post-1").read_bytes().startswith(b"dry-mp3")
        assert findings and findings[0]["kind"] == "podcast_sting_mix_failed"

    @pytest.mark.asyncio
    async def test_install_failure_keeps_the_dry_episode(self, tmp_path, monkeypatch):
        """shutil.move can fail (permissions, vanished temp). The original is
        untouched by the mixer, so the dry cut must survive."""
        theme = tmp_path / "theme.wav"
        theme.write_bytes(b"RIFFfake")
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(podcast_sting_file_path=str(theme)),
        )

        async def fake_mix(*a, **k):
            out = tmp_path / "mixed.mp3"
            out.write_bytes(b"MIXED" * 100)
            return str(out)

        def boom(src, dst):
            raise OSError("cross-device link not permitted")

        import shutil as _shutil

        import services.podcast_sting_mixer as mixer
        monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
        monkeypatch.setattr(_shutil, "move", boom)

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.success
        assert result.duration_seconds == 300
        assert svc.get_episode_path("post-1").read_bytes().startswith(b"dry-mp3")

    @pytest.mark.asyncio
    async def test_unprobeable_mix_falls_back_to_the_estimate(
        self, tmp_path, monkeypatch,
    ):
        theme = tmp_path / "theme.wav"
        theme.write_bytes(b"RIFFfake")
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(podcast_sting_file_path=str(theme)),
        )

        async def fake_mix(*a, **k):
            out = tmp_path / "mixed.mp3"
            out.write_bytes(b"M" * 2048)
            return str(out)

        async def no_duration(path):
            return None

        import services.podcast_sting_mixer as mixer
        monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)
        monkeypatch.setattr(mixer, "probe_duration_s", no_duration)

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.success
        assert result.duration_seconds == 300  # never 0 — feedback_no_dummy_data
        assert result.file_size_bytes == 2048

    @pytest.mark.asyncio
    async def test_disabled_switch_skips_the_mix(self, tmp_path, monkeypatch):
        theme = tmp_path / "theme.wav"
        theme.write_bytes(b"RIFFfake")
        svc = PodcastService(
            output_dir=tmp_path,
            site_config=_sc(
                podcast_sting_file_path=str(theme),
                podcast_sting_mix_enabled="false",
            ),
        )
        calls: list[Any] = []

        async def fake_mix(*a, **k):
            calls.append(a)
            return None

        import services.podcast_sting_mixer as mixer
        monkeypatch.setattr(mixer, "mix_intro_outro", fake_mix)

        with patch.object(svc, "_generate_with_voice", _dry_render()):
            result = await _run(svc)

        assert result.success and calls == []
        assert svc.get_episode_path("post-1").read_bytes().startswith(b"dry-mp3")
