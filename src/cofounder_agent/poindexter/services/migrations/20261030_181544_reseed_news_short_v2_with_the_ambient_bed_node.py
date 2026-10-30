"""Reseed ``news_short`` v2: the ``ambient_bed`` node.

``media.generate_ambient_bed`` runs between ``qa_audio`` and
``render_short_video``. It makes the instrumental bed a blog post's Short has
carried since #679 (``modules/content/ambient_bed``, the same code
``generate_media_scripts`` now calls), so news Shorts no longer go out
narration-only. Best-effort: no bed means the Short renders as before.

The graph's nodes and edges change, so the stored v1 row would fail the
load-time drift gate (``pipeline_architect.assert_graph_def_current``) without
this reseed. Declares the graph signature the row is brought to, per the
``20260922_021635`` convention (``test_graph_def_reseed_gate``); printed with
``REGEN_GRAPH_DEF_FP=1 pytest …::test__print_graph_signatures -s``.
"""

from __future__ import annotations

import logging

from poindexter.services.graph_def_reseed import apply_reseeds

logger = logging.getLogger(__name__)

# (slug, new_version, spec module, spec attr, graph signature the reseed brings the row to)
_RESEEDS = (
    (
        "news_short",
        2,
        "poindexter.services.news_short_spec",
        "NEWS_SHORT_GRAPH_DEF",
        "c8e880885e36",
    ),
)


async def up(pool) -> None:
    """Re-seed ``news_short`` with the ``ambient_bed`` node."""
    written = await apply_reseeds(pool, _RESEEDS, log_prefix="reseed_news_short_v2_ambient_bed")
    logger.info("reseed_news_short_v2_ambient_bed up: %d row(s) written", written)


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would re-open the load-time drift failure; the contract lives in
    code, not in this row.
    """
    logger.info("reseed_news_short_v2_ambient_bed down: no-op (one-way reseed)")
