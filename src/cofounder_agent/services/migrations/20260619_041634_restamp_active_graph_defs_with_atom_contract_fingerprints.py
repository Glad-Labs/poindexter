"""Migration: re-stamp active graph_defs with atom contract fingerprints.

ISSUE: Glad-Labs/poindexter#755

Establishes the drift-gate baseline for graph_defs already in production. The
load-time gate (TemplateRunner.run → assert_graph_def_current) refuses to run a
graph_def whose per-node ``_contract_fp`` no longer matches the registry — but
rows seeded before #755 carry no stamps. This migration recomputes each active
row's fingerprints from the live atom registry and writes the stamped graph_def
back in place, so the gate has a baseline to compare against instead of
halting every pipeline on first deploy.

Import-guarded: the atom registry walks ``modules.content.atoms.*`` which pulls
runtime deps not present in the migrations-smoke environment. If discovery is
unavailable we skip (smoke uses a throwaway DB with nothing to protect); on the
real worker boot the registry is available and the rows get stamped. Stamps are
additive node keys, so re-running is harmless and down() is a no-op.
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
            "restamp_active_graph_defs: atom registry unavailable, skipping (%s)",
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
                    "restamp_active_graph_defs: slug=%s could not be stamped (%s)",
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
        "Migration restamp_active_graph_defs up: stamped %d active graph_def(s)",
        stamped_count,
    )


async def down(pool) -> None:
    """No-op: stamps are additive node keys; there is nothing to reverse."""
    logger.info(
        "Migration restamp_active_graph_defs down: no-op (stamps are additive)"
    )
