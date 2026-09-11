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
    from poindexter.services.title_generation import (
        DEFAULT_TITLE_EXCERPT_CHARS,
        build_title_grounding_digest,
    )
    from poindexter.services.title_generation import (
        check_title_originality as _check_title_originality,
    )
    from poindexter.services.title_generation import (
        choose_canonical_title as _choose_canonical_title,
    )
    from poindexter.services.title_generation import (
        generate_canonical_title as _generate_canonical_title,
    )
    from poindexter.services.title_generation import (
        originality_rank as _originality_rank,
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
    from poindexter.services.title_avoidance import build_avoidance_block_for_pool

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

    # Originality: the web AND our own corpus (stack#3213). ``pool``
    # enables the internal check; ``exclude_task_id`` stops a re-run matching
    # the title it wrote last time.
    originality = await _check_title_originality(
        title, site_config=site_config,  # type: ignore[arg-type]
        pool=pool, exclude_task_id=str(task_id) if task_id else None,
    )
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
            originality_v2 = await _check_title_originality(
                title_v2, site_config=site_config,  # type: ignore[arg-type]
                pool=pool, exclude_task_id=str(task_id) if task_id else None,
            )
            if _originality_rank(originality_v2) < _originality_rank(originality):
                logger.info(
                    "[TITLE] Regenerated title is more original "
                    "(external %.0f%%→%.0f%%, internal %.0f%%→%.0f%%): %s",
                    originality["max_similarity"] * 100,
                    originality_v2["max_similarity"] * 100,
                    originality.get("internal_similarity", 0.0) * 100,
                    originality_v2.get("internal_similarity", 0.0) * 100,
                    title_v2,
                )
                title = title_v2
                originality = originality_v2
            else:
                logger.info("[TITLE] Keeping original title — regeneration wasn't more unique")

    # Searchability gate (2026-09-07). A title with no noun anyone types has
    # no query to match: the August 2026 cohort ("The Gap Nobody Names",
    # "The Stuck Task") drew 3.4 impressions per post in three weeks and zero
    # clicks. Deterministic check (services.title_searchability); on failure,
    # ONE corrective regeneration that names the article's concrete terms,
    # then ship whichever candidate is searchable — and always leave a
    # finding when the shipped title still is not, because a silent miss is
    # how this cohort happened.
    title, originality = await _apply_searchability_gate(
        title=title,
        originality=originality,
        topic=topic,
        primary_keyword=primary_keyword,
        tags=state.get("tags") or [],
        content_text=content_text,
        content_digest=content_digest,
        avoidance_block=avoidance_block,
        site_config=site_config,
        pool=pool,
        task_id=task_id,
    )

    # A duplicate that SURVIVED regeneration ships anyway (a near-duplicate
    # title beats no post), but it must not ship silently — this is the signal
    # that the threshold or the avoidance prompt needs attention.
    if originality.get("internal_duplicate"):
        from utils.findings import emit_finding

        matches = originality.get("internal_matches") or []
        emit_finding(
            source="content.generate_title",
            kind="title_internal_duplicate",
            title=f"Title near-duplicates an existing post: {title!r}",
            body=(
                f"{title!r} scored {originality.get('internal_similarity', 0.0):.0%} "
                f"against our own corpus (threshold "
                f"{originality.get('internal_threshold', '?')}), and regeneration "
                f"did not clear it. Closest existing title: "
                f"{matches[0]!r}. The post still ships — review whether the "
                f"title should be edited before publish."
            ),
            severity="info",
            dedup_key=f"title_internal_duplicate:{task_id}",
            extra={
                "task_id": str(task_id) if task_id else None,
                "title": title,
                "internal_similarity": originality.get("internal_similarity"),
                "matches": matches[:3],
            },
        )

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


def _heading_terms(content_text: str, limit: int = 6) -> list[str]:
    """Proper nouns / digit tokens from the article's own headings — the
    concrete things the regeneration directive may name. Never invents."""
    from poindexter.services.title_searchability import find_searchable_entities

    terms: list[str] = []
    for line in (content_text or "").splitlines():
        if not line.startswith("#"):
            continue
        for ent in find_searchable_entities(line.lstrip("# ").strip()).entities:
            if ent not in terms:
                terms.append(ent)
        if len(terms) >= limit:
            break
    return terms[:limit]


async def _apply_searchability_gate(
    *,
    title: str,
    originality: dict[str, Any],
    topic: str,
    primary_keyword: str,
    tags: list[str],
    content_text: str,
    content_digest: str,
    avoidance_block: str,
    site_config: Any,
    pool: Any,
    task_id: Any,
) -> tuple[str, dict[str, Any]]:
    """Enforce "the title names something searchable"; see module notes."""
    from poindexter.services.title_generation import (
        check_title_originality as _check_title_originality,
    )
    from poindexter.services.title_generation import (
        generate_canonical_title as _generate_canonical_title,
    )
    from poindexter.services.title_searchability import (
        has_searchable_entity,
        render_entity_directive,
    )
    from utils.findings import emit_finding

    enabled = True
    mode = "regenerate"
    if site_config is not None:
        try:
            enabled = site_config.get_bool("title_searchable_entity_enabled", True)
            mode = (site_config.get("title_searchable_entity_mode", "regenerate") or "regenerate").strip().lower()
        except Exception:  # noqa: BLE001 — stubbed site_config
            enabled, mode = True, "regenerate"
    if not enabled:
        return title, originality

    report = has_searchable_entity(
        title, primary_keyword=primary_keyword, tags=tags, topic=topic,
    )
    if report.ok:
        return title, originality

    logger.warning(
        "[TITLE] No searchable entity in %r (keyword terms: %s)",
        title, ", ".join(report.keyword_terms[:6]) or "-",
    )
    regen_title: str | None = None
    regen_report = None
    if mode == "regenerate":
        directive = render_entity_directive(
            primary_keyword=primary_keyword,
            tags=tags,
            topic=topic,
            rejected_title=title,
            heading_terms=_heading_terms(content_text),
        )
        block = f"{avoidance_block}\n\n{directive}" if avoidance_block else directive
        regen_title = await _generate_canonical_title(
            topic, primary_keyword, content_digest,
            avoidance_block=block,
            site_config=site_config,
            pool=pool,
        )
        if regen_title:
            regen_report = has_searchable_entity(
                regen_title, primary_keyword=primary_keyword, tags=tags, topic=topic,
            )
            if regen_report.ok:
                regen_originality = await _check_title_originality(
                    regen_title, site_config=site_config,
                    pool=pool, exclude_task_id=str(task_id) if task_id else None,
                )
                logger.info(
                    "[TITLE] Regenerated for searchability: %r -> %r (entities: %s)",
                    title, regen_title, ", ".join(regen_report.entities),
                )
                return regen_title, regen_originality

    # Still unsearchable (advisory mode, regen failed, or regen also blank).
    emit_finding(
        source="content.generate_title",
        kind="title_no_searchable_entity",
        title=f"Title names nothing searchable: {title!r}",
        body=(
            f"{title!r} contains no digit token, proper noun, or article keyword "
            f"(keyword terms: {', '.join(report.keyword_terms[:8]) or 'none'}). "
            + (
                f"Regeneration produced {regen_title!r}, also unsearchable. "
                if regen_title else
                ("Regeneration returned nothing. " if mode == "regenerate" else "Advisory mode — not regenerated. ")
            )
            + "The post ships with this title; a reader has no query that reaches it."
        ),
        severity="info",
        dedup_key=f"title_no_searchable_entity:{task_id}",
        extra={
            "task_id": str(task_id) if task_id else None,
            "title": title,
            "regenerated_title": regen_title,
            "mode": mode,
            "keyword_terms": list(report.keyword_terms[:8]),
        },
    )
    return title, originality


__all__ = ["ATOM_META", "run"]
