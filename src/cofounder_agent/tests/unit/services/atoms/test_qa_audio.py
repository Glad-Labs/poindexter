"""Unit tests for the qa.audio atom (#1193, Phase 2).

Covers:
- Skips cleanly when podcast_audio_path is absent or the file doesn't exist
- Silence check: flags long silence, passes short silence, records unavailable when ffmpeg absent
- Volume check: detects clipping, too-quiet, clean audio; unavailable when ffmpeg absent
- Duration check: flags too-short, too-long, ok, no-script-skip, unavailable when ffprobe absent
- Emitting the right finding kinds
- The atom never raises even if every helper blows up
"""

from unittest.mock import AsyncMock, patch

import pytest

import modules.content.atoms.qa_audio as qa

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _state(**kwargs):
    base = {
        "task_id": "task-test-123",
        "podcast_audio_path": "/fake/narration.wav",
        "podcast_script": "hello world this is a test narration with ten words",
    }
    base.update(kwargs)
    return base


async def _run(state):
    """Drive ``_qa_one`` (single-lane) the way the old ``run()`` behaved.

    The legacy checks exercise one lane's QA logic over ``podcast_audio_path`` /
    ``podcast_script``; ``_qa_one`` returns the bare per-check result dict, which
    we wrap in ``audio_qa_result`` so the existing ``out["audio_qa_result"][...]``
    assertions stay intact. Dual-lane orchestration is covered separately below.
    """
    return {"audio_qa_result": await qa._qa_one(
        audio_path=(state.get("podcast_audio_path") or "").strip(),
        script=state.get("podcast_script") or "",
        task_id=state.get("task_id"),
        label="long",
        site_config=state.get("site_config"),
    )}


# ---------------------------------------------------------------------------
# Basic skip cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_audio_path_returns_empty():
    result = await _run({"task_id": "t1"})
    assert result == {"audio_qa_result": {}}


@pytest.mark.asyncio
async def test_audio_file_missing_returns_empty(tmp_path):
    gone = str(tmp_path / "gone.wav")
    result = await _run({"task_id": "t1", "podcast_audio_path": gone})
    assert result == {"audio_qa_result": {}}


# ---------------------------------------------------------------------------
# Check D: Silence detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_silence_check_ok_when_no_long_silences(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)  # fake wav header

    short_silences = [{"start_s": 1.0, "end_s": 2.5, "duration_s": 1.5}]
    with patch.object(qa, "_detect_silences", AsyncMock(return_value=short_silences)):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=None)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=None)):
                out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["silence_check"] == "ok"
    assert out["audio_qa_result"].get("silence_long_segments") == []


@pytest.mark.asyncio
async def test_silence_check_warns_on_long_silence(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)

    long_silences = [{"start_s": 5.0, "end_s": 10.0, "duration_s": 5.0}]
    emit = AsyncMock()
    with patch.object(qa, "_detect_silences", AsyncMock(return_value=long_silences)):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=None)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=None)):
                with patch.object(qa, "emit_finding", emit):
                    out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["silence_check"] == "warn"
    assert len(out["audio_qa_result"]["silence_long_segments"]) == 1
    emit.assert_called_once()
    assert emit.call_args.kwargs["kind"] == "audio_long_silence"


@pytest.mark.asyncio
async def test_silence_check_unavailable_when_ffmpeg_absent(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)

    with patch.object(qa, "_detect_silences", AsyncMock(return_value=None)):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=None)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=None)):
                out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["silence_check"] == "unavailable"


# ---------------------------------------------------------------------------
# Check E: Volume levels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_volume_check_ok(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    vol = {"mean_volume_db": -18.0, "max_volume_db": -3.0}

    with patch.object(qa, "_detect_silences", AsyncMock(return_value=[])):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=vol)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=None)):
                out = await _run(_state(podcast_audio_path=str(f)))

    r = out["audio_qa_result"]
    assert r["volume_check"] == "ok"
    assert r["mean_volume_db"] == -18.0
    assert r["max_volume_db"] == -3.0


@pytest.mark.asyncio
async def test_volume_check_flags_clipping(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    vol = {"mean_volume_db": -6.0, "max_volume_db": 0.0}  # 0 dBFS → clipping
    emit = AsyncMock()

    with patch.object(qa, "_detect_silences", AsyncMock(return_value=[])):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=vol)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=None)):
                with patch.object(qa, "emit_finding", emit):
                    out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["volume_check"] == "clipping"
    emit.assert_called_once()
    assert emit.call_args.kwargs["kind"] == "audio_clipping"


@pytest.mark.asyncio
async def test_volume_check_flags_too_quiet(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    vol = {"mean_volume_db": -50.0, "max_volume_db": -20.0}  # very quiet
    emit = AsyncMock()

    with patch.object(qa, "_detect_silences", AsyncMock(return_value=[])):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=vol)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=None)):
                with patch.object(qa, "emit_finding", emit):
                    out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["volume_check"] == "too_quiet"
    emit.assert_called_once()
    assert emit.call_args.kwargs["kind"] == "audio_too_quiet"


# ---------------------------------------------------------------------------
# Check F: Duration vs script estimate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duration_check_ok(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    # Script has 10 words; at 2.5 wps expected ~4s. Actual 5s → ok.
    with patch.object(qa, "_detect_silences", AsyncMock(return_value=[])):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=None)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=5.0)):
                out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["duration_check"] == "ok"
    assert out["audio_qa_result"]["actual_duration_s"] == 5.0


@pytest.mark.asyncio
async def test_duration_check_too_short(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    # 10 words → expected ~4s. Actual 1.0s → 25% → below 40% threshold.
    emit = AsyncMock()
    with patch.object(qa, "_detect_silences", AsyncMock(return_value=[])):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=None)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=1.0)):
                with patch.object(qa, "emit_finding", emit):
                    out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["duration_check"] == "too_short"
    emit.assert_called_once()
    assert emit.call_args.kwargs["kind"] == "audio_duration_mismatch"


@pytest.mark.asyncio
async def test_duration_check_too_long(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    # 10 words → expected ~4s. Actual 20s → 500% → above 250% threshold.
    emit = AsyncMock()
    with patch.object(qa, "_detect_silences", AsyncMock(return_value=[])):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=None)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=20.0)):
                with patch.object(qa, "emit_finding", emit):
                    out = await _run(_state(podcast_audio_path=str(f)))

    assert out["audio_qa_result"]["duration_check"] == "too_long"
    emit.assert_called_once()
    assert emit.call_args.kwargs["kind"] == "audio_duration_mismatch"


@pytest.mark.asyncio
async def test_duration_check_no_script(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    with patch.object(qa, "_detect_silences", AsyncMock(return_value=[])):
        with patch.object(qa, "_measure_volume", AsyncMock(return_value=None)):
            with patch.object(qa, "_probe_duration", AsyncMock(return_value=10.0)):
                out = await _run({
                    "task_id": "t1",
                    "podcast_audio_path": str(f),
                    # no podcast_script
                })

    assert out["audio_qa_result"]["duration_check"] == "no_script"
    assert out["audio_qa_result"]["actual_duration_s"] == 10.0


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_atom_never_raises_on_total_failure(tmp_path):
    """Even if all helpers raise, the atom returns an empty result dict."""
    f = tmp_path / "audio.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 36)
    boom = AsyncMock(side_effect=RuntimeError("ffmpeg exploded"))

    with patch.object(qa, "_detect_silences", boom):
        # _detect_silences raises → the atom catches and returns empty
        out = await _run(_state(podcast_audio_path=str(f)))

    assert out == {"audio_qa_result": {}}
    assert out["audio_qa_result"] is not None


# ---------------------------------------------------------------------------
# run() — dual-lane orchestration (#689)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa_audio_checks_both_lanes(monkeypatch):
    """run() QAs the long + short narration lanes and nests the results."""
    seen = []

    async def _fake_one(*, audio_path, script, task_id, label, site_config):
        seen.append((label, audio_path, script))
        return {"volume_check": "ok"}

    monkeypatch.setattr(qa, "_qa_one", _fake_one)
    out = await qa.run({
        "task_id": "t1",
        "long_narration_audio_path": "/tmp/long.mp3",
        "short_narration_audio_path": "/tmp/short.mp3",
        "video_long_script": "long script",
        "short_summary_script": "short script",
    })
    assert {lbl for (lbl, _a, _s) in seen} == {"long", "short"}
    assert out["audio_qa_result"]["long"]["volume_check"] == "ok"
    assert out["audio_qa_result"]["short"]["volume_check"] == "ok"
    by_label = {lbl: (a, s) for (lbl, a, s) in seen}
    assert by_label["long"] == ("/tmp/long.mp3", "long script")
    assert by_label["short"] == ("/tmp/short.mp3", "short script")


@pytest.mark.asyncio
async def test_qa_audio_long_falls_back_to_podcast_script(monkeypatch):
    """Empty video_long_script → long lane QAs against the podcast script."""
    seen = {}

    async def _fake_one(*, audio_path, script, task_id, label, site_config):
        seen[label] = script
        return {}

    monkeypatch.setattr(qa, "_qa_one", _fake_one)
    await qa.run({
        "task_id": "t1",
        "long_narration_audio_path": "/tmp/long.mp3",
        "video_long_script": "",
        "podcast_script": "podcast body",
    })
    assert seen["long"] == "podcast body"


@pytest.mark.asyncio
async def test_qa_audio_run_failsoft_no_audio_both_lanes():
    """No narration audio in either lane → empty per-lane results, no raise."""
    out = await qa.run({"task_id": "t-empty"})
    assert out == {"audio_qa_result": {"long": {}, "short": {}}}


# ---------------------------------------------------------------------------
# run() — podcast lane (Stage-3 podcast_pipeline)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa_audio_checks_podcast_lane(monkeypatch):
    """run() QAs the podcast narration audio when ``podcast_audio_path`` is
    present, nesting the result under ``audio_qa_result['podcast']``.

    Regression guard for the gap where the podcast_pipeline's qa.audio node ran
    but never inspected ``podcast_audio_path`` — only the video long/short lanes
    — so podcast audio shipped to Apple/Spotify entirely un-QA'd.
    """
    seen = []

    async def _fake_one(*, audio_path, script, task_id, label, site_config):
        seen.append((label, audio_path, script))
        return {"duration_check": "ok"}

    monkeypatch.setattr(qa, "_qa_one", _fake_one)
    out = await qa.run({
        "task_id": "t1",
        "podcast_audio_path": "/tmp/ep.mp3",
        "podcast_script": "the podcast body script",
    })

    assert ("podcast", "/tmp/ep.mp3", "the podcast body script") in seen
    assert out["audio_qa_result"]["podcast"]["duration_check"] == "ok"


@pytest.mark.asyncio
async def test_qa_audio_omits_podcast_lane_without_path(monkeypatch):
    """A video-only run (no ``podcast_audio_path``) does NOT add a 'podcast'
    key — the media_pipeline's audio_qa_result contract is unchanged."""
    async def _fake_one(*, audio_path, script, task_id, label, site_config):
        return {}

    monkeypatch.setattr(qa, "_qa_one", _fake_one)
    out = await qa.run({
        "task_id": "t1",
        "long_narration_audio_path": "/tmp/long.mp3",
        "short_narration_audio_path": "/tmp/short.mp3",
    })

    assert "podcast" not in out["audio_qa_result"]


# ---------------------------------------------------------------------------
# ATOM_META shape
# ---------------------------------------------------------------------------

def test_atom_meta_shape():
    assert qa.ATOM_META.name == "qa.audio"
    assert "audio_qa_result" in {f.name for f in qa.ATOM_META.outputs}
    assert qa.ATOM_META.idempotent is True
    assert qa.ATOM_META.retry.max_attempts == 1
