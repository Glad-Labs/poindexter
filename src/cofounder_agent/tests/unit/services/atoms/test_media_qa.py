"""Unit tests for the media.qa Stage-2 media-QA atom (Plan 6, #1193).

QA-checks the rendered videos AFTER the render nodes — three checks per asset:

A. **A/V duration sync** (deterministic): probe the rendered video duration and
   compare to the lane's ACTUAL narration audio when present (silent-tail fix
   — asymmetric bounds: under-run beyond ``media.qa.av_sync_tolerance_s`` cuts
   the speaker off; over-run beyond ``video_fit_trailing_hold_seconds`` + the
   tolerance is a silent tail), falling back to the shot-list
   ``total_duration_s`` when no narration exists. Beyond a bound emits an
   advisory ``av_desync`` finding.
B. **Caption presence** (deterministic): a missing ``caption_srt_path`` (captions
   are best-effort upstream) emits a single advisory ``missing_captions`` finding.
C. **Frame human-detection** (vision, GATED + fail-soft): when enabled, extract a
   midpoint frame and ask the local vision model whether it contains a photoreal
   human (policy #675). A "yes" emits an advisory ``human_in_frame`` finding. The
   whole check is fail-soft — a missing ffmpeg / vision error records "unavailable"
   and emits NO finding (don't cry wolf when the tool isn't there).

A QA failure must NEVER halt the graph — the whole atom body is wrapped so it
always returns a ``media_qa_result`` dict, even if a check raises.

We patch ``_probe_duration``, the ffmpeg/_run_argv frame extract, and the vision
``dispatch_complete`` where they are imported in ``media_qa`` (the call-site
module), per the standard mocking discipline mirrored from
test_media_transcribe_narration.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.atoms import media_qa
from modules.content.atoms.media_qa import run as qa_run


def _existing_file(tmp_path, name="video.mp4"):
    """Create a real file on disk so the atom's os.path.exists gate passes."""
    p = tmp_path / name
    p.write_bytes(b"\x00" * 1024)
    return str(p)


def _site_config(**overrides):
    """A SiteConfig double whose .get reads from a plain dict (str values)."""
    cfg = {
        "media_qa_frame_detection_enabled": "true",
        "media.qa.av_sync_tolerance_s": "2.0",
        # poindexter#716 — vision_alt_model is now required (no hardcoded
        # fallback in production code); tests that exercise the vision path
        # must supply it explicitly via this helper.
        "vision_alt_model": "qwen3-vl:30b",
        **overrides,
    }
    return SimpleNamespace(get=lambda key, default=None: cfg.get(key, default))


@asynccontextmanager
async def _noop_gpu_lock(*_args: Any, **_kwargs: Any):  # type: ignore[misc]
    """Drop-in for ``gpu.lock`` that never acquires the real GPU advisory lock.

    Mirrors the seam-test idiom in ``test_platform_dispatch_seam.py``.
    """
    yield


@pytest.fixture(autouse=True)
def _neutralize_gpu_lock():
    """Isolate every media.qa test from the REAL cross-process GPU lock.

    ``_detect_human_in_frame`` wraps its vision dispatch in
    ``gpu.lock("ollama", ...)`` (``services.gpu_scheduler``), which takes a
    Postgres ``pg_advisory_lock`` on a dedicated connection whenever a DSN
    resolves. On the operator PC that lock is held by the live worker, so a unit
    test that acquires it blocks until ``gpu_lock_acquire_timeout_seconds`` and
    the atom's fail-soft ``except`` records "unavailable" — intermittently
    flaking the human_found/clean assertions. CI has no bootstrap.toml DSN, so
    the lock no-ops there and the flake stays invisible (nightly green, local
    red).

    ``_detect_human_in_frame`` does a *function-local*
    ``from services.gpu_scheduler import gpu``, so the patch must target the
    SOURCE singleton's ``lock`` attribute — not a call-site module global, which
    a function-local import would ignore. Patching the whole ``lock`` method
    also neutralises its ``finally`` (which otherwise writes ``gpu_task_sessions``
    to the DB). This autouse fixture covers every test in the file — the four
    that reach the lock today (model_says_no / model_says_yes / strips_think_block
    / vision_error) and any future vision test — so none contend on real
    infrastructure. See ``test_human_detect_isolated_from_real_gpu_lock`` for the
    regression tripwire.
    """
    with patch("services.gpu_scheduler.gpu.lock", _noop_gpu_lock):
        yield


# ---------------------------------------------------------------------------
# Check A — A/V duration sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_av_sync_within_tolerance_emits_no_desync(tmp_path):
    """actual ≈ expected (within tolerance) → av_sync_ok True, no av_desync."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-avok",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        # frame detection disabled to isolate check A
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.5)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    result = out["media_qa_result"]
    assert result["long"]["av_sync_ok"] is True
    assert result["long"]["actual_duration_s"] == 60.5
    assert result["long"]["expected_duration_s"] == 60.0
    # No av_desync finding for an in-tolerance asset.
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "av_desync" not in kinds


@pytest.mark.asyncio
async def test_av_sync_beyond_tolerance_emits_desync(tmp_path):
    """actual far from expected (beyond tolerance) → av_sync_ok False + finding."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-avbad",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=50.0)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    result = out["media_qa_result"]
    assert result["long"]["av_sync_ok"] is False
    desync = [c for c in mock_emit.call_args_list if c.kwargs.get("kind") == "av_desync"]
    assert len(desync) == 1
    assert desync[0].kwargs["severity"] == "warn"


@pytest.mark.asyncio
async def test_av_sync_probe_failure_records_unknown(tmp_path):
    """probe returns None → av_sync_ok None (unknown), no av_desync finding."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-avnone",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=None)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    result = out["media_qa_result"]
    assert result["long"]["av_sync_ok"] is None
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "av_desync" not in kinds


@pytest.mark.asyncio
async def test_av_sync_custom_tolerance_from_site_config(tmp_path):
    """Tolerance is DB-configurable via media.qa.av_sync_tolerance_s."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    # 5s drift; default tol 2.0 would flag, but we crank tolerance to 10.0.
    state = {
        "task_id": "t-avtol",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(
            media_qa_frame_detection_enabled="false",
            **{"media.qa.av_sync_tolerance_s": "10.0"},
        ),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=55.0)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["av_sync_ok"] is True
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "av_desync" not in kinds


def _probe_by_path(mapping: dict[str, float | None]):
    """AsyncMock side_effect: return a per-path duration (video vs narration)."""
    async def _probe(path, *args, **kwargs):
        return mapping.get(str(path))
    return _probe


@pytest.mark.asyncio
async def test_av_sync_narration_reference_silent_tail_emits_desync(tmp_path):
    """The 2026-07 rejection shape: video faithfully renders a 300s plan over a
    175s narration → zero drift vs PLAN (the old check shipped it silently) but
    a 125s silent tail vs the voice → av_desync, reference=narration."""
    long_video = _existing_file(tmp_path, "long.mp4")
    narration = _existing_file(tmp_path, "narration.mp3")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-tail",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 300.0},
        "long_narration_audio_path": narration,
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(
        media_qa, "_probe_duration",
        AsyncMock(side_effect=_probe_by_path({long_video: 300.0, narration: 175.0})),
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    result = out["media_qa_result"]["long"]
    assert result["av_sync_ok"] is False
    assert result["av_sync_reference"] == "narration"
    assert result["narration_duration_s"] == 175.0
    desync = [c for c in mock_emit.call_args_list if c.kwargs.get("kind") == "av_desync"]
    assert len(desync) == 1
    assert desync[0].kwargs["extra"]["reference"] == "narration"
    assert "silent" in desync[0].kwargs["body"]


@pytest.mark.asyncio
async def test_av_sync_narration_reference_within_hold_is_ok(tmp_path):
    """A fitted render lands at narration + the deliberate outro hold — that
    must NOT flag (the old plan-compare false-alarmed on every fitted short)."""
    long_video = _existing_file(tmp_path, "long.mp4")
    narration = _existing_file(tmp_path, "narration.mp3")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-hold",
        "long_video_path": long_video,
        # Plan is far from actual — irrelevant once narration is the reference.
        "video_shot_list": {"total_duration_s": 300.0},
        "long_narration_audio_path": narration,
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(
        media_qa, "_probe_duration",
        AsyncMock(side_effect=_probe_by_path({long_video: 178.0, narration: 175.0})),
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    result = out["media_qa_result"]["long"]
    assert result["av_sync_ok"] is True
    assert result["av_sync_reference"] == "narration"
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "av_desync" not in kinds


@pytest.mark.asyncio
async def test_av_sync_narration_reference_voice_cut_emits_desync(tmp_path):
    """Video shorter than narration beyond tolerance = the speaker is cut off
    → av_desync (the under-run direction keeps the tight 2s tolerance)."""
    long_video = _existing_file(tmp_path, "long.mp4")
    narration = _existing_file(tmp_path, "narration.mp3")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-cut",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 300.0},
        "long_narration_audio_path": narration,
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(
        media_qa, "_probe_duration",
        AsyncMock(side_effect=_probe_by_path({long_video: 170.0, narration: 175.0})),
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["av_sync_ok"] is False
    desync = [c for c in mock_emit.call_args_list if c.kwargs.get("kind") == "av_desync"]
    assert len(desync) == 1
    assert "cut off" in desync[0].kwargs["body"]


@pytest.mark.asyncio
async def test_av_sync_falls_back_to_plan_without_narration(tmp_path):
    """No narration audio on state → the plan total remains the reference
    (backcompat: silent/no-narration renders still get a sanity check)."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-fallback",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.5)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    result = out["media_qa_result"]["long"]
    assert result["av_sync_ok"] is True
    assert result["av_sync_reference"] == "plan"


@pytest.mark.asyncio
async def test_captions_present_via_per_lane_key(tmp_path):
    """#689 made captions per-lane (long_caption_srt_path); Check B read the
    dead shared key and false-alarmed missing_captions on every render while
    the per-lane SRTs were transcribed and burned fine (2026-08-01 e68e)."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-lane-cap",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "long_caption_srt_path": str(tmp_path / "long.srt"),
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["caption_present"] is True
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "missing_captions" not in kinds


# ---------------------------------------------------------------------------
# Check B — caption presence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captions_present_no_finding(tmp_path):
    """caption_srt_path set → caption_present True, no missing_captions finding."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-cap",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "caption_srt_path": "/tmp/captions.srt",
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["caption_present"] is True
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "missing_captions" not in kinds


@pytest.mark.asyncio
async def test_captions_absent_emits_finding_once(tmp_path):
    """No caption path on EITHER lane → caption_present False per asset and
    one missing_captions finding PER LANE (per-lane since the #689 caption
    split — the old once-total emit hid which lane lacked its track)."""
    long_video = _existing_file(tmp_path, "long.mp4")
    short_video = _existing_file(tmp_path, "short.mp4")
    mock_emit = MagicMock()
    state = {
        "task_id": "t-nocap",
        "long_video_path": long_video,
        "short_video_path": short_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "short_shot_list": {"total_duration_s": 30.0},
        # caption_srt_path absent
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    result = out["media_qa_result"]
    assert result["long"]["caption_present"] is False
    assert result["short"]["caption_present"] is False
    missing = [c for c in mock_emit.call_args_list if c.kwargs.get("kind") == "missing_captions"]
    assert len(missing) == 2
    assert {c.kwargs["extra"]["label"] for c in missing} == {"long", "short"}
    assert all(c.kwargs["severity"] == "info" for c in missing)


# ---------------------------------------------------------------------------
# Check C — frame human-detection (gated + fail-soft)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frame_detection_disabled_is_skipped(tmp_path):
    """Gate off → human_detection 'disabled', vision never called, no finding."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    mock_dispatch = AsyncMock()
    state = {
        "task_id": "t-disabled",
        "platform": SimpleNamespace(dispatch=SimpleNamespace(complete=mock_dispatch)),
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["human_detection"] == "disabled"
    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_frame_detection_ffmpeg_unavailable_failsoft(tmp_path):
    """ffmpeg extract fails → human_detection 'unavailable', no finding,
    vision NOT called (don't cry wolf when the tool isn't there)."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    mock_dispatch = AsyncMock()
    state = {
        "task_id": "t-noffmpeg",
        "platform": SimpleNamespace(dispatch=SimpleNamespace(complete=mock_dispatch)),
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(),
    }
    # _extract_frame returns None (ffmpeg missing / extract failed).
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "_extract_frame", AsyncMock(return_value=None)
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["human_detection"] == "unavailable"
    mock_dispatch.assert_not_awaited()
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "human_in_frame" not in kinds


@pytest.mark.asyncio
async def test_frame_detection_model_says_no_is_clean(tmp_path):
    """Vision model answers 'no' → human_detection 'clean', no finding."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    mock_dispatch = AsyncMock(return_value=SimpleNamespace(text="no"))
    state = {
        "task_id": "t-clean",
        "platform": SimpleNamespace(dispatch=SimpleNamespace(complete=mock_dispatch)),
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "_extract_frame", AsyncMock(return_value=b"PNGBYTES")
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["human_detection"] == "clean"
    mock_dispatch.assert_awaited()
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "human_in_frame" not in kinds


@pytest.mark.asyncio
async def test_frame_detection_model_says_yes_emits_finding(tmp_path):
    """Vision model answers 'yes' → human_detection 'human_found' + warn finding."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    mock_dispatch = AsyncMock(return_value=SimpleNamespace(text="Yes"))
    state = {
        "task_id": "t-human",
        "platform": SimpleNamespace(dispatch=SimpleNamespace(complete=mock_dispatch)),
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "_extract_frame", AsyncMock(return_value=b"PNGBYTES")
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["human_detection"] == "human_found"
    human = [c for c in mock_emit.call_args_list if c.kwargs.get("kind") == "human_in_frame"]
    assert len(human) == 1
    assert human[0].kwargs["severity"] == "warn"


@pytest.mark.asyncio
async def test_frame_detection_strips_think_block(tmp_path):
    """Reasoning-model <think>…</think> is stripped before parsing yes/no."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    mock_dispatch = AsyncMock(
        return_value=SimpleNamespace(text="<think>let me look...</think>\nyes")
    )
    state = {
        "task_id": "t-think",
        "platform": SimpleNamespace(dispatch=SimpleNamespace(complete=mock_dispatch)),
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "_extract_frame", AsyncMock(return_value=b"PNGBYTES")
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["human_detection"] == "human_found"


@pytest.mark.asyncio
async def test_frame_detection_vision_error_failsoft(tmp_path):
    """dispatch_complete raises → human_detection 'unavailable', no finding."""
    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    mock_dispatch = AsyncMock(side_effect=RuntimeError("vision boom"))
    state = {
        "task_id": "t-visionerr",
        "platform": SimpleNamespace(dispatch=SimpleNamespace(complete=mock_dispatch)),
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "_extract_frame", AsyncMock(return_value=b"PNGBYTES")
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    assert out["media_qa_result"]["long"]["human_detection"] == "unavailable"
    kinds = [c.kwargs.get("kind") for c in mock_emit.call_args_list]
    assert "human_in_frame" not in kinds


@pytest.mark.asyncio
async def test_human_detect_isolated_from_real_gpu_lock(tmp_path, monkeypatch):
    """The media.qa vision path must NOT acquire the real cross-process GPU
    advisory lock in a unit test.

    ``_detect_human_in_frame`` wraps its vision dispatch in
    ``gpu.lock("ollama", ...)`` (services.gpu_scheduler), which takes a Postgres
    ``pg_advisory_lock`` on a dedicated connection whenever a DSN resolves. On
    the operator PC that lock is held by the live worker, so a test that reaches
    the REAL lock blocks until ``gpu_lock_acquire_timeout_seconds`` and the
    atom's fail-soft ``except`` records "unavailable" — flaking every
    human_found/clean assertion (``'unavailable' == 'human_found'``). CI has no
    bootstrap.toml DSN, so the lock no-ops there and the flake stays invisible
    (nightly green, local red).

    Tripwire: sabotage the REAL lock's cross-process acquire so that *reaching*
    it is fatal to the result. The module's autouse GPU-lock isolation keeps the
    vision path off the real lock, so the happy path still returns "human_found".
    If that isolation regresses, this test acquires the sabotaged lock →
    GpuLockTimeoutError → "unavailable" and fails loudly in CI — no live-worker
    contention required to reproduce the flake.
    """
    from services import gpu_scheduler

    async def _sabotaged_acquire(self, *_args, **_kwargs):
        # Stand in for a contended cross-process holder (the live worker on the
        # operator PC) WITHOUT opening a real DB connection — raise the same
        # typed error the real acquire raises when it times out on a held lock.
        raise gpu_scheduler.GpuLockTimeoutError(
            "simulated contended GPU lock — media.qa reached the REAL "
            "pg_advisory_lock instead of the test no-op"
        )

    monkeypatch.setattr(
        gpu_scheduler.GPUScheduler,
        "_acquire_pg_advisory_lock",
        _sabotaged_acquire,
    )

    long_video = _existing_file(tmp_path, "long.mp4")
    mock_emit = MagicMock()
    mock_dispatch = AsyncMock(return_value=SimpleNamespace(text="yes"))
    state = {
        "task_id": "t-lockguard",
        "platform": SimpleNamespace(dispatch=SimpleNamespace(complete=mock_dispatch)),
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(),
    }
    with patch.object(media_qa, "_probe_duration", AsyncMock(return_value=60.0)), patch.object(
        media_qa, "_extract_frame", AsyncMock(return_value=b"PNGBYTES")
    ), patch.object(media_qa, "emit_finding", mock_emit):
        out = await qa_run(state)

    # With the module's GPU-lock isolation in place the sabotaged real acquire is
    # never reached, so the vision dispatch runs and the "yes" answer stands.
    assert out["media_qa_result"]["long"]["human_detection"] == "human_found"
    mock_dispatch.assert_awaited()


# ---------------------------------------------------------------------------
# Asset selection + never-raise discipline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_video_path_is_skipped_no_probe(tmp_path):
    """An empty video path (asset not rendered) is skipped — not in the result,
    and _probe_duration is never called for it."""
    short_video = _existing_file(tmp_path, "short.mp4")
    probe = AsyncMock(return_value=30.0)
    state = {
        "task_id": "t-skip",
        "long_video_path": "",  # not rendered
        "short_video_path": short_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "short_shot_list": {"total_duration_s": 30.0},
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", probe), patch.object(
        media_qa, "emit_finding", MagicMock()
    ):
        out = await qa_run(state)

    result = out["media_qa_result"]
    assert "long" not in result  # skipped — empty path
    assert "short" in result
    # Probe called only for the short asset.
    assert probe.await_count == 1


@pytest.mark.asyncio
async def test_nonexistent_video_path_is_skipped(tmp_path):
    """A non-empty path that does not exist on disk is skipped."""
    probe = AsyncMock(return_value=60.0)
    state = {
        "task_id": "t-nofile",
        "long_video_path": "/tmp/does-not-exist-12345.mp4",
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    with patch.object(media_qa, "_probe_duration", probe), patch.object(
        media_qa, "emit_finding", MagicMock()
    ):
        out = await qa_run(state)

    assert "long" not in out["media_qa_result"]
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_atom_never_raises_even_if_a_check_raises(tmp_path):
    """If a check raises mid-run, the atom still returns a media_qa_result dict
    (a QA failure must never halt the graph)."""
    long_video = _existing_file(tmp_path, "long.mp4")
    state = {
        "task_id": "t-raise",
        "long_video_path": long_video,
        "video_shot_list": {"total_duration_s": 60.0},
        "site_config": _site_config(media_qa_frame_detection_enabled="false"),
    }
    # Make the deterministic probe blow up.
    with patch.object(
        media_qa, "_probe_duration", AsyncMock(side_effect=RuntimeError("probe boom"))
    ), patch.object(media_qa, "emit_finding", MagicMock()):
        out = await qa_run(state)

    assert isinstance(out, dict)
    assert "media_qa_result" in out
    assert isinstance(out["media_qa_result"], dict)


@pytest.mark.asyncio
async def test_no_rendered_assets_returns_empty_result(tmp_path):
    """Both video paths empty → empty result dict, no probe, no findings."""
    probe = AsyncMock(return_value=60.0)
    mock_emit = MagicMock()
    state = {
        "task_id": "t-none",
        "long_video_path": "",
        "short_video_path": "",
        "site_config": _site_config(),
    }
    with patch.object(media_qa, "_probe_duration", probe), patch.object(
        media_qa, "emit_finding", mock_emit
    ):
        out = await qa_run(state)

    assert out["media_qa_result"] == {}
    probe.assert_not_awaited()
    mock_emit.assert_not_called()


# ---------------------------------------------------------------------------
# ATOM_META shape
# ---------------------------------------------------------------------------


def test_atom_meta_shape():
    from modules.content.atoms.media_qa import ATOM_META

    assert ATOM_META.name == "media.qa"
    assert ATOM_META.type == "atom"
    assert ATOM_META.requires == ("task_id",)
    assert ATOM_META.produces == ("media_qa_result",)
    out_names = {f.name for f in ATOM_META.outputs}
    assert "media_qa_result" in out_names
