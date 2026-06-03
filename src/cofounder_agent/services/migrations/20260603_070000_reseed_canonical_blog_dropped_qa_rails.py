"""Re-seed canonical_blog graph_def with four more dropped QA rails (version 6)
+ seed the two missing qa_gates rows (advisory-first).

WHY (Glad-Labs/poindexter#658/#659/#660/#661): the #355 atom-cutover replaced
the monolithic ``cross_model_qa`` stage — whose ``MultiModelQA.review()`` ran a
suite of QA checks inline — with the ``qa.*`` atom chain, but only ported a
subset. Four more checks went cold on the live ``canonical_blog`` graph_def path:

1. **topic_delivery** (#658) — bait-and-switch veto (title promises something
   the body never delivers). Ran UNCONDITIONALLY in review() with no qa_gates
   row. Cold on the live path.
2. **citation_verifier** (#659) — default-on dead-link / min-citation gate
   (``provider='http_head'``, the ``qa_citation_*`` settings family). Distinct
   from the already-reconciled ``url_verifier``. Cold on the live path.
3. **internal_consistency** (#660, qa_gates name ``consistency``) — cross-section
   self-contradiction check. The ``consistency`` gate row is still
   ``enabled=true`` but nothing on the live path runs the check.
4. **web_factcheck** (#661) — DuckDuckGo product/spec verification (training-
   cutoff override) AND the ``known_wrong_fact`` web-rescue. The rescue regression
   is the worst: ``qa.programmatic`` emits a non-advisory ``known_wrong_fact``
   veto that HARD-REJECTS legit post-cutoff content with no web second opinion
   (the exact false-positive the rescue was built for).

This migration:

1. Re-seeds ``pipeline_templates.graph_def`` (canonical_blog) from the updated
   authoritative spec, which now wires four new rails between ``qa.vision`` and
   ``qa.aggregate``: ``qa.topic_delivery → qa.citations → qa.consistency →
   qa.web_factcheck``. ``qa.web_factcheck`` is LAST so ``qa.aggregate`` can apply
   the ``known_wrong_fact`` rescue (suppressing a stale-regex known_wrong_fact
   veto when the web confirmed the claim). Version bumps 5 → 6.

2. Seeds the TWO missing qa_gates rows — ``topic_delivery`` and
   ``citation_verifier`` — so the rails are DB-configurable per
   ``feedback_db_first_config``. Both are seeded **advisory-first**
   (``required_to_pass=false``): they SCORE + feed the weighted average on every
   pass but do NOT yet veto. An operator graduates either to a hard veto (the
   legacy binary topic-delivery veto, the legacy dead-link veto) by flipping
   ``required_to_pass=true`` — no code deploy (poindexter#454). ``consistency``
   and ``web_factcheck`` already have advisory rows in the baseline; left as-is.

NO behaviour regression of the #661 rescue: the rescue PREVENTS the existing
wrong hard-reject (a correctness fix), it never adds a new veto. All four new
rails are advisory, so this PR can ship without changing any approve/reject
outcome except removing the known_wrong_fact false-positive.

Imports only ``canonical_blog_spec`` (pure data — no LangGraph) + stdlib so it
stays light for the migrations-smoke env.
"""
from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)


_TOPIC_DELIVERY_META = json.dumps(
    {
        "description": (
            "Bait-and-switch topic-delivery gate — does the body deliver what "
            "the title/topic promised? Restored from the #355-dropped "
            "review() step 2b (Glad-Labs/poindexter#658). Advisory-first: "
            "scores but does not veto until graduated to required_to_pass=true."
        ),
        "legacy_setting_key": None,
        "seeded_by_migration": "20260603_070000",
    }
)

_CITATION_VERIFIER_META = json.dumps(
    {
        "description": (
            "Dead-link / minimum-citation gate — HTTP-HEAD-checks every external "
            "URL and scores the dead-link ratio (qa_citation_* settings). "
            "Restored from the #355-dropped review() step 1b "
            "(Glad-Labs/poindexter#659). Distinct from url_verifier. "
            "Advisory-first: scores but does not veto until graduated."
        ),
        "legacy_setting_key": "qa_citation_verify_enabled",
        "seeded_by_migration": "20260603_070000",
    }
)


async def up(pool) -> None:
    payload = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        # 1. Re-seed the active graph_def from the authoritative spec.
        await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $1::jsonb, version = 6, updated_at = NOW()
             WHERE slug = 'canonical_blog'
            """,
            payload,
        )
        # 2a. topic_delivery — NEW gate row (review() ran it unconditionally,
        #     so it never had one). Advisory-first (required_to_pass=false).
        await conn.execute(
            """
            INSERT INTO qa_gates
                (name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, metadata)
            VALUES ('topic_delivery', 'qa', 410, 'topic_delivery',
                    false, true, $1::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            _TOPIC_DELIVERY_META,
        )
        # 2b. citation_verifier — NEW gate row. Advisory-first.
        await conn.execute(
            """
            INSERT INTO qa_gates
                (name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, metadata)
            VALUES ('citation_verifier', 'qa', 150, 'citation_verifier',
                    false, true, $1::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            _CITATION_VERIFIER_META,
        )
    logger.info(
        "reseed_canonical_blog_dropped_qa_rails: applied (graph_def v6; "
        "+qa.topic_delivery/qa.citations/qa.consistency/qa.web_factcheck; "
        "seeded topic_delivery + citation_verifier gate rows advisory-first)"
    )


async def down(pool) -> None:
    # Forward-only for the graph_def reseed. Remove the two NEW gate rows on
    # rollback so the schema returns to its prior shape (consistency /
    # web_factcheck rows are baseline-owned and left untouched).
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM qa_gates WHERE name IN ('topic_delivery', 'citation_verifier')"
        )
    logger.info(
        "reseed_canonical_blog_dropped_qa_rails: down — removed topic_delivery "
        "+ citation_verifier gate rows (graph_def reseed is forward-only)"
    )
