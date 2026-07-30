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

from modules.content.stages._media_gpu_skip import surface_media_gpu_busy_skip
from modules.content.stages.generate_video_shot_list import (
    _DIRECTOR_MAX_RETRIES_DEFAULT,
    _DIRECTOR_MAX_TOKENS_DEFAULT,
    _DIRECTOR_TIMEOUT_DEFAULT,
    _extract_json_object,
    _log_audit,
    _reconcile_shot_list,
    _resolve_director_think,
    _tolerant_json_loads,
)
from plugins.stage import StageResult
from schemas.video_shot_list import VideoShotList
from services.gpu_admission import GpuBusyError
from services.gpu_scheduler import media_wait_budget_s

logger = logging.getLogger(__name__)


class ReviewVideoShotListStage:
    """Pipeline stage: director self-critique + revision of the shot list."""

    name = "review_video_shot_list"
    description = "Director self-critique — revise the shot list before Gate 1"
    # Two lanes (long + short), each up to (1 + max_retries) writer-grade
    # director-class LLM calls capped by the shared ``video_director_timeout_seconds``
    # DB setting — the same knobs the director uses, since the review is the
    # identical model doing a second structured pass. This stage-level budget only
    # bites on the legacy template_runner path (canonical_blog runs on graph_def,
    # which enforces the per-call ceiling), so size it above the worst-case
    # dispatch count so it never pre-empts the dispatches it wraps. Non-halting: an
    # overrun skips the review, never blocks the post.
    timeout_seconds = (1 + _DIRECTOR_MAX_RETRIES_DEFAULT) * 2 * _DIRECTOR_TIMEOUT_DEFAULT + 80
    halts_on_failure = False

    async def _resolve_model(self, *, cfg: Any, pool: Any) -> str | None:
        """Resolve the director model — mirrors generate_video_shot_list's chain.

        Per-step pin: video_director_model → video_scene_model →
        default_ollama_model. "auto"/unset → None (review skipped). The
        cost_tier.standard.model fallback was removed; ``pool`` is retained
        for call-site signature parity with the sibling stage.
        """
        configured = (
            cfg.get("video_director_model")
            or cfg.get("video_scene_model")
            or cfg.get("default_ollama_model")
        )
        if configured and configured != "auto":
            return configured
        logger.warning(
            "[VIDEO_REVIEW] no director model configured "
            "(video_director_model / video_scene_model / default_ollama_model "
            "unset or 'auto') — review skipped",
        )
        return None

    async def _review_one(
        self,
        *,
        platform: Any,
        pool: Any,
        model: str,
        timeout_s: int,
        prompt_key: str,
        script_var: str,
        script: str,
        current: dict[str, Any],
        title: str,
        content_text: str,
        site_name: str,
        task_id: str | None,
        now_iso: str,
        think: bool | None = None,
        max_tokens: int = _DIRECTOR_MAX_TOKENS_DEFAULT,
        max_retries: int = 0,
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

        # Disable the reasoning channel (default) — the reviewer is the same
        # thinking-capable director model doing a second structured pass, so
        # leaving thinking on starves the revised JSON exactly like the
        # director's (see _resolve_director_think). Skip ``think`` on None.
        think_kwargs: dict[str, Any] = {} if think is None else {"think": think}

        # Retry on an empty/truncated extract, same as the director — a verbose
        # revision cut past max_tokens otherwise silently discards the whole
        # review (non-halting → the unreviewed draft ships). Dispatch exceptions
        # (infra) fail fast; only a structurally-empty result retries.
        body: str | None = None
        attempts = 1 + max(0, max_retries)
        for attempt in range(attempts):
            try:
                async with gpu.lock(
                    "ollama", model=model, task_id=task_id, phase="video_review",
                    # poindexter#914 P2 group 2 — bounded wait. This review is
                    # already fail-soft (a None here ships the unreviewed shot
                    # list), so skipping beats queueing behind a ~230s render
                    # up to the 900s lock ceiling on a finished article.
                    max_wait_s=media_wait_budget_s(), priority="background",
                ):
                    result = await platform.dispatch.complete(
                        pool=pool,
                        messages=[{"role": "user", "content": rendered}],
                        model=model,
                        tier="standard",
                        timeout_s=timeout_s,
                        temperature=0.4,
                        max_tokens=max_tokens,
                        **think_kwargs,
                    )
                output = (getattr(result, "text", "") or "").strip()
            except GpuBusyError as busy:
                # Ahead of the broad handler below on purpose: contention is not
                # a dispatch fault, and logging it as one would make render
                # pressure read as the director breaking. Retrying is pointless
                # — the GPU is busy now — so this returns rather than looping.
                logger.info(
                    "[VIDEO_REVIEW] skipped — GPU busy (%s, %s)", busy.reason, prompt_key,
                )
                surface_media_gpu_busy_skip("video_review", busy, task_id=task_id)
                return None
            except Exception as exc:
                logger.warning("[VIDEO_REVIEW] dispatch failed (%s, %s)", exc, prompt_key)
                return None

            body = _extract_json_object(output)
            if body:
                break
            if attempt < attempts - 1:
                logger.info(
                    "[VIDEO_REVIEW] no JSON in review output (%s, attempt %d/%d) "
                    "— retrying", prompt_key, attempt + 1, attempts,
                )

        if not body:
            logger.warning("[VIDEO_REVIEW] no JSON in review output (%s)", prompt_key)
            return None
        try:
            parsed = _reconcile_shot_list(_tolerant_json_loads(body))
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

        # Per-call LLM timeout — reuse the director's knob. The review is the same
        # writer-grade model doing a second structured-output pass, so the old
        # hardcoded 120s timed it out mid shot-list exactly like the director's did
        # (validation finding: gemma-4-31B needs well over 120s for a 6144-token list).
        review_timeout = cfg.get_int(
            "video_director_timeout_seconds", _DIRECTOR_TIMEOUT_DEFAULT
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        site_name = cfg.get("site_name") or ""
        title = context.get("title", "")
        content_text = context.get("content", "")
        task_id = context.get("task_id")
        # Disable the reasoning channel (default) — same rationale as the
        # director: leaving it on starves the revised JSON (see
        # _resolve_director_think). Same output-token + retry budget too.
        director_think = _resolve_director_think(cfg)
        review_max_tokens = cfg.get_int(
            "video_director_max_tokens", _DIRECTOR_MAX_TOKENS_DEFAULT
        )
        review_max_retries = cfg.get_int(
            "video_director_max_retries", _DIRECTOR_MAX_RETRIES_DEFAULT
        )

        # LONG. Non-halting: fall back to the unreviewed list on any failure.
        revised = await self._review_one(
            platform=platform, pool=pool, model=model, timeout_s=review_timeout,
            prompt_key="video.review_v1", script_var="podcast_script",
            script=context.get("podcast_script", ""), current=current,
            title=title, content_text=content_text, site_name=site_name,
            task_id=task_id, now_iso=now_iso, think=director_think,
            max_tokens=review_max_tokens, max_retries=review_max_retries,
        )
        updates: dict[str, Any] = {
            "video_shot_list": revised if revised is not None else current,
        }

        # SHORT (best-effort, only when present).
        short = context.get("short_shot_list")
        if short:
            revised_short = await self._review_one(
                platform=platform, pool=pool, model=model, timeout_s=review_timeout,
                prompt_key="video.review_short_v1", script_var="short_script",
                script=context.get("short_summary_script", ""), current=short,
                title=title, content_text=content_text, site_name=site_name,
                task_id=task_id, now_iso=now_iso, think=director_think,
                max_tokens=review_max_tokens, max_retries=review_max_retries,
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
