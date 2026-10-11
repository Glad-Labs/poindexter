"""The chatterbox config builder must carry each ``plugin.tts_provider.chatterbox.*``
pacing key to the provider.

The provider forwards a key only when it is set, so a key this builder forgets
reaches the sidecar as "unset" and the sidecar's built-in default wins: the
setting reads as live and does nothing. These keys had no test at this hop.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.plugins.tts_provider import TTSResult
from poindexter.services.podcast_service import PodcastService
from poindexter.services.site_config import SiteConfig

_KEYS = {
    "plugin.tts_provider.chatterbox.chunk_gap_seconds": ("chunk_gap_seconds", "0.40"),
    "plugin.tts_provider.chatterbox.truncation_max_rate_ratio": ("truncation_max_rate_ratio", "1.5"),
    "plugin.tts_provider.chatterbox.truncation_retries": ("truncation_retries", "2"),
    "plugin.tts_provider.chatterbox.max_internal_silence_seconds": (
        "max_internal_silence_seconds", "0.55",
    ),
    "plugin.tts_provider.chatterbox.fold_name_case": ("fold_name_case", "true"),
    "plugin.tts_provider.chatterbox.keep_case_words": ("keep_case_words", "August,Polish"),
}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pacing_settings_reach_the_provider_config(tmp_path: Path):
    sc = SiteConfig(initial_config={k: v for k, (_, v) in _KEYS.items()})
    svc = PodcastService(output_dir=tmp_path, site_config=sc)
    out = tmp_path / "ep.mp3"
    result = TTSResult(audio_path=out, duration_seconds=1, voice="v", sample_rate=24000,
                       audio_format="mp3", file_size_bytes=1)
    with patch(
        "poindexter.services.tts_providers.chatterbox.ChatterboxTTSProvider.synthesize",
        new=AsyncMock(return_value=result),
    ) as synth:
        episode = await svc._generate_with_chatterbox("hello", "v", out)
    assert episode.success
    config = synth.await_args.kwargs["config"]
    for setting, (field, value) in _KEYS.items():
        assert config[field] == value, f"{setting} did not reach config[{field!r}]"
