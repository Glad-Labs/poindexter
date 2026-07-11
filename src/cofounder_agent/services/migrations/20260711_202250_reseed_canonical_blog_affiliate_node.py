"""Migration 20260711_202250: reseed canonical_blog graph_def (affiliate node).

ISSUE: affiliate-link injection rebuild (design + plan under docs/superpowers/).

Background — the canonical_blog graph_def gained an ``inject_affiliate_links``
node (``content.inject_affiliate_links``) between ``llm_reconcile_citations`` and
``quality_evaluation`` so curated affiliate links are injected before the QA
rails. The stored ``pipeline_templates.graph_def`` row must be reseeded to the
new spec or the atom-contract freshness gate (``assert_graph_def_current``) would
reject it at load. This writes the RAW spec; the boot self-heal
(``ensure_active_graph_defs_stamped``) re-stamps the contract fingerprints on the
next boot — the same reseed shape as 20260623_035500 (media/podcast).

Idempotent: fresh installs already seed the current spec via the baseline, so
the UPDATE simply rewrites an identical (unstamped) graph_def; prod picks up the
new node.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Rewrite pipeline_templates.graph_def for canonical_blog to the current spec."""
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, updated_at = now() "
            "WHERE slug = 'canonical_blog'",
            raw,
        )
    logger.info("reseed_canonical_blog_affiliate_node up: %s", tag)


async def down(pool) -> None:
    """No-op: the boot self-heal owns stamping; a forward reseed is not reverted."""
    logger.info("reseed_canonical_blog_affiliate_node down: no-op")
