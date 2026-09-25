"""Reseed ``media_pipeline`` v7: the ``render_thumbnail`` node (custom YouTube thumbnails).

v7 inserts ``render_thumbnail`` (``media.render_thumbnail``) between
``render_short_video`` and ``media_qa``, and ``media.persist`` declares two new
optional inputs (``long_thumbnail_path``, ``long_thumbnail_meta``) to make the
thumbnail durable. That is a topology change AND a contract change, so the
stored v6 row would fail the load-time drift gate
(``pipeline_architect.assert_graph_def_current``) without this reseed — the
2026-09-22 incident, where a contract change shipped without one and every
Stage-2 render failed at load.

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
        7,
        "poindexter.services.media_pipeline_spec",
        "MEDIA_PIPELINE_GRAPH_DEF",
        "92299616ae00",
    ),
)


async def up(pool) -> None:
    """Re-seed ``media_pipeline`` with the thumbnail node."""
    written = await apply_reseeds(pool, _RESEEDS, log_prefix="reseed_media_pipeline_v7_thumbnail")
    logger.info("reseed_media_pipeline_v7_thumbnail up: %d row(s) written", written)


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would re-open the load-time drift failure; the node and the
    persist contract live in code, not in this row.
    """
    logger.info("reseed_media_pipeline_v7_thumbnail down: no-op (one-way reseed)")
