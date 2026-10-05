"""Migration 20260806_164855: drop Gitea-era issues embeddings.

ISSUE: Glad-Labs/poindexter#991

``embeddings`` where ``source_table='issues'`` held 112 rows from a one-off
backfill against the Gitea instance that was decommissioned 2026-04-30.
``GiteaIssuesTap`` was retired 2026-05-08 and nothing replaced it, so the
rows sat frozen at 2026-04-02 — 126 days stale by the time the
corpus-staleness panel (poindexter#989) surfaced them.

They are dropped rather than migrated because their key scheme is unsafe.
Every legacy row keys ``source_id`` on the **bare issue number** (``185``),
which cannot represent more than one repo: ``Glad-Labs/poindexter#185`` and
``Glad-Labs/poindexter#185`` collide on the
``(source_table, source_id, chunk_index, embedding_model)`` unique
constraint, so whichever embedded second would silently overwrite the
first. ``GitHubIssuesTap`` keys on ``github/{owner}/{repo}/issues/{number}``
instead, and re-ingests every issue from both repos on its first run — so
this deletes superseded content, not unique content.

Scoped deliberately narrowly: only ``source_table='issues'`` rows whose
``source_id`` is entirely digits. A row already using the new
repo-qualified scheme is left alone, which makes this safe to run after the
Tap has started writing (order-independent) and a no-op on re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration. Idempotent: re-running deletes nothing."""
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            """
            WITH removed AS (
                DELETE FROM embeddings
                 WHERE source_table = 'issues'
                   AND source_id ~ '^[0-9]+$'
             RETURNING 1
            )
            SELECT count(*) FROM removed
            """
        )
        logger.info(
            "Migration %s: dropped %s legacy Gitea-era issues embedding(s); "
            "GitHubIssuesTap re-ingests both repos under repo-qualified "
            "source_ids on its next run.",
            __name__,
            deleted,
        )


async def down(pool) -> None:
    """One-way — the source rows cannot be reconstructed.

    The Gitea instance they came from was decommissioned 2026-04-30, and
    their key scheme is the bug being fixed. Re-running ``GitHubIssuesTap``
    repopulates the same issues from GitHub under safe source_ids.
    """
    return
