"""Handler: ``tap.builtin_topic_source`` (b1 rewrite).

Dispatches the single ``topic_source`` plugin named in ``row.tap_type``
with full niche context, dedups, and INSERTs the survivors into the tap's
``target_table`` (``topic_pool``). This is the per-source loop body lifted
from ``TopicBatchService._discover_external`` — b2 deletes that method, so
keeping the logic identical makes the deletion a move, not a rewrite.

The pre-b1 version delegated to ``topic_sources.runner.run_all`` and threw
the topics away (returned only a count). That hollow path is gone.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.config import PluginConfig
from plugins.registry import get_topic_sources
from services.integrations.registry import register_handler
from services.niche_service import NicheService
from services.topic_dedup_semantic import get_deduplicator
from services.topic_pool import insert_pooled_topics
from services.topic_sanity import evaluate_topic_sanity, resolve_min_alpha_words
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


@register_handler("tap", "builtin_topic_source")
async def builtin_topic_source(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Run one niche-bound topic source and store its candidates in the pool."""
    if pool is None:
        raise RuntimeError("tap.builtin_topic_source: pool unavailable")

    niche_id = row.get("niche_id")
    if not niche_id:
        raise ValueError(
            "tap.builtin_topic_source: topic taps require a niche_id "
            "(this tap row has none). feedback_no_silent_defaults."
        )

    source_name = row.get("tap_type")
    if not source_name:
        raise ValueError(
            "tap.builtin_topic_source: row.tap_type must name a registered "
            "topic_source plugin (e.g. 'hackernews', 'web_search')"
        )

    niche = await NicheService(pool).get_by_id(niche_id)
    if niche is None:
        raise ValueError(f"tap.builtin_topic_source: unknown niche_id {niche_id}")

    # Resolve the single source. internal_rag isn't an entry-point plugin —
    # branch to its service class (same as _discover_internal does).
    if source_name == "internal_rag":
        from services.internal_rag_source import InternalRagSource

        source: Any = InternalRagSource(pool, site_config=site_config)
    else:
        registry = {
            getattr(p, "name", type(p).__name__): p for p in get_topic_sources()
        }
        source = registry.get(source_name)
        if source is None:
            raise ValueError(
                f"tap.builtin_topic_source: source {source_name!r} is not a "
                "registered topic_source plugin — check install or rename"
            )

    # Build extract_config exactly as _discover_external does: per-install
    # plugin config, then the tap row's own config (e.g. seeded categories),
    # then the niche context the source needs to scope its output.
    plugin_cfg = await PluginConfig.load(pool, "topic_source", source_name)
    extract_config: dict[str, Any] = dict(plugin_cfg.config)
    extract_config.update(dict(row.get("config") or {}))
    extract_config.update(
        {
            "_site_config": site_config,
            "niche_slug": niche.slug,
            "niche_id": str(niche.id),
            "niche_name": niche.name,
            "target_audience_tags": list(niche.target_audience_tags),
        }
    )

    topics = await source.extract(pool, extract_config)

    # Fuzzy/semantic dedup (honours topic_dedup_engine). DiscoveredTopic
    # already exposes .title + .is_duplicate, so the deduper marks in place.
    # niche_slug scopes the content engine's recent-coverage pass to this
    # tap's niche (a dev_diary post must not block a glad-labs candidate).
    if topics:
        deduper = get_deduplicator(
            pool, site_config=site_config, niche_slug=niche.slug,
        )
        try:
            await deduper.mark_duplicates(topics)
        except Exception:
            logger.warning(
                "tap.builtin_topic_source: dedup pass failed — proceeding "
                "with un-deduped candidates",
                exc_info=True,
            )
    fresh = [t for t in (topics or []) if not getattr(t, "is_duplicate", False)]

    # Deterministic topic-sanity gate at the ingest seam, so contentless
    # titles never enter topic_pool at all. The sweep-intake / batch-handoff
    # gates (#2037) already stop this class from becoming pipeline_tasks
    # rows, but without an ingest gate the junk still accumulates as
    # 'pooled' rows that every sweep re-reads and re-filters. Same rules,
    # same operator knob (topic_sanity_min_alpha_words); one aggregated
    # topic_sanity_rejected finding per tap run keeps a junk-emitting
    # source visible on the Findings board (feedback_no_silent_defaults).
    min_words = resolve_min_alpha_words(site_config)
    sane: list[Any] = []
    dropped: list[tuple[str, str]] = []  # (reason, title)
    for t in fresh:
        verdict = evaluate_topic_sanity(
            getattr(t, "title", "") or "", min_alpha_words=min_words,
        )
        if verdict.ok:
            sane.append(t)
        else:
            dropped.append((verdict.reason or "", getattr(t, "title", "") or ""))
    if dropped:
        logger.warning(
            "[tap.builtin_topic_source] %s/%s: dropped %d contentless "
            "topic(s) at ingest: %s",
            niche.slug, source_name, len(dropped),
            "; ".join(f"[{r}] {t!r:.60}" for r, t in dropped),
        )
        emit_finding(
            source="tap_builtin_topic_source",
            kind="topic_sanity_rejected",
            title=(
                f"Dropped {len(dropped)} contentless topic(s) at tap ingest "
                f"({niche.slug}/{source_name})"
            ),
            body="\n".join(f"- {reason}: {title!r}" for reason, title in dropped),
            severity="warn",
            dedup_key=f"topic-sanity-ingest:{niche.slug}:{source_name}",
            extra={
                "stage": "tap_ingest",
                "niche_slug": niche.slug,
                "source": source_name,
                "dropped": [
                    {"reason": reason, "title": title[:200]}
                    for reason, title in dropped
                ],
            },
        )
    fresh = sane

    target_table = row.get("target_table") or "topic_pool"
    async with pool.acquire() as conn:
        inserted = await insert_pooled_topics(
            conn,
            niche_id=niche.id,
            source=source_name,
            topics=fresh,
            table=target_table,
        )

    logger.info(
        "[tap.builtin_topic_source] %s/%s: %d pooled (%d fetched, %d after "
        "dedup + sanity)",
        niche.slug, source_name, inserted, len(topics or []), len(fresh),
    )
    return {"records": inserted, "source": source_name}
