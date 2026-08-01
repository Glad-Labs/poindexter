"""Migration 20260801_035528_reseed_media_pipeline_graph_def_for_media_qa_narration_reference:
re-stamp media_pipeline after the media.qa contract change (silent-tail fix).

ISSUE: Glad-Labs/poindexter#961

The silent-tail fix extends the ``media.qa`` atom's contract: Check A now
compares the rendered video against the lane's ACTUAL narration audio, so
``AtomMeta.inputs`` gained the two optional ``long_narration_audio_path`` /
``short_narration_audio_path`` FieldSpecs and the atom's
``contract_fingerprint()`` changed. ``media_pipeline`` is the only active
graph_def referencing a ``media.qa`` node; its stored stamp would go stale and
the load-time drift gate (``assert_graph_def_current``) would halt the whole
Stage-2 media lane on the next boot — the #1876 failure mode the
``test_graph_def_contract_freshness`` CI gate exists to catch. version 1 → 2.

Graph behavior is otherwise unchanged (the new inputs are optional and were
already declared PipelineState channels; the atom simply declares it reads
them now).

Writes the RAW spec (no per-node ``_contract_fp``), then calls the boot
self-heal (``ensure_active_graph_defs_stamped``, poindexter#755) directly so a
full-dependency env re-stamps at migration time — mirroring
``20260726_032205_reseed_seo_refresh_graph_def_with_gate_graduation.py``. The
import is guarded NARROWLY: only ImportError means "dependency-light env"
(migrations-smoke has no LangGraph) and defers stamping to the worker's boot
self-heal; any other failure in a full env must fail the migration loudly.
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
            "version = 2, updated_at = now() "
            "WHERE slug = 'media_pipeline' AND active = true",
            json.dumps(MEDIA_PIPELINE_GRAPH_DEF),
        )
    logger.info(
        "reseed_media_pipeline_graph_def_for_media_qa_narration_reference up: "
        "media_pipeline=%s", tag,
    )

    # Re-stamp contract fingerprints NOW when the env allows it. Import-guarded
    # NARROWLY: only ImportError means "dependency-light env" (migrations-smoke
    # has no LangGraph) and defers to the boot self-heal; any other failure in
    # a full env is a broken deploy and must fail the migration loudly.
    try:
        from services.pipeline_architect import (
            ensure_active_graph_defs_stamped,
        )
    except ImportError as exc:
        logger.info(
            "reseed_media_pipeline_graph_def_for_media_qa_narration_reference: "
            "registry env unavailable, stamps deferred to boot self-heal (%s)",
            exc,
        )
        return
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info(
        "reseed_media_pipeline_graph_def_for_media_qa_narration_reference: "
        "re-stamped %d active graph_def row(s)", stamped,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would restore a stale media.qa contract stamp and halt the
    media lane at load; the reseeded graph is structurally identical, so
    there is nothing unsafe to roll back.
    """
    logger.info(
        "reseed_media_pipeline_graph_def_for_media_qa_narration_reference "
        "down: no-op (one-way reseed)"
    )
