"""Migration: drop gitea/forgejo decommission residue tables.

Legacy-deletion #936 batch D. Gitea was decommissioned 2026-04-30 and no
forgejo/gitea container runs against this database (operator-confirmed dead).
Two gitea-schema tables linger in ``poindexter_brain`` as pure residue:

  - ``notification`` — 0 rows, gitea schema, no Python writer/reader.
  - ``auth_token``   — 2 EXPIRED gitea session tokens (expires_unix in late
    May 2026, now passed), gitea schema, no Python writer/reader.

Verified: no FK dependents (``pg_constraint`` confrelid check returned none),
no live code touches either table. They were captured in 0000_baseline.schema
at the 2026-05-08 squash; this forward migration removes them.

NOTE: the poindexter-legacy ``logs`` table (migration 011 admin-logging,
0 rows) is intentionally NOT dropped here — it still has the dead
``admin_db.add_log_entry`` / ``get_logs`` accessors + delegate tests, which a
separate cleanup removes first. ``down()`` is a no-op: recreating decommissioned
gitea tables (empty / expired) serves no purpose.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS notification")
        await conn.execute("DROP TABLE IF EXISTS auth_token")
    logger.info("drop_gitea_decommission_tables: dropped notification + auth_token")


async def down(pool) -> None:
    # No-op: gitea decommission residue (gitea retired 2026-04-30). Recreating
    # the empty/expired tables would serve no purpose.
    logger.info("drop_gitea_decommission_tables down: no-op (decommissioned gitea residue)")
