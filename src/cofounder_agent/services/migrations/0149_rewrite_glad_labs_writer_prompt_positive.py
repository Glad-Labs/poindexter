"""Migration 0149: rewrite ``niches['glad-labs'].writer_prompt_override``
with positive directives instead of negation lists.

Per ``feedback_positive_directives``: prompts phrased as "Do X" are
more reliable than "Don't do Y" for two reasons:

1. LLMs follow positive instructions more reliably; negation requires
   the model to first understand the bad behavior, then suppress it,
   which adds error modes.
2. Negation surfaces the forbidden pattern as a suggestion. Listing
   FORBIDDEN phrasings ("as noted in", "according to", etc.) puts
   those tokens into the model's working context and meaningfully
   raises the probability of producing them.

This migration replaces the migration-0141 prompt with a positive-
directive equivalent that describes the target behavior literally
("Cite with full markdown links pointing to a real URL") rather than
listing the failure modes by name.

Idempotent — re-running re-applies the same prompt body. Operator
edits to the niches.writer_prompt_override column WILL be overwritten
on re-run; if you want sticky operator tweaks, edit them in this
migration text instead of the DB.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Memory: ``feedback_positive_directives``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_GLAD_LABS_WRITER_PROMPT = """\
You are writing a blog post for Glad Labs — an AI-operated content
business covering AI/ML, gaming, and PC hardware for indie developers
and tinkerers.

CITATIONS: when you reference a study, source, expert, library, or
external fact, use a FULL MARKDOWN LINK pointing at a real URL. The
content validator accepts a citation when the linked URL appears
within ~100 characters of the citation phrase. Two patterns work:

- Inline: "the team at [Anthropic](https://anthropic.com) published"
- Trailing: "research from MIT ([source](https://...))"

When you do not have a real URL for the source, describe the idea in
your own voice without naming a specific source ("There's a class of
attacks where..." instead of "[1] documents..."). Treat the URL as
the gating evidence — write the citation only when you can produce
the URL.

GROUNDING: every named expert, statistic, quote, study title, or
product version comes from a verifiable source you have a URL for.
Round numbers and named examples are either checkable or omitted —
write "MIT researchers reported..." only when the article includes
the link to that report.

TOPIC FIDELITY: the article delivers on the headline. When the topic
is "X", every section advances X. Tangents are paths back to X.

INTERNAL CONSISTENCY: every claim aligns across sections. When the
piece argues for approach A in section 1, sections 2-N either build
on A or explicitly explain a switch with the reasoning visible.

SCOPE: describe Glad Labs's own work in first person ("we", "our
system", "we adopted"). Cover external projects and tools in third
person ("Project X published", "the Y library does Z"). The reader
can tell at a glance which work is yours.

STYLE: short paragraphs, plain language, peer-to-peer register —
write for a fellow developer who knows the territory.

This is the OSS default prompt. Glad Labs Premium Prompts (Pro tier,
delivered via Lemon Squeezy) unlock a tuned version with brand voice,
structural scaffolding, and citation-density targets. See
https://gladlabs.io/pricing"""


async def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2)",
            table, column,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _column_exists(conn, "niches", "writer_prompt_override"):
            logger.info(
                "Migration 0149: niches.writer_prompt_override missing — skipping"
            )
            return
        result = await conn.execute(
            """
            UPDATE niches
               SET writer_prompt_override = $1, updated_at = NOW()
             WHERE slug = 'glad-labs'
            """,
            _GLAD_LABS_WRITER_PROMPT,
        )
        logger.info(
            "Migration 0149: rewrote glad-labs writer_prompt_override "
            "with positive directives (%s)",
            result,
        )


async def down(pool) -> None:
    # No automatic revert — re-run migration 0141 to restore the
    # negation-style prompt if needed.
    logger.info(
        "Migration 0149 down: no-op (re-run 0141 to restore old prompt)"
    )
