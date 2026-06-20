"""GenerateVideoShotListStage — director output for the post's video.

Runs after ``generate_media_scripts`` (which produces the podcast script
the shot list aligns its narration to) and before ``capture_training_data``
/ ``finalize_task``. Calls the LLM director, validates the result against
the Pydantic schema in ``schemas/video_shot_list.py``, persists to
context + audit_log.

This PR (1 of 3) only LANDS the shot list in context + audit_log so the
operator can review director output for a few real posts. PR 2 wires the
shot list into actual video rendering. Design doc:
``docs/architecture/video-composition.md`` (issue #649).

## Context reads

- ``task_id`` (str), ``title`` (str), ``content`` (str)
- ``podcast_script`` (str, produced by ``generate_media_scripts``)
- ``platform`` (capability handle — ``config`` for model/site_name reads +
  ``dispatch`` for the LLM call; Seam 1 Waves 3d/3e, #667)
- ``database_service`` (DI seam — pool for the LLM dispatcher)

## Context writes

- ``video_shot_list`` (dict) — JSON-serialized VideoShotList; absent on
  failure (downstream stages treat absent as "no director output, fall
  back to legacy renderer")
- ``stages["video_shot_list"]`` (bool)

## Audit log

Writes ``video_director.shot_list_produced`` on success or
``video_director.shot_list_failed`` on failure. Operator-visible in
the audit_log dashboard so they can monitor director quality without
grepping container logs.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from plugins.stage import StageResult
from schemas.video_shot_list import VideoShotList

logger = logging.getLogger(__name__)


_DEFAULT_TARGET_DURATION_S = 60.0  # Fallback when podcast script length unknown
_WORDS_PER_SECOND = 2.5  # Rough TTS narration pace; ~150 WPM


# Per-call ceiling for the director LLM dispatch. Writer-grade director models
# (e.g. gemma-4-31B, the prod default) emit a full structured shot list
# (max_tokens 6144) and routinely need well over the old hardcoded 120s — the
# call was timing out at exactly 120.0s and leaving an empty shot list, so
# Stage-2 video never rendered. Tunable via the ``video_director_timeout_seconds``
# DB setting; this is just the seed default for operators on faster models.
_DIRECTOR_TIMEOUT_DEFAULT = 300


def _estimate_target_duration(podcast_script: str) -> float:
    """Estimate the podcast's spoken duration from word count.

    The director needs a target duration. The actual podcast file may
    not exist yet at this stage (podcast gen is post-publish), so we
    estimate from the script: ~2.5 words/second is a reasonable TTS
    narration pace.

    Clamped to [20, 300] seconds — shorter than 20s isn't worth the
    director call; longer than 300s exceeds the renderer's practical
    output length.
    """
    if not podcast_script:
        return _DEFAULT_TARGET_DURATION_S
    word_count = len(podcast_script.split())
    estimated = word_count / _WORDS_PER_SECOND
    return max(20.0, min(estimated, 300.0))


_DEFAULT_SHORT_DURATION_S = 20.0  # Fallback when short script length unknown


def _estimate_short_duration(short_script: str) -> float:
    """Estimate the short-form clip duration from word count.

    Same ~2.5 words/second estimate as the long-form director, but
    clamped to [15, 45] seconds — the short-form retention window. Below
    15s isn't enough to land a hook + payoff; above 45s a "short" stops
    being short and viewers scroll. If empty, return the 20s default.
    """
    if not short_script:
        return _DEFAULT_SHORT_DURATION_S
    word_count = len(short_script.split())
    estimated = word_count / _WORDS_PER_SECOND
    return max(15.0, min(estimated, 45.0))


def _extract_json_object(text: str) -> str | None:
    """Strip prose / code-fence wrappers and return the JSON object body.

    LLMs occasionally ignore the "no prose" instruction and emit
    ```json ... ``` or "Here is the shot list: { ... }". Trim those.
    Returns ``None`` when no plausible JSON object is found.
    """
    if not text:
        return None
    stripped = text.strip()

    # Code-fence strip.
    fence_match = re.match(
        r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL,
    )
    if fence_match:
        stripped = fence_match.group(1).strip()

    # Find the first '{' and the matching '}' — naive bracket-counting.
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(stripped[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start:i + 1]
    return None


_MAX_SHOTS = 30  # Mirror VideoShotList.shots max_length — cap before validation.


def _reconcile_shot_list(parsed: Any) -> Any:
    """Deterministically repair the director's mechanical slips before schema
    validation.

    The local "standard"-tier LLM reliably botches three pieces of bookkeeping
    the ``VideoShotList`` schema enforces as hard gates — and every botch
    rejected the WHOLE shot list, so Stage-2 video rendering silently no-opped
    on every post (audit: ``shot_list_failed`` 11 / ``shot_list_produced`` 0
    over 10 days). These constraints are *calculated*, not creative, so we
    compute them here rather than throw away the director's otherwise-usable
    shot choices (``feedback_calculated_vs_generated``). Creative fields
    (sources, prompts, queries, intents) are never touched.

    Repairs, in order:

    1. **Pacing** — break runs of >2 consecutive same-source non-holdover shots
       by inserting a ``holdover`` transition (a pure cross-fade, no asset).
    2. **Count** — cap to the schema max (30 shots); the director front-loads
       the hook, so dropping the tail is the least-bad truncation.
    3. **Index** — renormalize ``idx`` to a contiguous ``0..n-1`` (the renderer
       concats in idx order; the schema requires no gaps).
    4. **Total duration** — set ``total_duration_s`` to the sum of shot
       durations so the director can never disagree with its own arithmetic.

    A non-dict input, or a dict with no usable ``shots`` list, is returned
    untouched — so a genuinely-empty director output still flows to the
    stage's failure path rather than being fabricated into a fake success.
    """
    if not isinstance(parsed, dict):
        return parsed
    raw_shots = parsed.get("shots")
    if not isinstance(raw_shots, list):
        return parsed
    shots = [s for s in raw_shots if isinstance(s, dict)]
    if not shots:
        return parsed

    # 1. Break same-source streaks with holdover transitions.
    paced: list[dict[str, Any]] = []
    streak_src: str | None = None
    streak = 0
    for shot in shots:
        src = shot.get("source")
        if src == "holdover":
            streak_src, streak = None, 0
            paced.append(shot)
            continue
        if src == streak_src:
            streak += 1
            if streak > 2:
                paced.append({
                    "duration_s": 0.5,
                    "intent": "transition",
                    "source": "holdover",
                    "narration_offset_s": shot.get("narration_offset_s", 0.0) or 0.0,
                })
                streak = 1  # this shot opens a fresh run after the transition
        else:
            streak_src, streak = src, 1
        paced.append(shot)

    # 2. Cap to the schema max.
    paced = paced[:_MAX_SHOTS]

    # 3 + 4. Reindex contiguously and recompute the total from the durations.
    total = 0.0
    for i, shot in enumerate(paced):
        shot["idx"] = i
        raw_duration = shot.get("duration_s")
        try:
            total += float(raw_duration or 0.0)
        except (TypeError, ValueError):
            # A non-numeric duration_s contributes 0.0 to the total — the
            # per-shot schema validator rejects the shot downstream anyway, so
            # we don't fail reconciliation here. Log it so the bad director
            # output is diagnosable rather than silently swallowed.
            logger.debug(
                "[VIDEO_DIRECTOR] reconcile: non-numeric duration_s %r at "
                "idx=%d — counting as 0.0", raw_duration, i,
            )

    parsed["shots"] = paced
    parsed["total_duration_s"] = round(total, 1)
    return parsed


async def _log_audit(
    pool: Any,
    *,
    event_type: str,
    task_id: str | None,
    details: dict[str, Any],
    severity: str = "info",
) -> None:
    """Insert an audit_log row. Best-effort — a failure here MUST NOT
    take the stage down (the stage's whole point is logging+context;
    failing on the log itself would be the worst outcome).
    """
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, task_id, details, severity)
            VALUES ($1, 'video_director_stage', $2, $3::jsonb, $4)
            """,
            event_type, task_id, json.dumps(details), severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VIDEO_DIRECTOR] audit_log insert failed (%s): %s",
            event_type, exc,
        )


class GenerateVideoShotListStage:
    """Pipeline stage: produce a video shot list via LLM director."""

    name = "generate_video_shot_list"
    description = "Director — produce shot list (sources, prompts, durations) for the post's video"
    # Up to two LLM dispatches (long + short), each capped at the
    # ``video_director_timeout_seconds`` DB setting (default
    # _DIRECTOR_TIMEOUT_DEFAULT). This stage-level budget only bites on the
    # legacy template_runner path — canonical_blog runs on graph_def, which
    # enforces the per-call ceiling inside _produce_shot_list — so keep it
    # comfortably above 2× the per-call default so it never pre-empts the
    # dispatches it wraps.
    timeout_seconds = 2 * _DIRECTOR_TIMEOUT_DEFAULT + 80
    halts_on_failure = False  # Director failure shouldn't block the post

    async def _produce_shot_list(
        self,
        *,
        platform: Any,
        pool: Any,
        model: str,
        script: str,
        target_duration_s: float,
        prompt_key: str,
        task_id: str | None,
        now_iso: str,
        title: str,
        content_text: str,
        site_name: str,
        timeout_s: int,
    ) -> dict[str, Any] | None:
        """Render the director prompt, dispatch the LLM, validate the result.

        Returns the validated ``shot_list.model_dump(mode="json")`` on
        success or ``None`` on any failure (logging the same audit events
        the inline flow used to). Shared by the long-form
        (``video.director_v1``, ``podcast_script=...``) and short-form
        (``video.director_short_v1``, ``short_script=...``) calls — the
        template variable the script binds to is keyed off ``prompt_key``.
        """
        # The two director prompts name the narration script differently:
        # the long prompt uses {podcast_script}, the short prompt uses
        # {short_script}. Bind the script under the right template var.
        script_param = (
            "short_script" if prompt_key == "video.director_short_v1"
            else "podcast_script"
        )

        # Render the director prompt via UnifiedPromptManager so edits
        # land in Langfuse + the YAML defaults stay in repo control.
        try:
            from services.prompt_manager import get_prompt_manager
            pm = get_prompt_manager()
            rendered_prompt = pm.get_prompt(
                prompt_key,
                title=title,
                content=content_text,
                target_duration_s=f"{target_duration_s:.1f}",
                model=model,
                now_iso=now_iso,
                # Operator brand templated into the director persona via
                # {site_name} (migrated to skills/content/video-director).
                site_name=site_name,
                **{script_param: script},
            )
        except Exception as exc:
            logger.warning(
                "[VIDEO_DIRECTOR] prompt render failed (%s, key=%s) — skipping",
                exc, prompt_key,
            )
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={"phase": "prompt_render", "prompt_key": prompt_key, "error": str(exc)},
                severity="warning",
            )
            return None

        # Dispatch the LLM call (Seam 1 Wave 3d, #667 — via the handle).
        from services.gpu_scheduler import gpu

        director_output = ""
        try:
            async with gpu.lock(
                "ollama", model=model,
                task_id=task_id, phase="video_director",
            ):
                result = await platform.dispatch.complete(
                    pool=pool,
                    messages=[{"role": "user", "content": rendered_prompt}],
                    model=model,
                    tier="standard",
                    timeout_s=timeout_s,
                    temperature=0.4,  # Lower temp — director should be decisive
                    # A full 30-shot list serializes past 3072 tokens, which
                    # truncated the JSON mid-list (json_extract failures) — the
                    # reconcile pass caps the shot count, but only if the model
                    # was allowed to finish emitting valid JSON first.
                    max_tokens=6144,
                )
            director_output = (getattr(result, "text", "") or "").strip()
        except Exception as exc:
            logger.warning(
                "[VIDEO_DIRECTOR] LLM dispatch failed (%s, key=%s) — skipping",
                exc, prompt_key,
            )
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={"phase": "llm_dispatch", "prompt_key": prompt_key, "error": str(exc)},
                severity="warning",
            )
            return None

        # Parse + validate. The schema enforces all the pacing rules
        # (no >2 consecutive same source, duration sum matches total,
        # idx contiguous from 0). Validation failures here mean the
        # director produced garbage; we record + skip.
        json_body = _extract_json_object(director_output)
        if not json_body:
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={
                    "phase": "json_extract",
                    "prompt_key": prompt_key,
                    "raw_output_preview": director_output[:500],
                },
                severity="warning",
            )
            return None

        try:
            parsed = json.loads(json_body)
            # Repair the director's mechanical/arithmetic slips (count cap,
            # idx, total-duration sum, pacing streaks) BEFORE validation — the
            # local LLM botches this bookkeeping every run, and an unrepaired
            # reject silently no-ops Stage-2 video. Creative fields untouched.
            parsed = _reconcile_shot_list(parsed)
            shot_list = VideoShotList.model_validate(parsed)
        except Exception as exc:
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={
                    "phase": "schema_validate",
                    "prompt_key": prompt_key,
                    "error": str(exc),
                    "raw_output_preview": json_body[:500],
                },
                severity="warning",
            )
            return None

        await _log_audit(
            pool,
            event_type="video_director.shot_list_produced",
            task_id=task_id,
            details={
                "model": model,
                "prompt_key": prompt_key,
                "aspect": shot_list.aspect,
                "shot_count": len(shot_list.shots),
                "total_duration_s": shot_list.total_duration_s,
                "sources": [s.source for s in shot_list.shots],
            },
        )
        logger.info(
            "[VIDEO_DIRECTOR] %s: %d shots produced (aspect=%s, total=%.1fs, sources=%s)",
            prompt_key,
            len(shot_list.shots),
            shot_list.aspect,
            shot_list.total_duration_s,
            ",".join(s.source for s in shot_list.shots),
        )
        return shot_list.model_dump(mode="json")

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        title = context.get("title", "")
        content_text = context.get("content", "")
        podcast_script = context.get("podcast_script", "")
        task_id = context.get("task_id")

        if not content_text or not title:
            return StageResult(
                ok=True,
                detail="nothing to direct (missing content or title)",
                metrics={"skipped": True},
            )
        if not podcast_script:
            # The director needs the narration script to align shot
            # durations to. Without it, we'd produce a shot list that
            # didn't actually correspond to any audio.
            return StageResult(
                ok=True,
                detail="no podcast_script in context — director skipped",
                metrics={"skipped": True},
            )

        database_service = context.get("database_service")
        pool = getattr(database_service, "pool", None) if database_service else None
        # Seam 1 Wave 3d (#667): LLM completion via the capability handle.
        platform = context.get("platform")

        if pool is None or platform is None:
            # Tests / bootstrap path — no DB / no kernel handle → no LLM call.
            # The stage is non-critical so this is fine.
            return StageResult(
                ok=True,
                detail="no DB pool / Platform handle in context — director skipped",
                metrics={"skipped": True},
            )

        # Seam 1 Wave 3e (#667): config reads go through the handle. The guard
        # above guarantees ``platform`` is non-None here, so no None-tolerance
        # dance is needed — ``platform.config`` is the only config seam now.
        cfg = platform.config

        # poindexter#716 — route through the cost-tier resolver when the
        # operator has not set an explicit director/scene/default model.
        # "auto" is treated as "not set" so operators can leave the key
        # in its default state and still have their tier mapping respected.
        _configured_model = (
            cfg.get("video_director_model")
            or cfg.get("video_scene_model")
            or cfg.get("default_ollama_model")
        )
        if not _configured_model or _configured_model == "auto":
            from services.llm_providers.dispatcher import resolve_tier_model
            try:
                model = await resolve_tier_model(pool, "standard")
            except Exception as _exc:
                logger.warning(
                    "generate_video_shot_list: resolve_tier_model failed (%s); "
                    "director skipped",
                    _exc,
                )
                return StageResult(
                    ok=True,
                    detail=f"model resolution failed: {_exc}",
                    metrics={"skipped": True},
                )
        else:
            model = _configured_model

        now_iso = datetime.now(timezone.utc).isoformat()
        site_name = cfg.get("site_name") or ""
        # Per-call LLM ceiling — DB-tunable so writer-grade director models get
        # the headroom they need (see _DIRECTOR_TIMEOUT_DEFAULT). The old
        # hardcoded 120s timed out gemma-4-31B and left an empty shot list, so
        # Stage-2 video never rendered.
        director_timeout = cfg.get_int(
            "video_director_timeout_seconds", _DIRECTOR_TIMEOUT_DEFAULT
        )

        # LONG (unchanged behavior): the 16:9 director over podcast_script.
        long_shot_list = await self._produce_shot_list(
            platform=platform,
            pool=pool,
            model=model,
            script=podcast_script,
            target_duration_s=_estimate_target_duration(podcast_script),
            prompt_key="video.director_v1",
            task_id=task_id,
            now_iso=now_iso,
            title=title,
            content_text=content_text,
            site_name=site_name,
            timeout_s=director_timeout,
        )

        if long_shot_list is None:
            # The long-form director failed — the stage is non-critical, so
            # record + skip (per-phase audit was already logged in the helper).
            return StageResult(
                ok=True,
                detail="director did not produce a long-form shot list",
                metrics={"failed": True},
            )

        # SHORT (new, best-effort): the purpose-built 9:16 vertical director
        # over short_summary_script. A short failure must NOT affect the long
        # result or halt the stage — so we only add short_shot_list when the
        # short script is present AND the helper returns a validated dict.
        short_summary_script = context.get("short_summary_script", "")
        short_shot_list = None
        if short_summary_script:
            short_shot_list = await self._produce_shot_list(
                platform=platform,
                pool=pool,
                model=model,
                script=short_summary_script,
                target_duration_s=_estimate_short_duration(short_summary_script),
                prompt_key="video.director_short_v1",
                task_id=task_id,
                now_iso=now_iso,
                title=title,
                content_text=content_text,
                site_name=site_name,
                timeout_s=director_timeout,
            )

        # Success — return via context_updates so it survives the graph_def
        # state merge (#674: direct context writes are dropped on graph_def).
        stages = context.setdefault("stages", {})
        stages["video_shot_list"] = True

        context_updates: dict[str, Any] = {
            "video_shot_list": long_shot_list,
            "stages": stages,
        }
        metrics: dict[str, Any] = {
            "shot_count": len(long_shot_list["shots"]),
            "total_duration_s": long_shot_list["total_duration_s"],
        }
        if short_shot_list is not None:
            stages["short_shot_list"] = True
            context_updates["short_shot_list"] = short_shot_list
            metrics["short_shot_count"] = len(short_shot_list["shots"])

        detail = (
            f"{len(long_shot_list['shots'])} shots, "
            f"{long_shot_list['total_duration_s']:.1f}s total"
        )
        if short_shot_list is not None:
            detail += f" (+ {len(short_shot_list['shots'])}-shot 9:16 short)"

        return StageResult(
            ok=True,
            detail=detail,
            context_updates=context_updates,
            metrics=metrics,
        )
