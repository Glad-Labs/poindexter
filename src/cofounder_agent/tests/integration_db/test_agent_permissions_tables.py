"""Integration: the agent-permission gate's tables exist after migrations.

Regression for the production outage where ``mcp set_setting`` failed closed
with ``UndefinedTableError: relation "agent_permissions" does not exist``. The
``agent_permissions`` + ``approval_queue`` tables were created in 2026-04, then
accidentally dropped by #687's dead-Gitea-table sweep, while the gate code
(``services/agent_permissions.py``) stayed live — so every MCP / agent
settings-write blocked. Migration
``20260618_021205_create_agent_permissions_and_approval_queue_tables`` recreates
them.

These run against a fresh DB that has had EVERY migration applied (the
``schema_loaded`` chain behind ``test_txn``), so they prove the migration —
not just the baseline — yields the schema the gate needs. They exercise the
REAL gate functions rather than re-asserting raw SQL, so a future schema/code
drift surfaces here.
"""

from __future__ import annotations

import pytest

from services import agent_permissions

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_gate_tables_exist(test_txn) -> None:
    ap = await test_txn.fetchval("SELECT to_regclass('agent_permissions')")
    aq = await test_txn.fetchval("SELECT to_regclass('approval_queue')")
    assert ap is not None, "agent_permissions table missing after migrations"
    assert aq is not None, "approval_queue table missing after migrations"


async def test_agent_permissions_has_unique_triple(test_txn) -> None:
    # The (agent_name, resource, action) UNIQUE constraint is load-bearing —
    # check_write_permission looks up exactly that triple.
    conname = await test_txn.fetchval(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'agent_permissions'::regclass AND contype = 'u'"
    )
    assert conname is not None, "agent_permissions is missing its UNIQUE constraint"


async def test_check_write_permission_permissive_on_empty_table(test_txn) -> None:
    # No row for this triple → permissive default (the documented contract).
    # This is the behavior that unblocks Matt's phone settings-writes.
    allowed, requires_approval = await agent_permissions.check_write_permission(
        test_txn, "mcp_server", "app_settings", "write"
    )
    assert allowed is True
    assert requires_approval is False


async def test_queue_for_approval_round_trips_jsonb(test_txn) -> None:
    # The requires_approval path inserts into approval_queue. Exercise it
    # against the real JSONB column to prove the table + column type + the
    # json.dumps→jsonb coercion all line up end-to-end.
    await agent_permissions.queue_for_approval(
        test_txn,
        agent_name="mcp_server",
        resource="app_settings",
        action="write",
        proposed_change={"key": "max_posts_per_day", "value": "9"},
        reason="integration round-trip",
    )
    stored = await test_txn.fetchval(
        "SELECT proposed_change->>'key' FROM approval_queue "
        "WHERE reason = 'integration round-trip'"
    )
    assert stored == "max_posts_per_day"
