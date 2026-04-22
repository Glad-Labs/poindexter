"""GenerateContentStage — stage 2 of the content pipeline.

Ports ``_stage_generate_content`` from content_router_service.py with
byte-equivalent observable behavior. The helpers this stage depends on
(``_build_writing_style_context``, ``_build_rag_context``,
``_generate_canonical_title``, ``_check_title_originality``,
``normalize_text``, ``_scrub_fabricated_links``,
``_self_review_and_revise``, ``_parse_model_preferences``) stay in
content_router_service.py for now — we import them here until Phase E
cleanup lifts them into shared utility modules.

## Flow

1. Parse model preferences from ``models_by_phase``
2. Build writing-style + research + RAG context
3. Generate content via AI (GPU-locked to ollama)
4. Generate canonical title, then web-originality check + regenerate if duplicate
5. Normalize + scrub + strip leaked image prompts
6. Run writer self-review (if enabled)
7. Persist content + title + model to ``content_tasks``
8. Log cost

## Context reads

- ``task_id``, ``topic``, ``style``, ``tone``, ``target_length``,
  ``tags``, ``models_by_phase``, ``database_service``

## Context writes

- ``content``, ``content_length``, ``title``, ``model_used``,
  ``models_used_by_phase``, ``model_selection_log``,
  ``research_context``
- ``stages["2_content_generated"] = True``
- ``generate_metrics`` (raw dict from the generator — consumed by
  downstream stages that need e.g. the original cost_log)

## Error semantics

- Missing content_text from the generator raises ``ValueError`` — the
  runner catches it, records the stage as failed, and halts (default
  ``halts_on_failure = True``). Matches legacy behavior.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class GenerateContentStage:
    name = "generate_content"
    description = "Generate the blog post body + title via the configured LLM"
    # Legacy _get_stage_timeout("generate_content") returned 900s for this
    # stage specifically (it runs the largest writer model). Preserve that.
    timeout_seconds = 900
    halts_on_failure = True  # Legacy raised RuntimeError on failure.

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        # Phase H (GH#95): site_config is seeded on the pipeline context
        # by content_router_service. Resolve it up-front so the whole
        # execute() body has access. Transitional fallback to the module
        # singleton for tests that build their own context dict.
        _sc = context.get("site_config")
        if _sc is None:
            from services.site_config import site_config as _sc

        # Late imports — these helpers live in the legacy god-file and
        # form a cluster that's still being decomposed. Import-at-call
        # sidesteps any circular-import risk with content_router_service
        # during the Phase E transition.
        from services.ai_content_generator import get_content_generator
        from services.audit_log import audit_log_bg
        from services.model_preferences import parse_model_preferences as _parse_model_preferences
        from services.self_review import self_review_and_revise as _self_review_and_revise
        from services.text_utils import normalize_text, scrub_fabricated_links
        from services.title_generation import (
            check_title_originality as _check_title_originality,
        )
        from services.title_generation import (
            generate_canonical_title as _generate_canonical_title,
        )
        from services.writing_style_context import (
            build_writing_style_context as _build_writing_style_context,
        )

        task_id = context.get("task_id")
        topic = context.get("topic", "")
        style = context.get("style", "")
        tone = context.get("tone", "")
        target_length = int(context.get("target_length", 1200))
        tags = context.get("tags") or []
        models_by_phase = context.get("models_by_phase") or {}
        database_service = context.get("database_service")

        if not task_id or database_service is None:
            return StageResult(
                ok=False,
                detail="context missing task_id or database_service",
            )

        logger.info("STAGE 2: Generating blog content...")

        content_generator = get_content_generator()
        preferred_model, preferred_provider = _parse_model_preferences(models_by_phase)

        # Fetch active writing style samples for voice/tone matching.
        writing_style_context = await _build_writing_style_context(database_service)

        # Build research context — start with anything the API caller
        # attached to the task (task_metadata JSON, metadata JSONB, or
        # top-level field), then layer on the ResearchService's auto-
        # built context, then RAG-from-pgvector.
        research_context = await self._collect_research_context(
            database_service, task_id, topic,
            source_tags=context.get("tags") or [],
            source_category=context.get("category") or "",
        )

        # Surface the research corpus on the context so the downstream
        # multi-model QA stage can ground its fact-checking against the
        # same sources the writer consulted.
        context["research_context"] = research_context

        # Generate content (GPU-locked to ollama mode).
        from services.gpu_scheduler import gpu
        async with gpu.lock(
            "ollama", model=preferred_model,
            task_id=task_id, phase="generate_content",
        ):
            content_text, model_used, metrics = await content_generator.generate_blog_post(
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags=tags,
                preferred_model=preferred_model,
                preferred_provider=preferred_provider,
                writing_style_context=writing_style_context,
                research_context=research_context,
            )

        if not content_text:
            logger.error("Content generation returned None or empty")
            raise ValueError("Content generation failed: no content produced")

        # Generate canonical title with recent-titles avoidance prompt.
        logger.info("Generating title from content...")
        primary_keyword = tags[0] if tags else topic
        existing_titles = await self._fetch_existing_titles(database_service)
        title = await _generate_canonical_title(
            topic, primary_keyword, content_text[:500], existing_titles=existing_titles,
        ) or topic
        logger.info("Title generated: %s", title)

        # Title originality: if the title collides with something on the
        # web, regenerate with a stronger avoidance list. Only take the
        # regenerated version if it's actually more original.
        originality = await _check_title_originality(title, site_config=_sc)
        if not originality["is_original"]:
            logger.warning(
                "[TITLE] Title too similar to existing content — regenerating with stronger uniqueness prompt"
            )
            avoid_list = existing_titles
            for dup_title in originality["similar_titles"][:5]:
                avoid_list += f"\n- {dup_title}"
            title_v2 = await _generate_canonical_title(
                topic, primary_keyword, content_text[:500],
                existing_titles=avoid_list,
            )
            if title_v2:
                originality_v2 = await _check_title_originality(title_v2, site_config=_sc)
                # GH-87: prefer the regenerated title if it drops below
                # either the internal-corpus similarity threshold OR the
                # external-duplicate flag. Previously we only looked at
                # max_similarity, which ignored verbatim external matches.
                v1_ext_dup = bool(originality.get("external_verbatim_match"))
                v2_ext_dup = bool(originality_v2.get("external_verbatim_match"))
                more_original = (
                    originality_v2["max_similarity"] < originality["max_similarity"]
                    or (v1_ext_dup and not v2_ext_dup)
                )
                if more_original:
                    logger.info(
                        "[TITLE] Regenerated title is more original (%.0f%% → %.0f%%): %s",
                        originality["max_similarity"] * 100,
                        originality_v2["max_similarity"] * 100,
                        title_v2,
                    )
                    title = title_v2
                    originality = originality_v2
                else:
                    logger.info("[TITLE] Keeping original title — regeneration wasn't more unique")

        # GH-87: persist the originality report so (a) the QA stage can
        # apply the configured penalty to its final score, and (b) the
        # approver UI can surface external near-matches as a warning.
        context["title_originality"] = originality

        # Normalize smart quotes / special chars.
        content_text = normalize_text(content_text)
        title = normalize_text(title)

        # Build the real-slug allowlist from the content generator's
        # internal-links cache, then scrub fabricated links using it.
        real_slug_set: set[str] = set()
        try:
            links_cache = getattr(content_generator, "_internal_links_cache", [])
            for link_line in links_cache:
                if "/posts/" in link_line:
                    slug = link_line.split("/posts/")[-1].strip().strip('"')
                    if slug:
                        real_slug_set.add(slug)
        except Exception:
            pass
        content_text = scrub_fabricated_links(
            content_text, known_slugs=real_slug_set, site_config=_sc,
        )

        # Strip leaked image prompts / descriptions. LLMs sometimes emit
        # visual placeholders that we don't want in the body.
        content_text = _strip_leaked_image_prompts(content_text)

        # Writer self-review pass (opt-in via enable_writer_self_review).
        if _self_review_enabled(_sc):
            try:
                # generate_content stage hasn't migrated away from the
                # singleton yet (pending its own Phase H pass); pass it
                # through so self_review no longer imports the singleton
                # at module scope.
                from services.site_config import site_config as _sc
                revised, sr_meta = await _self_review_and_revise(
                    content_text, title, topic, _sc,
                )
                if sr_meta.get("revised"):
                    logger.info(
                        "[SELF_REVIEW] Writer revised draft — %d contradictions fixed",
                        sr_meta.get("contradictions_found", 0),
                    )
                    content_text = revised
                else:
                    logger.info(
                        "[SELF_REVIEW] Draft passed self-review (%d chars)",
                        len(content_text),
                    )
                audit_log_bg(
                    "writer_self_review", "content_router",
                    {
                        "contradictions_found": sr_meta.get("contradictions_found", 0),
                        "revised": sr_meta.get("revised", False),
                        "skipped": sr_meta.get("skipped", False),
                        "reason": sr_meta.get("reason"),
                    },
                    task_id=task_id,
                )
            except Exception as sr_err:
                logger.warning(
                    "[SELF_REVIEW] Self-review pass failed (non-fatal): %s", sr_err,
                )

        # Persist to content_tasks.
        await database_service.update_task(
            task_id=task_id,
            updates={
                "status": "in_progress",
                "content": content_text,
                "title": title,
                "model_used": model_used,
                "models_used_by_phase": metrics.get("models_used_by_phase", {}),
                "model_selection_log": metrics.get("model_selection_log", {}),
            },
        )

        # Context updates.
        stages = context.setdefault("stages", {})
        stages["2_content_generated"] = True

        updates = {
            "content": content_text,
            "content_length": len(content_text),
            "title": title,
            "model_used": model_used,
            "models_used_by_phase": metrics.get("models_used_by_phase", {}),
            "model_selection_log": metrics.get("model_selection_log", {}),
            "generate_metrics": metrics,
            "stages": stages,
        }
        logger.info("Content generated (%d chars) using %s", len(content_text), model_used)

        # Log cloud API cost if the generator tracked one.
        cost_log = metrics.get("cost_log")
        if cost_log:
            try:
                cost_log["task_id"] = task_id
                await database_service.log_cost(cost_log)
                logger.info(
                    "Cost logged: $%.4f (%s/%s)",
                    cost_log["cost_usd"], cost_log["provider"], cost_log["model"],
                )
            except Exception as e:
                logger.warning("Cost logging failed (non-critical): %s", e)

        # Snapshot the initial draft into content_revisions so the feedback
        # loop can later diff this against the QA-revised + finalized
        # versions (gitea#271 Phase 3.A2).
        try:
            from services.content_revisions_logger import log_revision
            await log_revision(
                database_service.pool,
                task_id=task_id,
                content=content_text,
                title=title,
                change_type="initial_draft",
                change_summary="Writer first-pass output",
                model_used=model_used,
                quality_score=metrics.get("final_quality_score"),
            )
        except Exception as rev_err:
            logger.debug("[content_revisions] initial snapshot failed: %s", rev_err)

        return StageResult(
            ok=True,
            detail=f"{len(content_text)} chars via {model_used}",
            context_updates=updates,
            metrics={"content_length": len(content_text), "model_used": model_used},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _collect_research_context(
        self,
        database_service: Any,
        task_id: str,
        topic: str,
        source_tags: list[str] | None = None,
        source_category: str | None = None,
    ) -> str:
        """Layer caller-provided + ResearchService + RAG contexts into one blob.

        GH-88: ``source_tags`` / ``source_category`` are threaded through to
        the RAG step so :mod:`services.research_context` can apply the
        coherence filter and reject off-topic internal-link candidates.
        """
        research_context = ""

        # 1. Anything the API caller attached to the task.
        try:
            task_row = await database_service.get_task(task_id)
            if task_row:
                caller_context = _extract_caller_research(task_row)
                if caller_context:
                    research_context = caller_context
                    logger.info(
                        "Research context from task: %d chars", len(caller_context),
                    )
        except Exception as e:
            logger.debug("Failed to load task research_context: %s", e)

        # 2. ResearchService auto-built context.
        try:
            from services.research_service import ResearchService
            # Content-pipeline stages don't receive site_config via DI
            # yet — use a transitional module-singleton import at the
            # call site until stage fn signatures migrate (pending Phase
            # H follow-up). GH#95.
            from services.site_config import site_config as _sc
            research_svc = ResearchService(
                pool=database_service.pool if database_service else None,
                site_config=_sc,
            )
            auto = await research_svc.build_context(topic)
            if auto:
                research_context = (
                    f"{research_context}\n\n{auto}" if research_context else auto
                )
                logger.info("Research context built: %d chars", len(research_context))
        except Exception as e:
            logger.warning("Research context skipped: %s", e)

        # 3. RAG context via pgvector similarity search.
        try:
            from services.research_context import build_rag_context
            # GH-88: pass source_tags + source_category so the coherence
            # filter can reject off-topic candidates (e.g. CadQuery pinned
            # as "related" on an asyncio or AI-engineering post).
            rag = await build_rag_context(
                database_service,
                topic,
                source_tags=source_tags or [],
                source_category=source_category or "",
            )
            if rag:
                research_context = (
                    f"{research_context}\n\n{rag}" if research_context else rag
                )
                logger.info("RAG context injected: %d chars", len(rag))
        except Exception as e:
            logger.warning("RAG context skipped (non-fatal): %s", e)

        return research_context

    async def _fetch_existing_titles(self, database_service: Any) -> str:
        """Return newline-separated recent published titles for avoidance prompt."""
        try:
            pool = getattr(database_service, "pool", None)
            if not pool:
                return ""
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT title FROM content_tasks WHERE status = 'published' "
                    "ORDER BY created_at DESC LIMIT 20"
                )
            return "\n".join(f"- {r['title']}" for r in rows if r["title"])
        except Exception:
            return ""  # Non-critical — proceed without diversity check.


# ---------------------------------------------------------------------------
# Module-level helpers (lifted out for readability + testability)
# ---------------------------------------------------------------------------


_LEAKED_IMAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Standalone italic scene descriptions: *A dramatic scene of...*
    re.compile(r'(?m)^\s*\*(?:A |An |Imagine |Visual |Split|Close)[^*]{40,}\*\s*$'),
    # `: *description*` image caption patterns
    re.compile(r'(?m)^\s*:\s*\*[A-Z][^*]{30,}\*\s*$'),
    # [IMAGE-N: description] placeholders
    re.compile(r'\[IMAGE(?:-\d+)?:\s*[^\]]+\]'),
    # [FIGURE: description] placeholders
    re.compile(r'\[FIGURE:\s*[^\]]+\]'),
)

_COLLAPSE_BLANK_LINES = re.compile(r'\n{3,}')


def _strip_leaked_image_prompts(content: str) -> str:
    """Remove image-description placeholders the LLM may have emitted in prose."""
    out = content
    for pat in _LEAKED_IMAGE_PATTERNS:
        out = pat.sub('', out)
    return _COLLAPSE_BLANK_LINES.sub('\n\n', out)


def _extract_caller_research(task_row: dict[str, Any]) -> str:
    """Dig the caller-attached research_context out of task row metadata."""
    import json as _json

    task_meta = task_row.get("task_metadata") or "{}"
    if isinstance(task_meta, str):
        try:
            task_meta = _json.loads(task_meta)
        except Exception:
            task_meta = {}

    metadata_jsonb = task_row.get("metadata") or {}
    if isinstance(metadata_jsonb, str):
        try:
            metadata_jsonb = _json.loads(metadata_jsonb)
        except Exception:
            metadata_jsonb = {}

    return (
        task_meta.get("research_context")
        or metadata_jsonb.get("research_context")
        or task_row.get("research_context")
        or ""
    )


def _self_review_enabled(site_config: Any = None) -> bool:
    """Read the ``enable_writer_self_review`` feature flag from site_config.

    ``site_config`` is the config object threaded through the pipeline
    context (Phase H step 4). When None, falls back to the module-level
    singleton for backward compatibility — removed in step 5.
    """
    try:
        cfg = site_config
        if cfg is None:
            from services.site_config import site_config as cfg
        raw = cfg.get("enable_writer_self_review", "true")
        return str(raw).lower() in ("true", "1", "yes")
    except Exception:
        return True
