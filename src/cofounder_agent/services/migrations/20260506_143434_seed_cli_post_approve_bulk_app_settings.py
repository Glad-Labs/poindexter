"""Migration 20260506_143434: seed cli post approve bulk app_settings

ISSUE: Glad-Labs/poindexter#338 (gate system polish — bulk approval bullet).

Adds the two safety knobs the new ``poindexter post approve --filter ...``
bulk mode reads on every invocation:

- ``cli_post_approve_bulk_max_count`` — hard ceiling for matched posts in
  one ``--no-dry-run`` execution. Default 100. Operator can override per
  call with ``--max=N`` (subject to this ceiling, not bypassing it
  unless the ceiling itself is raised in this table).
- ``cli_post_approve_bulk_require_confirm`` — when ``true``, the y/N
  confirmation prompt fires even with ``--yes``. Belt-and-suspenders
  for ``feedback_no_bulk_publish.md``; flip to ``false`` only for
  fully-scripted environments where the operator has already gated
  the call upstream.

Both are tunable at runtime via ``poindexter set <key> <value>``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES
                ('cli_post_approve_bulk_max_count', '100', 'cli',
                 'Hard ceiling for matched-post count in a single ''poindexter post approve --no-dry-run --filter ...'' invocation. Refuses to execute when the filter matches more than this many posts unless the operator passes --max=N (still subject to this ceiling). Tighten in production; loosen for backfills.',
                 false, true),
                ('cli_post_approve_bulk_require_confirm', 'true', 'cli',
                 'When true, ''poindexter post approve --filter ... --no-dry-run'' always prompts y/N before approving — even if --yes was passed. Belt-and-suspenders for the no-bulk-publish rule. Flip to false only for fully scripted environments where the call site has its own approval gate.',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260506_143434: applied (2 cli_post_approve_bulk_* settings)"
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'cli_post_approve_bulk_max_count',
                'cli_post_approve_bulk_require_confirm'
            )
            """
        )
        logger.info("Migration 20260506_143434: reverted")
