"""Migration 20260922_015301: reseed ``media_pipeline`` after the four render
atoms declared the optional ``niche_slug`` input (glad-labs-stack#3928).

``media.render_narration``, ``media.render_long_video``,
``media.render_short_video`` and ``media.qa`` now declare ``niche_slug`` as an
optional ``FieldSpec`` input so the per-niche media policy (house style,
subject policy) reaches Stage 2 through the graph instead of being invisible
to it. Declaring an input changes each atom's ``contract_fingerprint()``, so
the active ``media_pipeline`` row (v4, stamped 2026-08-06) carries four stale
``_contract_fp`` stamps and the load-time drift gate
(``pipeline_architect.assert_graph_def_current``) refuses to run it::

    GraphContractError: FIX: stored graph_def is out of date with the atom registry:
      - node 'render_narration': atom 'media.render_narration' contract drifted ...
      - node 'render_long_video': ...
      - node 'render_short_video': ...
      - node 'media_qa': ...

Observed on prod 2026-09-22 01:4xZ, on the first media dispatch after the
#3928 deploy: every Stage-2 video render fails at load until this lands. It is
the #1876 failure mode again — the PR regenerated the CI fingerprint snapshot
(which made the freshness gate green) but shipped no reseed, and nothing
required one. The follow-up hardens that gate; this migration is the prod fix.

The boot self-heal does NOT cover this. ``ensure_active_graph_defs_stamped``
deliberately leaves any row carrying a fingerprint alone (so the gate can still
catch real drift) and only baseline-stamps fully-unstamped rows — and the prod
``media_pipeline`` row is stamped. Hence an explicit reseed.

Writes the RAW in-tree spec (un-stamped — keeps this importable in the
migrations-smoke env, which has no atom registry), then calls the self-heal in
full-dependency envs to restamp it, mirroring
``20260919_001943_reseed_podcast_pipeline_for_the_podcastpersist_audio_qa_carry_forward_contract``.
ImportError alone defers stamping to the worker's boot self-heal (the row is
un-stamped by then, which is the case the self-heal DOES handle); any other
failure fails loud.

The node list itself is unchanged — only four atoms' declared inputs grew —
so this is a stamp refresh, not a topology change. The experimental
``plan_*`` chat-plan templates that also reference these atoms are
deliberately NOT reseeded (miswired throwaways, per 20260806_033653); if one
is ever run it should fail the drift gate with the reseed guidance.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# (slug, new_version, spec module, spec attr) — v4 → v5.
_RESEEDS = (
    ("media_pipeline", 5, "poindexter.services.media_pipeline_spec", "MEDIA_PIPELINE_GRAPH_DEF"),
)


async def up(pool) -> None:
    """Re-seed ``media_pipeline`` so its stamps match the niche_slug-declaring
    render atoms."""
    import importlib

    async with pool.acquire() as conn:
        for slug, version, module_name, attr in _RESEEDS:
            spec = getattr(importlib.import_module(module_name), attr)
            tag = await conn.execute(
                "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
                "version = $2, updated_at = now() "
                "WHERE slug = $3 AND active = true",
                json.dumps(spec),
                version,
                slug,
            )
            logger.info(
                "reseed_media_pipeline_v5_niche_slug up: %s v%d %s",
                slug,
                version,
                tag,
            )

    try:
        from poindexter.services.pipeline_architect import (
            ensure_active_graph_defs_stamped,
        )
    except ImportError as exc:
        logger.info(
            "reseed_media_pipeline_v5_niche_slug: registry env unavailable, "
            "stamps deferred to boot self-heal (%s)",
            exc,
        )
        return
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info(
        "reseed_media_pipeline_v5_niche_slug: restamped %d row(s)",
        stamped,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting the stamps would only re-open the load-time failure; the atom
    contracts that moved live in code, not in this row.
    """
    logger.info("reseed_media_pipeline_v5_niche_slug down: no-op (one-way reseed)")
