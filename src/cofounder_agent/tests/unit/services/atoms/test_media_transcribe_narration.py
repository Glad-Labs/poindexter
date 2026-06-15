"""Unit tests for media.transcribe_narration — per-lane ASR atom (#676/#689).

``_transcribe_one`` runs one ASR pass over a single lane's narration → its SRT
path (or "" on any no-op/failure). ``run`` calls it per lane (long + short) and
surfaces ``long_caption_srt_path`` / ``short_caption_srt_path``.

Captions are best-effort — a caption failure must NEVER halt the graph. The
fidelity check is advisory: a low ASR-vs-script ratio emits a finding but does
not fail. We patch the caption provider class + ``emit_finding`` where they are
imported in the call-site module, per the standard mocking discipline.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.atoms import media_transcribe_narration
from modules.content.atoms.media_transcribe_narration import _transcribe_one
from modules.content.atoms.media_transcribe_narration import run as transcribe_run
from plugins.caption_provider import CaptionResult, CaptionSegment


def _caption_result(*, success=True, srt_text="1\n00:00:00,000 --> 00:00:02,000\nhi\n", segments=None):
    if segments is None:
        segments = [CaptionSegment(start_s=0.0, end_s=2.0, text="hello world")]
    return CaptionResult(success=success, segments=segments, srt_text=srt_text)


def _patch_provider(result):
    """Swap the provider class with one whose transcribe() returns ``result``."""
    provider = MagicMock()
    provider.transcribe = AsyncMock(return_value=result)
    factory = MagicMock(return_value=provider)
    return patch.object(
        media_transcribe_narration, "WhisperLocalCaptionProvider", factory
    ), provider, factory


# ---------------------------------------------------------------------------
# _transcribe_one — single-lane ASR logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcribe_one_writes_lane_srt():
    result = _caption_result(
        segments=[
            CaptionSegment(start_s=0.0, end_s=1.0, text="hello"),
            CaptionSegment(start_s=1.0, end_s=2.0, text="world"),
        ],
        srt_text="SRT-DOC",
    )
    ctx, provider, _ = _patch_provider(result)
    with ctx:
        srt = await _transcribe_one(
            audio_path="/tmp/narration.wav", script="", task_id="t-success",
            label="long", site_config=None,
        )
    assert srt and os.path.exists(srt)
    assert srt.endswith("captions_t-success_long.srt")
    with open(srt, encoding="utf-8") as f:
        assert f.read() == "SRT-DOC"
    assert provider.transcribe.await_args.kwargs["audio_path"] == "/tmp/narration.wav"


@pytest.mark.asyncio
async def test_transcribe_one_no_audio_noop():
    ctx, provider, factory = _patch_provider(_caption_result())
    with ctx:
        srt = await _transcribe_one(
            audio_path="", script="", task_id="t-noaudio", label="long",
            site_config=None,
        )
    assert srt == ""
    factory.assert_not_called()
    provider.transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcribe_one_provider_unavailable_returns_empty():
    result = _caption_result(success=False, srt_text="", segments=[])
    ctx, _, _ = _patch_provider(result)
    with ctx:
        srt = await _transcribe_one(
            audio_path="/tmp/narration.wav", script="", task_id="t-fail",
            label="short", site_config=None,
        )
    assert srt == ""


@pytest.mark.asyncio
async def test_transcribe_one_raises_emits_caption_failed_no_raise():
    provider = MagicMock()
    provider.transcribe = AsyncMock(side_effect=RuntimeError("boom"))
    factory = MagicMock(return_value=provider)
    mock_emit = MagicMock()
    with patch.object(
        media_transcribe_narration, "WhisperLocalCaptionProvider", factory
    ), patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        srt = await _transcribe_one(
            audio_path="/tmp/narration.wav", script="", task_id="t-raise",
            label="long", site_config=None,
        )
    assert srt == ""
    assert mock_emit.call_count == 1
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["kind"] == "caption_failed"
    assert kwargs["severity"] == "warn"
    assert kwargs["dedup_key"] == "caption_failed:t-raise:long"


@pytest.mark.asyncio
async def test_transcribe_one_fidelity_below_threshold_emits_finding():
    script = " ".join(["the quick brown fox jumps over the lazy dog"] * 5)
    asr = "the quick brown fox"
    result = _caption_result(
        segments=[CaptionSegment(start_s=0.0, end_s=1.0, text=asr)], srt_text="SRT",
    )
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        srt = await _transcribe_one(
            audio_path="/tmp/narration.wav", script=script, task_id="t-lowfid",
            label="long", site_config=None,
        )
    assert srt  # still produced (advisory only)
    assert mock_emit.call_count == 1
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["kind"] == "caption_fidelity"
    assert kwargs["extra"]["ratio"] < 0.80
    assert kwargs["extra"]["threshold"] == 0.80
    assert kwargs["extra"]["lane"] == "long"


@pytest.mark.asyncio
async def test_transcribe_one_fidelity_above_threshold_no_finding():
    script = "the quick brown fox jumps over the lazy dog"
    result = _caption_result(
        segments=[CaptionSegment(start_s=0.0, end_s=1.0, text=script)], srt_text="SRT",
    )
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        await _transcribe_one(
            audio_path="/tmp/narration.wav", script=script, task_id="t-hifid",
            label="long", site_config=None,
        )
    mock_emit.assert_not_called()


@pytest.mark.asyncio
async def test_transcribe_one_no_script_skips_fidelity():
    result = _caption_result(srt_text="SRT")
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        srt = await _transcribe_one(
            audio_path="/tmp/narration.wav", script="", task_id="t-noscript",
            label="short", site_config=None,
        )
    assert srt
    mock_emit.assert_not_called()


@pytest.mark.asyncio
async def test_transcribe_one_threshold_from_site_config():
    script = "the quick brown fox jumps over the lazy dog"
    result = _caption_result(
        segments=[CaptionSegment(start_s=0.0, end_s=1.0, text=script)], srt_text="SRT",
    )
    ctx, _, _ = _patch_provider(result)
    mock_emit = MagicMock()
    site_config = SimpleNamespace(
        get=lambda key, default=None: "1.01"
        if key == "media.caption.fidelity_min_ratio" else default
    )
    with ctx, patch.object(media_transcribe_narration, "emit_finding", mock_emit):
        await _transcribe_one(
            audio_path="/tmp/narration.wav", script=script, task_id="t-cfg",
            label="long", site_config=site_config,
        )
    assert mock_emit.call_count == 1
    assert mock_emit.call_args.kwargs["extra"]["threshold"] == pytest.approx(1.01)


# ---------------------------------------------------------------------------
# run() — dual-lane orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_transcribes_both_lanes(monkeypatch):
    seen = []

    async def _fake_one(*, audio_path, script, task_id, label, site_config):
        seen.append((label, audio_path, script))
        return f"/tmp/{task_id}_{label}.srt"

    monkeypatch.setattr(media_transcribe_narration, "_transcribe_one", _fake_one)
    out = await transcribe_run({
        "task_id": "t1",
        "long_narration_audio_path": "/tmp/long.mp3",
        "short_narration_audio_path": "/tmp/short.mp3",
        "video_long_script": "long script", "short_summary_script": "short script",
    })
    assert out["long_caption_srt_path"] == "/tmp/t1_long.srt"
    assert out["short_caption_srt_path"] == "/tmp/t1_short.srt"
    by_label = {lbl: (a, s) for (lbl, a, s) in seen}
    assert by_label["long"] == ("/tmp/long.mp3", "long script")
    assert by_label["short"] == ("/tmp/short.mp3", "short script")


@pytest.mark.asyncio
async def test_run_failsoft_returns_empty_both_lanes():
    """No narration audio in either lane → both caption paths empty, no raise."""
    out = await transcribe_run({"task_id": "t-empty"})
    assert out == {"long_caption_srt_path": "", "short_caption_srt_path": ""}


def test_atom_meta_shape():
    from modules.content.atoms.media_transcribe_narration import ATOM_META

    assert ATOM_META.name == "media.transcribe_narration"
    assert ATOM_META.requires == ("task_id",)
    assert set(ATOM_META.produces) == {"long_caption_srt_path", "short_caption_srt_path"}
