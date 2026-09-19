"""Migration 20260919_001943: reseed ``podcast_pipeline`` after ``podcast.persist``
declared the ``audio_qa_result`` input.

``podcast.persist`` now reads ``audio_qa_result`` off the state and stamps the
podcast lane onto ``media_assets.metadata->'audio_qa'``, giving the ``qa.audio``
atom its first consumer — until now it produced that channel and nothing read
it. Declaring the input changes the atom's ``contract_fingerprint()``, so the
active ``podcast_pipeline`` row carries a stale ``_contract_fp`` stamp and
would trip the load-time drift gate (``assert_graph_def_current``) on the next
run: the #1876 failure mode, which halted the whole Stage-2 video lane in prod.

The boot self-heal does NOT cover this. ``ensure_active_graph_defs_stamped``
deliberately leaves any row carrying a fingerprint alone (so the gate can still
catch real drift) and only baseline-stamps fully-unstamped rows — and the prod
``podcast_pipeline`` row is stamped. Hence an explicit reseed.

Writes the RAW in-tree spec (un-stamped — keeps this importable in the
migrations-smoke env, which has no atom registry), then calls the self-heal in
full-dependency envs to restamp it, mirroring
``20260806_033653_reseed_media_graphs_for_stage_and_renderer_contract_declarations``.
ImportError alone defers stamping to the worker's boot self-heal (the row is
un-stamped by then, which is the case the self-heal DOES handle); any other
failure fails loud.

The node list itself is unchanged — only the terminal atom's declared inputs
moved — so this is a stamp refresh, not a topology change.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# (slug, new_version, spec module, spec attr) — v2 → v3.
_RESEEDS = (
    ("podcast_pipeline", 3,
     "poindexter.services.podcast_pipeline_spec", "PODCAST_PIPELINE_GRAPH_DEF"),
)


async def up(pool) -> None:
    """Re-seed ``podcast_pipeline`` so its stamp matches ``podcast.persist``."""
    import importlib

    async with pool.acquire() as conn:
        for slug, version, module_name, attr in _RESEEDS:
            spec = getattr(importlib.import_module(module_name), attr)
            tag = await conn.execute(
                "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
                "version = $2, updated_at = now() "
                "WHERE slug = $3 AND active = true",
                json.dumps(spec), version, slug,
            )
            logger.info(
                "reseed_podcast_pipeline_audio_qa_carry_forward up: %s v%d %s",
                slug, version, tag,
            )

    try:
        from poindexter.services.pipeline_architect import (
            ensure_active_graph_defs_stamped,
        )
    except ImportError as exc:
        logger.info(
            "reseed_podcast_pipeline_audio_qa_carry_forward: registry env "
            "unavailable, stamps deferred to boot self-heal (%s)", exc,
        )
        return
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info(
        "reseed_podcast_pipeline_audio_qa_carry_forward: restamped %d row(s)",
        stamped,
    )
