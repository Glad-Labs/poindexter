"""Migration 20260922_021635: reseed every active graph_def, each declaring the
graph signature the reseed brings the row to.

This is the bootstrap for the reseed gate (``services/graph_def_reseed.py``,
``tests/unit/services/test_graph_def_reseed_gate.py``). From here on, the
NEWEST ``_RESEEDS`` entry for each active in-tree spec must declare the
signature the live atom registry produces — so an atom-contract change cannot
merge without the migration that carries it to prod. Every earlier reseed
predates the convention (4-field entries or inline SQL, no signature), so the
gate needs one migration that anchors all six active graphs at once.

Why this is needed at all: the load-time drift gate
(``pipeline_architect.assert_graph_def_current``) refuses a stored row whose
per-node ``_contract_fp`` stamps no longer match the registry, and the boot
self-heal restamps only rows carrying NO fingerprint. A contract change
therefore needs an explicit reseed, and twice the PR that changed the
contract refreshed the CI fingerprint snapshot instead (poindexter#1876,
glad-labs-stack#3928 — the latter halted every Stage-2 video render on prod
2026-09-22 until ``20260922_015301`` reseeded ``media_pipeline``). The
snapshot was a stand-in for prod's row that could be made green without
touching prod; a migration cannot.

Topology unchanged for all six. Writes each RAW in-tree spec (un-stamped —
importable in the migrations-smoke env, which has no atom registry) and
restamps through ``ensure_active_graph_defs_stamped`` where the registry
imports, else defers to the worker's boot self-heal — the same mechanics as
``20260806_033653`` / ``20260919_001943`` / ``20260922_015301``, now shared in
``graph_def_reseed.apply_reseeds``. Restamping with identical fingerprints
leaves the graph signature unchanged, so in-flight LangGraph checkpoints stay
valid (``template_runner`` discards one only when the signature differs); the
``version`` bump is bookkeeping and not part of the signature.

Versions: canonical_blog 15→16, dev_diary 3→4, image_rebuild 1→2,
media_pipeline 5→6, podcast_pipeline 3→4, seo_refresh 2→3. Signatures were
computed against the registry at 2026-09-22 (``REGEN_GRAPH_DEF_FP=1 pytest
…::test__print_graph_signatures``). The ``plan_*`` chat-plan throwaways are
deliberately NOT reseeded (miswired, per ``20260806_033653``).
"""

from __future__ import annotations

import logging

from poindexter.services.graph_def_reseed import apply_reseeds

logger = logging.getLogger(__name__)

# (slug, new_version, spec module, spec attr, graph signature the reseed brings the row to)
_RESEEDS = (
    (
        "canonical_blog",
        16,
        "poindexter.services.canonical_blog_spec",
        "CANONICAL_BLOG_GRAPH_DEF",
        "0d249bd3f1eb",
    ),
    ("dev_diary", 4, "poindexter.services.dev_diary_spec", "DEV_DIARY_GRAPH_DEF", "d964f6fa99b5"),
    (
        "image_rebuild",
        2,
        "poindexter.services.image_rebuild_spec",
        "IMAGE_REBUILD_GRAPH_DEF",
        "a2a411521e37",
    ),
    (
        "media_pipeline",
        6,
        "poindexter.services.media_pipeline_spec",
        "MEDIA_PIPELINE_GRAPH_DEF",
        "0d1f3d11b475",
    ),
    (
        "podcast_pipeline",
        4,
        "poindexter.services.podcast_pipeline_spec",
        "PODCAST_PIPELINE_GRAPH_DEF",
        "33f6011f2a2f",
    ),
    (
        "seo_refresh",
        3,
        "poindexter.services.seo_refresh_spec",
        "SEO_REFRESH_GRAPH_DEF",
        "64fd97b217ab",
    ),
)


async def up(pool) -> None:
    """Re-seed all six active graph_defs under the signature-declaring convention."""
    written = await apply_reseeds(
        pool, _RESEEDS, log_prefix="reseed_all_active_graph_defs_with_signatures"
    )
    logger.info("reseed_all_active_graph_defs_with_signatures up: %d row(s) written", written)


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op (the rows are restamped from the
    live registry either way; nothing safe or useful to roll back to)."""
    logger.info("reseed_all_active_graph_defs_with_signatures down: no-op (one-way reseed)")
