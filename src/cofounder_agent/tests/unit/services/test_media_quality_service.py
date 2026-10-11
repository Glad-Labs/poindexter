"""Tests for ``services.media_quality_service`` (Layer 1).

The ffmpeg / ffprobe subprocess calls are stubbed so the tests don't
need real audio files. We focus on the decision logic:

- Threshold lookups fall back to defaults when app_settings is silent
- Failing thresholds flip the row to ``status='rejected'`` with
  ``decided_by='auto:layer1'`` and the offending signal in ``notes``
- Passing thresholds leave the row alone (still ``pending``) and write
  ``quality_signals`` JSON. ``quality_score`` is the Layer-2 composite, or
  NULL when Layer 2 could not measure — never a fabricated 100
- ``evaluate_video`` rejects unknown media values (no silent default)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import media_quality_service


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
    # The flip is CONDITIONAL now: it overturns a pending or an auto
    # decision and never an operator's, because the eval also runs on
    # auto-approved rows.
    assert "THEN 'rejected'" in update_sql
    assert "decided_by LIKE 'auto:%'" in update_sql
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

    # No site_config passed → Layer 2 off → status "disabled" and NO score.
    # Layer 1 only answers "is the file broken?"; writing 100 for a clean
    # Layer-1 pass claimed a content grade nothing had performed.
    assert result["score"] is None
    assert result["layer1_failures"] == []
    assert result["layer2_status"] == "disabled"
    # The UPDATE that fires must be the passing branch (no status change).
    update_sql = mock_db.execute.call_args.args[0]
    assert "THEN 'rejected'" not in update_sql
    assert "quality_score = $3" in update_sql


async def test_podcast_pass_enabled_stamps_unavailable(
    mock_db: MagicMock, tmp_path,
) -> None:
    """Layer 2 enabled but unable to score → status 'unavailable', score NULL.

    The score used to be stamped 100 here. That made a failed transcription
    and a genuinely perfect episode identical on the row — 12 of 39 evaluated
    podcasts were scored 100 with nothing having read them.
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

    assert result["score"] is None
    assert result["layer2_status"] == "unavailable"
    # "unavailable" must be diagnosable from the row alone.
    assert result["layer2_unavailable_reason"]


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

    # Same rule as the podcast lane: Layer 2 off → no content grade → NULL.
    assert result["score"] is None
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
    """N frames → 0-100 score; per-call budget >= 1024 (#2224)."""
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


def _shot(idx, source, dur, score=92.0, outcome="accepted"):
    return {"shot_idx": idx, "source": source, "duration_s": dur,
            "qa_score": score, "outcome": outcome}


class _SQLCapture(_DBStub):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.sql: list[str] = []

    async def fetch(self, sql, *_a, **_k):
        self.sql.append(sql)
        return self._fetch_rows


async def test_aggregate_matches_rows_stamped_with_the_task_id():
    """The renderer stamps details.post_id with the TASK id; match it too.

    Matching the post id alone found no rows for any video, so shot_fidelity
    was never measured and shot_coverage always read 0.
    """
    db = _SQLCapture(fetch_rows=[_shot(0, "pexels", 6.0)])
    await media_quality_service._aggregate_shot_qa_scores(db, "post-1")
    assert "pipeline_task_id" in db.sql[0]


async def test_aggregate_folds_only_the_render_that_made_this_file():
    """Long video + Short share a task; the file's duration picks its render."""
    long_render = [_shot(i, "pexels", 8.0, score=90.0) for i in range(20)]  # 160 s
    short_render = [_shot(i, "pexels", 6.0, score=40.0) for i in range(5)]  # 30 s
    db = _DBStub(fetch_rows=long_render + short_render)

    short = await media_quality_service._aggregate_shot_qa_scores(db, "p", 34.0)
    assert short["total"] == 5
    assert short["shot_fidelity"] == 40.0

    long = await media_quality_service._aggregate_shot_qa_scores(db, "p", 168.0)
    assert long["total"] == 20
    assert long["shot_fidelity"] == 90.0


def test_render_groups_split_where_shot_idx_restarts():
    rows = [_shot(0, "a", 1), _shot(1, "a", 1), _shot(0, "a", 1), _shot(1, "a", 1)]
    groups = media_quality_service._split_render_groups(rows)
    assert [len(g) for g in groups] == [2, 2]


def test_pick_render_group_tie_goes_to_latest():
    a = [_shot(0, "x", 10.0)]
    b = [_shot(0, "y", 10.0)]
    assert media_quality_service._pick_render_group([a, b], 10.0) is b
    assert media_quality_service._pick_render_group([a, b], None) is b


def test_topic_timestamps_skip_presenter_card_and_holdover():
    shots = [
        _shot(0, "presenter", 4.0),
        _shot(1, "pexels", 6.0),                                # mid 7.0
        _shot(2, "image_kenburns", 4.0, outcome="card"),
        _shot(3, "pexels", 4.0, outcome="fallback_holdover"),
        _shot(4, "generative", 10.0),                           # mid 23.0
        _shot(5, "presenter", 4.0),
    ]
    ts = media_quality_service._topic_match_timestamps(
        shots, 40.0, 5, _SC({"media.video.topic_match_skip_sources": "presenter"}),
    )
    assert ts == [7.0, 23.0]


def test_topic_timestamps_space_samples_across_the_video():
    shots = [_shot(i, "pexels", 10.0) for i in range(9)]  # mids 5, 15, ..., 85
    ts = media_quality_service._topic_match_timestamps(shots, 90.0, 3, _SC({}))
    assert ts == [5.0, 45.0, 85.0]


def test_topic_timestamps_scale_onto_a_shorter_file():
    """Cross-fades make the file shorter than the shot sum; stay inside it."""
    shots = [_shot(0, "pexels", 10.0), _shot(1, "pexels", 10.0)]
    ts = media_quality_service._topic_match_timestamps(shots, 10.0, 2, _SC({}))
    assert ts == [2.5, 7.5]


def test_topic_timestamps_fallback_avoids_the_end_card():
    """No shot rows: even samples over the runtime minus the end card."""
    ts = media_quality_service._topic_match_timestamps(
        None, 48.0, 3, _SC({"video_endcard_max_seconds": "8"}),
    )
    assert ts == [10.0, 20.0, 30.0]


def test_topic_timestamps_all_presenter_falls_back_to_even_sampling():
    shots = [_shot(0, "presenter", 10.0), _shot(1, "presenter", 10.0)]
    ts = media_quality_service._topic_match_timestamps(
        shots, 20.0, 1, _SC({"video_endcard_max_seconds": "0"}),
    )
    assert ts == [10.0]


async def test_topic_match_scores_each_frame_alone_and_takes_the_median(
    monkeypatch,
) -> None:
    """One call per frame at temperature 0; one off-topic frame can't sink it."""
    async def _fake_frame(file_path, at_s):
        return b"png"
    monkeypatch.setattr(media_quality_service, "_extract_frame_at", _fake_frame)
    replies = iter(['{"score": 10}', '{"score": 80}', '{"score": 85}'])
    dispatch = AsyncMock(
        side_effect=lambda *a, **k: SimpleNamespace(text=next(replies)),
    )
    monkeypatch.setattr(
        media_quality_service, "dispatch_complete", dispatch, raising=False,
    )
    shots = [_shot(i, "pexels", 10.0) for i in range(3)]
    score = await media_quality_service._score_video_topic_match(
        _DBStub(), "/v.mp4", "t",
        _SC({"media.video.topic_match_model": "m", "media.video.topic_match_frames": "3"}),
        shots=shots, duration=30.0,
    )
    assert score == 80.0
    assert dispatch.await_count == 3
    for call in dispatch.await_args_list:
        msg_content = call.args[1][0]["content"]
        assert sum(1 for c in msg_content if c["type"] == "image_url") == 1
        assert call.kwargs["temperature"] == 0.0
        assert call.kwargs["think"] is False
        assert call.kwargs["response_format"] == {"type": "json_object"}


async def test_topic_match_skips_unparseable_frames(monkeypatch) -> None:
    async def _fake_frame(file_path, at_s):
        return b"png"
    monkeypatch.setattr(media_quality_service, "_extract_frame_at", _fake_frame)
    replies = iter(["no json here", '{"score": 70}'])
    monkeypatch.setattr(
        media_quality_service, "dispatch_complete",
        AsyncMock(side_effect=lambda *a, **k: SimpleNamespace(text=next(replies))),
        raising=False,
    )
    score = await media_quality_service._score_video_topic_match(
        _DBStub(), "/v.mp4", "t",
        _SC({"media.video.topic_match_model": "m", "media.video.topic_match_frames": "2"}),
        shots=[_shot(0, "pexels", 5.0), _shot(1, "pexels", 5.0)], duration=10.0,
    )
    assert score == 70.0


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
    score, reason, why = await media_quality_service._score_podcast_faithfulness(
        _DB(), "p", "/ep.mp3", sc,
    )
    assert score == 88.0
    assert "faithful" in reason
    assert why == ""


async def test_podcast_faithfulness_unavailable_no_transcript(monkeypatch) -> None:
    """Transcription failure → no score, and the reason names the step."""
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
    score, _, why = await media_quality_service._score_podcast_faithfulness(
        _DB(), "p", "/ep.mp3", sc,
    )
    assert score is None
    assert why == "transcribe_failed"


async def test_podcast_faithfulness_unavailable_no_model() -> None:
    """No faithfulness_model and no ragas_judge_model fallback → None (skip)."""
    sc = _SC({"media.podcast.faithfulness_model": "", "ragas_judge_model": ""})
    score, _, why = await media_quality_service._score_podcast_faithfulness(
        _DBStub(), "p", "/ep.mp3", sc,
    )
    assert score is None
    assert why == "no_judge_model"


# ---------------------------------------------------------------------------
# _run_podcast_layer2 + evaluate_podcast 0-100 composite
# ---------------------------------------------------------------------------


async def test_podcast_layer2_score_is_faithfulness(monkeypatch) -> None:
    """Faithfulness is the podcast Layer-2 signal → it IS the composite."""
    monkeypatch.setattr(
        media_quality_service, "_score_podcast_faithfulness",
        AsyncMock(return_value=(88.0, "ok", "")),
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
        AsyncMock(return_value=(None, "", "judge_unparsed")),
    )
    out = await media_quality_service._run_podcast_layer2(
        _DBStub(), "p", "/ep.mp3", _SC({"media.layer2.enabled": "true"}),
    )
    assert out["layer2_score"] is None
    assert out["layer2_status"] == "unavailable"
    assert out["layer2_unavailable_reason"] == "judge_unparsed"


async def test_podcast_layer2_low_faithfulness_emits_finding(monkeypatch) -> None:
    """Faithfulness below its min raises an advisory finding."""
    monkeypatch.setattr(
        media_quality_service, "_score_podcast_faithfulness",
        AsyncMock(return_value=(30.0, "drifted", "")),
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


# ---------------------------------------------------------------------------
# judge != writer (project_qa_model_placement_doctrine)
#
# Prod pinned podcast_script_model AND media.podcast.faithfulness_model to the
# same model (ollama/gemma-4-31B-it-qat:latest). Across 21 scored episodes the
# self-grade never dropped below 92 and hit exactly 100 fifteen times, while
# the operator was independently rejecting episodes that scored 100.
# ---------------------------------------------------------------------------


def test_normalize_model_id_strips_provider_prefixes() -> None:
    """One engine, three spellings — compare identity, not the string.

    cost-logs-model-identity.md: LiteLLM adds/strips the provider prefix per
    consumer, so a judge/writer collision can hide behind "ollama/".
    """
    n = media_quality_service._normalize_model_id
    assert n("ollama/Gemma-4-31B:latest") == "gemma-4-31b:latest"
    assert n("ollama_chat/gemma-4-31b:latest") == "gemma-4-31b:latest"
    assert n("  gemma-4-31b:latest ") == "gemma-4-31b:latest"
    assert n(None) == ""


def test_faithfulness_judge_falls_back_when_pin_is_the_writer(monkeypatch) -> None:
    """The pinned judge wrote the script → refuse it, use ragas_judge_model."""
    captured: list[dict] = []
    monkeypatch.setattr(
        media_quality_service, "emit_finding", lambda **kw: captured.append(kw),
    )
    sc = _SC({
        "podcast_script_model": "ollama/gemma-4-31B-it-qat:latest",
        "media.podcast.faithfulness_model": "ollama/gemma-4-31B-it-qat:latest",
        "ragas_judge_model": "ollama/qwen3-vl:30b-a3b-instruct",
    })
    model, why = media_quality_service._resolve_faithfulness_model(sc)
    assert model == "ollama/qwen3-vl:30b-a3b-instruct"
    assert why == ""
    # The override has to be visible, not silent.
    assert [f["kind"] for f in captured] == ["media_judge_is_writer"]


def test_faithfulness_judge_collision_survives_a_prefix_difference(
    monkeypatch,
) -> None:
    """`gemma-4-31b` and `ollama/gemma-4-31b` are the SAME model."""
    monkeypatch.setattr(media_quality_service, "emit_finding", lambda **kw: None)
    sc = _SC({
        "podcast_script_model": "gemma-4-31B-it-qat:latest",
        "media.podcast.faithfulness_model": "ollama/gemma-4-31b-it-qat:latest",
        "ragas_judge_model": "ollama/qwen3-vl:30b-a3b-instruct",
    })
    model, _ = media_quality_service._resolve_faithfulness_model(sc)
    assert model == "ollama/qwen3-vl:30b-a3b-instruct"


def test_faithfulness_judge_refuses_when_every_candidate_is_the_writer(
    monkeypatch,
) -> None:
    """No judge outside the writer's family → no score, named reason.

    Refusing beats self-grading: an honest hole routes a finding and leaves
    quality_score NULL, where a self-graded 100 reads as a clean episode.
    """
    monkeypatch.setattr(media_quality_service, "emit_finding", lambda **kw: None)
    sc = _SC({
        "podcast_script_model": "ollama/gemma-4-31b:latest",
        "media.podcast.faithfulness_model": "gemma-4-31b:latest",
        "ragas_judge_model": "ollama_chat/gemma-4-31b:latest",
    })
    model, why = media_quality_service._resolve_faithfulness_model(sc)
    assert model == ""
    assert why == "judge_is_writer"


def test_faithfulness_judge_unaffected_when_families_differ(monkeypatch) -> None:
    """The normal case stays exactly as it was — the pin wins, no finding."""
    captured: list[dict] = []
    monkeypatch.setattr(
        media_quality_service, "emit_finding", lambda **kw: captured.append(kw),
    )
    sc = _SC({
        "podcast_script_model": "ollama/gemma-4-31b:latest",
        "media.podcast.faithfulness_model": "ollama/qwen3-vl:30b-a3b-instruct",
        "ragas_judge_model": "ollama/something-else",
    })
    model, why = media_quality_service._resolve_faithfulness_model(sc)
    assert model == "ollama/qwen3-vl:30b-a3b-instruct"
    assert why == ""
    assert captured == []


async def test_faithfulness_reports_judge_collision_as_the_reason(
    monkeypatch,
) -> None:
    """The refusal reaches the row as the unavailable reason, not as silence."""
    monkeypatch.setattr(media_quality_service, "emit_finding", lambda **kw: None)
    sc = _SC({
        "podcast_script_model": "ollama/gemma-4-31b:latest",
        "media.podcast.faithfulness_model": "ollama/gemma-4-31b:latest",
        "ragas_judge_model": "gemma-4-31b:latest",
    })
    score, _, why = await media_quality_service._score_podcast_faithfulness(
        _DBStub(), "p", "/ep.mp3", sc,
    )
    assert score is None
    assert why == "judge_is_writer"


# ---------------------------------------------------------------------------
# A Layer-2 hole is visible, not a silent pass
# ---------------------------------------------------------------------------


async def test_evaluate_podcast_emits_finding_when_layer2_unavailable(
    monkeypatch,
) -> None:
    """Layer 1 passed, Layer 2 could not score → NULL score + a routed finding.

    Both halves matter: the NULL stops the fabricated pass, the finding is what
    makes the hole visible to someone. 12 of 39 evaluated podcasts sat in this
    state stamped 100.
    """
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
            "layer2_status": "unavailable", "layer2_score": None,
            "layer2_unavailable_reason": "transcribe_failed",
        }),
    )
    monkeypatch.setattr(media_quality_service, "_notify_if_pending", AsyncMock())
    monkeypatch.setattr(
        media_quality_service, "_fetch_render_audio_qa", AsyncMock(return_value=None),
    )
    captured: list[dict] = []
    monkeypatch.setattr(
        media_quality_service, "emit_finding", lambda **kw: captured.append(kw),
    )

    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=None)
    out = await media_quality_service.evaluate_podcast(
        db, "p", "/ep.mp3", site_config=_SC({"media.layer2.enabled": "true"}),
    )

    assert out["score"] is None
    finding = next(f for f in captured if f["kind"] == "media_layer2_unavailable")
    assert "transcribe_failed" in finding["title"]
    assert finding["severity"] == "warn"
    # The NULL must reach the column too, not just the returned dict.
    # Passing-branch UPDATE args are (sql, post_id, medium, score, signals).
    assert [c.args[3] for c in db.execute.await_args_list] == [None]


async def test_evaluate_podcast_silent_when_layer2_is_merely_disabled(
    monkeypatch,
) -> None:
    """A master switch OFF is an operator choice, not a failure — no finding."""
    monkeypatch.setattr(
        media_quality_service, "_probe_duration", AsyncMock(return_value=600.0),
    )
    monkeypatch.setattr(
        media_quality_service, "_probe_silence_ratio", AsyncMock(return_value=0.1),
    )
    monkeypatch.setattr(media_quality_service, "_file_size", lambda p: 8_000_000)
    monkeypatch.setattr(media_quality_service, "_notify_if_pending", AsyncMock())
    monkeypatch.setattr(
        media_quality_service, "_fetch_render_audio_qa", AsyncMock(return_value=None),
    )
    captured: list[dict] = []
    monkeypatch.setattr(
        media_quality_service, "emit_finding", lambda **kw: captured.append(kw),
    )

    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=None)
    out = await media_quality_service.evaluate_podcast(
        db, "p", "/ep.mp3", site_config=_SC({"media.layer2.enabled": "false"}),
    )

    assert out["layer2_status"] == "disabled"
    assert out["score"] is None
    assert not [f for f in captured if f["kind"] == "media_layer2_unavailable"]


# ---------------------------------------------------------------------------
# render-time qa.audio reaches the approval row
# ---------------------------------------------------------------------------


async def test_evaluate_podcast_folds_in_render_audio_qa(monkeypatch) -> None:
    """The in-pipeline qa.audio measurements land in quality_signals."""
    lane = {"silence_check": "warn", "mean_volume_db": -19.4, "volume_check": "ok"}

    class _DB:
        def __init__(self):
            self.execute = AsyncMock()

        async def fetchrow(self, sql, *_a, **_k):
            if "media_assets" in sql:
                return {"metadata": json.dumps({"audio_qa": lane})}
            return None

    monkeypatch.setattr(
        media_quality_service, "_probe_duration", AsyncMock(return_value=600.0),
    )
    monkeypatch.setattr(
        media_quality_service, "_probe_silence_ratio", AsyncMock(return_value=0.1),
    )
    monkeypatch.setattr(media_quality_service, "_file_size", lambda p: 8_000_000)
    monkeypatch.setattr(media_quality_service, "_notify_if_pending", AsyncMock())
    monkeypatch.setattr(media_quality_service, "emit_finding", lambda **kw: None)

    out = await media_quality_service.evaluate_podcast(_DB(), "p", "/ep.mp3")
    assert out["render_audio_qa"] == lane


async def test_render_audio_qa_absent_adds_no_key(monkeypatch) -> None:
    """No asset / no metadata / bad JSON → no key at all, never an empty dict."""
    class _DB:
        def __init__(self, row):
            self._row = row

        async def fetchrow(self, *_a, **_k):
            return self._row

    for row in (None, {"metadata": None}, {"metadata": "not json"},
                {"metadata": {}}, {"metadata": {"audio_qa": {}}}):
        got = await media_quality_service._fetch_render_audio_qa(_DB(row), "p")
        assert got is None, row


async def test_render_audio_qa_never_fails_the_eval() -> None:
    """A raising lookup is swallowed — surfacing must not break grading."""
    class _DB:
        async def fetchrow(self, *_a, **_k):
            raise RuntimeError("column does not exist")
    assert await media_quality_service._fetch_render_audio_qa(_DB(), "p") is None


# ---------------------------------------------------------------------------
# Layer-1 auto-reject may overturn an AUTO decision, never a human's
# ---------------------------------------------------------------------------


def test_layer1_reject_sql_guards_a_human_decision() -> None:
    """The eval now runs on auto-approved rows, so the flip must be scoped.

    It has to be able to reject an auto-approved 0-byte render while leaving
    an operator's approve/reject untouched — and it must still record the
    quality columns either way, so a human-decided row keeps its measurements.
    """
    sql = " ".join(media_quality_service._LAYER1_REJECT_SQL.split())
    guard = "WHEN status = 'pending' OR decided_by LIKE 'auto:%'"
    # Every decision column is conditional...
    for col in ("status =", "decided_at =", "decided_by =", "notes ="):
        head = sql.split(col, 1)[1].lstrip()
        assert head.startswith("CASE " + guard), col
    # ...and every quality column is not.
    assert "quality_score = $4," in sql
    assert "quality_signals = $5::jsonb," in sql
    assert "quality_evaluated_at = now()" in sql
