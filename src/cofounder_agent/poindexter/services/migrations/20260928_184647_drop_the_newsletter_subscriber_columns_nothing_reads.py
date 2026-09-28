"""Migration: drop the newsletter_subscribers columns nothing reads

ISSUE: Glad-Labs/poindexter#1109

``newsletter_subscribers`` carried five columns that code wrote and nothing
ever read:

- ``company``, ``interest_categories``, ``marketing_consent``. The public
  signup form collected them until 2026-09-28, and after that only direct
  callers of ``POST /api/newsletter/subscribe`` sent them. The send path
  (``newsletter_service._get_active_subscribers``) mails every verified,
  not-unsubscribed row and never looked at ``marketing_consent``. No view,
  dashboard, console panel, CLI command or MCP tool selects any of the three.
- ``ip_address`` and ``user_agent``: the address and user-agent of whatever
  called that route. That is a proxy hop or a server-side fetch, never the
  subscriber. The only populated row on the install this was checked against
  held a Docker bridge address and the user-agent ``node``. The pair could
  not serve as consent evidence. On a worker exposed directly it would be a
  visitor's personal data, kept forever with no reader.

Public signups arrive through ``SyncNewsletterAudienceJob``, which never
wrote any of the five, so for nearly every row they were empty already.
Personal data that nothing consumes runs against data minimisation, and a
column nothing reads misleads a reader into thinking something does.

The route stops writing all five in the same change. It still accepts the
three request fields, ignores them, and answers with a ``Deprecation``
header. ``SyncService.pull_newsletter_subscribers``, the only other writer,
was deleted too: it has had no caller since April 2026.

Also drops ``idx_newsletter_interests_gin``, the GIN index over
``interest_categories``. Postgres would drop it with the column anyway.
Naming it keeps the intent readable and ``down()`` symmetric.

No view, trigger, policy or publication depends on the table (checked
against ``pg_depend``, ``pg_trigger``, ``pg_policy`` and
``pg_publication_rel`` on 2026-09-28).

**The drop is one-way for the data.** ``up()`` logs how many filled values
it discards, so an install that did collect them can see that it did.
``down()`` restores the columns and the index, empty.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RETIRED_COLUMNS: tuple[str, ...] = (
    "company",
    "interest_categories",
    "marketing_consent",
    "ip_address",
    "user_agent",
)

# Static SQL over a fixed column list, run only when all five columns exist.
# The route stored an empty user-agent as '' rather than NULL, and
# marketing_consent defaults to false, so "filled" differs per column.
_COUNT_FILLED_SQL = """
SELECT count(*)                                  AS total_rows,
       count(NULLIF(company, ''))                AS company,
       count(interest_categories)                AS interest_categories,
       count(*) FILTER (WHERE marketing_consent) AS marketing_consent,
       count(ip_address)                         AS ip_address,
       count(NULLIF(user_agent, ''))             AS user_agent
  FROM newsletter_subscribers
"""

_DROP_COLUMNS_SQL = """
ALTER TABLE IF EXISTS newsletter_subscribers
    DROP COLUMN IF EXISTS company,
    DROP COLUMN IF EXISTS interest_categories,
    DROP COLUMN IF EXISTS marketing_consent,
    DROP COLUMN IF EXISTS ip_address,
    DROP COLUMN IF EXISTS user_agent
"""

# Same definitions as 0000_baseline.schema.sql.
_RESTORE_COLUMNS_SQL = """
ALTER TABLE newsletter_subscribers
    ADD COLUMN IF NOT EXISTS company character varying(255),
    ADD COLUMN IF NOT EXISTS interest_categories jsonb,
    ADD COLUMN IF NOT EXISTS ip_address character varying(45),
    ADD COLUMN IF NOT EXISTS user_agent text,
    ADD COLUMN IF NOT EXISTS marketing_consent boolean DEFAULT false
"""


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        present = {
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'newsletter_subscribers'
                   AND column_name = ANY($1::text[])
                """,
                list(_RETIRED_COLUMNS),
            )
        }

        if present == set(_RETIRED_COLUMNS):
            counts = await conn.fetchrow(_COUNT_FILLED_SQL)
            filled = {name: counts[name] for name in _RETIRED_COLUMNS if counts[name]}
            if filled:
                logger.warning(
                    "Migration 20260928_184647: discarding filled values across %d "
                    "newsletter_subscribers row(s): %s",
                    counts["total_rows"],
                    ", ".join(f"{name}={n}" for name, n in filled.items()),
                )
        elif present:
            logger.info(
                "Migration 20260928_184647: only %s still present; dropping those",
                ", ".join(sorted(present)),
            )

        await conn.execute("DROP INDEX IF EXISTS idx_newsletter_interests_gin")
        await conn.execute(_DROP_COLUMNS_SQL)
    logger.info(
        "Migration 20260928_184647: dropped %s from newsletter_subscribers",
        ", ".join(_RETIRED_COLUMNS),
    )


async def down(pool) -> None:
    """Revert the migration.

    Restores the five columns (same types and default as the baseline) and the
    GIN index. The values are not restored; ``up()`` discarded them.
    """
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT to_regclass('public.newsletter_subscribers')") is None:
            return
        await conn.execute(_RESTORE_COLUMNS_SQL)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_newsletter_interests_gin "
            "ON newsletter_subscribers USING gin (interest_categories)"
        )
    logger.info("Migration 20260928_184647: reverted")
