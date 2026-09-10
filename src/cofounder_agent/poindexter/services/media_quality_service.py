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

Layer 2 (semantic scoring, spec 2026-07-09):
- Composes a 0-100 quality_score on top of Layer 1: a hard-fail floors
  it at 0, a pass hands off to the per-medium Layer-2 runner, which means
  whichever of its signals are available.
- Video: shot-fidelity aggregate (mean of the per-shot vision scores the
  renderer already emitted — zero marginal cost) + topic-match (one
  vision call scoring whether N sampled frames belong in an article with
  this title).
- Podcast: faithfulness — full-episode Whisper transcription -> one LLM
  call judging the transcript against the source post body.
- Advisory-first: Layer 2 never auto-rejects; sub-scores below their
  configured minima emit advisory findings only.

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
import base64
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any

from services.caption_providers import get_caption_provider
from services.llm_providers.dispatcher import dispatch_complete
from utils.findings import emit_finding

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

# Layer-2 LLM-judge budget. qwen3-vl (and other thinking models) share the
# max_tokens budget between the <think> trace and the JSON answer; a small
# budget silently starves the answer to empty. 1024 keeps the tiny JSON reply
# alive — the exact PR #2224 / poindexter#563 lesson, mirrored from
# shot_vision_qa. Used by both the vision (topic-match) and text (faithfulness)
# judges.
_LAYER2_VISION_MAX_TOKENS = 1024

_TOPIC_MATCH_PROMPT = (
    "You are grading whether a video still belongs in an article titled "
    "{title!r}. Consider subject, style, and on-topic-ness. Reply with ONLY a "
    'JSON object: {{"score": <0-100 integer>}}. 100 = perfectly on-topic, '
    "0 = unrelated."
)

_FAITHFULNESS_PROMPT = (
    "Compare a podcast transcript against the source article it was generated "
    "from. Does the episode faithfully represent the article's key claims "
    "(no fabrication, no major omission, no contradiction)?\n\n"
    "SOURCE ARTICLE:\n{source}\n\nEPISODE TRANSCRIPT:\n{transcript}\n\n"
    'Reply with ONLY JSON: {{"score": <0-100 integer>, "reason": "<one sentence>"}}. '
    "100 = fully faithful, 0 = unrelated or fabricated."
)


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


# Outcomes whose rows carry no meaningful per-shot vision score: a reused
# clip was scored on a prior run (double-counting it skews the aggregate),
# and an unscored clip never got a vision judgment at all. Both are excluded
# from the shot-fidelity mean but still count toward coverage's denominator.
_SHOT_EXCLUDE_OUTCOMES = frozenset({"unscored", "skipped_reused"})


async def _aggregate_shot_qa_scores(db: Any, post_id: str) -> dict[str, Any]:
    """Aggregate per-shot vision QA scores for a post into a fidelity signal.

    Reads the ``video_shot_rendered`` audit rows the shot-list renderer
    already emits (one per shot, carrying ``qa_score`` 0-100 + ``qa_outcome``)
    and folds them into:

    - ``shot_fidelity`` — mean of the scored shots' ``qa_score`` (0-100), or
      ``None`` when no shot carried a usable score (all reused/unscored).
    - ``shot_coverage`` — fraction of shots that were actually scored
      (``scored / total``), a confidence weight on the fidelity number.
    - ``scored`` / ``total`` — the raw counts behind those ratios.

    This is the zero-new-LLM-cost half of Layer 2: the vision scoring already
    happened at render time, so we only re-read audit rows here.
    """
    rows = await db.fetch(
        """
        SELECT (details->>'qa_score')::float AS qa_score,
               details->>'qa_outcome'        AS outcome
        FROM audit_log
        WHERE event_type = 'video_shot_rendered'
          AND details->>'post_id' = $1
        """,
        post_id,
    )
    total = len(rows)
    scored_vals = [
        r["qa_score"]
        for r in rows
        if r["qa_score"] is not None
        and (r["outcome"] or "") not in _SHOT_EXCLUDE_OUTCOMES
    ]
    scored = len(scored_vals)
    fidelity = (sum(scored_vals) / scored) if scored else None
    coverage = (scored / total) if total else 0.0
    return {
        "shot_fidelity": fidelity,
        "shot_coverage": coverage,
        "scored": scored,
        "total": total,
    }


async def _extract_frame_at(file_path: str, at_s: float) -> bytes | None:
    """Extract ONE frame at ``at_s`` seconds as PNG bytes. None on any failure.

    Reuses the module's ``_run_argv`` (no shell) and the fast ``-ss``-before-``-i``
    seek. Fail-soft: missing ffmpeg / failed extract / missing output → None.
    """
    if not shutil.which("ffmpeg"):
        return None
    out = os.path.join(
        tempfile.gettempdir(),
        f"mediaqa_topic_{os.getpid()}_{abs(hash((file_path, at_s))) % 10**8}.png",
    )
    try:
        rc, _, _ = await _run_argv(
            [
                "ffmpeg", "-y", "-hide_banner", "-nostats",
                "-ss", f"{max(0.0, at_s):.3f}", "-i", file_path,
                "-frames:v", "1", out,
            ],
        )
    except Exception:  # noqa: BLE001 — fail-soft
        # silent-ok: best-effort frame grab; a failed ffmpeg extract just skips
        # this frame — we sample several timestamps and need only one to land.
        return None
    if rc != 0:
        return None
    try:
        with open(out, "rb") as f:
            return f.read() or None
    except OSError:
        return None
    finally:
        try:
            os.remove(out)
        except OSError:
            # silent-ok: best-effort temp-file cleanup; an unlink failure on a
            # scratch PNG is not operator-actionable.
            pass


def _resolve_topic_match_model(site_config: Any) -> str:
    """media.video.topic_match_model, falling back to qa_vision_model. '' → skip."""
    raw = (site_config.get("media.video.topic_match_model", "") or "").strip()
    if raw:
        return raw
    return (site_config.get("qa_vision_model", "") or "").strip()


async def _score_video_topic_match(
    db: Any, file_path: str, title: str, site_config: Any,
) -> float | None:
    """Sample N frames from the composed video and vision-score topic match.

    One ``dispatch_complete`` call carrying all N frames → one 0-100 score
    (does the footage actually belong in an article with this title?).
    Returns None (fail-soft) on: no model, no duration, no extractable frame,
    dispatch error, or unparseable response. Never raises.
    """
    model = _resolve_topic_match_model(site_config)
    if not model:
        return None
    try:
        n = int(site_config.get("media.video.topic_match_frames", "3") or "3")
    except (TypeError, ValueError):
        n = 3
    n = max(1, n)

    duration = await _probe_duration(file_path)
    if not duration or duration <= 0:
        return None

    # Even interior timestamps: duration*i/(n+1) for i in 1..n (skips the
    # black first/last frames a boundary sample would catch).
    frames_b64: list[str] = []
    for i in range(1, n + 1):
        at_s = duration * i / (n + 1)
        png = await _extract_frame_at(file_path, at_s)
        if png:
            frames_b64.append(base64.b64encode(png).decode("ascii"))
    if not frames_b64:
        return None

    # OpenAI-multimodal message; LiteLLM translates image_url data URIs into
    # Ollama's native images array (same shape shot_vision_qa proved out).
    content: list[dict[str, Any]] = [
        {"type": "text", "text": _TOPIC_MATCH_PROMPT.format(title=title or "this topic")},
    ]
    for b64 in frames_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    messages = [{"role": "user", "content": content}]

    pool = getattr(db, "pool", db)
    try:
        completion = await dispatch_complete(
            pool, messages, model,
            tier="standard", phase="media_qa_topic_match",
            temperature=0.2, max_tokens=_LAYER2_VISION_MAX_TOKENS, timeout_s=150.0,
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("[media_quality] topic-match dispatch failed: %s", exc)
        return None

    match = re.search(r'"score"\s*:\s*([\d.]+)', text)
    if not match:
        return None
    try:
        return max(0.0, min(100.0, float(match.group(1))))
    except (TypeError, ValueError):
        return None


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


# ---------------------------------------------------------------------------
# Layer 2 — semantic scoring (spec 2026-07-09)
#
# Layer 1 answers "is this file broken?" (binary, deterministic). Layer 2
# answers "is this file *good*?" (0-100, semantic). The two compose: a
# Layer-1 hard-fail floors the score at 0; a Layer-1 pass hands off to the
# Layer-2 runner, which returns a 0-100 composite (or None → 100 "nothing
# measurable is wrong"). Advisory-first — nothing here auto-rejects.
# ---------------------------------------------------------------------------


def _layer2_enabled(site_config: Any) -> bool:
    """Master switch. Absent site_config (legacy callers) → Layer 2 off."""
    if site_config is None:
        return False
    raw = site_config.get("media.layer2.enabled", "true")
    return str(raw if raw is not None else "true").strip().lower() in (
        "true", "1", "yes",
    )


def _cfg_float(site_config: Any, key: str, default: float) -> float:
    """Read a float tunable off site_config; fall back on missing/garbled."""
    try:
        return float(site_config.get(key, default) or default)
    except (TypeError, ValueError):
        return default


async def _fetch_post_title(db: Any, post_id: str) -> str:
    """The post's title (the topic the video is supposed to be about). '' on miss."""
    row = await db.fetchrow("SELECT title FROM posts WHERE id = $1::uuid", post_id)
    return (row["title"] if row and row["title"] else "") if row is not None else ""


async def _run_video_layer2(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> dict[str, Any]:
    """Compute the video Layer-2 signals + composite. Fail-soft; never raises.

    Two signals compose into the 0-100 score:
    - **shot-fidelity** — the mean of the per-shot vision scores the renderer
      already emitted (zero marginal cost; re-read from audit rows).
    - **topic-match** — one vision call over N sampled frames scoring whether
      the composed footage actually belongs in an article with this title.

    The composite is the mean of whichever signals are *available*, so a
    missing signal degrades gracefully. Returns ``{"layer2_status",
    "layer2_score", "shot_fidelity", "shot_coverage", "topic_match"}``. No
    signal available → status ``"unavailable"``, score ``None`` (the caller
    maps that to 100 on a clean Layer-1 pass). Emits advisory findings when
    a sub-score is below its configured minimum.
    """
    if not _layer2_enabled(site_config):
        return {"layer2_status": "disabled", "layer2_score": None}

    signals: dict[str, Any] = {}
    try:
        agg = await _aggregate_shot_qa_scores(db, post_id)
        signals["shot_fidelity"] = agg["shot_fidelity"]
        signals["shot_coverage"] = agg["shot_coverage"]

        title = await _fetch_post_title(db, post_id)
        topic_match = await _score_video_topic_match(
            db, file_path, title, site_config,
        )
        signals["topic_match"] = topic_match

        available = [
            s for s in (agg["shot_fidelity"], topic_match) if s is not None
        ]
        composite = (sum(available) / len(available)) if available else None
        signals["layer2_score"] = composite
        signals["layer2_status"] = "scored" if composite is not None else "unavailable"

        # Advisory findings (post-compose, so a missing signal never fires one).
        fid_min = _cfg_float(site_config, "media.video.shot_fidelity_min", 60.0)
        tm_min = _cfg_float(site_config, "media.video.topic_match_min", 50.0)
        if agg["shot_fidelity"] is not None and agg["shot_fidelity"] < fid_min:
            emit_finding(
                source="media_quality",
                kind="video_shot_fidelity_low",
                title=f"video shot-fidelity {agg['shot_fidelity']:.0f} < {fid_min:.0f}",
                body=(
                    f"post {post_id[:8]}: mean rendered-shot vision score "
                    f"{agg['shot_fidelity']:.1f} (coverage "
                    f"{agg['shot_coverage']:.0%}) below {fid_min:.0f}. Advisory."
                ),
                severity="warn",
                dedup_key=f"video_shot_fidelity_low:{post_id}",
                extra={
                    "post_id": post_id,
                    "shot_fidelity": agg["shot_fidelity"],
                    "coverage": agg["shot_coverage"],
                    "threshold": fid_min,
                },
            )
        if topic_match is not None and topic_match < tm_min:
            emit_finding(
                source="media_quality",
                kind="video_topic_mismatch",
                title=f"video topic-match {topic_match:.0f} < {tm_min:.0f}",
                body=(
                    f"post {post_id[:8]}: composed-video frames scored "
                    f"{topic_match:.1f} for topic relevance against the post "
                    f"title, below {tm_min:.0f}. Advisory."
                ),
                severity="warn",
                dedup_key=f"video_topic_mismatch:{post_id}",
                extra={
                    "post_id": post_id,
                    "topic_match": topic_match,
                    "threshold": tm_min,
                },
            )
    except Exception as exc:  # noqa: BLE001 — Layer 2 must never fail the eval
        logger.warning(
            "[media_quality] video Layer 2 raised (fail-soft): %s", exc,
        )
        signals.setdefault("layer2_status", "unavailable")
        signals.setdefault("layer2_score", None)
    return signals


def _resolve_faithfulness_model(site_config: Any) -> str:
    """media.podcast.faithfulness_model, falling back to ragas_judge_model. '' → skip."""
    raw = (site_config.get("media.podcast.faithfulness_model", "") or "").strip()
    if raw:
        return raw
    return (site_config.get("ragas_judge_model", "") or "").strip()


async def _score_podcast_faithfulness(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> tuple[float | None, str]:
    """Transcribe the full episode + judge faithfulness vs the source post.

    Returns ``(score_0_100, reason)``. Fail-soft → ``(None, "")`` on: no model,
    no source body, transcription failure, dispatch error, or unparseable judge
    response. Never raises — Layer 2 must never fail the eval.
    """
    model = _resolve_faithfulness_model(site_config)
    if not model:
        return None, ""

    row = await db.fetchrow("SELECT content FROM posts WHERE id = $1::uuid", post_id)
    source = (row["content"] if row and row["content"] else "") if row is not None else ""
    if not source.strip():
        return None, ""

    try:
        provider = get_caption_provider(site_config)
        result = await provider.transcribe(audio_path=file_path, task_id=post_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("[media_quality] podcast transcribe failed: %s", exc)
        return None, ""
    if not getattr(result, "success", False):
        return None, ""
    transcript = " ".join(
        s.text for s in (result.segments or []) if getattr(s, "text", "")
    ).strip()
    if not transcript:
        return None, ""

    prompt = _FAITHFULNESS_PROMPT.format(source=source, transcript=transcript)
    pool = getattr(db, "pool", db)
    try:
        completion = await dispatch_complete(
            pool, [{"role": "user", "content": prompt}], model,
            tier="standard", phase="media_qa_faithfulness",
            temperature=0.2, max_tokens=_LAYER2_VISION_MAX_TOKENS, timeout_s=180.0,
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("[media_quality] faithfulness dispatch failed: %s", exc)
        return None, ""

    match = re.search(r'"score"\s*:\s*([\d.]+)', text)
    if not match:
        return None, ""
    try:
        score = max(0.0, min(100.0, float(match.group(1))))
    except (TypeError, ValueError):
        return None, ""
    reason_match = re.search(r'"reason"\s*:\s*"([^"]{0,200})"', text)
    return score, (reason_match.group(1) if reason_match else "")


async def _run_podcast_layer2(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> dict[str, Any]:
    """Compute the podcast Layer-2 faithfulness signal. Fail-soft; never raises.

    Faithfulness (transcript vs source article) is the single podcast signal,
    so it IS the composite. None → status ``"unavailable"`` (caller maps to 100
    on a clean Layer-1 pass). Emits an advisory ``podcast_faithfulness_low``
    finding below ``media.podcast.faithfulness_min``.
    """
    if not _layer2_enabled(site_config):
        return {"layer2_status": "disabled", "layer2_score": None}

    signals: dict[str, Any] = {}
    try:
        score, reason = await _score_podcast_faithfulness(
            db, post_id, file_path, site_config,
        )
        signals["faithfulness"] = score
        signals["faithfulness_reason"] = reason
        signals["layer2_score"] = score
        signals["layer2_status"] = "scored" if score is not None else "unavailable"

        f_min = _cfg_float(site_config, "media.podcast.faithfulness_min", 60.0)
        if score is not None and score < f_min:
            emit_finding(
                source="media_quality",
                kind="podcast_faithfulness_low",
                title=f"podcast faithfulness {score:.0f} < {f_min:.0f}",
                body=(
                    f"post {post_id[:8]}: episode faithfulness vs the source "
                    f"article scored {score:.1f} (< {f_min:.0f}). reason: "
                    f"{reason}. Advisory."
                ),
                severity="warn",
                dedup_key=f"podcast_faithfulness_low:{post_id}",
                extra={
                    "post_id": post_id,
                    "faithfulness": score,
                    "threshold": f_min,
                },
            )
    except Exception as exc:  # noqa: BLE001 — Layer 2 must never fail the eval
        logger.warning(
            "[media_quality] podcast Layer 2 raised (fail-soft): %s", exc,
        )
        signals.setdefault("layer2_status", "unavailable")
        signals.setdefault("layer2_score", None)
    return signals


async def evaluate_podcast(
    db: Any, post_id: str, file_path: str, *, site_config: Any = None,
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

    # Layer 1 hard-fail floors the score at 0; otherwise Layer 2 computes the
    # 0-100 faithfulness composite (None → 100 when nothing measurable is wrong).
    if failures:
        score = 0.0
    else:
        layer2 = await _run_podcast_layer2(db, post_id, file_path, site_config)
        signals.update({k: v for k, v in layer2.items() if k != "layer2_score"})
        signals["layer2_status"] = layer2.get("layer2_status", "unavailable")
        l2_score = layer2.get("layer2_score")
        score = float(l2_score) if l2_score is not None else 100.0
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
    site_config: Any = None,
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

    # Layer 1 hard-fail floors the score at 0; otherwise Layer 2 computes the
    # 0-100 composite (None → 100 when nothing measurable is wrong).
    if failures:
        score = 0.0
    else:
        layer2 = await _run_video_layer2(db, post_id, file_path, site_config)
        signals.update({k: v for k, v in layer2.items() if k != "layer2_score"})
        signals["layer2_status"] = layer2.get("layer2_status", "unavailable")
        l2_score = layer2.get("layer2_score")
        score = float(l2_score) if l2_score is not None else 100.0
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
