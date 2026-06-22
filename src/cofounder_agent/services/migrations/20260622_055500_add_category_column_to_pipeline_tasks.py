"""Migration 20260622_055500: re-add ``pipeline_tasks.category`` (base-table-only).

Restores the physical ``category`` column that the **same-day** shimmed-drop
migration ``20260622_032938`` (#1843) had removed. That drop rebuilt the
``content_tasks`` / ``pipeline_tasks_view`` views to project
``NULL::character varying AS category`` and stripped ``category`` from the
INSTEAD OF trigger functions and the direct INSERTs — but it **missed one read
path**: ``services/flows/content_generation.py::claim_pending_task`` SELECTs
``category`` straight off the base ``pipeline_tasks`` table. So after deploy
every task-claim crashed with ``asyncpg.exceptions.UndefinedColumnError: column
"category" does not exist`` and the content pipeline went dark (no row could be
claimed).

This migration re-adds **only the base column** (``ADD COLUMN IF NOT EXISTS``,
idempotent) to restore the claim path. It deliberately does **not** restore the
view projection or the trigger writes — those keep ``20260622_032938``'s
NULL-shim / category-free form on purpose, because ``category`` is retired:

  * ``category`` is vestigial — superseded by ``niche_slug`` (#796). Nothing
    populates it: ``tasks_db.add_task`` / ``bulk_add_tasks``, the INSTEAD OF
    trigger functions, and ``pipeline_db.upsert_task`` all omit it, so the
    re-added column is, and stays, NULL (0 of 1,830 rows on prod
    ``poindexter_brain`` at the 2026-06-22 reconciliation). ``create_post``
    writes ``posts.category_id`` — a different column on a different table —
    NOT ``pipeline_tasks.category``.
  * Reads through the views (``SELECT *`` / ``TaskRecord.category`` / the
    ``GET /tasks?category=`` filter) therefore still return NULL, and writes
    through the views / ``add_task`` are accepted-but-ignored — unchanged from
    the 032938 shim.

Net: ``category`` is a **base-table-only** column that exists solely so the
historical ``claim_pending_task`` SELECT resolves (the value it reads is always
NULL and is defaulted to ``"technology"`` downstream in
``content_router_service``). The base-table-only contract is documented at the
``tasks_db.add_task`` INSERT and the ``claim_pending_task`` SELECT, and locked by
the ``TestAddTaskAgainstRealDb`` guards in
``tests/unit/services/test_tasks_db.py`` (a future "restore to canonical" of the
view projection or the INSERT turns them red). This docstring supersedes the
original, which mis-stated the final state (it claimed a view reads
``pt.category``, the trigger writes ``NEW.category``, and ``create_post``
supplies it — none of which is true after 032938).

``ADD COLUMN IF NOT EXISTS`` is a no-op where the column already exists (fresh
installs that ran 032938 then this, and the live prod DB once hotfixed). The
``integration_db`` ``test_claim_pending_task`` tier — which builds its schema by
running this migration chain — also gains the column, so its three claim tests
go green.

Pure additive DDL: imports only stdlib, so the migrations-smoke CI step applies
it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS category character varying"
        )
    logger.info(
        "add_category_column_to_pipeline_tasks up: "
        "ensured pipeline_tasks.category exists (claim path restored)"
    )


async def down(pool) -> None:
    # No-op: the base ``pipeline_tasks.category`` column is required by
    # ``claim_pending_task``'s SELECT — re-dropping it re-breaks the claim path,
    # which is exactly what this migration fixed. It is additive + nullable, so
    # there is nothing to reverse. (The column is base-table-only: the views
    # still project the 032938 NULL shim and the triggers don't write it, so
    # there is no view/trigger state to revert either.)
    logger.info(
        "add_category_column_to_pipeline_tasks down: no-op (base column required by claim path)"
    )
