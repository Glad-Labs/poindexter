"""Unit tests for ``PodcastService.synthesize`` — the pure render core extracted
for the Stage-3 ``podcast.render`` atom (#689 deviation).

``synthesize`` runs the voice-rotation loop over ``_generate_with_voice`` and
returns ``(path, duration_seconds)`` for an already-built script, with no
post_id-keyed naming, media_assets recording, or narration-sibling side effects
(those stay in the legacy ``generate_episode``).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.podcast_service import EpisodeResult, PodcastService
from services.site_config import SiteConfig

_SCRIPT = "This is a long enough podcast narration body to render into audio."


def _svc(tmp_path: Path) -> PodcastService:
    sc = SiteConfig(initial_config={"podcast_name": "Test Show", "site_domain": "test.io"})
    return PodcastService(output_dir=tmp_path, site_config=sc)


@pytest.mark.asyncio
async def test_synthesize_returns_path_and_duration(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    out = tmp_path / "render.mp3"

    async def fake_gen(script: str, voice: str, output_path: Path) -> EpisodeResult:
        Path(output_path).write_bytes(b"audio-bytes")
        return EpisodeResult(
            success=True, file_path=str(output_path),
            duration_seconds=42, file_size_bytes=11,
        )

    with patch.object(svc, "_generate_with_voice", side_effect=fake_gen):
        path, duration = await svc.synthesize(_SCRIPT, output_path=out, key="t1")

    assert path == str(out)
    assert duration == 42


@pytest.mark.asyncio
async def test_synthesize_rotates_to_next_voice_on_failure(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    out = tmp_path / "render.mp3"
    calls: list[str] = []

    async def fake_gen(script: str, voice: str, output_path: Path) -> EpisodeResult:
        calls.append(voice)
        if len(calls) == 1:
            return EpisodeResult(success=False, error="first voice down")
        Path(output_path).write_bytes(b"audio")
        return EpisodeResult(success=True, file_path=str(output_path), duration_seconds=10)

    with patch.object(svc, "_generate_with_voice", side_effect=fake_gen):
        path, duration = await svc.synthesize(_SCRIPT, output_path=out, key="t1")

    assert len(calls) == 2  # rotated past the failing voice
    assert path == str(out)


@pytest.mark.asyncio
async def test_synthesize_raises_when_all_voices_fail(tmp_path: Path) -> None:
    svc = _svc(tmp_path)

    async def always_fail(script: str, voice: str, output_path: Path) -> EpisodeResult:
        return EpisodeResult(success=False, error="tts down")

    with patch.object(svc, "_generate_with_voice", side_effect=always_fail):
        with pytest.raises(RuntimeError):
            await svc.synthesize(_SCRIPT, output_path=tmp_path / "x.mp3", key="t1")
