"""ReviewVideoShotListStage — director self-critique of the video shot list.

Runs after ``generate_video_shot_list`` and before ``capture_training_data``
in the ``canonical_blog`` graph. Feeds the director its OWN draft shot list and
asks for a revised one (better coverage / variety / hero-shot selection /
on-brand), validates the result against ``VideoShotList``, and replaces the
draft. Non-halting: any failure keeps the unreviewed list so the post is never
blocked. In Stage 1 the reviewed plan is what the human approves at Gate 1.

Reuses the JSON-extract / reconcile / audit helpers from
``generate_video_shot_list`` so the two director passes can't drift, and
mirrors its exact model-resolution chain (``video_director_model`` is the
shared "director + critique model" key per the video-quality spec §3.1).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from modules.content.stages.generate_video_shot_list import (
    _extract_json_object,
    _log_audit,
    _reconcile_shot_list,
)
from plugins.stage import StageResult
from schemas.video_shot_list import VideoShotList

logger = logging.getLogger(__name__)


class ReviewVideoShotListStage:
    """Pipeline stage: director self-critique + revision of the shot list."""

    name = "review_video_shot_list"
    description = "Director self-critique — revise the shot list before Gate 1"
    # Up to two writer-grade LLM calls (long + short). gemma-4-31B is slow; budget
    # generously. Non-halting, so an overrun just skips the review, never blocks.
    timeout_seconds = 600
    halts_on_failure = False

    async def _resolve_model(self, *, cfg: Any, pool: Any) -> str | None:
        """Mirror generate_video_shot_list's model chain exactly."""
        configured = (
            cfg.get("video_director_model")
            or cfg.get("video_scene_model")
            or cfg.get("default_ollama_model")
        )
        if configured and configured != "auto":
            return configured
        from services.llm_providers.dispatcher import resolve_tier_model
        try:
            return await resolve_tier_model(pool, "standard")
        except Exception as exc:
            logger.warning(
                "[VIDEO_REVIEW] resolve_tier_model failed (%s) — review skipped", exc,
            )
            return None

    async def _review_one(
        self,
        *,
        platform: Any,
        pool: Any,
        model: str,
        prompt_key: str,
        script_var: str,
        script: str,
        current: dict[str, Any],
        title: str,
        content_text: str,
        site_name: str,
        task_id: str | None,
        now_iso: str,
    ) -> dict[str, Any] | None:
        """Render the review prompt, dispatch, validate. ``None`` on any failure."""
        from services.gpu_scheduler import gpu
        from services.prompt_manager import get_prompt_manager

        try:
            pm = get_prompt_manager()
            rendered = pm.get_prompt(
                prompt_key,
                title=title,
                content=content_text,
                current_shot_list=json.dumps(current),
                model=model,
                now_iso=now_iso,
                site_name=site_name,
                **{script_var: script},
            )
        except Exception as exc:
            logger.warning("[VIDEO_REVIEW] prompt render failed (%s, %s)", exc, prompt_key)
            return None

        try:
            async with gpu.lock("ollama", model=model, task_id=task_id, phase="video_review"):
                result = await platform.dispatch.complete(
                    pool=pool,
                    messages=[{"role": "user", "content": rendered}],
                    model=model,
                    tier="standard",
                    timeout_s=120,
                    temperature=0.4,
                    max_tokens=6144,
                )
            output = (getattr(result, "text", "") or "").strip()
        except Exception as exc:
            logger.warning("[VIDEO_REVIEW] dispatch failed (%s, %s)", exc, prompt_key)
            return None

        body = _extract_json_object(output)
        if not body:
            logger.warning("[VIDEO_REVIEW] no JSON in review output (%s)", prompt_key)
            return None
        try:
            parsed = _reconcile_shot_list(json.loads(body))
            revised = VideoShotList.model_validate(parsed)
        except Exception as exc:
            logger.warning("[VIDEO_REVIEW] revised list invalid (%s): %s", prompt_key, exc)
            return None

        await _log_audit(
            pool,
            event_type="video_director.shot_list_reviewed",
            task_id=task_id,
            details={
                "prompt_key": prompt_key,
                "shot_count": len(revised.shots),
                "sources": [s.source for s in revised.shots],
            },
        )
        return revised.model_dump(mode="json")

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        current = context.get("video_shot_list")
        if not current:
            return StageResult(
                ok=True,
                detail="no shot list to review",
                metrics={"skipped": True},
            )

        platform = context.get("platform")
        database_service = context.get("database_service")
        pool = getattr(database_service, "pool", None) if database_service else None
        if platform is None or pool is None:
            return StageResult(
                ok=True,
                detail="no platform / pool in context — review skipped",
                metrics={"skipped": True},
            )

        cfg = platform.config
        model = await self._resolve_model(cfg=cfg, pool=pool)
        if not model:
            return StageResult(
                ok=True,
                detail="model resolution failed — review skipped",
                metrics={"skipped": True},
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        site_name = cfg.get("site_name") or ""
        title = context.get("title", "")
        content_text = context.get("content", "")
        task_id = context.get("task_id")

        # LONG. Non-halting: fall back to the unreviewed list on any failure.
        revised = await self._review_one(
            platform=platform, pool=pool, model=model,
            prompt_key="video.review_v1", script_var="podcast_script",
            script=context.get("podcast_script", ""), current=current,
            title=title, content_text=content_text, site_name=site_name,
            task_id=task_id, now_iso=now_iso,
        )
        updates: dict[str, Any] = {
            "video_shot_list": revised if revised is not None else current,
        }

        # SHORT (best-effort, only when present).
        short = context.get("short_shot_list")
        if short:
            revised_short = await self._review_one(
                platform=platform, pool=pool, model=model,
                prompt_key="video.review_short_v1", script_var="short_script",
                script=context.get("short_summary_script", ""), current=short,
                title=title, content_text=content_text, site_name=site_name,
                task_id=task_id, now_iso=now_iso,
            )
            updates["short_shot_list"] = revised_short if revised_short is not None else short

        stages = context.setdefault("stages", {})
        stages["review_video_shot_list"] = True
        updates["stages"] = stages

        return StageResult(
            ok=True,
            detail="reviewed" if revised is not None else "review fell back to draft",
            context_updates=updates,
            metrics={"reviewed": revised is not None},
        )
