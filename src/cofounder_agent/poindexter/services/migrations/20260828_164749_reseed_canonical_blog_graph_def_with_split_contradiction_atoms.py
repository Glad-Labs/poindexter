"""Reseed canonical_blog: writer_self_review stage -> two contradiction atoms.

`writer_self_review` ran as ONE graph node (`stage.writer_self_review`) that
made TWO LLM calls — detect contradictions, then revise for them. As of
2026-08-28 those calls can use different models
(`writer_self_review_review_model` for detect, `writer_self_review_model` for
revise), because they are measurably better served by different ones:

    gemma-4-31B-it-qat    detected 1/4, revised correctly when it did
    glm-4.7-5090 (think)  detected 4/4, every revision REJECTED as too long
                          (2.40x / 4.77x / 5.36x / 7.39x)

A single node fanning out to two models hides half of what runs from the graph,
which is exactly what the atom contract forbids ("the graph must be the whole
truth of what runs"). So the stage is replaced by two atoms —
`content.detect_contradictions` -> `content.revise_contradictions` — passing
the detector output through the new `contradiction_review` state channel. Same
shape as the atom-cutover that deleted the `cross_model_qa` stage in favour of
`qa.*` atoms delegating to the `multi_model_qa` library; here both atoms
delegate to `services/self_review.py`.

Behaviour is unchanged until an operator sets
`writer_self_review_review_model` — it is seeded empty, which means "use the
reviser for both", exactly as before.

canonical_blog version 10 -> 11. The stored row must be rewritten because the
baseline runs once, so an existing install would otherwise keep dispatching a
`stage.writer_self_review` node whose class no longer exists.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Rewrite the stored canonical_blog graph_def with the split atoms."""
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 11, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            json.dumps(CANONICAL_BLOG_GRAPH_DEF),
        )
    logger.info("reseed_canonical_blog_split_contradiction_atoms up: %s", tag)

    # Re-stamp contract fingerprints now when the env allows it, so the
    # runtime-parity gate has something to check before the next boot.
    # Import-guarded NARROWLY: only ImportError means "dependency-light env"
    # (migrations-smoke has no LangGraph) and defers to the boot self-heal;
    # any other failure in a full env is a broken deploy and must fail loudly.
    try:
        from services.pipeline_architect import ensure_active_graph_defs_stamped
    except ImportError as exc:
        logger.info(
            "reseed_canonical_blog_split_contradiction_atoms: registry env "
            "unavailable, stamps deferred to boot self-heal (%s)", exc,
        )
        return
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info(
        "reseed_canonical_blog_split_contradiction_atoms: re-stamped %d "
        "active graph_def row(s)", stamped,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    Rolling back would restore a node pointing at `stage.writer_self_review`,
    whose class is deleted in this same change, so the reverted graph would
    fail to build. Reverting the code is the way back, not reverting this row.
    """
    logger.info(
        "reseed_canonical_blog_split_contradiction_atoms down: no-op "
        "(one-way reseed)"
    )
