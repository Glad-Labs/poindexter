"""Migration 20260618_021205: create agent_permissions and approval_queue tables

ISSUE: Glad-Labs/glad-labs-stack — MCP set_setting fails closed with
``UndefinedTableError: relation "agent_permissions" does not exist``.

The agent-permission gate (``services/agent_permissions.py``, queried by the
MCP ``set_setting`` tool) reads ``agent_permissions`` and writes
``approval_queue``. Both tables were created on 2026-04-02 ("agent permission
system"), then accidentally dropped on 2026-05-28 by #687's "drop 60 dead
Gitea schema tables" sweep (they have single-word-ish names and looked
orphaned). The gate code was later re-hardened (#750 — fail-closed) and
extracted to the service layer (#1663) without recreating the tables, so on
prod every MCP / agent settings-write started failing closed. The post-squash
Phase E baseline carries no DDL for either table.

This migration recreates both with their original schema (recovered from the
pre-squash baseline ``8a292379c``). No seed rows: the gate is permissive by
default (a missing row = implicitly allowed — see ``agent_permissions.py``), so
an empty table already lets legitimate agents (mcp_server, etc.) through.
Operators opt in to restrictions by inserting rows; that is also why seeds do
not belong here (per the seed-in-baseline convention).

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Permission rows: one per (agent, resource, action). The UNIQUE constraint is
# load-bearing — ``check_write_permission`` looks up exactly that triple, and a
# future upsert path relies on the conflict target existing.
_CREATE_AGENT_PERMISSIONS = """
CREATE TABLE IF NOT EXISTS agent_permissions (
    id                BIGSERIAL PRIMARY KEY,
    agent_name        VARCHAR(100) NOT NULL,
    resource          VARCHAR(100) NOT NULL,
    action            VARCHAR(20)  NOT NULL,
    allowed           BOOLEAN      NOT NULL DEFAULT FALSE,
    requires_approval BOOLEAN      NOT NULL DEFAULT FALSE,
    description       TEXT         NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (agent_name, resource, action)
);
"""

# Pending changes proposed by an agent whose permission row has
# ``requires_approval=true``. ``proposed_change`` is JSONB so the approval UI /
# dashboards can query into it; ``queue_for_approval`` passes a ``json.dumps``
# string, which asyncpg coerces straight into the JSONB column (verified).
_CREATE_APPROVAL_QUEUE = """
CREATE TABLE IF NOT EXISTS approval_queue (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      VARCHAR(100) NOT NULL,
    resource        VARCHAR(100) NOT NULL,
    action          VARCHAR(20)  NOT NULL,
    proposed_change JSONB        NOT NULL,
    reason          TEXT         NOT NULL DEFAULT '',
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending',
    reviewed_by     VARCHAR(100),
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""


async def up(pool) -> None:
    """Create ``agent_permissions`` and ``approval_queue`` (idempotent).

    ``IF NOT EXISTS`` keeps this safe to re-run and a no-op on the rare DB
    where the tables already survive.
    """
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_AGENT_PERMISSIONS)
        await conn.execute(_CREATE_APPROVAL_QUEUE)
    logger.info(
        "Migration create_agent_permissions_and_approval_queue: tables ensured"
    )


async def down(pool) -> None:
    """Drop both tables. Reverses ``up()`` only — they hold no data the rest of
    the system depends on (the gate is permissive-by-default when absent)."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS approval_queue")
        await conn.execute("DROP TABLE IF EXISTS agent_permissions")
    logger.info(
        "Migration create_agent_permissions_and_approval_queue down: reverted"
    )
