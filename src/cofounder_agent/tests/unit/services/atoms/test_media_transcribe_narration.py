"""Unit tests for the media.transcribe_narration Stage-2 ASR atom (Plan 5, #676).

One ASR pass over the podcast narration BEFORE the renders: it produces a
``caption_srt_path`` (so both renders burn the same captions in) and an
``asr_transcript`` for a fidelity check against the source ``podcast_script``.

Captions are best-effort — a caption failure (whisper not installed, audio
missing, provider exception) must NEVER halt the graph. The fidelity check is
advisory: a low ASR-vs-script ratio emits a finding but does not fail.

We patch the caption provider class + ``emit_finding`` where they are imported
in ``media_transcribe_narration`` (the call-site module), per the standard
mocking discipline mirrored from test_media_load_scripts / test_media_render_video.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.atoms import media_transcribe_narration
from modules.content.atoms.media_transcribe_narration import run as transcribe_run
from plugins.caption_provider import CaptionResult, CaptionSegment


def _caption_result(*, success=True, srt_text="1\n00:00:00,000 --> 00:00:02,000\nhello world\n", segments=None):
    if segments is None:
        segments = [
            CaptionSegment(start_s=0.0, end_s=2.0, text="hello world"),
        ]
    return CaptionResult(success=success, segments=segments, srt_text=srt_text)


def _patch_provider(result):
    """Return a patch context that swaps the provider class with one whose
    transcribe() returns ``result``. The atom instantiates
    WhisperLocalCaptionProvider(site_config=...) directly (no resolver in
    caption_providers/__init__)."""
    provider = MagicMock()
    provider.transcribe = AsyncMock(return_value=result)
    factory = MagicMock(return_value=provider)
    return patch.object(
        media_transcribe_narration, "WhisperLocalCaptionProvider", factory
    ), provider, factory


@pytest.mark.asyncio
async def test_success_writes_srt_and_returns_transcript(tmp_path):
    """A successful transcription writes the SRT to a temp file, sets
    caption_srt_path, and joins the segments into asr_transcript."""
    result = _caption_result(
        segments=[
            CaptionSegment(start_s=0.0, end_s=1.0, text="hello"),
            CaptionSegment(start_s=1.0, end_s=2.0, text="world"),
        ],
        srt_text="SRT-DOC",
    )
    ctx, provider, _ = _patch_provider(result)
    state = {"task_id": "t-success", "podcast_audio_path": "/tmp/narration.wav"}
    with ctx:
        out = await transcribe_run(state)

    assert out["caption_srt_path"]
    assert os.path.exists(out["caption_srt_path"])
    with open(out["caption_srt_path"], encoding="utf-8") as f:
        assert f.read() == "SRT-DOC"
    assert out["asr_transcript"] == "hello world"
    provider.transcribe.assert_awaited_once()
    # The narration audio path was passed through.
    assert provider.transcribe.await_args.kwargs["audio_path"] == "/tmp/narration.wav"


@pytest.mark.asyncio
async def test_no_audio_is_graceful_noop_provider_not_called():
    """No podcast_audio_path → empty no-op; the provider is NOT constructed
    or called (nothing to transcribe)."""
    ctx, provider, factory = _patch_provider(_caption_result())
    state = {"task_id": "t-noaudio"}
    with ctx:
        out = await transcribe_run(state)

    assert out == {"caption_srt_path": "", "asr_transcript": ""}
    factory.assert_not_called()
    provider.transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_returns_empty_caption_no_raise():
    """provider.transcribe returns success=False (e.g. whisper not installed)
    → empty caption path, no raise. asr_transcript falls back to joined
    segments (here empty)."""
    result = _caption_result(success=False, srt_text="", segments=[])
    ctx, provider, _ = _patch_provider(result)
    state = {"task_id": "t-fail", "podcast_audio_path": "/tmp/narration.wav"}
    with ctx:
        out = await transcribe_run(state)

    assert out["caption_srt_path"] == ""
    assert out["asr_transcript"] == ""


@pytest.mark.asyncio
async def test_provider_raises_emits_caption_failed_finding_no_raise():
    """An exception out of transcribe() must be swallowed: emit a
    caption_failed finding and return empty keys (a caption failure must
    not halt the graph)."""
    provider = MagicMock()
    provider.transcribe = AsyncMock(side_effect=RuntimeError("boom"))
    factory = MagicMock(return_value=provider)
    mock_emit = MagicMock()
    state = {"task_id": "t-raise", "podcast_audio_path": "/tmp/narration.wav"}
    with patch.object(
        media_transcribe_narration, "WhisperLocalCaptionProvider", factory
    ), patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        out = await transcribe_run(state)

    assert out == {"caption_srt_path": "", "asr_transcript": ""}
    assert mock_emit.call_count == 1
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["kind"] == "caption_failed"
    assert kwargs["severity"] == "warn"


@pytest.mark.asyncio
async def test_fidelity_below_threshold_emits_finding():
    """ASR transcript diverges sharply from the source script → caption_fidelity
    finding (catches TTS dropouts / truncation). Advisory only — caption path
    is still returned."""
    # Script is long; ASR captured only a fragment → low ratio.
    script = " ".join(["the quick brown fox jumps over the lazy dog"] * 5)
    asr = "the quick brown fox"
    result = _caption_result(
        segments=[CaptionSegment(start_s=0.0, end_s=1.0, text=asr)],
        srt_text="SRT",
    )
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    state = {
        "task_id": "t-lowfid",
        "podcast_audio_path": "/tmp/narration.wav",
        "podcast_script": script,
    }
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        out = await transcribe_run(state)

    assert out["caption_srt_path"]  # still produced
    assert mock_emit.call_count == 1
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["kind"] == "caption_fidelity"
    assert kwargs["severity"] == "warn"
    assert kwargs["extra"]["ratio"] < 0.80
    assert kwargs["extra"]["threshold"] == 0.80


@pytest.mark.asyncio
async def test_fidelity_above_threshold_emits_no_finding():
    """ASR transcript closely matches the source script → no finding."""
    script = "the quick brown fox jumps over the lazy dog"
    asr = "the quick brown fox jumps over the lazy dog"
    result = _caption_result(
        segments=[CaptionSegment(start_s=0.0, end_s=1.0, text=asr)],
        srt_text="SRT",
    )
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    state = {
        "task_id": "t-hifid",
        "podcast_audio_path": "/tmp/narration.wav",
        "podcast_script": script,
    }
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        await transcribe_run(state)

    mock_emit.assert_not_called()


@pytest.mark.asyncio
async def test_no_script_skips_fidelity_check():
    """No podcast_script in state → fidelity check is skipped (nothing to
    compare against), no finding, caption still produced."""
    result = _caption_result(srt_text="SRT")
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    state = {"task_id": "t-noscript", "podcast_audio_path": "/tmp/narration.wav"}
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        out = await transcribe_run(state)

    assert out["caption_srt_path"]
    mock_emit.assert_not_called()


@pytest.mark.asyncio
async def test_fidelity_threshold_read_from_site_config():
    """The fidelity threshold is DB-configurable via
    media.caption.fidelity_min_ratio on site_config."""
    script = "the quick brown fox jumps over the lazy dog"
    asr = "the quick brown fox jumps over the lazy dog"
    result = _caption_result(
        segments=[CaptionSegment(start_s=0.0, end_s=1.0, text=asr)],
        srt_text="SRT",
    )
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    # Threshold cranked to 1.01 — even a near-perfect match falls below it,
    # so the finding fires. Proves the value comes from site_config.
    site_config = SimpleNamespace(
        get=lambda key, default=None: "1.01"
        if key == "media.caption.fidelity_min_ratio"
        else default
    )
    state = {
        "task_id": "t-cfg",
        "podcast_audio_path": "/tmp/narration.wav",
        "podcast_script": script,
        "site_config": site_config,
    }
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        await transcribe_run(state)

    assert mock_emit.call_count == 1
    assert mock_emit.call_args.kwargs["extra"]["threshold"] == pytest.approx(1.01)


def test_atom_meta_shape():
    from modules.content.atoms.media_transcribe_narration import ATOM_META

    assert ATOM_META.name == "media.transcribe_narration"
    assert ATOM_META.requires == ("task_id",)
    assert set(ATOM_META.produces) == {"caption_srt_path", "asr_transcript"}
