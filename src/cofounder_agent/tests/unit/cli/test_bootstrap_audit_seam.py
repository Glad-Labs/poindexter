"""Unit tests for the CLI audit-sink seam (``open_cli_pool`` / ``close_cli_pool``).

Regression coverage for the 2026-08-26 first-live-Pro-purchase incident:
``poindexter pro sync`` emitted a ``pro_delivery_action_needed`` finding, but
CLI contexts never initialised the global ``AuditLogger`` (only the worker /
Prefect ``DatabaseService`` path did), so ``audit_log_bg`` DROPPED the warn
finding and it never reached the alert pipeline.

The seam gives every ``poindexter <cmd>`` DB pool the same two guarantees the
worker has:

- ``open_cli_pool`` attaches the global audit sink to the command's pool, so
  ``emit_finding`` from any CLI-invoked service persists;
- ``close_cli_pool`` detaches + drains in-flight fire-and-forget writes
  BEFORE closing the pool, so a finding emitted moments before command
  teardown can't die on ``InterfaceError('pool is closing')`` (the GlitchTip
  #863 race ``DatabaseService.close`` already drains for).

Both halves are fail-soft: a broken audit seam must never break the command.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import services.audit_log as audit_mod
from poindexter.cli._bootstrap import close_cli_pool, open_cli_pool
from services.audit_log import get_audit_logger, init_global_audit_logger
from utils.findings import emit_finding

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal asyncpg-pool stand-in recording call order across execute/close."""

    def __init__(self, name: str = "pool"):
        self.name = name
        self.closed = False
        self.events: list[tuple] = []

    async def execute(self, sql, *args, **kwargs):
        if self.closed:
            raise RuntimeError("pool is closing")
        self.events.append(("execute", sql, args))

    async def close(self):
        self.closed = True
        self.events.append(("close",))


@pytest.fixture(autouse=True)
def _restore_global_audit_logger():
    """Save/restore the module-level singleton around every test."""
    original = audit_mod._global_audit_logger
    audit_mod._global_audit_logger = None
    try:
        yield
    finally:
        audit_mod._global_audit_logger = original


def _patch_create_pool(fake_pool):
    async def _create_pool(_dsn, **_kwargs):
        return fake_pool

    return patch("asyncpg.create_pool", new=_create_pool)


# ---------------------------------------------------------------------------
# open_cli_pool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenCliPool:
    async def test_attaches_global_audit_logger_to_created_pool(self):
        fake = _FakePool()
        with _patch_create_pool(fake):
            pool = await open_cli_pool("postgresql://x")

        assert pool is fake
        logger = get_audit_logger()
        assert logger is not None
        assert logger.pool is fake

    async def test_returns_pool_even_when_audit_attach_fails(self):
        fake = _FakePool()
        with _patch_create_pool(fake), patch(
            "services.audit_log.init_global_audit_logger",
            side_effect=RuntimeError("boom"),
        ):
            pool = await open_cli_pool("postgresql://x")

        assert pool is fake
        assert get_audit_logger() is None

    async def test_explicit_dsn_and_kwargs_reach_create_pool(self):
        seen = {}

        async def _create_pool(dsn, **kwargs):
            seen["dsn"] = dsn
            seen["kwargs"] = kwargs
            return _FakePool()

        with patch("asyncpg.create_pool", new=_create_pool):
            await open_cli_pool("postgresql://explicit", timeout=8)

        assert seen["dsn"] == "postgresql://explicit"
        assert seen["kwargs"] == {"min_size": 1, "max_size": 2, "timeout": 8}


# ---------------------------------------------------------------------------
# close_cli_pool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCloseCliPool:
    async def test_emitted_finding_lands_before_pool_close(self):
        """THE incident regression: a warn finding emitted by a CLI-invoked
        service between open and close must land in audit_log, and land
        BEFORE the pool closes (drain-before-close, #863)."""
        fake = _FakePool()
        with _patch_create_pool(fake):
            pool = await open_cli_pool("postgresql://x")

        # What services.pro_delivery does mid-command:
        emit_finding(
            source="pro_delivery",
            kind="pro_delivery_action_needed",
            title="buyer needs manual GitHub link",
            body="run: poindexter pro link SUB USERNAME",
            severity="warn",
        )

        await close_cli_pool(pool)

        kinds = [e[0] for e in fake.events]
        assert "execute" in kinds, "finding write never reached the pool"
        assert kinds.index("execute") < kinds.index("close")
        insert = next(e for e in fake.events if e[0] == "execute")
        assert "audit_log" in insert[1]
        assert insert[2][0] == "finding"  # event_type bind param
        assert insert[2][4] == "warn"  # severity bind param

    async def test_resets_global_so_stale_pool_is_never_a_sink(self):
        fake = _FakePool()
        with _patch_create_pool(fake):
            pool = await open_cli_pool("postgresql://x")
        assert get_audit_logger() is not None

        await close_cli_pool(pool)

        assert get_audit_logger() is None
        assert fake.closed is True

    async def test_does_not_clobber_logger_owned_by_another_pool(self):
        """cli/pipeline.py builds a full DatabaseService whose pool re-inits
        the global; closing the CLI pool must not tear that logger down."""
        cli_pool = _FakePool("cli")
        other_pool = _FakePool("database_service")
        with _patch_create_pool(cli_pool):
            pool = await open_cli_pool("postgresql://x")

        init_global_audit_logger(other_pool)  # DatabaseService takes over
        await close_cli_pool(pool)

        logger = get_audit_logger()
        assert logger is not None
        assert logger.pool is other_pool
        assert cli_pool.closed is True

    async def test_closes_pool_even_when_drain_raises(self):
        fake = _FakePool()
        with _patch_create_pool(fake):
            pool = await open_cli_pool("postgresql://x")

        with patch(
            "services.audit_log.drain_pending_writes",
            side_effect=RuntimeError("drain exploded"),
        ):
            await close_cli_pool(pool)

        assert fake.closed is True

    async def test_close_without_prior_open_still_closes(self):
        """close_cli_pool on a pool the seam never attached (or after a
        failed attach) is a plain close — no raise, no global touched."""
        fake = _FakePool()
        await close_cli_pool(fake)
        assert fake.closed is True
        assert get_audit_logger() is None
