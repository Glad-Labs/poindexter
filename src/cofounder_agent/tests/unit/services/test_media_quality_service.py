"""Tests for ``services.media_quality_service`` (Layer 1).

The ffmpeg / ffprobe subprocess calls are stubbed so the tests don't
need real audio files. We focus on the decision logic:

- Threshold lookups fall back to defaults when app_settings is silent
- Failing thresholds flip the row to ``status='rejected'`` with
  ``decided_by='auto:layer1'`` and the offending signal in ``notes``
- Passing thresholds leave the row alone (still ``pending``) but write
  ``quality_score=1.0`` + ``quality_signals`` JSON
- ``evaluate_video`` rejects unknown media values (no silent default)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import media_quality_service


@pytest.fixture
def mock_db() -> MagicMock:
    db = MagicMock()
    # _get_threshold reads via fetchrow; default returns None so the
    # service falls back to _DEFAULT_THRESHOLDS.
    db.fetchrow = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value="UPDATE 1")
    return db


# ---------------------------------------------------------------------------
# Layer 2 — shared test stubs (spec 2026-07-09)
# ---------------------------------------------------------------------------


class _DBStub:
    """Minimal async DB double for the Layer-2 helpers."""

    def __init__(self, *, fetch_rows=None, post_title="A Title"):
        self._fetch_rows = fetch_rows if fetch_rows is not None else []
        self._post_title = post_title

    async def fetch(self, *_a, **_k):
        return self._fetch_rows

    async def fetchrow(self, *_a, **_k):
        return {"title": self._post_title, "content": "source body"}

    async def execute(self, *_a, **_k):
        return None


class _SC:
    """Sync .get site_config stub (mirrors SiteConfig.get(key, default))."""

    def __init__(self, cfg):
        self._c = cfg

    def get(self, k, d=None):
        return self._c.get(k, d)


# ---------------------------------------------------------------------------
# Layer 2 — shot-fidelity aggregate
# ---------------------------------------------------------------------------


async def test_aggregate_shot_qa_scores_excludes_reused_and_unscored():
    rows = [
        {"qa_score": 90.0, "outcome": "accepted"},
        {"qa_score": 70.0, "outcome": "regenerated"},
        {"qa_score": None, "outcome": "unscored"},        # excluded
        {"qa_score": 95.0, "outcome": "skipped_reused"},  # excluded
    ]
    out = await media_quality_service._aggregate_shot_qa_scores(
        _DBStub(fetch_rows=rows), "post-1",
    )
    assert out["shot_fidelity"] == 80.0          # mean(90, 70)
    assert out["scored"] == 2
    assert out["total"] == 4
    assert out["shot_coverage"] == 0.5           # 2 / 4


async def test_aggregate_shot_qa_scores_none_when_no_scored_rows():
    rows = [{"qa_score": None, "outcome": "unscored"}]
    out = await media_quality_service._aggregate_shot_qa_scores(
        _DBStub(fetch_rows=rows), "post-1",
    )
    assert out["shot_fidelity"] is None
    assert out["scored"] == 0
    assert out["total"] == 1
    assert out["shot_coverage"] == 0.0


# ---------------------------------------------------------------------------
# evaluate_podcast — auto-reject on Layer 1 failures
# ---------------------------------------------------------------------------


async def test_podcast_auto_rejects_when_too_short(
    mock_db: MagicMock, tmp_path,
) -> None:
    """Duration under the threshold trips the auto-reject path."""
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 100_000)  # > min_file_size_bytes default

    with patch.object(
        media_quality_service, "_probe_duration", return_value=5.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.1,
    ):
        result = await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    assert result["score"] == 0.0
    assert "duration_seconds" in result["layer1_failures"][0]
    # The UPDATE that fires must be the rejecting branch.
    update_sql = mock_db.execute.call_args.args[0]
    assert "status = 'rejected'" in update_sql
    assert "auto:layer1" in update_sql


async def test_podcast_auto_rejects_when_silence_too_high(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 100_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=120.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.95,
    ):
        result = await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    assert result["score"] == 0.0
    assert any("silence_ratio" in f for f in result["layer1_failures"])


async def test_podcast_passes_layer1_when_signals_clean(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 1_000_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=180.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.05,
    ):
        result = await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    # 0-100 rescale: a clean pass with no Layer-2 signal writes 100, not 1.0.
    # No site_config passed → Layer 2 off → status "disabled".
    assert result["score"] == 100.0
    assert result["layer1_failures"] == []
    assert result["layer2_status"] == "disabled"
    # The UPDATE that fires must be the passing branch (no status change).
    update_sql = mock_db.execute.call_args.args[0]
    assert "status = 'rejected'" not in update_sql
    assert "quality_score = $3" in update_sql


async def test_podcast_pass_enabled_stamps_unavailable(
    mock_db: MagicMock, tmp_path,
) -> None:
    """Layer 2 enabled but no zero-cost podcast signal yet → status
    'unavailable', score still the 0-100 clean-pass value (100).

    This pins the cheap-half seam: the podcast faithfulness signal
    (follow-up PR) replaces the bare 100 with a real composite here.
    """
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 1_000_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=180.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.05,
    ):
        result = await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
            site_config=_SC({"media.layer2.enabled": "true"}),
        )

    assert result["score"] == 100.0
    assert result["layer2_status"] == "unavailable"


async def test_podcast_persists_signals_as_json(
    mock_db: MagicMock, tmp_path,
) -> None:
    """Signals dict must round-trip through JSON encode."""
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 1_000_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=120.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.1,
    ):
        await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    # The signals JSON is the 4th positional arg of the passing UPDATE.
    json_arg = mock_db.execute.call_args.args[4]
    parsed = json.loads(json_arg)
    assert parsed["duration_seconds"] == 120.0
    assert parsed["silence_ratio"] == 0.1


# ---------------------------------------------------------------------------
# evaluate_video
# ---------------------------------------------------------------------------


async def test_video_rejects_unknown_medium(mock_db: MagicMock) -> None:
    """Typo'd medium MUST fail loud — no silent fallback."""
    with pytest.raises(ValueError, match="unsupported medium"):
        await media_quality_service.evaluate_video(
            mock_db, "post-id", "/tmp/f.mp4", medium="audio",
        )


async def test_video_auto_rejects_when_too_small(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp4"
    f.write_bytes(b"x" * 10)  # way below default min_file_size_bytes

    with patch.object(
        media_quality_service, "_probe_duration", return_value=30.0,
    ):
        result = await media_quality_service.evaluate_video(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
            medium="video",
        )

    assert result["score"] == 0.0
    assert any("file_size_bytes" in fl for fl in result["layer1_failures"])


async def test_video_passes_layer1_when_signals_clean(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp4"
    f.write_bytes(b"x" * 5_000_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=45.0,
    ):
        result = await media_quality_service.evaluate_video(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
            medium="video",
        )

    # No site_config passed → Layer 2 disabled → a clean Layer-1 pass now
    # writes the 0-100 "nothing wrong" score (100.0), not the old binary 1.0.
    assert result["score"] == 100.0
    assert result["layer1_failures"] == []
    assert result["layer2_status"] == "disabled"


# ---------------------------------------------------------------------------
# _run_video_layer2 — composite from shot-fidelity (cheap-half slice)
# ---------------------------------------------------------------------------


async def test_video_layer2_composite_is_shot_fidelity(monkeypatch) -> None:
    """Shot-fidelity is the only wired signal today, so composite == it."""
    monkeypatch.setattr(
        media_quality_service, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={
            "shot_fidelity": 80.0, "shot_coverage": 1.0, "scored": 5, "total": 5,
        }),
    )
    out = await media_quality_service._run_video_layer2(
        _DBStub(), "p", "/v.mp4", _SC({"media.layer2.enabled": "true"}),
    )
    assert out["layer2_score"] == 80.0
    assert out["layer2_status"] == "scored"


async def test_video_layer2_unavailable_when_no_signals(monkeypatch) -> None:
    """All shots reused/unscored → no signal → status 'unavailable'."""
    monkeypatch.setattr(
        media_quality_service, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={
            "shot_fidelity": None, "shot_coverage": 0.0, "scored": 0, "total": 0,
        }),
    )
    out = await media_quality_service._run_video_layer2(
        _DBStub(), "p", "/v.mp4", _SC({"media.layer2.enabled": "true"}),
    )
    assert out["layer2_score"] is None
    assert out["layer2_status"] == "unavailable"


async def test_video_layer2_disabled_by_master_switch(monkeypatch) -> None:
    """Master switch off → short-circuit; the aggregate is never queried."""
    agg = AsyncMock()
    monkeypatch.setattr(media_quality_service, "_aggregate_shot_qa_scores", agg)
    out = await media_quality_service._run_video_layer2(
        _DBStub(), "p", "/v.mp4", _SC({"media.layer2.enabled": "false"}),
    )
    assert out["layer2_status"] == "disabled"
    agg.assert_not_awaited()


async def test_video_layer2_low_fidelity_emits_finding(monkeypatch) -> None:
    """Shot-fidelity below the configured min raises an advisory finding."""
    monkeypatch.setattr(
        media_quality_service, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={
            "shot_fidelity": 20.0, "shot_coverage": 1.0, "scored": 4, "total": 4,
        }),
    )
    captured: list[dict] = []
    monkeypatch.setattr(
        media_quality_service, "emit_finding", lambda **kw: captured.append(kw),
    )
    await media_quality_service._run_video_layer2(
        _DBStub(), "p", "/v.mp4",
        _SC({"media.layer2.enabled": "true", "media.video.shot_fidelity_min": "60"}),
    )
    assert any(f["kind"] == "video_shot_fidelity_low" for f in captured)


async def test_video_layer2_composite_is_mean_of_both_signals(monkeypatch) -> None:
    """Shot-fidelity + topic-match both present → composite is their mean."""
    monkeypatch.setattr(
        media_quality_service, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={
            "shot_fidelity": 80.0, "shot_coverage": 1.0, "scored": 5, "total": 5,
        }),
    )
    monkeypatch.setattr(
        media_quality_service, "_score_video_topic_match",
        AsyncMock(return_value=60.0),
    )
    monkeypatch.setattr(
        media_quality_service, "_fetch_post_title", AsyncMock(return_value="T"),
    )
    out = await media_quality_service._run_video_layer2(
        _DBStub(), "p", "/v.mp4", _SC({"media.layer2.enabled": "true"}),
    )
    assert out["layer2_score"] == 70.0  # mean(80, 60)
    assert out["layer2_status"] == "scored"
    assert out["topic_match"] == 60.0


async def test_video_layer2_topic_mismatch_emits_finding(monkeypatch) -> None:
    """Topic-match below its min raises an advisory finding (fidelity is fine)."""
    monkeypatch.setattr(
        media_quality_service, "_aggregate_shot_qa_scores",
        AsyncMock(return_value={
            "shot_fidelity": 90.0, "shot_coverage": 1.0, "scored": 4, "total": 4,
        }),
    )
    monkeypatch.setattr(
        media_quality_service, "_score_video_topic_match",
        AsyncMock(return_value=20.0),
    )
    monkeypatch.setattr(
        media_quality_service, "_fetch_post_title", AsyncMock(return_value="T"),
    )
    captured: list[dict] = []
    monkeypatch.setattr(
        media_quality_service, "emit_finding", lambda **kw: captured.append(kw),
    )
    await media_quality_service._run_video_layer2(
        _DBStub(), "p", "/v.mp4",
        _SC({"media.layer2.enabled": "true", "media.video.topic_match_min": "50"}),
    )
    assert any(f["kind"] == "video_topic_mismatch" for f in captured)


async def test_evaluate_video_pass_writes_0_100_composite(monkeypatch) -> None:
    """A Layer-1 pass writes the Layer-2 composite (0-100), not 1.0."""
    monkeypatch.setattr(
        media_quality_service, "_probe_duration", AsyncMock(return_value=45.0),
    )
    monkeypatch.setattr(media_quality_service, "_file_size", lambda p: 5_000_000)
    monkeypatch.setattr(
        media_quality_service, "_run_video_layer2",
        AsyncMock(return_value={
            "layer2_status": "scored", "layer2_score": 80.0,
            "shot_fidelity": 80.0, "shot_coverage": 1.0,
        }),
    )
    monkeypatch.setattr(
        media_quality_service, "_notify_if_pending", AsyncMock(),
    )
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=None)  # thresholds → defaults
    out = await media_quality_service.evaluate_video(
        db, "p", "/v.mp4", medium="video",
        site_config=_SC({"media.layer2.enabled": "true"}),
    )
    assert out["score"] == 80.0
    assert out["layer2_status"] == "scored"
    # The passing UPDATE wrote 80.0 as quality_score (0-100), not 1.0.
    update_args = [c.args for c in db.execute.await_args_list]
    assert any(80.0 in a for a in update_args)


# ---------------------------------------------------------------------------
# _score_video_topic_match — vision score of composed frames vs title
# ---------------------------------------------------------------------------


async def test_topic_match_scores_and_uses_thinking_safe_budget(monkeypatch) -> None:
    """One dispatch call over N frames → 0-100 score; budget >= 1024 (#2224)."""
    async def _fake_frame(file_path, at_s):
        return b"\x89PNG-fake"
    monkeypatch.setattr(media_quality_service, "_extract_frame_at", _fake_frame)
    monkeypatch.setattr(
        media_quality_service, "_probe_duration", AsyncMock(return_value=30.0),
    )
    dispatch = AsyncMock(return_value=SimpleNamespace(text='{"score": 82}'))
    monkeypatch.setattr(
        media_quality_service, "dispatch_complete", dispatch, raising=False,
    )

    sc = _SC({
        "media.video.topic_match_model": "ollama/qwen3-vl:30b",
        "media.video.topic_match_frames": "3",
    })
    score = await media_quality_service._score_video_topic_match(
        _DBStub(), "/v.mp4", "GPU undervolting", sc,
    )

    assert score == 82.0
    _, kwargs = dispatch.call_args
    assert kwargs["max_tokens"] >= 1024  # thinking-model budget pin


async def test_topic_match_unavailable_when_no_model() -> None:
    """No topic_match_model and no qa_vision_model fallback → None (skip)."""
    sc = _SC({"media.video.topic_match_model": "", "qa_vision_model": ""})
    score = await media_quality_service._score_video_topic_match(
        _DBStub(), "/v.mp4", "t", sc,
    )
    assert score is None


async def test_topic_match_fail_soft_on_dispatch_raise(monkeypatch) -> None:
    """A dispatch error is swallowed → None, never propagates."""
    async def _fake_frame(file_path, at_s):
        return b"png"
    monkeypatch.setattr(media_quality_service, "_extract_frame_at", _fake_frame)
    monkeypatch.setattr(
        media_quality_service, "_probe_duration", AsyncMock(return_value=30.0),
    )
    monkeypatch.setattr(
        media_quality_service, "dispatch_complete",
        AsyncMock(side_effect=RuntimeError("ollama down")), raising=False,
    )
    sc = _SC({"media.video.topic_match_model": "ollama/qwen3-vl:30b"})
    assert await media_quality_service._score_video_topic_match(
        _DBStub(), "/v.mp4", "t", sc,
    ) is None


# ---------------------------------------------------------------------------
# _score_podcast_faithfulness — transcript vs source article
# ---------------------------------------------------------------------------


async def test_podcast_faithfulness_scores(monkeypatch) -> None:
    """Transcribe → LLM judge → (score, reason)."""
    seg = SimpleNamespace(text="the episode says gpus run cooler undervolted")
    result = SimpleNamespace(success=True, segments=[seg], error=None)
    provider = SimpleNamespace(transcribe=AsyncMock(return_value=result))
    monkeypatch.setattr(
        media_quality_service, "get_caption_provider", lambda sc: provider,
        raising=False,
    )
    monkeypatch.setattr(
        media_quality_service, "dispatch_complete",
        AsyncMock(return_value=SimpleNamespace(
            text='{"score": 88, "reason": "faithful"}')),
        raising=False,
    )

    class _DB:
        async def fetchrow(self, *_a, **_k):
            return {"content": "Undervolting keeps GPUs cool."}

    sc = _SC({"media.podcast.faithfulness_model": "ollama/qwen3.6:latest"})
    score, reason = await media_quality_service._score_podcast_faithfulness(
        _DB(), "p", "/ep.mp3", sc,
    )
    assert score == 88.0
    assert "faithful" in reason


async def test_podcast_faithfulness_unavailable_no_transcript(monkeypatch) -> None:
    """Transcription failure → (None, '') fail-soft."""
    result = SimpleNamespace(success=False, segments=[], error="whisper down")
    provider = SimpleNamespace(transcribe=AsyncMock(return_value=result))
    monkeypatch.setattr(
        media_quality_service, "get_caption_provider", lambda sc: provider,
        raising=False,
    )

    class _DB:
        async def fetchrow(self, *_a, **_k):
            return {"content": "body"}

    sc = _SC({"media.podcast.faithfulness_model": "ollama/qwen3.6:latest"})
    score, _ = await media_quality_service._score_podcast_faithfulness(
        _DB(), "p", "/ep.mp3", sc,
    )
    assert score is None


async def test_podcast_faithfulness_unavailable_no_model() -> None:
    """No faithfulness_model and no ragas_judge_model fallback → None (skip)."""
    sc = _SC({"media.podcast.faithfulness_model": "", "ragas_judge_model": ""})
    score, _ = await media_quality_service._score_podcast_faithfulness(
        _DBStub(), "p", "/ep.mp3", sc,
    )
    assert score is None


# ---------------------------------------------------------------------------
# _run_podcast_layer2 + evaluate_podcast 0-100 composite
# ---------------------------------------------------------------------------


async def test_podcast_layer2_score_is_faithfulness(monkeypatch) -> None:
    """Faithfulness is the podcast Layer-2 signal → it IS the composite."""
    monkeypatch.setattr(
        media_quality_service, "_score_podcast_faithfulness",
        AsyncMock(return_value=(88.0, "ok")),
    )
    out = await media_quality_service._run_podcast_layer2(
        _DBStub(), "p", "/ep.mp3", _SC({"media.layer2.enabled": "true"}),
    )
    assert out["layer2_score"] == 88.0
    assert out["layer2_status"] == "scored"
    assert out["faithfulness"] == 88.0


async def test_podcast_layer2_unavailable(monkeypatch) -> None:
    """No faithfulness score → status 'unavailable', score None."""
    monkeypatch.setattr(
        media_quality_service, "_score_podcast_faithfulness",
        AsyncMock(return_value=(None, "")),
    )
    out = await media_quality_service._run_podcast_layer2(
        _DBStub(), "p", "/ep.mp3", _SC({"media.layer2.enabled": "true"}),
    )
    assert out["layer2_score"] is None
    assert out["layer2_status"] == "unavailable"


async def test_podcast_layer2_low_faithfulness_emits_finding(monkeypatch) -> None:
    """Faithfulness below its min raises an advisory finding."""
    monkeypatch.setattr(
        media_quality_service, "_score_podcast_faithfulness",
        AsyncMock(return_value=(30.0, "drifted")),
    )
    captured: list[dict] = []
    monkeypatch.setattr(
        media_quality_service, "emit_finding", lambda **kw: captured.append(kw),
    )
    await media_quality_service._run_podcast_layer2(
        _DBStub(), "p", "/ep.mp3",
        _SC({"media.layer2.enabled": "true", "media.podcast.faithfulness_min": "60"}),
    )
    assert any(f["kind"] == "podcast_faithfulness_low" for f in captured)


async def test_evaluate_podcast_pass_writes_0_100(monkeypatch) -> None:
    """A Layer-1 pass writes the podcast Layer-2 composite (0-100), not 100 bare."""
    monkeypatch.setattr(
        media_quality_service, "_probe_duration", AsyncMock(return_value=600.0),
    )
    monkeypatch.setattr(
        media_quality_service, "_probe_silence_ratio", AsyncMock(return_value=0.1),
    )
    monkeypatch.setattr(media_quality_service, "_file_size", lambda p: 8_000_000)
    monkeypatch.setattr(
        media_quality_service, "_run_podcast_layer2",
        AsyncMock(return_value={
            "layer2_status": "scored", "layer2_score": 88.0, "faithfulness": 88.0,
        }),
    )
    monkeypatch.setattr(
        media_quality_service, "_notify_if_pending", AsyncMock(),
    )
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=None)  # thresholds → defaults
    out = await media_quality_service.evaluate_podcast(
        db, "p", "/ep.mp3", site_config=_SC({"media.layer2.enabled": "true"}),
    )
    assert out["score"] == 88.0
    assert out["layer2_status"] == "scored"


# ---------------------------------------------------------------------------
# _get_threshold — app_settings override + numeric coercion
# ---------------------------------------------------------------------------


async def test_threshold_falls_back_to_default(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = None
    val = await media_quality_service._get_threshold(
        mock_db, "media.podcast.min_duration_seconds",
    )
    assert val == 30.0  # _DEFAULT_THRESHOLDS


async def test_threshold_uses_app_settings_override(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = {"value": "90"}
    val = await media_quality_service._get_threshold(
        mock_db, "media.podcast.min_duration_seconds",
    )
    assert val == 90.0


async def test_threshold_invalid_value_falls_back(mock_db: MagicMock) -> None:
    """A garbled value (operator typo) must NOT crash — fall back to default."""
    mock_db.fetchrow.return_value = {"value": "not-a-number"}
    val = await media_quality_service._get_threshold(
        mock_db, "media.podcast.min_duration_seconds",
    )
    assert val == 30.0
