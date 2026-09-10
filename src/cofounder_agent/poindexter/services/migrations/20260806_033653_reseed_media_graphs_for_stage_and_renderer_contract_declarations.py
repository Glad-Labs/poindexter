"""Migration 20260806_033653: reseed the three media-referencing graph_defs
after the poindexter#983 contract declarations.

ISSUE: Glad-Labs/poindexter#983

Virtual stage atoms advertised ``requires: ()`` / ``produces: ()`` — the
architect composed blind (it could not see ``stage.generate_media_scripts``
is the script writer, and reachability validation green-lit render nodes
with no script source; two live podcast plans miswired this way). The fix
surfaces stage-declared ``atom_requires``/``atom_produces`` onto the
virtual atoms and declares the real inputs on ``podcast.render``
(``podcast_script``) and ``media.render_narration``
(``short_summary_script``).

Those declarations change each atom's ``contract_fingerprint()``, so every
active graph_def referencing them carries stale ``_contract_fp`` stamps and
would trip the load-time drift gate (``assert_graph_def_current``) on the
next run — the #1876 failure mode. Affected active rows: ``canonical_blog``
(stage nodes), ``podcast_pipeline`` (podcast.render), ``media_pipeline``
(media.render_narration).

Writes each RAW in-tree spec (un-stamped — keeps this importable in the
migrations-smoke env, which has no atom registry), then calls the boot
self-heal (``ensure_active_graph_defs_stamped``) in full-dependency envs,
mirroring ``20260801_054247_reseed_media_pipeline_v3``. ImportError alone
defers stamping to the worker's boot self-heal; any other failure fails
loud.

The experimental ``plan_*`` podcast templates cached by the chat-plan
shakedown are deliberately NOT reseeded — they are miswired throwaways; if
one is ever run it should fail the drift gate with the reseed guidance.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_RESEEDS = (
    # (slug, new_version, spec module, spec attr)
    ("canonical_blog", 9,
     "services.canonical_blog_spec", "CANONICAL_BLOG_GRAPH_DEF"),
    ("podcast_pipeline", 2,
     "services.podcast_pipeline_spec", "PODCAST_PIPELINE_GRAPH_DEF"),
    ("media_pipeline", 4,
     "services.media_pipeline_spec", "MEDIA_PIPELINE_GRAPH_DEF"),
)


async def up(pool) -> None:
    """Re-seed the media-referencing graph_defs so their stamps match the
    contract-declared atoms."""
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
                "reseed_media_graphs_for_contract_declarations up: %s v%d %s",
                slug, version, tag,
            )

    try:
        from services.pipeline_architect import (
            ensure_active_graph_defs_stamped,
        )
    except ImportError as exc:
        logger.info(
            "reseed_media_graphs_for_contract_declarations: registry env "
            "unavailable, stamps deferred to boot self-heal (%s)", exc,
        )
        return
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info(
        "reseed_media_graphs_for_contract_declarations: restamped %d row(s)",
        stamped,
    )
