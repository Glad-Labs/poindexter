"""Migration 20260623_035500: reseed media_pipeline + podcast_pipeline graph_defs.

PR #1876 ("QA podcast audio + re-encode TTS to one clean stream") changed the
``qa.audio`` atom contract (``AtomMeta`` fingerprint d24ed9f4d409 -> 5e1038ae4850)
but shipped no graph_def reseed. Both the ``media_pipeline`` and
``podcast_pipeline`` graph_defs carry a ``qa_audio`` node, so their stored
per-node ``_contract_fp`` stamps went stale. The load-time drift gate
(``services.pipeline_architect.assert_graph_def_current``) then rejected both on
every dispatch -- halting the **entire Stage-2 video lane**:
``dispatch_media_pipeline`` failed for every eligible post and
``media_reconciliation`` reported posts stuck "missing video".

This is the convergence step. It rewrites both active rows with the **raw**
(unstamped) spec -- the exact reseed shape the boot-time self-heal
(``ensure_active_graph_defs_stamped``, which runs after migrations on every
boot) expects: it baseline-stamps fully-unstamped active rows against the
*current* atom registry, re-recording ``qa.audio`` as 5e1038ae4850 and
unblocking dispatch. Already-stamped rows are left alone by the self-heal, so
genuine future drift is still caught by the gate.

  * Fresh installs -- the Phase F baseline already seeds these graph_defs (with
    the pre-#1876 stamp); this UPDATE rewrites them raw and the same-boot
    self-heal re-stamps to current. Converges correctly.
  * Existing installs (prod) -- clears the stale d24ed9f4d409 stamp so the
    self-heal records the current contract. The real fix.

Imports the specs (pure data -- typing-only, no LangGraph) so the
migrations-smoke CI step applies it without a full app boot, mirroring the
canonical_blog reseed pattern documented in
``pipeline_architect.ensure_active_graph_defs_stamped``.
"""
from __future__ import annotations

import json
import logging

from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
from services.podcast_pipeline_spec import PODCAST_PIPELINE_GRAPH_DEF

logger = logging.getLogger(__name__)

# (slug, raw spec) pairs to rewrite. Raw = no ``_contract_fp`` on any node, so
# the boot self-heal re-stamps both against the current atom registry.
_RESEED: tuple[tuple[str, dict], ...] = (
    ("media_pipeline", MEDIA_PIPELINE_GRAPH_DEF),
    ("podcast_pipeline", PODCAST_PIPELINE_GRAPH_DEF),
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for slug, spec in _RESEED:
            result = await conn.execute(
                "UPDATE pipeline_templates "
                "SET graph_def = $1::jsonb, updated_at = now() "
                "WHERE slug = $2 AND active = true",
                json.dumps(spec),
                slug,
            )
            logger.info(
                "reseed_media_podcast_graph_defs up: rewrote %s graph_def raw "
                "(boot self-heal re-stamps to current contracts) -- %s",
                slug,
                result,
            )


async def down(pool) -> None:
    # No-op: the raw reseed is re-stamped by the boot self-heal on the next
    # boot; the prior stamp was stale (the very drift this migration fixes), so
    # there is nothing worth restoring. Same one-way posture as
    # 20260622_200222_drop_pipeline_tasks_category.
    logger.info(
        "reseed_media_podcast_graph_defs down: no-op (boot self-heal owns stamping)"
    )
