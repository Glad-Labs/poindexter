"""Unit tests for services/service_restart_requests.py (poindexter#909)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.service_restart_requests import (
    InvalidContainerName,
    SelfDefeatingRestart,
    create_restart_request,
    get_restart_request,
    is_self_defeating,
    is_valid_container_name,
)

# No module-level asyncio mark — pyproject asyncio_mode="auto" already
# auto-marks the coroutine tests below; an explicit mark would wrongly tag
# TestIsValidContainerName's plain sync tests too.


class TestIsValidContainerName:
    @pytest.mark.parametrize(
        "name",
        [
            "poindexter-worker",
            "poindexter-brain-daemon",
            "poindexter-ci-runner-1",
            "poindexter-postgres-exporter",
        ],
    )
    def test_accepts_real_container_names(self, name):
        assert is_valid_container_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "worker",  # missing poindexter- prefix
            "poindexter-",  # nothing after the prefix
            "poindexter--worker",  # empty segment
            "poindexter-worker; rm -rf /",  # injection attempt
            "poindexter-Worker",  # uppercase
            "../etc/passwd",
            "poindexter_worker",  # underscore, not hyphen
        ],
    )
    def test_rejects_malformed_names(self, name):
        assert not is_valid_container_name(name)


def _fake_pool(fetchrow_result):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


class TestCreateRestartRequest:
    async def test_valid_name_inserts_and_returns_row(self):
        row = {
            "id": "11111111-1111-1111-1111-111111111111",
            "container": "poindexter-pyroscope",
            "status": "pending",
            "requested_at": datetime.now(timezone.utc),
        }
        pool, conn = _fake_pool(row)

        out = await create_restart_request(pool, "poindexter-pyroscope")

        assert out == row
        sql, args = conn.fetchrow.call_args[0][0], conn.fetchrow.call_args[0][1:]
        assert "INSERT INTO service_restart_requests" in sql
        assert args == ("poindexter-pyroscope", "console")

    async def test_custom_requested_by_is_threaded_through(self):
        pool, conn = _fake_pool({"id": "x", "container": "c", "status": "pending", "requested_at": None})
        await create_restart_request(pool, "poindexter-worker", requested_by="mcp")
        args = conn.fetchrow.call_args[0][1:]
        assert args[1] == "mcp"

    async def test_invalid_name_raises_before_touching_the_pool(self):
        pool, conn = _fake_pool(None)
        with pytest.raises(InvalidContainerName):
            await create_restart_request(pool, "not-a-container")
        conn.fetchrow.assert_not_called()


class TestGetRestartRequest:
    async def test_returns_row_for_known_uuid(self):
        row_id = "22222222-2222-2222-2222-222222222222"
        row = {"id": row_id, "container": "poindexter-worker", "status": "done"}
        pool, conn = _fake_pool(row)

        out = await get_restart_request(pool, row_id)

        assert out == row
        # asyncpg binds a real uuid.UUID, not the raw string, to a uuid column.
        import uuid as _uuid

        bound = conn.fetchrow.call_args[0][1]
        assert isinstance(bound, _uuid.UUID)
        assert str(bound) == row_id

    async def test_missing_row_returns_none(self):
        pool, conn = _fake_pool(None)
        out = await get_restart_request(pool, "33333333-3333-3333-3333-333333333333")
        assert out is None

    async def test_malformed_id_returns_none_without_querying(self):
        pool, conn = _fake_pool(None)
        out = await get_restart_request(pool, "not-a-uuid")
        assert out is None
        conn.fetchrow.assert_not_called()


class TestSelfDefeatingGuard:
    """Restarting the container that HOSTS the intent queue can never produce a
    terminal row: brain marks `claimed`, then the restart kills either brain
    itself (before the `done` write) or the database that write targets. The
    claim query only selects `status='pending'`, so the row strands forever.
    Refuse at enqueue time instead (glad-labs-stack#2505).
    """

    @pytest.mark.parametrize(
        "container", ["poindexter-brain-daemon", "poindexter-postgres-local"]
    )
    async def test_self_defeating_containers_are_refused(self, container):
        pool, conn = _fake_pool(None)

        with pytest.raises(SelfDefeatingRestart):
            await create_restart_request(pool, container)

        # Refused BEFORE any write — no row to strand.
        conn.fetchrow.assert_not_called()

    @pytest.mark.parametrize(
        "container",
        ["poindexter-worker", "poindexter-prefect-worker", "poindexter-loki"],
    )
    async def test_ordinary_containers_still_queue(self, container):
        row = {"id": "1", "container": container, "status": "pending"}
        pool, conn = _fake_pool(row)

        out = await create_restart_request(pool, container)

        assert out == row
        conn.fetchrow.assert_called_once()

    def test_guard_is_distinct_from_the_shape_check(self):
        """A self-defeating name is a WELL-FORMED name — the two rejections map
        to different HTTP codes (409 vs 400), so they must not collapse."""
        assert is_valid_container_name("poindexter-brain-daemon")
        assert is_self_defeating("poindexter-brain-daemon")
        assert not is_self_defeating("poindexter-worker")
        assert not issubclass(SelfDefeatingRestart, InvalidContainerName)
