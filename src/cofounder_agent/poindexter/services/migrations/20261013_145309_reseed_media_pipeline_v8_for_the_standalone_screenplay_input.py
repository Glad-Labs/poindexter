"""Reseed ``media_pipeline`` v8: ``media.persist`` reads an optional ``screenplay``.

Creative studio phase 6 (poindexter#1140). ``media.persist`` declares a new
optional input, ``screenplay``. A run that carries one and has no post is a
standalone video, so the asset is flagged for its own review and titled from
the screenplay. The graph's nodes and edges are unchanged, but the atom's
contract fingerprint moves, so the stored v7 row would fail the load-time
drift gate (``pipeline_architect.assert_graph_def_current``) without this
reseed.

Declares the graph signature the row is brought to, per the
``20260922_021635`` convention (``test_graph_def_reseed_gate``); the
signature was printed with ``REGEN_GRAPH_DEF_FP=1 pytest
…::test__print_graph_signatures -s``.
"""

from __future__ import annotations

import logging

from poindexter.services.graph_def_reseed import apply_reseeds

logger = logging.getLogger(__name__)

# (slug, new_version, spec module, spec attr, graph signature the reseed brings the row to)
_RESEEDS = (
    (
        "media_pipeline",
        8,
        "poindexter.services.media_pipeline_spec",
        "MEDIA_PIPELINE_GRAPH_DEF",
        "15ab4bcc14e0",
    ),
)


async def up(pool) -> None:
    """Re-seed ``media_pipeline`` with the new ``media.persist`` contract."""
    written = await apply_reseeds(pool, _RESEEDS, log_prefix="reseed_media_pipeline_v8_screenplay")
    logger.info("reseed_media_pipeline_v8_screenplay up: %d row(s) written", written)


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would re-open the load-time drift failure; the contract lives in
    code, not in this row.
    """
    logger.info("reseed_media_pipeline_v8_screenplay down: no-op (one-way reseed)")
