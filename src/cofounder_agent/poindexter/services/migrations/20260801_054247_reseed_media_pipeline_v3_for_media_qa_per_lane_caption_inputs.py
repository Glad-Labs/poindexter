"""Migration 20260801_054247_reseed_media_pipeline_v3_for_media_qa_per_lane_caption_inputs:
re-stamp media_pipeline after the media.qa caption-input contract change.

ISSUE: Glad-Labs/poindexter#966 (caption half — the false missing_captions)

media.qa's Check B read the pre-#689 shared ``caption_srt_path`` state key,
which went permanently empty when captions became per-lane — so it emitted a
false ``missing_captions`` finding on every render while the per-lane SRTs
were transcribed and burned fine. The fix reads
``long_caption_srt_path`` / ``short_caption_srt_path`` (legacy key as
fallback), and declaring those on ``AtomMeta.inputs`` changes the atom's
``contract_fingerprint()``. ``media_pipeline`` is the only active graph_def
referencing ``media.qa``; a stale stamp would trip the load-time drift gate
(``assert_graph_def_current``) and halt the Stage-2 media lane on the next
boot — the #1876 failure mode. version 2 -> 3.

Writes the RAW spec, then calls the boot self-heal
(``ensure_active_graph_defs_stamped``) in full-dependency envs — mirroring
``20260801_035528_reseed_media_pipeline_graph_def_for_media_qa_narration_reference.py``.
ImportError alone defers stamping to the worker's boot self-heal
(migrations-smoke has no LangGraph); any other failure fails loud.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Re-seed media_pipeline so its stamp matches the new media.qa contract."""
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF

    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 3, updated_at = now() "
            "WHERE slug = 'media_pipeline' AND active = true",
            json.dumps(MEDIA_PIPELINE_GRAPH_DEF),
        )
    logger.info(
        "reseed_media_pipeline_v3_for_media_qa_per_lane_caption_inputs up: "
        "media_pipeline=%s", tag,
    )

    try:
        from services.pipeline_architect import (
            ensure_active_graph_defs_stamped,
        )
    except ImportError as exc:
        logger.info(
            "reseed_media_pipeline_v3_for_media_qa_per_lane_caption_inputs: "
            "registry env unavailable, stamps deferred to boot self-heal (%s)",
            exc,
        )
        return
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info(
        "reseed_media_pipeline_v3_for_media_qa_per_lane_caption_inputs: "
        "re-stamped %d active graph_def row(s)", stamped,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would restore a stale media.qa contract stamp and halt the
    media lane at load; the reseeded graph is structurally identical.
    """
    logger.info(
        "reseed_media_pipeline_v3_for_media_qa_per_lane_caption_inputs "
        "down: no-op (one-way reseed)"
    )
