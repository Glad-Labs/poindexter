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
        from modules.content.ai_content_generator import get_content_generator
        from services.model_preferences import parse_model_preferences as _parse_model_preferences
        from services.self_review import self_review_and_revise as _self_review_and_revise
        from services.text_utils import normalize_text, scrub_fabricated_links
        from services.title_generation import (
            check_title_originality as _check_title_originality,
        )
        from services.title_generation import (
            choose_canonical_title as _choose_canonical_title,
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
        # Operator steering injected by atoms.approval_gate on a regen-at-gate
        # resume (#149). Empty string when no regen or no --reason was given.
        regen_steering = context.get("regen_steering") or ""
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

        content_generator = get_content_generator(
            site_config=context.get("site_config"),
            platform=context.get("platform"),
        )
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
            site_config=context.get("site_config"),
        )

        # Surface the research corpus on the context so the downstream
        # multi-model QA stage can ground its fact-checking against the
        # same sources the writer consulted.
        context["research_context"] = research_context

        # Niche-driven tasks route through the two_pass writer atom (RAG
        # snippets + optional external research + revision loop). The
        # legacy ``content_generator.generate_blog_post`` path stays for
        # manual / pre-niche tasks that arrive without a niche_slug.
        #
        # Pre-2026-05-28 this dispatched via writer_rag_modes by reading
        # ``pipeline_tasks.writer_rag_mode`` — that whole dispatcher (and
        # its column) was retired with the dead-mode cleanup. niche_slug
        # is the durable seam.
        niche_slug = await self._read_niche_slug(database_service, task_id)
        if niche_slug:
            # Surface niche_slug on context so downstream stages /
            # record_run see it consistently. content_router_service
            # already seeds this for tasks created via the niche path,
            # but stamping again here is idempotent and covers the
            # legacy code paths that read it directly off
            # pipeline_tasks.niche_slug for the first time at this
            # stage.
            context["niche_slug"] = niche_slug
            logger.info(
                "STAGE 2: niche=%s — dispatching to atoms.two_pass_writer",
                niche_slug,
            )
            content_text, model_used, metrics = await self._generate_via_two_pass_atom(
                topic=topic,
                style=style,
                tone=tone,
                tags=tags,
                database_service=database_service,
                task_id=task_id,
                niche_slug=niche_slug,
                site_config=context.get("site_config"),
                research_context=research_context,
                regen_steering=regen_steering,
            )
        else:
            # Generate content (GPU-locked to ollama mode).
            # Inject operator regen steering as a style prefix when present (#149).
            # The legacy path has no writer_prompt_override seam, so style is the
            # closest equivalent — a bare regen with no --reason leaves style unchanged.
            effective_style = (
                f"IMPORTANT — Operator feedback from prior review:\n"
                f"{regen_steering}\n\n"
                f"Address this feedback in your draft.\n\n"
                + (style or "")
            ).strip() if regen_steering else style
            from services.gpu_scheduler import gpu
            async with gpu.lock(
                "ollama", model=preferred_model,
                task_id=task_id, phase="generate_content",
            ):
                content_text, model_used, metrics = await content_generator.generate_blog_post(
                    topic=topic,
                    style=effective_style,
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

        # poindexter#691 — empty/too-short writer output must FAIL THE TASK
        # loud here, not flow into QA as a misleading reviewer_count:0 0/100
        # reject. A reasoning writer model can intermittently emit all of its
        # tokens into the thinking channel and return empty content (documented
        # in services/llm_text.py::resolve_structured_model). We do a
        # LOAD-BEARING terminal write (status='failed' + a specific
        # error_message + a finding) BEFORE raising — so the clear cause sticks
        # even though the graph_def node wrapper swallows the raise into a
        # (currently unhonored) ``_halt`` and keeps running downstream nodes.
        # This mirrors qa.aggregate's reject-persistence idiom; the GH-90
        # terminal-write guard then blocks any later QA-reject write, so the
        # writer-empty cause is what surfaces, not a confusing QA reject.
        stripped = (content_text or "").strip()
        min_chars = _min_draft_chars(context.get("site_config"))
        if not stripped or len(stripped) < min_chars:
            kind = "empty" if not stripped else "too-short"
            detail = (
                f"{len(stripped)} chars" if not stripped
                else f"{len(stripped)} chars < min {min_chars}"
            )
            error_message = (
                f"writer produced {kind} draft ({detail}); "
                f"model={model_used or '?'} — no content produced "
                f"(reasoning-model empty output; poindexter#691)"
            )
            logger.error("[generate_content] %s", error_message)
            await _fail_empty_draft(
                database_service,
                task_id=str(task_id),
                error_message=error_message,
                model=model_used,
                kind=kind,
                content_len=len(stripped),
                platform=context.get("platform"),
            )
            raise ValueError(error_message)

        # Generate canonical title with recent-titles avoidance prompt.
        logger.info("Generating title from content...")
        primary_keyword = tags[0] if tags else topic
        existing_titles = await self._fetch_existing_titles(database_service)
        llm_title = await _generate_canonical_title(
            topic, primary_keyword, content_text[:500], existing_titles=existing_titles,
            site_config=context.get("site_config"),  # type: ignore[arg-type]
        )
        # poindexter#471: prefer the writer's H1 over the raw topic when
        # the LLM title-gen call returns nothing. The raw topic carries
        # the QA test-batch tracking suffix into sitemaps / OG cards.
        # ``choose_canonical_title`` handles the suffix strip + H1
        # extraction + drift WARN in one place.
        title = _choose_canonical_title(topic, content_text, llm_title)
        logger.info("Title generated: %s", title)

        # Title originality: if the title collides with something on the
        # web, regenerate with a stronger avoidance list. Only take the
        # regenerated version if it's actually more original.
        originality = await _check_title_originality(
            title, site_config=context.get("site_config"),  # type: ignore[arg-type]
        )
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
                site_config=context.get("site_config"),  # type: ignore[arg-type]
            )
            if title_v2:
                originality_v2 = await _check_title_originality(
                    title_v2, site_config=context.get("site_config"),  # type: ignore[arg-type]
                )
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
        except Exception as exc:
            # poindexter#455 — used to be silent. An empty real-slug
            # allowlist makes scrub_fabricated_links() more aggressive
            # (anything not on the list is a candidate to scrub),
            # which can quietly remove legitimate internal links if
            # the cache layout shifts. Debug-log so the scrub behavior
            # change is traceable.
            logger.debug(
                "[generate_content] failed to build real-slug allowlist "
                "from _internal_links_cache (%s: %s) — scrub_fabricated_links "
                "will run with empty allowlist",
                type(exc).__name__, exc,
            )
        content_text = scrub_fabricated_links(content_text, known_slugs=real_slug_set)

        # Strip leaked image prompts / descriptions. LLMs sometimes emit
        # visual placeholders that we don't want in the body.
        content_text = _strip_leaked_image_prompts(content_text)

        # Writer self-review pass (opt-in via enable_writer_self_review).
        if _self_review_enabled(context.get("site_config")):
            try:
                # Lane B sweep: thread pool for cost-tier model resolution.
                _sr_pool = getattr(database_service, "pool", None)
                revised, sr_meta = await _self_review_and_revise(
                    content_text, title, topic, pool=_sr_pool,
                    site_config=context.get("site_config"),  # type: ignore[arg-type]
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
                # Seam 1 Wave 3c (#667) — audit through the capability handle.
                _platform = context.get("platform")
                if _platform is not None:
                    _platform.audit.write_bg(
                        "writer_self_review",
                        source="content_router",
                        details={
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
            # Surface the research corpus on the StageResult so it survives
            # the graph_def path. The bare ``context["research_context"] = ...``
            # mutation above is only visible to in-process callers that share
            # the same context dict; on the LangGraph graph_def path (atom-
            # cutover #355) ``make_stage_node`` merges ONLY
            # ``StageResult.context_updates`` back into the shared state, so a
            # mutation that isn't echoed here is dropped. Without this, the
            # qa.ragas / qa.deepeval grounding rails read an absent
            # ``research_context`` and skipped 100% of the time
            # (Glad-Labs/poindexter#553). Keep the two writes in lockstep.
            "research_context": research_context,
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
        # versions (internal tracker Phase 3.A2).
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

        # Phase 0 lab observability (2026-05-28) — surface the prompt
        # provenance + niche_slug on the StageResult.metrics dict so
        # capability_outcomes.record_run picks them up via the
        # per-record metrics path. None values are tolerated downstream
        # and recorded as NULL, which is the right signal for stages
        # that didn't resolve a UnifiedPromptManager key.
        stage_metrics: dict[str, Any] = {
            "content_length": len(content_text),
            "model_used": model_used,
        }
        if metrics.get("prompt_template_key") is not None:
            stage_metrics["prompt_template_key"] = metrics.get("prompt_template_key")
        if metrics.get("prompt_template_version") is not None:
            stage_metrics["prompt_template_version"] = metrics.get(
                "prompt_template_version"
            )
        niche_for_metrics = context.get("niche_slug")
        if niche_for_metrics:
            stage_metrics["niche_slug"] = niche_for_metrics
        # Phase 1 lab harness — surface the experiment-runner variant
        # assignment (when present) so record_run stamps variant_id on
        # this stage's outcome row. ``_generate_via_two_pass_atom``
        # stashes the variant on metrics + on context for downstream
        # stages. None when no experiment was active for the niche
        # (the common path).
        if metrics.get("variant_id") is not None:
            stage_metrics["variant_id"] = metrics.get("variant_id")

        return StageResult(
            ok=True,
            detail=f"{len(content_text)} chars via {model_used}",
            context_updates=updates,
            metrics=stage_metrics,
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
        *,
        site_config: Any = None,
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
                pool=database_service.pool if database_service else None,
                site_config=site_config,
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

    async def _read_niche_slug(
        self, database_service: Any, task_id: str,
    ) -> str | None:
        """Return the task's niche_slug if set, else None.

        Tasks dispatched by niche topic-discovery (TopicBatchService) +
        the dev_diary job carry a niche_slug; legacy / manual / CLI
        tasks leave it NULL and stay on the legacy generator path.

        Reads pipeline_tasks.niche_slug directly rather than going
        through ``database_service.get_task()`` — that helper passes
        rows through ``ModelConverter.to_task_response()``, which is
        built against the public TaskResponse schema and silently drops
        fields not declared there. Direct SQL avoids that schema gate.

        Replaces the prior ``_read_writer_rag_mode`` (2026-05-28: the
        ``writer_rag_mode`` column was retired with the dead-mode
        cleanup; niche_slug is the durable routing seam).
        """
        try:
            pool = getattr(database_service, "pool", None)
            if pool is None:
                return None
            async with pool.acquire() as conn:
                slug = await conn.fetchval(
                    "SELECT niche_slug FROM pipeline_tasks WHERE task_id = $1",
                    str(task_id),
                )
            if not slug:
                return None
            return str(slug).strip() or None
        except Exception as e:
            logger.warning(
                "Failed to read niche_slug for task %s: %s — falling back to legacy path",
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

    async def _generate_via_two_pass_atom(
        self,
        *,
        topic: str,
        style: str,
        tone: str,
        tags: list[str],
        database_service: Any,
        task_id: str,
        niche_slug: str | None = None,
        site_config: Any = None,
        research_context: str = "",
        regen_steering: str = "",
    ) -> tuple[str, str, dict[str, Any]]:
        """Run ``atoms.two_pass_writer`` and shape the result into the
        (content_text, model_used, metrics) tuple the rest of this stage
        already expects.

        The atom returns a richer dict ({draft, snippets_used, ...});
        we surface the writer-specific extras inside ``metrics`` so the
        downstream QA / persistence stages can see them but the main
        flow stays unchanged.

        Pre-2026-05-28 this dispatched through the now-deleted
        ``writer_rag_modes.dispatch_writer_mode("TWO_PASS")`` indirection.

        Phase 1 lab harness (PR #699 + this PR) — when ``niche_slug`` is
        provided, this method calls
        :func:`services.experiment_runner.pick_variant` exactly once for
        the task. If a variant is returned, the model resolution
        short-circuits to the variant's ``writer_model`` (when set) and
        the variant identifiers are surfaced on the returned ``metrics``
        dict so the recorder stamps them on the capability_outcomes row.
        Failure-safe: a variant-runner exception falls back to the
        no-variant production path (per the design doc's "Posture:
        testing in production").
        """
        from modules.content.atoms import two_pass_writer

        # The writer modes use "angle" rather than separate style/tone/tags;
        # collapse the available descriptors into a single angle string.
        angle_parts = [p for p in (style, tone) if p]
        if tags:
            angle_parts.append("tags: " + ", ".join(tags))
        angle = " | ".join(angle_parts) or "general"

        pool = getattr(database_service, "pool", None)
        if pool is None:
            raise ValueError(
                "atoms.two_pass_writer requires database_service.pool but it is None"
            )

        # Phase 1 lab harness hook — pick a variant from the niche's
        # active experiment, if any. Returns None for the common path
        # (no experiment running for this niche); production behavior
        # unchanged. The runner is internally fail-safe — any DB hiccup
        # logs a warning + returns None; this call cannot raise.
        variant = None
        if niche_slug:
            try:
                from services import experiment_runner
                variant = await experiment_runner.pick_variant(
                    pool, niche_slug, str(task_id),
                )
            except Exception as exc:  # noqa: BLE001 — defense in depth
                # pick_variant is itself wrapped in try/except, but a
                # bug at the import seam (e.g. test patching a missing
                # symbol) shouldn't crash the writer.
                logger.warning(
                    "[generate_content] experiment_runner.pick_variant "
                    "raised: %s — falling back to no variant", exc,
                )
                variant = None

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

        # Operator regen steering (#149) — prepend before any niche-level
        # writer_prompt_override so the operator's note is the first thing
        # the writer sees. A bare regen with no --reason leaves the override
        # unchanged. Truncated to 1000 chars to guard against a runaway
        # reason string eating the context window.
        if regen_steering:
            steering_prefix = (
                f"IMPORTANT — Operator feedback from prior review:\n"
                f"{regen_steering[:1000]}\n\n"
                f"Address this feedback in your draft.\n\n"
            )
            writer_prompt_override = steering_prefix + (writer_prompt_override or "")

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

        # 2026-05-12 (poindexter#485): the GPU-scheduler lock label and
        # the "model_used" attribution fallback both used to bake
        # ``glm-4.7-5090:latest`` — Matt's specific model — into a
        # public OSS file. Resolve from site_config so the scheduler
        # logs + metrics match whatever model the operator actually
        # has installed. resolve_local_model fails loud (raise) when
        # pipeline_writer_model is unset; the writer-mode dispatch below
        # would fail for the same reason, so we're not regressing
        # observability.
        #
        # Phase 1 lab harness — when a variant assigns ``writer_model``,
        # it short-circuits ``resolve_local_model`` (which accepts an
        # explicit model string and returns it after the ``ollama/``
        # prefix strip). The GPU lock label uses the same resolved
        # value so observability + scheduling match the model the
        # variant actually exercises.
        from services.gpu_scheduler import gpu
        from services.llm_text import resolve_local_model
        variant_model_override = (
            variant.writer_model if variant is not None else None
        )
        scheduler_model_label = resolve_local_model(
            model=variant_model_override, site_config=site_config,
        )
        async with gpu.lock(
            "ollama", model=scheduler_model_label,
            task_id=task_id, phase="generate_content",
        ):
            result = await two_pass_writer.run(
                topic=topic,
                angle=angle,
                niche_id=None,
                pool=pool,
                task_id=str(task_id) if task_id else None,
                writer_prompt_override=writer_prompt_override,
                context_bundle=context_bundle,
                # Pre-collected external research corpus — threaded so the
                # niche writer grounds + cites against the same SOURCES the
                # QA critic grades against. Without this the niche path
                # drafted research-blind and was rejected for "ignoring the
                # SOURCES corpus" (2026-06-09 disconnect fix).
                research_context=research_context,
                # DI seam (glad-labs-stack#330) — threaded so the atom
                # reads from the injected SiteConfig instead of
                # importing the legacy module-level singleton.
                site_config=site_config,
                # Phase 1 lab harness — when set, the writer atom's
                # _revise_node calls resolve_local_model(writer_model_override=...)
                # to honour the variant's model choice. None for the
                # production path (writer falls back to site_config
                # resolution as before).
                writer_model_override=variant_model_override,
            )

        draft = result.get("draft") or ""
        # The atom's writer helpers all call ollama_chat_text with this
        # model. Falls back to the scheduler label (same resolver) if
        # the atom doesn't surface model_used.
        model_used = result.get("model_used") or scheduler_model_label
        metrics: dict[str, Any] = {
            "writer_atom": "atoms.two_pass_writer",
            "snippets_used_count": len(result.get("snippets_used") or []),
            "models_used_by_phase": {"generate_content": model_used},
            "model_selection_log": {
                "generate_content": {
                    "preferred": model_used,
                    "actual": model_used,
                    "source": "atoms.two_pass_writer",
                },
            },
        }
        # Two-pass extras (LangGraph state machine output).
        for k in ("external_lookups", "revision_loops", "loop_capped"):
            if k in result:
                metrics[k] = result[k]
        # Phase 0 lab observability (2026-05-28) — propagate the prompt
        # resolution provenance the writer captured into metrics so the
        # caller (this stage's execute()) can forward them into the
        # StageResult.metrics dict that capability_outcomes reads.
        if result.get("prompt_template_key") is not None:
            metrics["prompt_template_key"] = result.get("prompt_template_key")
        if result.get("prompt_template_version") is not None:
            metrics["prompt_template_version"] = result.get(
                "prompt_template_version"
            )
        # Phase 1 lab harness — when a variant was assigned, stamp its
        # ids on the metrics dict so capability_outcomes.record_run
        # picks them up + writes them to the variant_id column on the
        # row. The model_selection_log gains an attribution entry so
        # the operator can trace "which experiment caused this model
        # choice?" by reading the metrics JSON.
        if variant is not None:
            metrics["variant_id"] = variant.variant_id
            metrics["variant_label"] = variant.variant_label
            metrics["experiment_id"] = variant.experiment_id
            metrics["experiment_key"] = variant.experiment_key
            metrics["model_selection_log"]["generate_content"]["variant"] = (
                f"{variant.experiment_key}/{variant.variant_label}"
            )
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


def _min_draft_chars(site_config: Any = None) -> int:
    """Minimum acceptable draft length before writer output is a failure.

    DB-configurable via ``writer_min_draft_chars``. The 200-char floor means
    even a single-sentence "post" from a degraded reasoning model is caught —
    a real canonical_blog article is never that short. Returns the floor when
    no site_config is available (tests / bootstrap). poindexter#691.
    """
    if site_config is None:
        return 200
    try:
        return int(site_config.get_int("writer_min_draft_chars", 200))
    except Exception:  # noqa: BLE001 — defensive against test stubs
        return 200


async def _fail_empty_draft(
    database_service: Any,
    *,
    task_id: str,
    error_message: str,
    model: str | None,
    kind: str,
    content_len: int,
    platform: Any = None,
) -> None:
    """Load-bearing terminal failure for an empty/too-short writer draft.

    Mirrors ``qa.aggregate``'s reject persistence: write the terminal status +
    ``error_message`` FIRST (so it sticks even though the graph_def node wrapper
    keeps running downstream nodes after the raise), then emit a finding +
    audit event so the writer-empty cause surfaces on the Findings dashboard /
    Discord instead of a misleading ``reviewer_count:0`` QA reject. The status
    write is the load-bearing one; the telemetry writes are best-effort.
    poindexter#691.
    """
    # 1. Terminal status — the load-bearing write.
    try:
        await database_service.update_task(task_id, {
            "status": "failed",
            "error_message": error_message,
            "model_used": model,
        })
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[generate_content] terminal failed-status write failed for %s: %s",
            task_id[:8], exc,
        )

    # 2. Finding → Findings dashboard + Discord (severity warn per the
    #    findings dispatcher policy). Late import so tests can patch it.
    try:
        from utils.findings import emit_finding
        emit_finding(
            source="modules.content.stages.generate_content",
            kind="writer_empty_draft",
            title=f"Writer produced {kind} draft — task failed",
            body=error_message,
            severity="warn",
            dedup_key=f"writer_empty_draft:{task_id}",
            extra={
                "task_id": task_id,
                "model": model,
                "kind": kind,
                "content_len": content_len,
            },
        )
    except Exception:  # noqa: BLE001 — finding emission must never block the fail path
        pass

    # 3. Audit event through the capability handle (Seam 1 Wave 3c, #667).
    try:
        if platform is not None:
            platform.audit.write_bg(
                "writer_empty_draft",
                source="generate_content",
                details={"kind": kind, "content_len": content_len, "model": model},
                task_id=task_id,
                severity="warning",
            )
    except Exception:  # noqa: BLE001
        pass


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
