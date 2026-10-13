"""Reseed ``media_pipeline`` v9: the ``package_short`` node.

``media.package_short`` runs between ``render_thumbnail`` and ``media_qa``. It
writes and checks the Short's own YouTube title and description lead, and
composes its vertical thumbnail from the long thumbnail's hook
(``services/short_package.py``, ``services/video_thumbnail.compose_short_thumbnail``).
``media.persist`` gains three optional inputs to store them with the Short.
The graph's nodes, edges and ``media.persist`` contract all change, so the
stored v8 row would fail the load-time drift gate
(``pipeline_architect.assert_graph_def_current``) without this reseed.

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
        9,
        "poindexter.services.media_pipeline_spec",
        "MEDIA_PIPELINE_GRAPH_DEF",
        "4745086441ca",
    ),
)


async def up(pool) -> None:
    """Re-seed ``media_pipeline`` with the ``package_short`` node."""
    written = await apply_reseeds(pool, _RESEEDS, log_prefix="reseed_media_pipeline_v9_package_short")
    logger.info("reseed_media_pipeline_v9_package_short up: %d row(s) written", written)


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would re-open the load-time drift failure; the contract lives in
    code, not in this row.
    """
    logger.info("reseed_media_pipeline_v9_package_short down: no-op (one-way reseed)")
