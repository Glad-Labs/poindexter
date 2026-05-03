"""Migration 0140: rewrite the dev_diary writer prompt to require Markdown links.

The original prompt told the writer to "cite specific PRs, commits,
decisions, and audit events by their ID (e.g., 'PR #156', 'commit
20b89a71', 'decision abc123')". The bare-ID format played badly with
``services/content_validator.py``'s ``unlinked_citation`` rule, which
flags "as noted in <Title>", "according to <Source>", "PR #156" style
references that don't point at a real URL. The validator's
"named-source-without-URL" promotion path (content_validator.py:1239)
then escalated each instance from warning to critical, vetoing the
whole post via ``programmatic_validator``.

Diagnosis from task 1738 (today's dev_diary):
``Multi-model QA rejected (score: 27, veto: programmatic_validator @ 27):
Unlinked citation -- possible hallucinated reference: 'As no...';
'... s Mor...'; 'The O...''``

Fix: require **Markdown link format** for every reference, and forbid
the "as noted in" / "according to" patterns the validator catches.
The PR + commit + decision IDs the writer has access to are all
URL-routable:

- PR #N → ``https://github.com/Glad-Labs/glad-labs-stack/pull/N``
- commit SHA → ``https://github.com/Glad-Labs/glad-labs-stack/commit/SHA``
- audit/decision IDs aren't URL-routable; the new prompt tells the
  writer to mention them inline as plain text without a citation
  preamble (e.g. "decision_log row a4f8c1" not "as logged in
  decision_log row a4f8c1").

Pairs with the validator-side fix in this PR that prevents
``unlinked_citation`` warnings from being promoted to critical (so
the writer has a soft-fail safety net even if it slips up).

Idempotent — re-runs simply re-set the writer_prompt_override to
the new canonical text. Operator overrides made in the table after
this migration runs WILL be overwritten on re-run; if you want to
preserve a manual edit, change the migration text first.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_NEW_PROMPT = """\
You are writing a daily dev diary post for Glad Labs — an AI-operated
content business. The voice is FIRST-PERSON ("we", "today", "this morning",
"just landed"). You are speaking as the Glad Labs team, not as a journalist.

Use the structured context bundle (merged PRs, notable commits, brain
decisions, resolved audit events, recent posts, cost summary) to
synthesise a short "what we shipped today" post.

Hard rules:

1. CITATIONS: when you reference a PR, commit, or external source, use
   FULL MARKDOWN LINKS. The pipeline's content validator rejects bare
   "as noted in...", "according to...", "[1]"-style markers because
   they're indistinguishable from hallucinated references.

   - PR #N         → [PR #N](https://github.com/Glad-Labs/glad-labs-stack/pull/N)
   - commit SHA    → [commit SHA[:7]](https://github.com/Glad-Labs/glad-labs-stack/commit/SHA)
   - decision/audit IDs are NOT URL-routable — mention them inline as
     plain text ("decision a4f8c1 covered the rollback") without a
     citation preamble like "as logged in...".

   FORBIDDEN phrasings (validator vetoes the post if these appear without
   an accompanying URL within ~100 chars):
   - "as noted in <Title>"
   - "according to <Source>"
   - "as documented in..."
   - "per the <X> guide"
   - bare "[1]" / "[2]" footnote markers

   When in doubt: don't cite. Describe what happened in your own voice,
   then drop a link if and only if you can produce a real URL.

2. SCOPE: only describe Glad Labs' own work. Do NOT claim external
   projects, libraries, or products as our own. If a PR uses a
   third-party tool, describe it as "we adopted X" or "we wired in Y",
   never "we built X".

3. FOOTER: end with this exact footer on its own line, with no
   surrounding commentary:

   _Auto-compiled by Poindexter from today's commits and system decisions._

4. SKIP-IF-EMPTY: if the context bundle has zero merged PRs, zero
   notable commits, AND zero brain decisions, do not invent activity.
   Return a one-sentence "quiet day" post and the footer.

Style: short paragraphs, plain language, no marketing voice. Treat the
reader as a fellow developer who wants to know what changed."""


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "niches"):
            logger.info(
                "Table 'niches' missing — skipping migration 0140"
            )
            return
        result = await conn.execute(
            """
            UPDATE niches
            SET writer_prompt_override = $1, updated_at = NOW()
            WHERE slug = 'dev_diary'
            """,
            _NEW_PROMPT,
        )
        logger.info(
            "Migration 0140: dev_diary writer prompt rewritten to require "
            "Markdown links + forbid validator-vetoed citation phrases (%s)",
            result,
        )


async def down(pool) -> None:
    # Leaving down as a no-op — the previous prompt is still in git
    # history (migration 0134); restoring would require copying that
    # text in here and is rarely worth the risk of mid-flight downgrade.
    del pool
    logger.info(
        "Migration 0140 down is a no-op — restore the prior prompt from "
        "migration 0134 manually if needed."
    )
