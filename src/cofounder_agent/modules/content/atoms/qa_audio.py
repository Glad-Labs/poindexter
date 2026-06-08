"""qa.audio — Stage-2 narration audio QA atom (#1193, Phase 2).

Three deterministic checks on the podcast narration audio
(``podcast_audio_path``) using ffprobe and ffmpeg — no AI model required:

D. **Silence detection** (``ffmpeg silencedetect``): flag any silence segment
   longer than DB-configurable ``media.qa.audio.max_silence_s`` (default 3.0s).
   Long silences reveal TTS dropout / mid-narration truncation.

E. **Volume level check** (``ffmpeg volumedetect``): flag audio that is
   clipping (``max_volume >= -0.1 dBFS``) or inaudibly quiet
   (``mean_volume < -35 dBFS``). Both thresholds are DB-configurable.

F. **Duration vs script estimate** (ffprobe + word count): estimate the
   expected duration from ``podcast_script`` word count at a configurable
   speaking rate (default 150 wpm / 2.5 wps). Flag if the actual duration is
   below 40% or above 250% of the estimate — both ratios are DB-configurable.

All checks are **fail-soft**: a missing ffprobe/ffmpeg, an unreadable audio
file, or any subprocess exception records ``"unavailable"`` for that check and
emits NO finding. The render proceeds regardless.

**A QA failure MUST NEVER halt the graph.** The whole body is wrapped so the
atom always returns ``{"audio_qa_result": {...}}``. ``audio_qa_result`` MUST be
a declared ``PipelineState`` channel (#674 trap) or LangGraph silently drops it.

(Qwen2-Audio semantic quality check — originally planned for this atom — is
DEFERRED until the model is available. This atom handles the deterministic
subset that can ship today.)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

# Reuse subprocess helpers from the audit-era QA service — they handle
# asyncio subprocess lifecycle and ffprobe/ffmpeg argv composition.
from services.media_quality_service import _probe_duration, _run_argv
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# --- Configurable thresholds (all overridable via app_settings) ---

# Check D — silence detection
_DEFAULT_MAX_SILENCE_S = 3.0  # silence segments longer than this → warn

# Check E — volume levels (dBFS)
_DEFAULT_MIN_MEAN_VOLUME_DB = -35.0  # mean_volume below this → too quiet
_DEFAULT_MAX_VOLUME_CLIP_DB = -0.1   # max_volume at/above this → clipping

# Check F — duration vs script estimate
_DEFAULT_WPS = 2.5  # words per second (≈ 150 wpm typical narrator)
_DEFAULT_DURATION_SHORT_RATIO = 0.4  # actual < 0.4× estimate → suspiciously short
_DEFAULT_DURATION_LONG_RATIO = 2.5   # actual > 2.5× estimate → suspiciously long


ATOM_META = AtomMeta(
    name="qa.audio",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2 narration audio QA (#1193): silence detection (TTS dropout), "
        "volume-level check (clipping / inaudible), and duration-vs-script "
        "consistency. All checks are deterministic and fail-soft (no AI model)."
    ),
    inputs=(
        FieldSpec(
            name="podcast_audio_path",
            type="str",
            description="narration audio file path (WAV/MP3/AAC)",
        ),
        FieldSpec(
            name="podcast_script",
            type="str",
            description=(
                "source narration script — word count used for duration estimate "
                "(Check F). Optional; skipped when absent."
            ),
            required=False,
        ),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(
            name="site_config",
            type="object",
            description="DI seam (QA thresholds)",
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="audio_qa_result",
            type="dict",
            description=(
                "Per-check QA signals: silence_ok, volume_ok, duration_ok plus "
                "raw measurements and any finding kinds emitted."
            ),
        ),
    ),
    requires=("task_id",),
    produces=("audio_qa_result",),
    capability_tier=None,
    cost_class="compute",
    idempotent=True,
    side_effects=("reads audio file via ffprobe/ffmpeg",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)


# ---------------------------------------------------------------------------
# Threshold helpers
# ---------------------------------------------------------------------------


def _cfg_float(site_config: Any, key: str, default: float) -> float:
    if site_config is None:
        return default
    try:
        return float(site_config.get(key, default) or default)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


async def _detect_silences(
    audio_path: str, *, max_silence_s: float
) -> list[dict[str, float]] | None:
    """Return silence segments longer than ``max_silence_s``.

    Runs ``ffmpeg -af silencedetect=noise=-30dB:d=<N>`` and parses the
    structured output from stderr. Returns ``None`` when ffmpeg is absent
    or the run fails (caller records "unavailable").
    """
    if not shutil.which("ffmpeg"):
        return None
    try:
        run_result = await _run_argv(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-i", audio_path,
                "-af", f"silencedetect=noise=-30dB:d={max_silence_s:.2f}",
                "-f", "null", os.devnull,
            ],
            timeout=120.0,
        )
        err = run_result[2]
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.debug("[qa.audio] silencedetect failed: %s", exc)
        return None

    # Parse matched pairs:
    #   silence_start: 4.45
    #   silence_end: 7.32 | silence_duration: 2.87
    starts = re.findall(r"silence_start:\s*([\d.]+)", err)
    end_dur_pairs = re.findall(
        r"silence_end:\s*([\d.]+)\s*[|]\s*silence_duration:\s*([\d.]+)", err
    )
    segments = []
    for s, (e, d) in zip(starts, end_dur_pairs):
        segments.append(
            {"start_s": float(s), "end_s": float(e), "duration_s": float(d)}
        )
    return segments


async def _measure_volume(audio_path: str) -> dict[str, float] | None:
    """Return ``{mean_volume_db, max_volume_db}`` via ``ffmpeg volumedetect``.

    Returns ``None`` when ffmpeg is absent, the run fails, or the expected
    output lines are missing from stderr (caller records "unavailable").
    """
    if not shutil.which("ffmpeg"):
        return None
    try:
        run_result = await _run_argv(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-i", audio_path,
                "-af", "volumedetect",
                "-f", "null", os.devnull,
            ],
            timeout=120.0,
        )
        err = run_result[2]
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.debug("[qa.audio] volumedetect failed: %s", exc)
        return None

    mean_m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err)
    max_m = re.search(r"max_volume:\s*([-\d.]+)\s*dB", err)
    if not mean_m or not max_m:
        return None
    return {
        "mean_volume_db": float(mean_m.group(1)),
        "max_volume_db": float(max_m.group(1)),
    }


# ---------------------------------------------------------------------------
# Main atom entry point
# ---------------------------------------------------------------------------


async def run(state: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
    """QA the podcast narration audio. Best-effort — NEVER raises.

    Returns ``{"audio_qa_result": {<check>: {...}, ...}}``. An absent or
    unreadable ``podcast_audio_path`` skips all checks and returns an empty
    result dict.
    """
    task_id = state.get("task_id")
    site_config = state.get("site_config")
    audio_path = (state.get("podcast_audio_path") or "").strip()
    podcast_script = state.get("podcast_script") or ""

    result: dict[str, Any] = {}

    try:
        if not audio_path or not os.path.exists(audio_path):
            logger.debug(
                "[qa.audio] no podcast_audio_path for task %s — skipping", task_id
            )
            return {"audio_qa_result": result}

        # --- Check D: Silence detection ---
        max_sil = _cfg_float(
            site_config, "media.qa.audio.max_silence_s", _DEFAULT_MAX_SILENCE_S
        )
        silence_segs = await _detect_silences(audio_path, max_silence_s=max_sil)

        if silence_segs is None:
            result["silence_check"] = "unavailable"
        else:
            long_silences = [
                s for s in silence_segs if s["duration_s"] >= max_sil
            ]
            result["silence_check"] = "ok" if not long_silences else "warn"
            result["silence_long_segments"] = long_silences
            if long_silences:
                worst = max(long_silences, key=lambda x: x["duration_s"])
                emit_finding(
                    source="qa.audio",
                    kind="audio_long_silence",
                    title=(
                        f"Long silence in narration: {worst['duration_s']:.1f}s "
                        f"at {worst['start_s']:.1f}s"
                    ),
                    body=(
                        f"Task {task_id}: {len(long_silences)} silence segment(s) "
                        f"≥ {max_sil}s detected in {audio_path!r}. "
                        f"Longest: {worst['duration_s']:.2f}s starting at "
                        f"{worst['start_s']:.2f}s. "
                        "Suggests TTS dropout or audio truncation. Advisory only."
                    ),
                    severity="warn",
                    dedup_key=f"audio_long_silence:{task_id}",
                    extra={
                        "task_id": str(task_id or ""),
                        "segments": long_silences,
                        "max_silence_threshold_s": max_sil,
                    },
                )

        # --- Check E: Volume levels ---
        min_mean = _cfg_float(
            site_config,
            "media.qa.audio.min_mean_volume_db",
            _DEFAULT_MIN_MEAN_VOLUME_DB,
        )
        max_clip = _cfg_float(
            site_config,
            "media.qa.audio.max_volume_clip_db",
            _DEFAULT_MAX_VOLUME_CLIP_DB,
        )
        vol = await _measure_volume(audio_path)

        if vol is None:
            result["volume_check"] = "unavailable"
        else:
            result["mean_volume_db"] = vol["mean_volume_db"]
            result["max_volume_db"] = vol["max_volume_db"]

            clipping = vol["max_volume_db"] >= max_clip
            too_quiet = vol["mean_volume_db"] < min_mean

            if clipping:
                result["volume_check"] = "clipping"
                emit_finding(
                    source="qa.audio",
                    kind="audio_clipping",
                    title=(
                        f"Narration audio clipping: "
                        f"max_volume={vol['max_volume_db']:.1f} dBFS"
                    ),
                    body=(
                        f"Task {task_id}: max_volume {vol['max_volume_db']:.2f} dBFS "
                        f"≥ clip threshold {max_clip} dBFS for {audio_path!r}. "
                        "Audio may have distortion artifacts in the rendered video."
                    ),
                    severity="warn",
                    dedup_key=f"audio_clipping:{task_id}",
                    extra={
                        "task_id": str(task_id or ""),
                        "mean_volume_db": vol["mean_volume_db"],
                        "max_volume_db": vol["max_volume_db"],
                        "threshold_db": max_clip,
                    },
                )
            elif too_quiet:
                result["volume_check"] = "too_quiet"
                emit_finding(
                    source="qa.audio",
                    kind="audio_too_quiet",
                    title=(
                        f"Narration audio too quiet: "
                        f"mean_volume={vol['mean_volume_db']:.1f} dBFS"
                    ),
                    body=(
                        f"Task {task_id}: mean_volume {vol['mean_volume_db']:.2f} dBFS "
                        f"< threshold {min_mean} dBFS for {audio_path!r}. "
                        "Audio may be inaudible in the rendered video."
                    ),
                    severity="warn",
                    dedup_key=f"audio_too_quiet:{task_id}",
                    extra={
                        "task_id": str(task_id or ""),
                        "mean_volume_db": vol["mean_volume_db"],
                        "max_volume_db": vol["max_volume_db"],
                        "threshold_db": min_mean,
                    },
                )
            else:
                result["volume_check"] = "ok"

        # --- Check F: Duration vs script estimate ---
        actual_duration = await _probe_duration(audio_path)
        result["actual_duration_s"] = actual_duration

        if actual_duration is None:
            result["duration_check"] = "unavailable"
        elif podcast_script.strip():
            wps = _cfg_float(
                site_config, "media.qa.audio.words_per_second", _DEFAULT_WPS
            )
            short_r = _cfg_float(
                site_config,
                "media.qa.audio.duration_short_ratio",
                _DEFAULT_DURATION_SHORT_RATIO,
            )
            long_r = _cfg_float(
                site_config,
                "media.qa.audio.duration_long_ratio",
                _DEFAULT_DURATION_LONG_RATIO,
            )
            word_count = len(podcast_script.split())
            expected = max(word_count / wps, 1.0)  # floor 1s to avoid div-by-zero
            result["estimated_duration_s"] = round(expected, 1)
            ratio = actual_duration / expected

            if ratio < short_r:
                result["duration_check"] = "too_short"
                emit_finding(
                    source="qa.audio",
                    kind="audio_duration_mismatch",
                    title=(
                        f"Narration audio shorter than expected: "
                        f"{actual_duration:.1f}s (expected ~{expected:.0f}s)"
                    ),
                    body=(
                        f"Task {task_id}: actual {actual_duration:.2f}s is only "
                        f"{ratio:.0%} of estimated {expected:.1f}s "
                        f"({word_count} words at {wps} wps). "
                        "Suggests TTS produced a truncated/incomplete narration."
                    ),
                    severity="warn",
                    dedup_key=f"audio_duration_mismatch:{task_id}",
                    extra={
                        "task_id": str(task_id or ""),
                        "actual_s": actual_duration,
                        "estimated_s": expected,
                        "ratio": ratio,
                        "word_count": word_count,
                    },
                )
            elif ratio > long_r:
                result["duration_check"] = "too_long"
                emit_finding(
                    source="qa.audio",
                    kind="audio_duration_mismatch",
                    title=(
                        f"Narration audio longer than expected: "
                        f"{actual_duration:.1f}s (expected ~{expected:.0f}s)"
                    ),
                    body=(
                        f"Task {task_id}: actual {actual_duration:.2f}s is "
                        f"{ratio:.0%} of estimated {expected:.1f}s "
                        f"({word_count} words at {wps} wps). "
                        "Suggests the audio contains unexpected extra content."
                    ),
                    severity="info",
                    dedup_key=f"audio_duration_mismatch:{task_id}",
                    extra={
                        "task_id": str(task_id or ""),
                        "actual_s": actual_duration,
                        "estimated_s": expected,
                        "ratio": ratio,
                        "word_count": word_count,
                    },
                )
            else:
                result["duration_check"] = "ok"
        else:
            # No script to estimate against — just record the duration.
            result["duration_check"] = "no_script"

    except Exception as exc:  # noqa: BLE001 — a QA failure must never halt the graph
        logger.exception("[qa.audio] unexpected error for task %s: %s", task_id, exc)

    return {"audio_qa_result": result}


__all__ = ["ATOM_META", "run"]
