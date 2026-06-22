"""Migration: re-stamp active graph_defs after the preview_gate reseed.

The #755 contract gate (``TemplateRunner.run`` -> ``assert_graph_def_current``)
refuses to load a graph_def whose nodes carry no per-node ``_contract_fp``
stamp. The restamp baseline (``20260619_041634``) stamped every active row that
existed at that time — but graph_def *reseed* migrations that run AFTER it write
the raw spec via ``json.dumps(CANONICAL_BLOG_GRAPH_DEF)`` with no
``stamp_graph_def`` (they import only the pure-data dict to stay smoke-safe),
which un-stamps the row again. Two such reseeds shipped after the baseline: the
v6 director-review reseed (``20260619_182232``) and the preview_gate reseed
(``20260622_044939``). On a fresh worker boot — or any install replaying the
migration chain — the active ``canonical_blog`` graph_def therefore loads
UNSTAMPED and every pipeline run halts with
``GraphContractError: stored graph_def is out of date with the atom registry``.

This re-stamps every active ``pipeline_templates`` row from the live registry,
identical in effect to the ``20260619_041634`` baseline, re-run so the
post-reseed rows carry fingerprints again. Stamps are additive node keys;
re-running is harmless and ``down()`` is a no-op.

Import-guarded: the atom registry walks ``modules.content.atoms.*`` which pulls
runtime deps absent from the migrations-smoke environment. If discovery is
unavailable we skip (smoke uses a throwaway DB that never runs a pipeline); on a
real worker boot the registry is available and the rows get stamped.

NOTE: this restores correctness for the *current* reseeds. A future graph_def
reseed that writes the raw spec will un-stamp again — the durable fix is to
stamp at seed time (or stamp-if-unstamped at boot). Tracked separately.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Re-stamp every active ``pipeline_templates`` row from the live registry."""
    try:
        from services.atom_registry import discover
        from services.pipeline_architect import stamp_graph_def

        discover()
    except Exception as exc:  # noqa: BLE001 — smoke-safe skip
        logger.warning(
            "restamp_active_graph_defs_after_preview_gate: atom registry "
            "unavailable, skipping (%s)",
            exc,
        )
        return

    stamped_count = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug, graph_def FROM pipeline_templates WHERE active = true"
        )
        for row in rows:
            raw = row["graph_def"]
            spec = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(spec, dict) or not spec.get("nodes"):
                continue
            try:
                stamped = stamp_graph_def(spec)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "restamp_active_graph_defs_after_preview_gate: slug=%s could "
                    "not be stamped (%s)",
                    row["slug"],
                    exc,
                )
                continue
            await conn.execute(
                "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
                "updated_at = NOW() WHERE slug = $2 AND active = true",
                json.dumps(stamped),
                row["slug"],
            )
            stamped_count += 1
    logger.info(
        "restamp_active_graph_defs_after_preview_gate up: stamped %d active "
        "graph_def(s)",
        stamped_count,
    )


async def down(pool) -> None:
    """No-op: stamps are additive node keys; there is nothing to reverse."""
    logger.info(
        "restamp_active_graph_defs_after_preview_gate down: no-op "
        "(stamps are additive)"
    )
