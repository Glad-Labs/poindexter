"""content.generate_title — generate the canonical blog post title.

Extracted from GenerateContentStage. Uses _generate_canonical_title +
_choose_canonical_title + _check_title_originality (the full regeneration
loop), then persists title to content_tasks.

Produces: title, title_originality.

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.generate_title",
    type="atom",
    version="1.0.0",
    description=(
        "LLM-generated canonical title with recent-titles avoidance. Runs the "
        "title-originality web check and regenerates when a near-duplicate is "
        "found. Updates content_tasks.title in DB."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="tags", type="list", description="tags; tags[0] is primary keyword", required=False),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="database_service", type="object", description="DB service"),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
    ),
    outputs=(
        FieldSpec(name="title", type="str", description="canonical post title"),
        FieldSpec(name="title_originality", type="dict", description="originality report from web check"),
    ),
    requires=("content", "task_id"),
    produces=("title", "title_originality"),
    capability_tier="cheap_critic",
    cost_class="api",
    idempotent=False,
    side_effects=("llm_call", "db_write"),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException")),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Generate and persist the canonical title, including originality check."""
    from modules.content.atoms._seo_common import resolve_primary_keyword
    from services.title_generation import (
        DEFAULT_TITLE_EXCERPT_CHARS,
        build_title_grounding_digest,
    )
    from services.title_generation import (
        check_title_originality as _check_title_originality,
    )
    from services.title_generation import (
        choose_canonical_title as _choose_canonical_title,
    )
    from services.title_generation import (
        generate_canonical_title as _generate_canonical_title,
    )

    content_text = (state.get("content") or "").strip()
    if not content_text:
        return {}

    task_id = state.get("task_id")
    topic = state.get("topic", "")
    database_service = state.get("database_service")
    site_config = state.get("site_config")
    # Thread the DB pool so generate_canonical_title routes through the
    # dispatcher — a cloud pipeline_writer_model then reaches LiteLLM instead
    # of 404-ing against local Ollama (Sonnet-canary fix; glad-labs-stack#2194).
    pool = getattr(database_service, "pool", None)

    # tags[0] → content-derived keyword → topic. Never the raw topic when the
    # article itself can supply a keyword (2026-07-24 topic-echo fix).
    primary_keyword = resolve_primary_keyword(state)

    # Ground the title prompt in the ARTICLE, not the topic label: opening
    # excerpt + full-article section headings. The old 500-char slice let the
    # model title the topic's most likely reading instead of the actual draft
    # (task 1149dfc8: "The Console Transition" → gaming-hardware title over an
    # operator-console essay).
    excerpt_chars = DEFAULT_TITLE_EXCERPT_CHARS
    if site_config is not None:
        try:
            excerpt_chars = site_config.get_int(
                "title_content_excerpt_chars", DEFAULT_TITLE_EXCERPT_CHARS
            )
        except Exception:  # noqa: BLE001 — stubbed site_config
            excerpt_chars = DEFAULT_TITLE_EXCERPT_CHARS
    content_digest = build_title_grounding_digest(content_text, max_chars=excerpt_chars)

    # Variety guidance for the title prompt: the recent corpus's structural
    # and lexical HABITS, not a dump of its titles. See
    # services.title_avoidance for why the dump made repetition worse.
    from services.title_avoidance import build_avoidance_block_for_pool

    avoidance_block = await build_avoidance_block_for_pool(
        pool, site_config=site_config, source="content.generate_title",
    )

    llm_title = await _generate_canonical_title(
        topic, primary_keyword, content_digest,
        avoidance_block=avoidance_block,
        site_config=site_config,  # type: ignore[arg-type]
        pool=pool,
    )
    title = _choose_canonical_title(
        topic, content_text, llm_title, site_config=site_config,
    )
    logger.info("Title generated: %s", title)

    # Title originality check + optional regeneration.
    originality = await _check_title_originality(title, site_config=site_config)  # type: ignore[arg-type]
    if not originality["is_original"]:
        logger.warning("[TITLE] Title too similar to existing content — regenerating")
        # Confirmed collisions DO get named verbatim — those are specific
        # titles to dodge, not a corpus to imitate.
        retry_block = await build_avoidance_block_for_pool(
            pool,
            site_config=site_config,
            near_duplicates=originality["similar_titles"][:5],
            source="content.generate_title",
        )
        title_v2 = await _generate_canonical_title(
            topic, primary_keyword, content_digest,
            avoidance_block=retry_block,
            site_config=site_config,  # type: ignore[arg-type]
            pool=pool,
        )
        if title_v2:
            originality_v2 = await _check_title_originality(title_v2, site_config=site_config)  # type: ignore[arg-type]
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

    # Persist title to content_tasks.
    if task_id and database_service is not None:
        try:
            await database_service.update_task(
                task_id=task_id,
                updates={"title": title},
            )
        except Exception as e:
            logger.warning("[content.generate_title] DB update failed (non-critical): %s", e)

    return {
        "title": title,
        "title_originality": originality,
    }


__all__ = ["ATOM_META", "run"]
