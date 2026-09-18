"""Migration 20260918_142710_reseed_canonical_blog_for_the_qaself_claim_experiment_claim_contract: re-stamp
canonical_blog after qa.self_claim's contract changed.

``qa.self_claim`` gained layer 8 (conducted-experiment claims,
poindexter#1050/#1052) and with it an optional ``research_context`` input. That
changes the atom's CONTRACT fingerprint — ``85f1098390fe`` → ``23881e300444``
— while leaving the node list untouched at 49.

Without this migration the deploy halts the pipeline. The stored graph_def
carries the old per-node ``_contract_fp``, ``assert_graph_def_current`` (via
``TemplateRunner.run``) raises ``GraphContractError`` on the mismatch, and
EVERY run fails. The boot self-heal does not rescue it:
``ensure_active_graph_defs_stamped`` deliberately leaves any row that carries
a fingerprint alone, precisely so real contract drift is still caught.

So this writes the RAW spec with no fingerprints, which makes the row fully
unstamped — the one state the self-heal does act on. It re-stamps from the
live registry on the same boot, and the drift gate passes with the new
contract. Same mechanism every graph_def reseed migration uses, and the reason
they all write the raw spec rather than calling ``stamp_graph_def`` (which
would need the atom registry, unavailable in the migrations-smoke env).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Re-seed canonical_blog's graph_def so boot re-stamps contract fps."""
    from poindexter.services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 15, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            raw,
        )
    logger.info("reseed_for_self_claim_experiment_contract up: graph_def=%s", tag)


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Reverting would restore a fingerprint that no longer matches the atom, i.e.
    re-create the halt this migration exists to avoid.
    """
    logger.info("reseed_for_self_claim_experiment_contract down: no-op (one-way reseed)")
