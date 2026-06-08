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
    # One LLM call up to 120s. Director output is structured JSON; small
    # generations. Budget 180 for slow disks + cold model loads.
    timeout_seconds = 180
    halts_on_failure = False  # Director failure shouldn't block the post

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

        # Same model resolution as generate_media_scripts — operators
        # can pin a director model via ``video_director_model`` later
        # if they want it different from the default ollama model.
        model = (
            cfg.get("video_director_model")
            or cfg.get("video_scene_model")
            or cfg.get("default_ollama_model")
            or "llama3:latest"
        )
        if model == "auto":
            model = "llama3:latest"

        target_duration_s = _estimate_target_duration(podcast_script)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Render the director prompt via UnifiedPromptManager so edits
        # land in Langfuse + the YAML defaults stay in repo control.
        try:
            from services.prompt_manager import get_prompt_manager
            pm = get_prompt_manager()
            rendered_prompt = pm.get_prompt(
                "video.director_v1",
                title=title,
                content=content_text,
                podcast_script=podcast_script,
                target_duration_s=f"{target_duration_s:.1f}",
                model=model,
                now_iso=now_iso,
                # Operator brand templated into the director persona via
                # {site_name} (migrated to skills/content/video-director).
                site_name=cfg.get("site_name") or "",
            )
        except Exception as exc:
            logger.warning(
                "[VIDEO_DIRECTOR] prompt render failed (%s) — skipping",
                exc,
            )
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={"phase": "prompt_render", "error": str(exc)},
                severity="warning",
            )
            return StageResult(
                ok=True,
                detail=f"prompt render failed: {exc}",
                metrics={"failed": True},
            )

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
                    timeout_s=120,
                    temperature=0.4,  # Lower temp — director should be decisive
                    max_tokens=3072,
                )
            director_output = (getattr(result, "text", "") or "").strip()
        except Exception as exc:
            logger.warning(
                "[VIDEO_DIRECTOR] LLM dispatch failed (%s) — skipping",
                exc,
            )
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={"phase": "llm_dispatch", "error": str(exc)},
                severity="warning",
            )
            return StageResult(
                ok=True,
                detail=f"LLM dispatch failed: {exc}",
                metrics={"failed": True},
            )

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
                    "raw_output_preview": director_output[:500],
                },
                severity="warning",
            )
            return StageResult(
                ok=True,
                detail="director output had no JSON object",
                metrics={"failed": True},
            )

        try:
            parsed = json.loads(json_body)
            shot_list = VideoShotList.model_validate(parsed)
        except Exception as exc:
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={
                    "phase": "schema_validate",
                    "error": str(exc),
                    "raw_output_preview": json_body[:500],
                },
                severity="warning",
            )
            return StageResult(
                ok=True,
                detail=f"director output failed validation: {exc}",
                metrics={"failed": True},
            )

        # Success — return via context_updates so it survives the graph_def
        # state merge (#674: direct context writes are dropped on graph_def).
        shot_list_dict = shot_list.model_dump(mode="json")
        stages = context.setdefault("stages", {})
        stages["video_shot_list"] = True

        await _log_audit(
            pool,
            event_type="video_director.shot_list_produced",
            task_id=task_id,
            details={
                "model": model,
                "shot_count": len(shot_list.shots),
                "total_duration_s": shot_list.total_duration_s,
                "sources": [s.source for s in shot_list.shots],
            },
        )
        logger.info(
            "[VIDEO_DIRECTOR] %d shots produced (total=%.1fs, sources=%s)",
            len(shot_list.shots),
            shot_list.total_duration_s,
            ",".join(s.source for s in shot_list.shots),
        )
        return StageResult(
            ok=True,
            detail=f"{len(shot_list.shots)} shots, {shot_list.total_duration_s:.1f}s total",
            context_updates={
                "video_shot_list": shot_list_dict,
                "stages": stages,
            },
            metrics={
                "shot_count": len(shot_list.shots),
                "total_duration_s": shot_list.total_duration_s,
            },
        )
