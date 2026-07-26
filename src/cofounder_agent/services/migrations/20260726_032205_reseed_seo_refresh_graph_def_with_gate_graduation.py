"""Migration 20260726_032205_reseed_seo_refresh_graph_def_with_gate_graduation:
wire the Lock-2 graduation setting into the live seo_refresh graph.

ISSUE: `seo.refresh.auto_publish_after_clean_runs` (seeded '5' since #763) had
NO reader — the "sign-off first, autonomy earned" graduation the SEO Harvest
design promised was never wired, so `seo_refresh_gate` paused for operator
sign-off forever. The atom side now implements graduation
(`modules/content/atoms/approval_gate.py`: trailing streak of clean human
approvals at the gate >= the setting → `auto_approved` history row +
pass-through), opted in per node via a `graduation_setting` config key.

Two reseeds, one cause:

1. **seo_refresh** — carries the new `refresh_gate` config key
   (`graduation_setting: 'seo.refresh.auto_publish_after_clean_runs'`) to
   prod's stored row: the baseline runs once, so existing installs never
   re-read the spec. version 1 → 2.
2. **canonical_blog** — its `draft_gate`/`preview_gate` nodes reference the
   same `atoms.approval_gate`, whose contract fingerprint changed with the
   graduation capability (version 2.0.0 → 2.1.0, new optional
   `graduation_setting` input). A stamped row holding the OLD fingerprint
   would trip the load-time drift gate (`assert_graph_def_current`) on the
   next boot and halt the lane — the #1876 failure mode the
   `test_graph_def_contract_freshness` CI gate exists to catch. version 7 → 8.
   (canonical_blog's gates set no `graduation_setting`, so their behavior is
   unchanged.)

Both writes are the RAW spec (no per-node `_contract_fp`), mirroring
`20260724_161837_title_coherence_rail_and_graph_reseed.py` — then the
migration finishes by calling the boot self-heal
(`ensure_active_graph_defs_stamped`, poindexter#755) directly, so in any
full-dependency env (worker startup, integration_db CI) every unstamped
active row is re-stamped from the live registry at migration time. That
last step matters here specifically: seo_refresh was the LAST active row
still carrying baseline stamps, and un-stamping it would leave the
runtime-parity gate (`test_seeded_graph_defs_current`) with nothing to
gate. The import is guarded: the dependency-light migrations-smoke env has
no LangGraph/atom registry, so there the rows stay raw and the worker's
boot self-heal stamps them on the next boot exactly as before.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Re-seed seo_refresh (graduation config) + canonical_blog (fresh stamps)."""
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF
    from services.seo_refresh_spec import SEO_REFRESH_GRAPH_DEF

    async with pool.acquire() as conn:
        seo_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 2, updated_at = now() "
            "WHERE slug = 'seo_refresh' AND active = true",
            json.dumps(SEO_REFRESH_GRAPH_DEF),
        )
        blog_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 8, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            json.dumps(CANONICAL_BLOG_GRAPH_DEF),
        )
    logger.info(
        "reseed_seo_refresh_graph_def_with_gate_graduation up: "
        "seo_refresh=%s canonical_blog=%s",
        seo_tag, blog_tag,
    )

    # Re-stamp contract fingerprints NOW when the env allows it — seo_refresh
    # was the last stamped active row, and leaving the whole set raw would
    # blind the runtime-parity gate until the next worker boot. Import-guarded
    # NARROWLY: only ImportError means "dependency-light env" (migrations-smoke
    # has no LangGraph) and defers to the boot self-heal; any other failure in
    # a full env is a broken deploy and must fail the migration loudly.
    try:
        from services.pipeline_architect import (
            ensure_active_graph_defs_stamped,
        )
    except ImportError as exc:
        logger.info(
            "reseed_seo_refresh_graph_def_with_gate_graduation: registry env "
            "unavailable, stamps deferred to boot self-heal (%s)", exc,
        )
        return
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info(
        "reseed_seo_refresh_graph_def_with_gate_graduation: re-stamped %d "
        "active graph_def row(s)", stamped,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would only re-orphan the graduation setting and restore stale
    approval_gate contract stamps; a gate whose graduation threshold is unmet
    (or set to 0) behaves exactly as the pre-reseed gate did, so there is
    nothing unsafe to roll back.
    """
    logger.info(
        "reseed_seo_refresh_graph_def_with_gate_graduation down: no-op "
        "(one-way reseed)"
    )
