"""Migration 20260506_005708: seed ``gh_token`` app_setting for dev_diary.

Closes Glad-Labs/poindexter#348 (worker now ships with ``gh`` + ``git``
installed; needs an auth token for ``gh pr list`` to hit the GitHub API).

The dev_diary topic source (services/topic_sources/dev_diary_source.py)
shells out to ``gh pr list --state merged --search merged:>=...`` to
gather the day's merged PRs as one of the writer's strongest signals.
``gh`` reads its auth token from the ``GH_TOKEN`` env var; without it,
the call hits the unauthenticated API and gets either rate-limited or
gets back zero PRs for private repos. Either way the writer ends up
with an empty PR list and the post falls back to fabricated filler
(or skips the day entirely under the PR #218 quiet-day guard).

This migration introduces:

- ``gh_token`` (default ``""``, ``is_secret=true``, ``category='integrations'``)
  — GitHub Personal Access Token (classic or fine-grained) with
  ``repo`` scope read access, used by the dev_diary topic source to
  authenticate ``gh pr list`` calls. Empty default leaves the source
  in unauthenticated mode (works for public repos with low traffic;
  fails / returns nothing for private repos like Glad-Labs/glad-labs-stack).

  Operators populate via:
      poindexter settings set gh_token <token>

  The auto-encrypt trigger (migration 0130) wraps the value as
  ``enc:v1:...`` on insert/update, so the row is never stored in
  plaintext. The worker reads it via the existing
  ``plugins.secrets.get_secret`` decryption path, then injects it
  into the subprocess env as ``GH_TOKEN`` for the duration of the
  ``gh pr list`` call (does not leak into the worker process env or
  into Docker layers).

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — operator-set values
preserved on re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_KEY = "gh_token"
_VALUE = ""
_CATEGORY = "integrations"
_DESCRIPTION = (
    "GitHub Personal Access Token used by the dev_diary topic source "
    "(services/topic_sources/dev_diary_source.py) to authenticate "
    "`gh pr list` subprocess calls when assembling the daily build-"
    "in-public context bundle. Empty default leaves the source in "
    "unauthenticated mode (works on public repos with low traffic; "
    "returns nothing for private repos like Glad-Labs/glad-labs-stack). "
    "Set via `poindexter settings set gh_token <token>` — the auto-"
    "encrypt trigger (0130) wraps the value as enc:v1:... on insert. "
    "Token needs `repo` read scope (classic) or `Contents: Read + "
    "Pull requests: Read` (fine-grained). Closes "
    "Glad-Labs/poindexter#348."
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
                "20260506_005708 (gh_token seed)"
            )
            return

        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY, _VALUE, _CATEGORY, _DESCRIPTION,
        )
        if result == "INSERT 0 1":
            logger.info(
                "Migration 20260506_005708: seeded %s='' "
                "(operator must set via 'poindexter settings set %s "
                "<token>' for gh pr list authentication)",
                _KEY, _KEY,
            )
        else:
            logger.info(
                "Migration 20260506_005708: %s already set, "
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
        logger.info("Migration 20260506_005708 rolled back: removed %s", _KEY)
