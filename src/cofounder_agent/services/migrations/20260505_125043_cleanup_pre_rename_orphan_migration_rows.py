"""Migration 20260505_125043: cleanup pre-rename orphan migration rows.

ISSUE: Glad-Labs/poindexter#378

Background — when PR #243 (#371 Phase 1.5) was first authored it used
the filename ``0158_seed_template_runner_postgres_checkpointer.py``.
At review time it was renamed to ``0159_…`` to dodge the collision
with the two other 0158 migrations that landed the same overnight
(see #378 for the full story). Operators who pulled the pre-rename
branch and ran their worker once will have an orphan row in
``schema_migrations`` referencing the old filename. The new
``0159_…`` file then applies cleanly because it doesn't match that
orphan row by name.

Mechanically harmless — the smoke test (``scripts/ci/migrations_smoke.py``)
flags orphan rows as a hard failure, but only against a fresh DB, so
it never tripped during CI on Matt's box. The cleanup is for the
benefit of anyone who diffs ``schema_migrations`` to reconcile against
the on-disk file list.

This is a one-shot, idempotent ``DELETE`` — runs cleanly on a fresh
DB (deletes nothing because no orphan exists) and on the unlucky
operator's box (deletes the one row). No-op on every machine that
never pulled the pre-rename PR.

Per the new naming convention adopted in #378, this migration uses
the timestamp prefix ``20260505_125043``. See
``docs/operations/migrations.md`` for the full convention.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The pre-rename filename. If a row references this name, it's an
# orphan from the brief window where PR #243 carried the old name.
_ORPHAN_NAME = "0158_seed_template_runner_postgres_checkpointer.py"


async def up(pool) -> None:
    """Drop the orphan ``schema_migrations`` row if it exists."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM schema_migrations WHERE name = $1",
            _ORPHAN_NAME,
        )
        # asyncpg returns "DELETE n" where n is the row count.
        try:
            n = int(result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            n = -1
        if n > 0:
            logger.info(
                "Migration 20260505_125043: removed %d orphan schema_migrations "
                "row(s) for %r (pre-rename leftover from #371)",
                n, _ORPHAN_NAME,
            )
        else:
            logger.info(
                "Migration 20260505_125043: no orphan rows for %r — clean install",
                _ORPHAN_NAME,
            )


async def down(pool) -> None:
    """No-op — we don't recreate orphan rows."""
    return None
