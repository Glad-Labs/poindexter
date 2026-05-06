"""Migration 20260506_172100: seed ``gh_repo`` app_setting for dev_diary.

Closes Glad-Labs/poindexter#405 (replace dev_diary subprocess gh/git
with direct GitHub REST API calls). The rewritten dev_diary topic
source no longer shells out to the local ``gh`` / ``git`` binaries —
it talks to ``https://api.github.com/repos/{owner}/{name}/...``
directly — so we need a DB-backed setting telling it which repo to
query.

Default value is the canonical Glad Labs operator monorepo
(``Glad-Labs/glad-labs-stack``). Operators on the public Poindexter
distribution would point this at their own repository.

Why a string default and not NULL: ``app_settings.value`` is
NOT NULL (per ``feedback_app_settings_value_not_null``). Seeding NULL
crashes CI. The literal default goes in the row; downstream code uses
the same string as its hardcoded fallback when the row is missing,
so the wire-up is consistent across "fresh DB" and "row-deleted"
states.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — operator-set values
are preserved on re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_KEY = "gh_repo"
_VALUE = "Glad-Labs/glad-labs-stack"
_CATEGORY = "integrations"
_DESCRIPTION = (
    "GitHub repository (``owner/name``) the dev_diary topic source "
    "queries for merged PRs and notable commits when assembling the "
    "daily build-in-public context bundle. Read by "
    "services/topic_sources/dev_diary_source.py via direct GitHub "
    "REST API calls (``GET /repos/{repo}/pulls`` + "
    "``GET /repos/{repo}/commits``). Pair with the ``gh_token`` secret "
    "for authenticated rate limits + private-repo access. Defaults to "
    "the Glad Labs operator monorepo; OSS operators on the public "
    "Poindexter distribution should point this at their own repo. "
    "Closes Glad-Labs/poindexter#405."
)


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
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration "
                "20260506_172100 (gh_repo seed)"
            )
            return

        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES ($1, $2, $3, $4, FALSE)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY, _VALUE, _CATEGORY, _DESCRIPTION,
        )
        if result == "INSERT 0 1":
            logger.info(
                "Migration 20260506_172100: seeded %s=%r "
                "(operators on a fork should override via "
                "'poindexter set %s <owner/name>')",
                _KEY, _VALUE, _KEY,
            )
        else:
            logger.info(
                "Migration 20260506_172100: %s already set, "
                "leaving operator value alone",
                _KEY,
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _KEY,
        )
        logger.info("Migration 20260506_172100 rolled back: removed %s", _KEY)
