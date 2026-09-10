"""Migration 20260809_180123: reseed dev_diary's graph_def onto shared canonical atoms.

Background: dev_diary's stored graph_def (v2, 4 nodes) had drifted away from the
canonical atoms in two ways that cost real output quality.

1. **No SEO node.** The 2026-06-02 "empty SERP snippet" fix added
   ``generate_seo_metadata`` to the legacy ``TEMPLATES`` *factory*, but by then
   the factory no longer ran — ``pipeline_use_graph_def=true`` plus an active
   dev_diary graph_def row wins in ``TemplateRunner.run``. The stored spec never
   got the node, so dev_diary posts kept publishing with no ``<meta
   description>``: 5/25 missing in May, 13/25 June, 15/25 July, **5/5 August**,
   versus 0/40 for canonical_blog.
2. **Coarse ``stage.finalize_task``** instead of canonical's
   ``compile_meta → persist_task → record_pipeline_version`` chain, so
   improvements to those atoms were invisible to the dev blog.

v3 (``services/dev_diary_spec.py``) makes every node except the writer
(``atoms.narrate_bundle``) the same atom canonical_blog runs. Semantics are
preserved, not changed: ``content.persist_task`` still lands
``status='awaiting_approval'``, and ``content.evaluate_auto_publish`` is
included because ``stage.finalize_task`` ran the auto-publish gate inline —
dropping it would have silently revoked dev_diary's configured opt-in.

Seeds the spec UNSTAMPED; ``ensure_active_graph_defs_stamped`` stamps
``_contract_fp`` per node on the next boot, the same path the other graph_defs
use. Reversible: ``down()`` restores the v2 spec verbatim.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The exact v2 spec this migration replaces, for down(). Inlined rather than
# imported: the module that used to build it (the dev_diary TEMPLATES factory)
# is deleted in the same change, and a rollback must not depend on code that
# only exists on one side of it.
_V2_SPEC: dict[str, Any] = {
    "name": "dev_diary",
    "description": (
        "dev_diary pipeline (atom-composed): verify_task -> narrate_bundle "
        "(single-call narrative writer) -> source_featured_image -> "
        "finalize_task. Mirrors the retired legacy factory; no QA/SEO rails "
        "by design."
    ),
    "entry": "verify_task",
    "nodes": [
        {"id": "verify_task", "atom": "stage.verify_task"},
        {"id": "narrate_bundle", "atom": "atoms.narrate_bundle"},
        {"id": "source_featured_image", "atom": "stage.source_featured_image"},
        {"id": "finalize_task", "atom": "stage.finalize_task"},
    ],
    "edges": [
        {"from": "verify_task", "to": "narrate_bundle"},
        {"from": "narrate_bundle", "to": "source_featured_image"},
        {"from": "source_featured_image", "to": "finalize_task"},
        {"from": "finalize_task", "to": "END"},
    ],
}


async def _reseed(pool, spec: dict[str, Any], version: int) -> None:
    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = $2, updated_at = now() "
            "WHERE slug = 'dev_diary' AND active = true",
            json.dumps(spec), version,
        )
    logger.info(
        "reseed_dev_diary_graph_def: dev_diary -> v%d (%s)", version, tag,
    )


async def up(pool) -> None:
    """Reseed dev_diary's active graph_def to the v3 shared-atom spec.

    Idempotent by construction: the UPDATE writes the same spec whenever it
    re-runs, and it no-ops when there is no active dev_diary row (a fresh DB
    that has not seeded pipeline_templates yet).
    """
    from services.dev_diary_spec import DEV_DIARY_GRAPH_DEF

    await _reseed(pool, DEV_DIARY_GRAPH_DEF, 3)


async def down(pool) -> None:
    """Restore the v2 4-node spec verbatim.

    Rolling back re-introduces the missing-meta-description gap described in
    the module docstring — it is a faithful revert, not a safe resting state.
    """
    await _reseed(pool, _V2_SPEC, 2)
