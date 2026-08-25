"""Unit tests for media.transcribe_narration — per-lane ASR atom (#676/#689).

``_transcribe_one`` runs one ASR pass over a single lane's narration → its SRT
path (or "" on any no-op/failure). ``run`` calls it per lane (long + short) and
surfaces ``long_caption_srt_path`` / ``short_caption_srt_path``.

Captions are best-effort — a caption failure must NEVER halt the graph. The
fidelity check is advisory: a low ASR-vs-script ratio emits a finding but does
not fail. We patch the caption-provider factory (``get_caption_provider``) +
``emit_finding`` where they are imported in the call-site module, per the
standard mocking discipline.
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
    """Patch the provider factory with one returning a stub whose transcribe()
    returns ``result``. The atom selects its provider via
    ``get_caption_provider(site_config)``, so we patch that seam (not a concrete
    class) — the configured engine is what production resolves."""
    provider = MagicMock()
    provider.transcribe = AsyncMock(return_value=result)
    factory = MagicMock(return_value=provider)
    return patch.object(
        media_transcribe_narration, "get_caption_provider", factory
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
        media_transcribe_narration, "get_caption_provider", factory
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

    async def _fake_one(*, audio_path, script, caption_text="", task_id, label, site_config):
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


@pytest.mark.asyncio
async def test_run_diffs_against_voiced_text_script_plus_cta(monkeypatch):
    """run() diffs the ASR against the VOICED text — labels stripped + the
    per-lane CTA outro — not the raw script. render_narration voices
    ``script + CTA``, so comparing against the bare script tanks fidelity
    (worst on the short lane, where the CTA dominates) — the false positive
    behind the caption_fidelity 0.00 alerts."""
    seen = {}

    async def _fake_one(*, audio_path, script, caption_text="", task_id, label, site_config):
        seen[label] = script
        return f"/tmp/{task_id}_{label}.srt"

    monkeypatch.setattr(media_transcribe_narration, "_transcribe_one", _fake_one)
    site_config = SimpleNamespace(
        get=lambda key, default=None: {
            "media.cta.video": "Subscribe to the channel.",
            "media.cta.video_short": "Follow for more.",
        }.get(key, default)
    )
    await transcribe_run({
        "task_id": "t1",
        "long_narration_audio_path": "/tmp/long.mp3",
        "short_narration_audio_path": "/tmp/short.mp3",
        "video_long_script": "Hook\nLong-form body.",
        "short_summary_script": "Short summary body.",
        "site_config": site_config,
    })
    # Label stripped, per-lane CTA appended — exactly what TTS voiced.
    assert seen["long"] == "Long-form body.\n\nSubscribe to the channel."
    assert seen["short"] == "Short summary body.\n\nFollow for more."


def test_atom_meta_shape():
    from modules.content.atoms.media_transcribe_narration import ATOM_META

    assert ATOM_META.name == "media.transcribe_narration"
    assert ATOM_META.requires == ("task_id",)
    assert set(ATOM_META.produces) == {"long_caption_srt_path", "short_caption_srt_path"}


# ---------------------------------------------------------------------------
# Script-alignment (2026-08-03): burned captions read the SCRIPT text on ASR
# timings — Whisper's homophones ("gait") and phonetic names ("PHY 4") never
# reach the video.
# ---------------------------------------------------------------------------


def _aligned_provider(monkeypatch, segments):
    from plugins.caption_provider import CaptionResult

    result = CaptionResult(
        success=True,
        segments=segments,
        srt_text="1\n00:00:00,000 --> 00:00:04,000\n" + segments[0].text + "\n",
        error=None,
    )

    class _P:
        async def transcribe(self, *, audio_path, task_id):
            return result

    monkeypatch.setattr(
        media_transcribe_narration, "get_caption_provider", lambda sc: _P(),
    )
    return result


@pytest.mark.asyncio
async def test_alignment_rewrites_captions_from_script(tmp_path, monkeypatch):
    from plugins.caption_provider import CaptionSegment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    _aligned_provider(monkeypatch, [
        CaptionSegment(start_s=0.0, end_s=4.0, text="the PHY 4 model guards the gait"),
    ])
    srt_path = await media_transcribe_narration._transcribe_one(
        audio_path=str(audio),
        script="the Phi-4 model guards the gate",
        caption_text="The Phi-4 model guards the gate.",
        task_id="t-align", label="long", site_config=None,
    )
    content = open(srt_path, encoding="utf-8").read()
    assert "Phi-4" in content
    assert "gate." in content
    assert "PHY" not in content
    assert "gait" not in content


@pytest.mark.asyncio
async def test_alignment_disabled_keeps_asr_text(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from plugins.caption_provider import CaptionSegment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    _aligned_provider(monkeypatch, [
        CaptionSegment(start_s=0.0, end_s=4.0, text="the PHY 4 model guards the gait"),
    ])
    sc = SimpleNamespace(
        get=lambda key, default=None: default,
        get_bool=lambda key, default=True: (
            False if key == "media.caption.script_alignment_enabled" else default
        ),
    )
    srt_path = await media_transcribe_narration._transcribe_one(
        audio_path=str(audio),
        script="the Phi-4 model guards the gate",
        caption_text="The Phi-4 model guards the gate.",
        task_id="t-alignoff", label="long", site_config=sc,
    )
    content = open(srt_path, encoding="utf-8").read()
    assert "PHY 4" in content  # legacy ASR text preserved


@pytest.mark.asyncio
async def test_alignment_low_match_falls_back_to_asr(tmp_path, monkeypatch):
    from plugins.caption_provider import CaptionSegment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    _aligned_provider(monkeypatch, [
        CaptionSegment(start_s=0.0, end_s=4.0, text="totally unrelated audio content here"),
    ])
    srt_path = await media_transcribe_narration._transcribe_one(
        audio_path=str(audio),
        script="quarterly numbers tell a different story",
        caption_text="Quarterly numbers tell a different story.",
        task_id="t-alignlow", label="long", site_config=None,
    )
    content = open(srt_path, encoding="utf-8").read()
    assert "unrelated audio" in content  # ASR kept — bad alignment never burned

# ---------------------------------------------------------------------------
# Display chunking (2026-08-24): sentence-sized whisper segments are re-cut
# into short cues before the SRT is written, so the burn never renders a
# frame-filling text wall on the 9:16 short.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_lane_chunks_long_segment_into_display_cues(tmp_path, monkeypatch):
    from plugins.caption_provider import CaptionSegment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    text = (
        "one two three four five six seven eight "
        "nine ten eleven twelve thirteen fourteen fifteen sixteen"
    )
    _aligned_provider(monkeypatch, [
        CaptionSegment(start_s=0.0, end_s=8.0, text=text),
    ])
    srt_path = await media_transcribe_narration._transcribe_one(
        audio_path=str(audio),
        script=text,
        caption_text="",  # no alignment — chunking applies to raw ASR text too
        task_id="t-chunk-short", label="short", site_config=None,
    )
    content = open(srt_path, encoding="utf-8").read()
    # 16 words at the short lane's default 5-word budget → 4 cues of 4 words.
    assert "\n4\n" in content
    assert "\n5\n" not in content
    cue_lines = [
        block.splitlines()[2]
        for block in content.strip().split("\n\n")
    ]
    assert all(len(line.split()) <= 5 for line in cue_lines)
    assert " ".join(cue_lines) == text


@pytest.mark.asyncio
async def test_long_lane_budget_tolerates_subtitle_sized_cues(tmp_path, monkeypatch):
    from plugins.caption_provider import CaptionSegment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    text = "one two three four five six seven eight nine ten eleven twelve"
    _aligned_provider(monkeypatch, [
        CaptionSegment(start_s=0.0, end_s=6.0, text=text),
    ])
    srt_path = await media_transcribe_narration._transcribe_one(
        audio_path=str(audio),
        script=text,
        caption_text="",
        task_id="t-chunk-long", label="long", site_config=None,
    )
    content = open(srt_path, encoding="utf-8").read()
    # 12 words fit the long lane's default 14-word budget — one cue, untouched.
    assert "\n2\n" not in content
    assert text in content


@pytest.mark.asyncio
async def test_chunking_disabled_via_zero_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from plugins.caption_provider import CaptionSegment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    text = (
        "one two three four five six seven eight "
        "nine ten eleven twelve thirteen fourteen fifteen sixteen"
    )
    _aligned_provider(monkeypatch, [
        CaptionSegment(start_s=0.0, end_s=8.0, text=text),
    ])
    sc = SimpleNamespace(
        get=lambda key, default=None: (
            0 if key == "media.caption.short_max_cue_words" else default
        ),
        get_bool=lambda key, default=True: default,
    )
    srt_path = await media_transcribe_narration._transcribe_one(
        audio_path=str(audio),
        script=text,
        caption_text="",
        task_id="t-chunk-off", label="short", site_config=sc,
    )
    content = open(srt_path, encoding="utf-8").read()
    assert "\n2\n" not in content  # single provider cue preserved verbatim


@pytest.mark.asyncio
async def test_chunking_composes_with_alignment(tmp_path, monkeypatch):
    from plugins.caption_provider import CaptionSegment

    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"x")
    # ASR heard phonetic junk; the aligner rewrites the text from the clean
    # script, THEN the splitter cuts the aligned text into display cues.
    _aligned_provider(monkeypatch, [
        CaptionSegment(
            start_s=0.0, end_s=8.0,
            text="the PHY 4 model guards the gait against errors in production loads today",
        ),
    ])
    script = "the Phi-4 model guards the gate against errors in production loads today"
    srt_path = await media_transcribe_narration._transcribe_one(
        audio_path=str(audio),
        script=script,
        caption_text=script,
        task_id="t-chunk-align", label="short", site_config=None,
    )
    content = open(srt_path, encoding="utf-8").read()
    assert "Phi-4" in content and "PHY" not in content
    assert "gate" in content and "gait" not in content
    assert "\n3\n" in content  # 13 words / 5-word budget → 3 cues
