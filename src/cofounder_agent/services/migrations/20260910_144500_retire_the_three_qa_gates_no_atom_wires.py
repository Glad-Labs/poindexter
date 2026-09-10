"""Migration 20260910_144500: retire the three qa_gates no atom wires.

ISSUE: wiring audit, 2026-09-10 — three enabled gates that never run.

`guardrails_brand`, `guardrails_competitor` and `url_verifier` are seeded
`enabled=true`, `guardrails_enabled` is `true`, and both CLAUDE.md and
docs/architecture/anti-hallucination.md document all three as live rails.

None of them has produced a review in 60 days, because **no atom wires them**.
The #355 atom cutover replaced `MultiModelQA.review()` with the `qa.*` atom
chain and ported `qa.critic`, `qa.programmatic` and the rest; these three legs
were left behind. `qa.programmatic`'s own docstring records the same thing
happening to `programmatic_validator` — that one was restored, these were not.
`services/guardrails_rails.py` still claims in its docstring to "run as the
`qa.guardrails` atom on the canonical_blog graph_def path"; #730 removed that
node, and `test_regen_services_doc` asserts it never comes back.

**Retiring the claim rather than restoring the rails**, because the protection
is not actually missing:

- `guardrails_brand` runs `content_validator._check_patterns` — the SAME sets
  `programmatic_validator` runs. Both it and `deepeval_brand_fabrication`
  scored 287 reviews each over the window. Its own docstring calls it "a
  parallel signal", i.e. a third correlation lens, not independent cover.
- `url_verifier` HTTP-checks external URLs; `citation_verifier` (262 reviews)
  is the live dead-link gate. Duplicate cover.
- `guardrails_competitor` IS unique — but measured across 203 published posts
  it has nothing to catch: one apparent hit ("Outranking") was the ordinary
  verb in "outranking the real judge-rail score", which the rail's
  case-insensitive word-boundary regex would have flagged wrongly. The
  competitor names that DO appear (Jasper, Copy.ai, AutoBlogging) show up in
  6 pooled TOPICS — all of them deliberate competitive-positioning subjects
  like "Defining the Real Competition" — and never leak into a post.

So the gates are disabled, not deleted: the rows and the code stay so the
capability can be rewired if comparison content ever makes it worth having.
What is removed is the false claim that they are protecting anything today.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RETIRED = ("guardrails_brand", "guardrails_competitor", "url_verifier")

_NOTE = (
    "Retired 2026-09-10 (wiring audit): no atom wires this rail since the #355 "
    "cutover, so it produced 0 reviews in 60 days while reading as enabled. "
    "Brand + URL cover is duplicated by programmatic_validator / "
    "deepeval_brand_fabrication / citation_verifier; competitor screening had "
    "nothing to catch across 203 posts. Row kept, not deleted, so the rail can "
    "be rewired if comparison content ever needs it."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        tag = await conn.execute(
            """
            UPDATE qa_gates
               SET enabled = false,
                   required_to_pass = false,
                   metadata = COALESCE(metadata, '{}'::jsonb)
                              || jsonb_build_object('retired_note', $2::text)
             WHERE name = ANY($1::text[])
            """,
            list(_RETIRED), _NOTE,
        )
        # The master switch reads `true` while nothing consumes it, which is
        # the same lie one level up.
        setting = await conn.execute(
            "UPDATE app_settings SET value = 'false', updated_at = NOW() "
            "WHERE key = 'guardrails_enabled'"
        )
    logger.info(
        "retire_unwired_qa_gates up: qa_gates=%s guardrails_enabled=%s", tag, setting,
    )


async def down(pool) -> None:
    """Re-enable the rows. Deliberately does NOT restore `required_to_pass`.

    All three were advisory before retirement, and turning a never-executing
    rail back on as a hard gate would be a worse state than the one this
    migration fixed.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE qa_gates SET enabled = true, "
            "metadata = COALESCE(metadata, '{}'::jsonb) - 'retired_note' "
            "WHERE name = ANY($1::text[])",
            list(_RETIRED),
        )
        await conn.execute(
            "UPDATE app_settings SET value = 'true', updated_at = NOW() "
            "WHERE key = 'guardrails_enabled'"
        )
    logger.info("retire_unwired_qa_gates: reverted")
