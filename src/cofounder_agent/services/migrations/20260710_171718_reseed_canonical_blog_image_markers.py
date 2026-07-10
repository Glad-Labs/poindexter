"""Migration 20260710_171718: reseed canonical_blog graph_def for the plan_image_markers contract bump.

PR #2250 (writer-nominated, content-grounded image direction) changed the
``content.plan_image_markers`` atom's contract — it now produces
``featured_image_subject`` (the writer's ``[HERO-IMAGE:]`` subject, consumed by
``source_featured_image`` to ground the featured image). That bumped the atom's
contract fingerprint (``5f20eda72266`` → ``1bad0eccc418``). The stored
``canonical_blog`` graph_def in ``pipeline_templates`` still carried the old
stamp, so ``pipeline_architect.assert_graph_def_current`` halted every
``canonical_blog`` run at load time with ``GraphContractError`` (the #1876-class
stale-stamp outage). PR #2250 regenerated the CI fingerprint snapshot
(``graph_def_contract_fingerprints.json``) — which silenced the PR-time drift
gate — but missed the prod re-seed. This migration is that convergence step.

Reseed the ``canonical_blog`` graph_def raw (unstamped); the boot-time self-heal
(``pipeline_architect.ensure_active_graph_defs_stamped``, which only stamps
UNSTAMPED rows) re-stamps it against the current atom registry on the same boot.
Mirrors the opening_originality (20260707_120000) and citation_grounding
(20260708_034620) reseeds: the raw UPDATE + same-boot re-stamp converges fresh
(baseline seeds the old-stamp graph_def → this UPDATE → re-stamp) and prod
(existing graph_def → this UPDATE → re-stamp) alike. Migrations run before the
worker claims tasks, so the re-stamp lands before the node first executes.

Imports the spec (pure data — typing-only, no LangGraph) so migrations-smoke
applies it without a full app boot, mirroring the canonical_blog reseed pattern.
"""
from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pipeline_templates "
            "SET graph_def = $1::jsonb, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            json.dumps(CANONICAL_BLOG_GRAPH_DEF),
        )
        logger.info(
            "reseed_canonical_blog_image_markers up: canonical_blog reseed=%s "
            "(boot self-heal re-stamps the graph_def to current atom contracts)",
            result,
        )


async def down(pool) -> None:
    # One-way: the graph_def reseed is re-stamped by the boot self-heal, and there
    # is no prior stamp to restore. Same posture as the opening_originality /
    # citation_grounding reseeds — the stamp is a derived artifact, not state.
    logger.info(
        "reseed_canonical_blog_image_markers down: no-op (boot self-heal owns stamping)"
    )
