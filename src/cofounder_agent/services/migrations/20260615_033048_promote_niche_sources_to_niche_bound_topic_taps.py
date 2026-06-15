"""Migration 20260615_033048_promote_niche_sources_to_topic_taps: niche-bound topic taps

ISSUE: Topic sourcing taps b1 (docs/superpowers/specs/2026-06-14-topic-sourcing-taps-design.md)

Two moves, lossless:
  1. delete the pre-existing GLOBAL builtin_topic_source rows (niche_id IS NULL,
     target_table='content_tasks') — they store nothing today, so nothing is lost.
  2. promote each niche_sources row -> one niche-bound external_taps tap
     (target_table='topic_pool', niche_id set, weight_pct + derived web_search
     categories in config).

_BANK_KEYS is a one-time snapshot of services.topic_sources._filters.CATEGORY_SEARCHES
keys at migration-authoring time — inlined to keep this migration light-import
(migrations-smoke) and self-contained. web_search's runtime resolution is the
live source of truth; this only seeds a starting config.categories for the
niches the bank covers (others fall through to web_search's tag-derived path).

Light imports only: json, logging, re stdlib.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Snapshot of CATEGORY_SEARCHES.keys() at authoring time (2026-06-14).
_BANK_KEYS = frozenset(
    {"technology", "startup", "security", "engineering", "insights",
     "business", "hardware", "gaming"}
)


def _derive_categories(source_name: str, slug: str, tags) -> list[str]:
    """web_search only: bank categories whose key matches a slug/tag token.

    Generic (works for any operator's niches): tokenize slug + tags on
    -/_/whitespace, intersect with the bank keys. Empty -> web_search falls
    through to its tag-derived path (§2b).
    """
    if source_name != "web_search":
        return []
    tokens: set[str] = set(re.split(r"[-_\s]+", (slug or "").lower()))
    for tag in tags or []:
        tokens |= set(re.split(r"[-_\s]+", str(tag).lower()))
    return sorted(tokens & _BANK_KEYS)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps "
            "WHERE handler_name = 'builtin_topic_source' AND niche_id IS NULL"
        )
        rows = await conn.fetch(
            """
            SELECT ns.niche_id, ns.source_name, ns.enabled, ns.weight_pct,
                   n.slug, n.name, n.target_audience_tags,
                   n.discovery_cadence_minute_floor AS floor
              FROM niche_sources ns
              JOIN niches n ON n.id = ns.niche_id
            """
        )
        promoted = 0
        for r in rows:
            cfg: dict = {"weight_pct": r["weight_pct"]}
            cats = _derive_categories(r["source_name"], r["slug"], r["target_audience_tags"])
            if cats:
                cfg["categories"] = cats
            await conn.execute(
                """
                INSERT INTO external_taps
                    (name, handler_name, tap_type, target_table, niche_id,
                     schedule, config, enabled, metadata)
                VALUES ($1, 'builtin_topic_source', $2, 'topic_pool', $3,
                        $4, $5::jsonb, $6, $7::jsonb)
                """,
                f"{r['slug']}_{r['source_name']}",
                r["source_name"],
                r["niche_id"],
                f"every {r['floor']} minutes",
                json.dumps(cfg),
                r["enabled"],
                json.dumps({"description": f"{r['source_name']} topics for the {r['name']} niche"}),
            )
            promoted += 1
    logger.info("Migration promote_niche_sources_to_topic_taps: promoted %d tap(s)", promoted)


async def down(pool) -> None:
    """Remove the niche-bound topic taps (leaves niche_sources intact)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps "
            "WHERE handler_name = 'builtin_topic_source' AND niche_id IS NOT NULL"
        )
    logger.info("Migration promote_niche_sources_to_topic_taps down: reverted")
