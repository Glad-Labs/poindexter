"""Media Quality Service — deterministic Layer 1 signals.

Computes cheap, fast signals on a freshly-generated podcast / video
file via ffprobe + ffmpeg. The signals land on the corresponding
``media_approvals`` row so the operator UI can surface "this podcast
is 90% silence" red flags alongside the approve/reject decision.

Layer 1 (this module):
- Deterministic signals only (no LLM calls)
- Audio: duration, silence ratio, file-size sanity
- Video: duration, file-size sanity
- Auto-reject threshold check at the end — if any signal crosses
  a configurable hard threshold (e.g. >50% silence, 0-byte file)
  the row gets ``decided_by='auto:layer1'`` rejection with the
  failing signal in ``notes``.

Layer 2 (future PR):
- Whisper transcription -> LLM faithfulness scoring vs source post
- Vision-model frame caption -> semantic match check

Thresholds
==========
Read from ``app_settings`` so the operator can tune per-deployment
without code changes (per ``feedback_db_first_config``). Defaults are
deliberately loose - better to surface a borderline file for human
review than auto-reject genuine content because thresholds were too
strict.

Subprocess invocation
=====================
Uses ``asyncio.create_subprocess_exec`` with a fixed argv list — no
shell, no interpolation of user content into a command string. All
``file_path`` values come from podcast_service/video_service write
paths (server-controlled), but argv-style invocation is the right
default regardless. ``shutil.which`` guards the call so a missing
ffmpeg gracefully degrades to "signals unavailable" rather than
crashing the gen path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from typing import Any

logger = logging.getLogger(__name__)


# Defaults — tuned to flag obvious garbage without false-positives on
# normal generations. Operator can override per-key in app_settings.
_DEFAULT_THRESHOLDS = {
    "media.podcast.min_duration_seconds": 30.0,
    "media.podcast.max_silence_ratio": 0.50,
    "media.podcast.min_file_size_bytes": 10_000,
    "media.video.min_duration_seconds": 10.0,
    "media.video.min_file_size_bytes": 50_000,
}


async def _run_argv(argv: list[str], *, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a subprocess via argv list (no shell) and return rc/stdout/stderr.

    Uses ``asyncio.create_subprocess_exec`` — the explicit-argv form —
    so file_path arguments can't trigger shell interpolation even if
    they ever contained metacharacters. ``timeout`` clamps how long
    ffprobe / ffmpeg can run on a single file.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode or 0,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


async def _probe_duration(file_path: str) -> float | None:
    """Return duration in seconds via ``ffprobe``. None on failure."""
    if not shutil.which("ffprobe"):
        return None
    try:
        rc, out, _ = await _run_argv(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", file_path,
            ],
        )
    except Exception:
        return None
    if rc != 0:
        return None
    try:
        data = json.loads(out)
        return float(data.get("format", {}).get("duration") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


async def _probe_silence_ratio(file_path: str, duration: float) -> float | None:
    """Return silence ratio (0.0 = no silence, 1.0 = all silence).

    Uses ``ffmpeg -af silencedetect`` which logs ``silence_duration``
    on stderr. We sum those durations + divide by total duration.

    Threshold for "silence": -50 dBFS (a quiet whisper barely
    registers above -40 dB; -50 catches anything that's not actual
    speech/music).
    """
    if not shutil.which("ffmpeg") or duration <= 0:
        return None
    try:
        rc, _, err = await _run_argv(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-i", file_path,
                "-af", "silencedetect=noise=-50dB:d=0.5",
                "-f", "null", "-",
            ],
            timeout=60.0,
        )
    except Exception:
        return None
    if rc != 0:
        return None
    total_silence = 0.0
    for match in re.finditer(r"silence_duration:\s*([\d.]+)", err):
        try:
            total_silence += float(match.group(1))
        except ValueError:
            continue
    return min(1.0, total_silence / duration)


def _file_size(file_path: str) -> int | None:
    try:
        return os.path.getsize(file_path)
    except OSError:
        return None


async def _get_threshold(db: Any, key: str) -> float:
    """Read a tunable threshold from app_settings; fall back to default."""
    row = await db.fetchrow(
        "SELECT value FROM app_settings WHERE key = $1 AND is_active = true",
        key,
    )
    if row and row["value"]:
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            logger.warning(
                "[media_quality] non-numeric app_settings.%s=%r; falling back to default",
                key, row["value"],
            )
    return _DEFAULT_THRESHOLDS[key]


async def evaluate_podcast(
    db: Any, post_id: str, file_path: str,
) -> dict[str, Any]:
    """Compute Layer 1 signals for a podcast file + store on the row.

    Returns the signals dict (also persisted to
    ``media_approvals.quality_signals``). When any hard-fail
    threshold trips, also flips the row's ``status='rejected'`` with
    ``decided_by='auto:layer1'`` so the operator UI surfaces the
    auto-rejection alongside human decisions.

    ``db`` accepts asyncpg Pool or Connection.
    """
    signals: dict[str, Any] = {"file_path": file_path}

    signals["file_size_bytes"] = _file_size(file_path)
    signals["duration_seconds"] = await _probe_duration(file_path)
    if signals["duration_seconds"]:
        signals["silence_ratio"] = await _probe_silence_ratio(
            file_path, signals["duration_seconds"],
        )

    min_dur = await _get_threshold(db, "media.podcast.min_duration_seconds")
    max_silence = await _get_threshold(db, "media.podcast.max_silence_ratio")
    min_size = await _get_threshold(db, "media.podcast.min_file_size_bytes")

    failures: list[str] = []
    if signals["file_size_bytes"] is not None and signals["file_size_bytes"] < min_size:
        failures.append(
            f"file_size_bytes={signals['file_size_bytes']} < {int(min_size)}",
        )
    if (
        signals["duration_seconds"] is not None
        and signals["duration_seconds"] < min_dur
    ):
        failures.append(
            f"duration_seconds={signals['duration_seconds']:.1f} < {min_dur}",
        )
    silence_ratio = signals.get("silence_ratio")
    if silence_ratio is not None and silence_ratio > max_silence:
        failures.append(
            f"silence_ratio={silence_ratio:.2f} > {max_silence}",
        )

    score = 0.0 if failures else 1.0
    signals["layer1_failures"] = failures
    signals["score"] = score

    if failures:
        await db.execute(
            """
            UPDATE media_approvals
            SET status = 'rejected',
                decided_at = now(),
                decided_by = 'auto:layer1',
                notes = $3,
                quality_score = $4,
                quality_signals = $5::jsonb,
                quality_evaluated_at = now()
            WHERE post_id = $1::uuid AND medium = $2
            """,
            post_id, "podcast", "; ".join(failures), score,
            json.dumps(signals),
        )
        logger.info(
            "[media_quality] podcast auto-rejected for %s: %s",
            post_id[:8], "; ".join(failures),
        )
    else:
        await db.execute(
            """
            UPDATE media_approvals
            SET quality_score = $3,
                quality_signals = $4::jsonb,
                quality_evaluated_at = now()
            WHERE post_id = $1::uuid AND medium = $2
            """,
            post_id, "podcast", score, json.dumps(signals),
        )
        logger.info(
            "[media_quality] podcast Layer 1 passed for %s "
            "(dur=%s silence=%s)",
            post_id[:8],
            signals.get("duration_seconds"),
            signals.get("silence_ratio"),
        )
        # Operator-surface ping: now that quality signals are on the
        # row, fire a Discord notification (skips silently when the
        # row's already approved via niche fast-path). Lazy-imported to
        # avoid a circular import (media_approval_service imports the
        # integration framework which can pull other services).
        await _notify_if_pending(db, post_id, "podcast")

    return signals


async def evaluate_video(
    db: Any, post_id: str, file_path: str, *, medium: str = "video",
) -> dict[str, Any]:
    """Compute Layer 1 signals for a video file + store on the row.

    ``medium`` defaults to ``"video"`` but accepts ``"video_short"``
    so the same eval applies to both flavors. Mirrors
    ``evaluate_podcast`` — same shape, same auto-reject logic, just
    different signals (no silence detection for video; frame metrics
    land in a Layer-2-adjacent follow-up).
    """
    if medium not in ("video", "video_short"):
        raise ValueError(f"evaluate_video: unsupported medium {medium!r}")

    signals: dict[str, Any] = {"file_path": file_path}
    signals["file_size_bytes"] = _file_size(file_path)
    signals["duration_seconds"] = await _probe_duration(file_path)

    min_dur = await _get_threshold(db, "media.video.min_duration_seconds")
    min_size = await _get_threshold(db, "media.video.min_file_size_bytes")

    failures: list[str] = []
    if signals["file_size_bytes"] is not None and signals["file_size_bytes"] < min_size:
        failures.append(
            f"file_size_bytes={signals['file_size_bytes']} < {int(min_size)}",
        )
    if (
        signals["duration_seconds"] is not None
        and signals["duration_seconds"] < min_dur
    ):
        failures.append(
            f"duration_seconds={signals['duration_seconds']:.1f} < {min_dur}",
        )

    score = 0.0 if failures else 1.0
    signals["layer1_failures"] = failures
    signals["score"] = score

    if failures:
        await db.execute(
            """
            UPDATE media_approvals
            SET status = 'rejected',
                decided_at = now(),
                decided_by = 'auto:layer1',
                notes = $3,
                quality_score = $4,
                quality_signals = $5::jsonb,
                quality_evaluated_at = now()
            WHERE post_id = $1::uuid AND medium = $2
            """,
            post_id, medium, "; ".join(failures), score,
            json.dumps(signals),
        )
        logger.info(
            "[media_quality] %s auto-rejected for %s: %s",
            medium, post_id[:8], "; ".join(failures),
        )
    else:
        await db.execute(
            """
            UPDATE media_approvals
            SET quality_score = $3,
                quality_signals = $4::jsonb,
                quality_evaluated_at = now()
            WHERE post_id = $1::uuid AND medium = $2
            """,
            post_id, medium, score, json.dumps(signals),
        )
        logger.info(
            "[media_quality] %s Layer 1 passed for %s (dur=%s)",
            medium, post_id[:8], signals.get("duration_seconds"),
        )
        # Operator-surface ping — same skip-on-non-pending logic as
        # the podcast path. See ``_notify_if_pending`` below.
        await _notify_if_pending(db, post_id, medium)

    return signals


async def _notify_if_pending(db: Any, post_id: str, medium: str) -> None:
    """Fire the Discord ops ping for a freshly-evaluated medium.

    Lazy-imports ``media_approval_service.notify_pending_for_review``
    so this module doesn't drag the integrations framework into its
    own import graph. Pure observability — failure is logged + swallowed
    by the helper itself, so callers don't need a defensive wrapper.
    """
    try:
        from services import media_approval_service

        await media_approval_service.notify_pending_for_review(
            db, post_id, medium,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[media_quality] notify_pending_for_review failed for %s/%s: %s",
            medium, post_id[:8], e,
        )
