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
- **Unmeasured is not a pass.** When Layer 2 cannot score (no judge model,
  transcription failure, unparseable judge reply), ``quality_score`` is
  written NULL and ``layer2_status='unavailable'`` carries the reason. It
  used to be stamped ``100.0`` — "nothing measurable is wrong" — which made
  a broken transcription and a genuinely perfect episode identical on the
  row. 12 of 39 evaluated podcasts were scored 100 that way. Only a judge
  writes 100 now; a hole in the grade reads as a hole.
- **The judge may not be the writer.** ``_resolve_faithfulness_model``
  refuses a judge whose model id matches ``podcast_script_model`` and falls
  back to ``ragas_judge_model`` (``project_qa_model_placement_doctrine``).
  Prod ran script and judge both on ``gemma-4-31B-it-qat`` and 15 of 21
  scored episodes came back at exactly 100.

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
import statistics
import tempfile
from typing import Any

from poindexter.services.caption_providers import get_caption_provider
from poindexter.services.llm_providers.dispatcher import dispatch_complete
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

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


def _get_prompt_manager():
    """The prompt registry (SKILL.md packs). Imported lazily: the module is
    also loaded by tooling that never renders a prompt."""
    from poindexter.services.prompt_manager import get_prompt_manager

    return get_prompt_manager()


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

# Outcomes whose frames are not topic footage: a title card is text, and a
# holdover re-shows the previous shot's last frame. Never sampled for
# topic-match (the presenter is excluded by setting, since it is a source).
_TOPIC_MATCH_SKIP_OUTCOMES = frozenset({"card", "fallback_holdover"})


def _split_render_groups(rows: list[Any]) -> list[list[Any]]:
    """Split ordered shot rows into one list per render.

    One task renders several times (the long video, the Short, and any
    re-render), and every render restarts ``shot_idx`` at 0. Rows arrive in
    insert order, so a ``shot_idx`` that does not climb starts a new render.
    Rows without a ``shot_idx`` stay in the current group.
    """
    groups: list[list[Any]] = []
    prev: int | None = None
    for r in rows:
        idx = r.get("shot_idx") if hasattr(r, "get") else r["shot_idx"]
        try:
            idx = int(idx) if idx is not None else None
        except (TypeError, ValueError):
            idx = None
        if not groups or (idx is not None and prev is not None and idx <= prev):
            groups.append([])
        groups[-1].append(r)
        if idx is not None:
            prev = idx
    return groups


def _row_duration(r: Any) -> float:
    try:
        return max(0.0, float(r.get("duration_s") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _pick_render_group(
    groups: list[list[Any]], duration: float | None,
) -> list[Any]:
    """The render group that produced THIS file.

    The file's probed duration picks it: the group whose summed shot duration
    is closest to the file's (a Short is ~30 s, the long video minutes, so the
    two never get confused). Ties, and an unknown duration, go to the latest
    render. Empty when there are no groups.
    """
    if not groups:
        return []
    if not duration or duration <= 0:
        return groups[-1]
    best_i = 0
    best_gap = float("inf")
    for i, g in enumerate(groups):
        gap = abs(sum(_row_duration(r) for r in g) - duration)
        if gap <= best_gap:  # <= so a tie goes to the later render
            best_i, best_gap = i, gap
    return groups[best_i]


async def _aggregate_shot_qa_scores(
    db: Any, post_id: str, duration: float | None = None,
) -> dict[str, Any]:
    """Aggregate per-shot vision QA scores for a post's video into a fidelity signal.

    Reads the ``video_shot_rendered`` audit rows the shot-list renderer
    already emits (one per shot, carrying ``qa_score`` 0-100 + ``qa_outcome``).
    The renderer stamps ``details.post_id`` with the PIPELINE TASK id (the post
    does not exist yet at render time), so rows are matched on the post's
    ``metadata->>'pipeline_task_id'`` as well as the post id itself. Matching
    the post id alone found no rows for any video, so ``shot_fidelity`` was
    never measured and ``shot_coverage`` always read 0.

    Only the render group that produced this file is folded (see
    ``_pick_render_group``), so the long video and the Short of one task no
    longer pool their shots. Returns:

    - ``shot_fidelity`` — mean of the scored shots' ``qa_score`` (0-100), or
      ``None`` when no shot carried a usable score (all reused/unscored).
    - ``shot_coverage`` — fraction of shots that were actually scored
      (``scored / total``), a confidence weight on the fidelity number.
    - ``scored`` / ``total`` — the raw counts behind those ratios.
    - ``shots`` — the chosen group's rows, in render order, for topic-match.

    This is the zero-new-LLM-cost half of Layer 2: the vision scoring already
    happened at render time, so we only re-read audit rows here.
    """
    rows = await db.fetch(
        """
        SELECT (details->>'qa_score')::float     AS qa_score,
               details->>'qa_outcome'            AS outcome,
               (details->>'shot_idx')::int       AS shot_idx,
               details->>'source'                AS source,
               (details->>'duration_s')::float   AS duration_s
        FROM audit_log
        WHERE event_type = 'video_shot_rendered'
          AND COALESCE((details->>'success')::boolean, true)
          AND details->>'post_id' IN (
              $1,
              (SELECT metadata->>'pipeline_task_id' FROM posts
                WHERE id::text = $1)
          )
        ORDER BY timestamp, id
        """,
        post_id,
    )
    group = _pick_render_group(_split_render_groups(list(rows)), duration)
    total = len(group)
    scored_vals = [
        r["qa_score"]
        for r in group
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
        "shots": group,
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


def _topic_match_timestamps(
    shots: list[Any] | None, duration: float, n: int, site_config: Any,
) -> list[float]:
    """Where to grab the N topic-match frames, in seconds.

    With the render's shot rows: the midpoint of up to N content shots, spaced
    evenly through the video. Presenter shots (``topic_match_skip_sources``),
    title cards and holdovers are skipped. A presenter frame is the same
    avatar in every video, and a card is text, so neither says whether the
    footage fits the title. A midpoint also stays clear of the cross-fades at
    each shot's edges.

    Without shot rows (an older render, or a lookup miss): N even interior
    timestamps over the video minus its end card, whose length is the
    ``video_endcard_max_seconds`` the renderer caps it at.
    """
    raw_skip = site_config.get("media.video.topic_match_skip_sources", "presenter")
    skip_sources = {
        s.strip().lower() for s in str(raw_skip or "").split(",") if s.strip()
    }

    if shots:
        total = sum(_row_duration(r) for r in shots)
        # Shot rows sum to the pre-transition length; scale onto the file so
        # cross-fade overlap can't push a late midpoint past the end.
        scale = min(1.0, duration / total) if total > 0 else 1.0
        content: list[float] = []
        t = 0.0
        for r in shots:
            d = _row_duration(r)
            source = str(r.get("source") or "").strip().lower()
            outcome = str(r.get("outcome") or "").strip().lower()
            if (
                d > 0
                and source not in skip_sources
                and outcome not in _TOPIC_MATCH_SKIP_OUTCOMES
            ):
                content.append((t + d / 2.0) * scale)
            t += d
        if content:
            if len(content) <= n:
                return content
            if n == 1:
                return [content[len(content) // 2]]
            step = (len(content) - 1) / (n - 1)
            return [content[round(i * step)] for i in range(n)]
        # Every shot was a presenter/card: nothing on-topic to sample, so
        # fall through to even sampling rather than scoring the avatar.

    tail = _cfg_float(site_config, "video_endcard_max_seconds", 0.0)
    span = duration - tail if duration - tail > 0 else duration
    return [span * i / (n + 1) for i in range(1, n + 1)]


async def _score_one_topic_frame(
    pool: Any, model: str, prompt: str, png: bytes,
) -> float | None:
    """One vision call over one frame → 0-100, or None on any failure."""
    b64 = base64.b64encode(png).decode("ascii")
    # OpenAI-multimodal message; LiteLLM translates image_url data URIs into
    # Ollama's native images array (same shape shot_vision_qa proved out).
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }]
    try:
        completion = await dispatch_complete(
            pool, messages, model,
            tier="standard", phase="media_qa_topic_match",
            temperature=0.0, max_tokens=_LAYER2_VISION_MAX_TOKENS, timeout_s=150.0,
            # The answer is one number. A thinking-capable judge (gemma-4,
            # qwen3.6) otherwise reasons first: gemma spent 353 tokens to
            # reach the same {"score": 100} it gives in 14 with think off.
            # JSON-constrained decoding keeps any model to the object.
            think=False, response_format={"type": "json_object"},
        )
        text = (getattr(completion, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "[media_quality] topic-match dispatch failed: %s",
            describe_exception(exc),
        )
        return None
    match = re.search(r'"score"\s*:\s*([\d.]+)', text)
    if not match:
        return None
    try:
        return max(0.0, min(100.0, float(match.group(1))))
    except (TypeError, ValueError):
        return None


async def _score_video_topic_match(
    db: Any,
    file_path: str,
    title: str,
    site_config: Any,
    *,
    shots: list[Any] | None = None,
    duration: float | None = None,
) -> float | None:
    """Vision-score whether the composed video's footage fits the title.

    Grabs up to N frames (``media.video.topic_match_frames``) at the midpoints
    of the video's content shots (see ``_topic_match_timestamps``), scores
    each frame in its OWN call at temperature 0, and returns the median.

    It used to send three frames at fixed fractions of the runtime in one
    call. Those fractions often landed on the presenter or the end card: two
    of the Open-Weight Models Short's three frames were the avatar, and it
    scored 25 beside its long video's 75. Separate calls and a median let
    one off-topic frame cost one vote instead of the verdict.

    Returns None (fail-soft) on: no model, no duration, no extractable frame,
    or no frame that scored. Never raises.
    """
    model = _resolve_topic_match_model(site_config)
    if not model:
        return None
    try:
        n = int(site_config.get("media.video.topic_match_frames", "5") or "5")
    except (TypeError, ValueError):
        n = 5
    n = max(1, n)

    if duration is None:
        duration = await _probe_duration(file_path)
    if not duration or duration <= 0:
        return None

    prompt = _get_prompt_manager().get_prompt(
        "qa.media.video_topic_match", title=title or "this topic",
    )
    pool = getattr(db, "pool", db)
    scores: list[float] = []
    for at_s in _topic_match_timestamps(shots, duration, n, site_config):
        png = await _extract_frame_at(file_path, at_s)
        if not png:
            continue
        score = await _score_one_topic_frame(pool, model, prompt, png)
        if score is not None:
            scores.append(score)
    if not scores:
        return None
    logger.info(
        "[media_quality] topic-match %s: per-frame %s → median %.0f",
        os.path.basename(file_path), [round(s) for s in scores],
        statistics.median(scores),
    )
    return float(statistics.median(scores))


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


_SUBJECT_TITLE_SQL = """
    SELECT COALESCE(
        (SELECT title FROM posts WHERE id::text = $1),
        (SELECT title FROM media_assets
          WHERE task_id = $1 AND post_id IS NULL AND COALESCE(title, '') <> ''
          ORDER BY created_at DESC LIMIT 1),
        (SELECT topic FROM pipeline_tasks WHERE task_id = $1)
    ) AS title
"""


async def _fetch_post_title(db: Any, post_id: str) -> str:
    """What the video is supposed to be about. '' on miss.

    ``post_id`` is the approval row's subject: a post id, or the task id of a
    video with no post (poindexter#1140). That video's title is the one
    ``media.persist`` stored on its asset (the screenplay's title), else the
    task's topic.
    """
    row = await db.fetchrow(_SUBJECT_TITLE_SQL, str(post_id))
    title = row["title"] if row is not None else None
    return title if isinstance(title, str) else ""


async def _run_video_layer2(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> dict[str, Any]:
    """Compute the video Layer-2 signals + composite. Fail-soft; never raises.

    Two signals compose into the 0-100 score:
    - **shot-fidelity** — the mean of the per-shot vision scores the renderer
      already emitted (zero marginal cost; re-read from audit rows).
    - **topic-match** — the median of per-frame vision scores over N content
      shots, scoring whether the composed footage belongs with this title.

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
        duration = await _probe_duration(file_path)
        agg = await _aggregate_shot_qa_scores(db, post_id, duration)
        signals["shot_fidelity"] = agg["shot_fidelity"]
        signals["shot_coverage"] = agg["shot_coverage"]

        title = await _fetch_post_title(db, post_id)
        topic_match = await _score_video_topic_match(
            db, file_path, title, site_config,
            shots=agg.get("shots"), duration=duration,
        )
        signals["topic_match"] = topic_match

        available = [
            s for s in (agg["shot_fidelity"], topic_match) if s is not None
        ]
        composite = (sum(available) / len(available)) if available else None
        signals["layer2_score"] = composite
        signals["layer2_status"] = "scored" if composite is not None else "unavailable"
        if composite is None:
            signals["layer2_unavailable_reason"] = "no_signals"

        # Advisory findings (post-compose, so a missing signal never fires one).
        fid_min = _cfg_float(site_config, "media.video.shot_fidelity_min", 60.0)
        tm_min = _cfg_float(site_config, "media.video.topic_match_min", 30.0)
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
        signals.setdefault("layer2_unavailable_reason", "layer2_exception")
    return signals


# LiteLLM adds/strips a provider prefix per consumer, so one local engine is
# spelled "glm-4.7", "ollama/glm-4.7" and "ollama_chat/glm-4.7" depending on
# who is looking (docs/architecture/cost-logs-model-identity.md). Comparing a
# judge pin against a writer pin on the raw string would miss a collision that
# differs only by prefix.
_PROVIDER_PREFIX_RE = re.compile(r"^(?:ollama_chat|ollama)/")


def _normalize_model_id(raw: Any) -> str:
    """Canonical model identity for comparison — prefix-stripped, lowercased."""
    return _PROVIDER_PREFIX_RE.sub("", str(raw or "").strip().lower())


def _resolve_faithfulness_model(site_config: Any) -> tuple[str, str]:
    """Pick the faithfulness judge, refusing one that wrote the script.

    Returns ``(model, unavailable_reason)``. ``("", reason)`` means no usable
    judge exists, so the caller records WHY Layer 2 could not score rather
    than reporting a silent pass.

    Order: ``media.podcast.faithfulness_model`` → ``ragas_judge_model``. A
    candidate whose normalized id equals ``podcast_script_model`` is REFUSED
    at each step — the model that wrote the episode cannot be the one
    certifying the episode is faithful to its source
    (``project_qa_model_placement_doctrine``: judge != writer family).

    This is not hypothetical. Prod pinned both
    ``podcast_script_model`` and ``media.podcast.faithfulness_model`` to
    ``ollama/gemma-4-31B-it-qat:latest``; across 21 scored episodes the
    self-grade never went below 92 and landed on exactly 100 fifteen times,
    while the operator was independently rejecting episodes that scored 100.
    Refusing here re-points prod at ``ragas_judge_model`` (the pinned GPU-1
    judge) automatically, and emits a finding so the override is visible
    rather than silent.
    """
    writer = _normalize_model_id(site_config.get("podcast_script_model", ""))
    pinned = (site_config.get("media.podcast.faithfulness_model", "") or "").strip()
    fallback = (site_config.get("ragas_judge_model", "") or "").strip()

    candidates = [c for c in (pinned, fallback) if c]
    refused = [c for c in candidates if writer and _normalize_model_id(c) == writer]
    usable = [c for c in candidates if c not in refused]

    if refused:
        emit_finding(
            source="media_quality",
            kind="media_judge_is_writer",
            title=(
                f"podcast faithfulness judge refused — {refused[0]} also "
                f"writes the script"
            ),
            body=(
                f"The configured faithfulness judge ({', '.join(refused)}) "
                f"resolves to the same model as podcast_script_model "
                f"({writer}). A model grading its own output is not a "
                f"reviewer, so the judge was refused and Layer 2 "
                + (
                    f"fell back to {usable[0]}."
                    if usable
                    else "has no usable judge — the episode is ungraded on "
                         "content. Set media.podcast.faithfulness_model (or "
                         "ragas_judge_model) to a model outside the writer's "
                         "family."
                )
            ),
            severity="warn",
            dedup_key=f"media_judge_is_writer:podcast:{writer}",
            extra={
                "refused": refused,
                "writer_model": writer,
                "fell_back_to": usable[0] if usable else None,
            },
        )

    if usable:
        return usable[0], ""
    return "", ("judge_is_writer" if refused else "no_judge_model")


async def _score_podcast_faithfulness(
    db: Any, post_id: str, file_path: str, site_config: Any,
) -> tuple[float | None, str, str]:
    """Transcribe the full episode + judge faithfulness vs the source post.

    Returns ``(score_0_100, judge_reason, unavailable_reason)``. Fail-soft: on
    any miss the score is ``None`` and ``unavailable_reason`` names WHICH step
    gave out — ``no_judge_model`` / ``judge_is_writer`` / ``no_source_body`` /
    ``transcribe_error`` / ``transcribe_failed`` / ``empty_transcript`` /
    ``judge_dispatch_error`` / ``judge_unparsed``. Never raises — Layer 2 must
    never fail the eval.

    The reason is the point: the caller no longer converts a miss into a
    passing 100, so "unavailable" has to be diagnosable from the row alone.
    An unparsed judge reply is explicitly NOT a pass
    (``reference_llm_judge_two_stage_and_fail_closed``).
    """
    model, why = _resolve_faithfulness_model(site_config)
    if not model:
        return None, "", why or "no_judge_model"

    row = await db.fetchrow("SELECT content FROM posts WHERE id = $1::uuid", post_id)
    source = (row["content"] if row and row["content"] else "") if row is not None else ""
    if not source.strip():
        return None, "", "no_source_body"

    try:
        provider = get_caption_provider(site_config)
        result = await provider.transcribe(audio_path=file_path, task_id=post_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("[media_quality] podcast transcribe failed: %s", exc)
        return None, "", "transcribe_error"
    if not getattr(result, "success", False):
        return None, "", "transcribe_failed"
    transcript = " ".join(
        s.text for s in (result.segments or []) if getattr(s, "text", "")
    ).strip()
    if not transcript:
        return None, "", "empty_transcript"

    prompt = _get_prompt_manager().get_prompt(
        "qa.media.podcast_faithfulness", source=source, transcript=transcript,
    )
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
        return None, "", "judge_dispatch_error"

    match = re.search(r'"score"\s*:\s*([\d.]+)', text)
    if not match:
        return None, "", "judge_unparsed"
    try:
        score = max(0.0, min(100.0, float(match.group(1))))
    except (TypeError, ValueError):
        return None, "", "judge_unparsed"
    reason_match = re.search(r'"reason"\s*:\s*"([^"]{0,200})"', text)
    return score, (reason_match.group(1) if reason_match else ""), ""


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
        score, reason, why = await _score_podcast_faithfulness(
            db, post_id, file_path, site_config,
        )
        signals["faithfulness"] = score
        signals["faithfulness_reason"] = reason
        signals["layer2_score"] = score
        signals["layer2_status"] = "scored" if score is not None else "unavailable"
        if score is None:
            signals["layer2_unavailable_reason"] = why or "unknown"

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
        signals.setdefault("layer2_unavailable_reason", "layer2_exception")
    return signals


# A Layer-1 hard fail flips the row to rejected — but ONLY when no human has
# decided it. The eval now runs on auto-approved rows too (it used to be
# skipped for them entirely), so this UPDATE must be able to overturn an
# AUTO-approval while never overturning an operator's. The quality columns are
# written unconditionally, so a human-decided row still records what was
# measured. In UPDATE ... SET, column references on the right read the
# PRE-update row, so every CASE here tests the same original status/decided_by.
_LAYER1_REJECT_SQL = """
    UPDATE media_approvals
    SET status = CASE WHEN status = 'pending' OR decided_by LIKE 'auto:%'
                      THEN 'rejected' ELSE status END,
        decided_at = CASE WHEN status = 'pending' OR decided_by LIKE 'auto:%'
                      THEN now() ELSE decided_at END,
        decided_by = CASE WHEN status = 'pending' OR decided_by LIKE 'auto:%'
                      THEN 'auto:layer1' ELSE decided_by END,
        notes = CASE WHEN status = 'pending' OR decided_by LIKE 'auto:%'
                      THEN $3 ELSE notes END,
        quality_score = $4,
        quality_signals = $5::jsonb,
        quality_evaluated_at = now()
    WHERE subject_id = $1 AND medium = $2
"""


def _emit_layer2_unavailable(
    post_id: str, medium: str, signals: dict[str, Any],
) -> None:
    """Advisory finding when Layer 1 passed but Layer 2 could not score.

    A medium with no semantic score is a HOLE in the grade, not a pass. The
    hole was invisible while the caller stamped 100 for it; now the score is
    NULL and this names the reason on the Findings board.

    ``disabled`` is an operator choice rather than a failure, so it emits
    nothing — only ``unavailable`` does.
    """
    if signals.get("layer2_status") != "unavailable":
        return
    reason = str(signals.get("layer2_unavailable_reason") or "unknown")
    emit_finding(
        source="media_quality",
        kind="media_layer2_unavailable",
        title=f"{medium} Layer-2 score unavailable ({reason})",
        body=(
            f"post {post_id[:8]}: the {medium} passed the deterministic "
            f"Layer-1 checks but Layer 2 produced no score ({reason}), so "
            f"quality_score is NULL instead of a fabricated pass. Nothing has "
            f"graded this file on content — review it by hand before "
            f"approving."
        ),
        severity="warn",
        dedup_key=f"media_layer2_unavailable:{post_id}:{medium}",
        extra={"post_id": post_id, "medium": medium, "reason": reason},
    )


async def _fetch_render_audio_qa(db: Any, post_id: str) -> dict[str, Any] | None:
    """The podcast lane's in-pipeline ``qa.audio`` result, off the asset row.

    ``qa.audio`` runs inside ``podcast_pipeline`` and measures things Gate-2
    never re-derives: where each long silence sits, and the mean/max dBFS
    levels. ``podcast.persist`` stamps that under
    ``media_assets.metadata->'audio_qa'``; this read is what finally gives the
    atom's output a consumer. Before it, ``audio_qa_result`` was produced,
    declared as a PipelineState channel, and read by nothing — the checks ran
    on every episode and reached no surface an operator looks at.

    Surfacing only, deliberately: these signals do NOT feed the score or the
    auto-reject. 23 shipped episodes carry a long-silence finding and the
    operator approved them, so promoting the measurement straight to a gate
    would start rejecting work that is currently acceptable. Graduating it is
    a separate, evidence-led decision.

    Best-effort — any failure returns ``None``.
    """
    try:
        row = await db.fetchrow(
            """
            SELECT metadata FROM media_assets
            WHERE post_id = $1::uuid AND type = 'podcast'
            ORDER BY created_at DESC LIMIT 1
            """,
            post_id,
        )
    except Exception as exc:  # noqa: BLE001 — surfacing must never fail the eval
        logger.debug("[media_quality] render audio QA lookup failed: %s", exc)
        emit_finding(
            source="media_quality",
            kind="render_audio_qa_lookup_failed",
            title="render-time audio QA could not be read off the asset row",
            body=(
                f"post {post_id[:8]}: reading media_assets.metadata->'audio_qa' "
                f"raised {describe_exception(exc)}. The Gate-2 eval continues without the "
                f"render-time measurements — quality_signals loses "
                f"render_audio_qa, nothing else changes."
            ),
            severity="info",
            dedup_key="render_audio_qa_lookup_failed",
        )
        return None
    if row is None:
        return None

    raw = row["metadata"] if "metadata" in row else None
    meta: dict[str, Any] = {}
    if isinstance(raw, str):
        try:
            meta = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(raw, dict):
        meta = raw
    audio_qa = meta.get("audio_qa")
    return audio_qa if isinstance(audio_qa, dict) and audio_qa else None


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

    # Layer 1 hard-fail floors the score at 0; otherwise the score IS the
    # Layer-2 composite — or NULL when Layer 2 could not measure. This used to
    # be 100.0 ("nothing measurable is wrong"), which made a failed
    # transcription, an unparseable judge reply and a genuinely perfect episode
    # indistinguishable on the row: 12 of 39 evaluated podcasts were stamped
    # 100 without anything having read them. Only a judge writes 100 now.
    score: float | None
    if failures:
        score = 0.0
    else:
        layer2 = await _run_podcast_layer2(db, post_id, file_path, site_config)
        signals.update({k: v for k, v in layer2.items() if k != "layer2_score"})
        signals["layer2_status"] = layer2.get("layer2_status", "unavailable")
        l2_score = layer2.get("layer2_score")
        score = float(l2_score) if l2_score is not None else None
        _emit_layer2_unavailable(post_id, "podcast", signals)

    # Fold in the render-time qa.audio measurements so they finally reach the
    # surface the operator reviews (see _fetch_render_audio_qa).
    render_qa = await _fetch_render_audio_qa(db, post_id)
    if render_qa:
        signals["render_audio_qa"] = render_qa

    signals["layer1_failures"] = failures
    signals["score"] = score

    if failures:
        await db.execute(
            _LAYER1_REJECT_SQL,
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
            WHERE subject_id = $1 AND medium = $2
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

    # Same rule as the podcast lane: a Layer-2 hole is NULL, never a
    # fabricated 100. See evaluate_podcast for the measurement behind it.
    score: float | None
    if failures:
        score = 0.0
    else:
        layer2 = await _run_video_layer2(db, post_id, file_path, site_config)
        signals.update({k: v for k, v in layer2.items() if k != "layer2_score"})
        signals["layer2_status"] = layer2.get("layer2_status", "unavailable")
        l2_score = layer2.get("layer2_score")
        score = float(l2_score) if l2_score is not None else None
        _emit_layer2_unavailable(post_id, medium, signals)
    signals["layer1_failures"] = failures
    signals["score"] = score

    if failures:
        await db.execute(
            _LAYER1_REJECT_SQL,
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
            WHERE subject_id = $1 AND medium = $2
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
        from poindexter.services import media_approval_service

        await media_approval_service.notify_pending_for_review(
            db, post_id, medium,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[media_quality] notify_pending_for_review failed for %s/%s: %s",
            medium, post_id[:8], e,
        )
