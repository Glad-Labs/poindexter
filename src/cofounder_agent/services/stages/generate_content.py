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
        # target_audience + domain feed the writer's blog_system_prompt
        # placeholders. content_router_service seeds both on the
        # context dict from the pipeline_tasks row (target_audience +
        # category columns). Empty/None passes through to the
        # generator's visible-sentinel fallback per
        # feedback_no_silent_defaults. See Glad-Labs/poindexter#369.
        target_audience = context.get("target_audience")
        domain = context.get("category")

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

        # Task 14 (RAG pivot): if the task carries a writer_rag_mode set by
        # the niche topic-discovery handoff, route to the new writer-mode
        # dispatcher instead of the legacy generator. The legacy path stays
        # intact for tasks with no writer_rag_mode (column is nullable per
        # migration 0114, so pre-niche tasks remain backward-compatible).
        writer_rag_mode = await self._read_writer_rag_mode(database_service, task_id)
        if writer_rag_mode:
            logger.info(
                "STAGE 2: writer_rag_mode=%s — dispatching to writer_rag_modes",
                writer_rag_mode,
            )
            content_text, model_used, metrics = await self._generate_via_writer_mode(
                writer_rag_mode=writer_rag_mode,
                topic=topic,
                style=style,
                tone=tone,
                tags=tags,
                database_service=database_service,
                task_id=task_id,
                site_config=context.get("site_config"),
            )
        else:
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
                    target_audience=target_audience,
                    domain=domain,
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
        originality = await _check_title_originality(title)
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
                originality_v2 = await _check_title_originality(title_v2)
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
        content_text = scrub_fabricated_links(content_text, known_slugs=real_slug_set)

        # Strip leaked image prompts / descriptions. LLMs sometimes emit
        # visual placeholders that we don't want in the body.
        content_text = _strip_leaked_image_prompts(content_text)

        # Writer self-review pass (opt-in via enable_writer_self_review).
        if _self_review_enabled(context.get("site_config")):
            try:
                revised, sr_meta = await _self_review_and_revise(
                    content_text, title, topic,
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
            research_svc = ResearchService(
                pool=database_service.pool if database_service else None
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

    async def _read_writer_rag_mode(
        self, database_service: Any, task_id: str,
    ) -> str | None:
        """Return the task's writer_rag_mode if set, else None.

        Task 14: niches set this column when handing tasks off via
        TopicBatchService; legacy/manual tasks leave it NULL and stay on the
        legacy generator path.

        Reads pipeline_tasks.writer_rag_mode directly rather than going
        through ``database_service.get_task()`` — that helper passes rows
        through ``ModelConverter.to_task_response()``, which is built
        against the public TaskResponse schema and silently drops fields
        not declared there. ``writer_rag_mode`` is one of the dropped
        fields, so the helper-based read returned None for every dev_diary
        task even when the column was set, sending the writer down the
        legacy path. Direct SQL avoids that schema gate.
        """
        try:
            pool = getattr(database_service, "pool", None)
            if pool is None:
                return None
            async with pool.acquire() as conn:
                mode = await conn.fetchval(
                    "SELECT writer_rag_mode FROM pipeline_tasks WHERE task_id = $1",
                    str(task_id),
                )
            if not mode:
                return None
            return str(mode).strip().upper() or None
        except Exception as e:
            logger.warning(
                "Failed to read writer_rag_mode for task %s: %s — falling back to legacy path",
                task_id, e,
            )
            return None

    async def _read_context_bundle(
        self, database_service: Any, task_id: str,
    ) -> dict[str, Any] | None:
        """Pull ``task_metadata.context_bundle`` from the live task row.

        Set by ``services/jobs/run_dev_diary_post.py::_create_dev_diary_task``
        on dev_diary tasks (PRs, notable commits, brain decisions, audit
        events, recent posts, cost summary). Niche-batch and ad-hoc
        tasks won't have this — returns None and the writer falls back
        to similarity-retrieved snippets only.

        Read order matters: must be called from a stage that runs
        BEFORE the writer's pipeline_versions UPSERT replaces the
        ``task_metadata`` key wholesale. ``generate_content`` is the
        first stage that touches the writer, so reading here gives the
        bundle a guaranteed clean view. After this stage runs the
        bundle is overwritten in storage by the writer's output (tone,
        style, content, etc.) — we capture once and pass through.

        Closes Glad-Labs/poindexter#353. Before this fix the writer
        only saw the topic title; with the bundle plumbed through it
        gets the actual PR titles + URLs to ground claims against.
        """
        try:
            # Read directly from pipeline_versions.stage_data rather than
            # going through database_service.get_task() — that helper passes
            # rows through ModelConverter, which projects task_metadata
            # against a UI-shaped schema and may surface a *different*
            # task_metadata than the raw row (the task_metadata column is
            # rebuilt from normalized columns for UI compatibility, which
            # drops the context_bundle key downstream stages set during
            # initial INSERT). The original bundle lives in stage_data
            # under task_metadata.context_bundle in the version-1 row
            # written by run_dev_diary_post.
            pool = getattr(database_service, "pool", None)
            if pool is None:
                return None
            import json as _json
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT stage_data FROM pipeline_versions "
                    "WHERE task_id = $1 ORDER BY version ASC LIMIT 1",
                    str(task_id),
                )
            if not row:
                return None
            sd = row["stage_data"]
            if isinstance(sd, str):
                try:
                    sd = _json.loads(sd)
                except Exception:
                    return None
            if not isinstance(sd, dict):
                return None
            # Preferred read: the dev_diary job stashes the bundle at the
            # top level under _dev_diary_bundle to dodge the
            # content_tasks_update_redirect trigger, which JSONB-merges
            # only (metadata, result, task_metadata) so a key outside
            # those survives writer updates. Fall through to the legacy
            # task_metadata.context_bundle read for older rows.
            preserved = sd.get("_dev_diary_bundle")
            if isinstance(preserved, dict) and preserved:
                return preserved
            if isinstance(preserved, str):
                try:
                    return _json.loads(preserved)
                except Exception:
                    pass
            tm = sd.get("task_metadata") or {}
            if isinstance(tm, str):
                try:
                    tm = _json.loads(tm)
                except Exception:
                    return None
            if not isinstance(tm, dict):
                return None
            cb = tm.get("context_bundle")
            if cb is None:
                inner = tm.get("metadata")
                if isinstance(inner, dict):
                    cb = inner.get("context_bundle")
            if isinstance(cb, str):
                try:
                    cb = _json.loads(cb)
                except Exception:
                    return None
            return cb if isinstance(cb, dict) else None
        except Exception as e:
            logger.warning(
                "Failed to read context_bundle for task %s: %s — "
                "falling back to no bundle",
                task_id, e,
            )
            return None

    async def _read_writer_prompt_override(
        self, database_service: Any, task_id: str,
    ) -> str | None:
        """Return ``niches.writer_prompt_override`` for the task's niche.

        Returns None when:
         - the task has no ``niche_slug`` (legacy/manual tasks)
         - the niche row doesn't exist
         - the niche has ``writer_prompt_override = NULL``
         - the lookup raises (logged as warning, falls back to no override)

        The override flows down to the writer mode handler via
        ``dispatch_writer_mode(writer_prompt_override=...)``. When a
        niche has a non-null override, it's prepended to the
        mode-specific instruction by the handler (currently TWO_PASS
        only — see ``writer_rag_modes/two_pass.py::_draft_node``).

        Wired by migration 0141 + companion changes; before that, the
        column was written by migrations but never read.
        """
        try:
            task_row = await database_service.get_task(task_id)
            niche_slug = (task_row or {}).get("niche_slug")
            if not niche_slug:
                return None
            pool = getattr(database_service, "pool", None)
            if pool is None:
                return None
            async with pool.acquire() as conn:
                value = await conn.fetchval(
                    "SELECT writer_prompt_override FROM niches WHERE slug = $1",
                    niche_slug,
                )
            return str(value) if value else None
        except Exception as e:
            logger.warning(
                "Failed to read writer_prompt_override for task %s: %s — "
                "falling back to no override",
                task_id, e,
            )
            return None

    async def _generate_via_writer_mode(
        self,
        *,
        writer_rag_mode: str,
        topic: str,
        style: str,
        tone: str,
        tags: list[str],
        database_service: Any,
        task_id: str,
        site_config: Any = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Run dispatch_writer_mode and shape the result into the
        (content_text, model_used, metrics) tuple the rest of this stage
        already expects.

        The dispatcher returns a richer dict ({draft, snippets_used, mode,
        ...}); we surface mode-specific extras inside ``metrics`` so the
        downstream QA / persistence stages can see them but the main flow
        stays unchanged.
        """
        from services.writer_rag_modes import dispatch_writer_mode

        # The writer modes use "angle" rather than separate style/tone/tags;
        # collapse the available descriptors into a single angle string.
        angle_parts = [p for p in (style, tone) if p]
        if tags:
            angle_parts.append("tags: " + ", ".join(tags))
        angle = " | ".join(angle_parts) or "general"

        pool = getattr(database_service, "pool", None)
        if pool is None:
            raise ValueError(
                "writer_rag_mode dispatch requires database_service.pool but it is None"
            )

        # niche_id is not strictly needed by the modes themselves — the modes
        # query the global embeddings table by topic+angle similarity. If a
        # downstream mode wants to scope to a niche it can look the niche_id
        # up via the task row's niche_slug.

        # niches.writer_prompt_override — wired by migration 0141. None
        # for legacy/manual tasks (no niche_slug) and for niches that
        # haven't seeded an override; the handler prepends a non-empty
        # override before the mode-specific instruction. Lookup is
        # best-effort: a DB error here logs a warning and falls back to
        # no override rather than failing the whole generation pass.
        writer_prompt_override = await self._read_writer_prompt_override(
            database_service, task_id,
        )

        # task_metadata.context_bundle — set by the dev_diary job
        # (PRs/commits/decisions/audit/recent posts/cost summary). We
        # read it HERE (before any subsequent stage replaces
        # task_metadata via the JSONB || merge) and plumb it through
        # to the writer mode so _draft_node can include it as a
        # GROUND TRUTH section in the prompt. Closes #353. None for
        # niche-batch/manual tasks (writer falls back to similarity-
        # retrieved snippets only).
        context_bundle = await self._read_context_bundle(
            database_service, task_id,
        )

        from services.gpu_scheduler import gpu
        async with gpu.lock(
            "ollama", model="glm-4.7-5090:latest",
            task_id=task_id, phase="generate_content",
        ):
            result = await dispatch_writer_mode(
                mode=writer_rag_mode,
                topic=topic,
                angle=angle,
                niche_id=None,
                pool=pool,
                writer_prompt_override=writer_prompt_override,
                context_bundle=context_bundle,
                # DI seam (glad-labs-stack#330) — threaded so each writer
                # mode handler reads from the injected SiteConfig instead
                # of importing the legacy module-level singleton.
                site_config=site_config,
            )

        draft = result.get("draft") or ""
        # The writer-mode helpers all call _ollama_chat_json with this model.
        # If a future mode picks a different model it should put it on the
        # result dict so we can surface the real value.
        model_used = result.get("model_used") or "glm-4.7-5090:latest"
        metrics: dict[str, Any] = {
            "writer_rag_mode": writer_rag_mode,
            "snippets_used_count": len(result.get("snippets_used") or []),
            "models_used_by_phase": {"generate_content": model_used},
            "model_selection_log": {
                "generate_content": {
                    "preferred": model_used,
                    "actual": model_used,
                    "source": "writer_rag_mode_dispatch",
                },
            },
        }
        # TWO_PASS-specific extras (LangGraph state machine output).
        for k in ("external_lookups", "revision_loops", "loop_capped", "spine"):
            if k in result:
                metrics[k] = result[k]
        return draft, model_used, metrics

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

    Accepts the SiteConfig instance as a parameter (DI seam from
    glad-labs-stack#330). Falls back to True when ``site_config`` is None
    so direct/test invocations of the stage don't accidentally disable
    self-review via a missing config.
    """
    if site_config is None:
        return True
    try:
        raw = site_config.get("enable_writer_self_review", "true")
        return str(raw).lower() in ("true", "1", "yes")
    except Exception:
        return True
