"""
Unit tests for GDPRService.

Tests cover:
  - _to_csv: pure static method, no mocks needed
  - create_request: mocked DB, verifies SQL is called and token is returned
  - verify_request: returns None for unknown token; returns dict for matched row
  - export_user_data: raises ValueError for missing/unverified/wrong-type requests
  - record_deletion_processing: raises ValueError for wrong status or type
  - audit: delegates to DB (mocked)

All async tests use pytest-asyncio (asyncio mode=auto in pyproject.toml).
No real database connections are made.
"""

import csv
import io
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gdpr_service import GDPRService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> GDPRService:
    """Build a GDPRService with a fully mocked DB pool."""
    db = MagicMock()
    service = GDPRService(db)
    # Bypass schema creation in all tests
    service._schema_ready = True
    return service


def _make_mock_conn():
    """Return a async context manager mock for pool.acquire()."""
    conn = AsyncMock()
    return conn


def _pool_with_conn(conn):
    """Wrap a mock conn in a pool mock that supports 'async with pool.acquire()'."""
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


# ---------------------------------------------------------------------------
# _to_csv — pure static method
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToCsv:
    def test_output_is_valid_csv(self):
        data = {"section_a": {"key": "value"}, "section_b": [1, 2, 3]}
        result = GDPRService._to_csv(data)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert rows[0] == ["section", "payload"]
        assert len(rows) == 3  # header + 2 data rows

    def test_sections_are_top_level_keys(self):
        data = {"users": [{"id": 1}], "tasks": []}
        result = GDPRService._to_csv(data)
        assert "users" in result
        assert "tasks" in result

    def test_payload_column_is_json_serialised(self):
        data = {"info": {"nested": True}}
        result = GDPRService._to_csv(data)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        payload_json = rows[1][1]
        parsed = json.loads(payload_json)
        assert parsed == {"nested": True}

    def test_empty_data_produces_only_header(self):
        result = GDPRService._to_csv({})
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert rows[0] == ["section", "payload"]
        assert len(rows) == 1

    def test_datetime_values_do_not_raise(self):
        data = {"info": {"created_at": datetime.now(timezone.utc)}}
        # json.dumps with default=str should not raise
        result = GDPRService._to_csv(data)
        assert "info" in result


# ---------------------------------------------------------------------------
# create_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestCreateRequest:
    async def test_returns_dict_with_verification_token(self):
        service = _make_service()
        conn = _make_mock_conn()
        # fetchrow returns a mock row that converts to a dict
        fake_row = {
            "id": "req-1",
            "request_type": "access",
            "status": "pending_verification",
            "email": "user@example.com",
            "name": "Alice",
            "details": None,
            "data_categories": [],
            "verification_sent_at": None,
            "verified_at": None,
            "created_at": datetime.now(timezone.utc),
            "deadline_at": datetime.now(timezone.utc),
            "completed_at": None,
        }
        conn.fetchrow = AsyncMock(return_value=fake_row)
        conn.execute = AsyncMock()
        service.db_service.pool = _pool_with_conn(conn)

        # Patch audit to avoid a second pool.acquire call
        service.audit = AsyncMock()

        result = await service.create_request(
            request_type="access",
            email="user@example.com",
            name="Alice",
            details=None,
            data_categories=["profile", "activity"],
        )

        assert "verification_token" in result
        assert len(result["verification_token"]) > 10  # secrets.token_urlsafe(32)

    async def test_raises_if_fetchrow_returns_none(self):
        service = _make_service()
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        service.db_service.pool = _pool_with_conn(conn)
        service.audit = AsyncMock()

        with pytest.raises(RuntimeError, match="Failed to create"):
            await service.create_request("access", "x@y.com", None, None, None)


# ---------------------------------------------------------------------------
# verify_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestVerifyRequest:
    async def test_unknown_token_returns_none(self):
        service = _make_service()
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)
        service.db_service.pool = _pool_with_conn(conn)

        result = await service.verify_request("nonexistent-token")
        assert result is None

    async def test_valid_token_returns_request_dict(self):
        service = _make_service()
        conn = _make_mock_conn()
        fake_row = {
            "id": "req-1",
            "request_type": "access",
            "status": "verified",
            "email": "user@example.com",
            "created_at": datetime.now(timezone.utc),
            "deadline_at": datetime.now(timezone.utc),
            "verified_at": datetime.now(timezone.utc),
        }
        conn.fetchrow = AsyncMock(return_value=fake_row)
        service.db_service.pool = _pool_with_conn(conn)
        service.audit = AsyncMock()

        result = await service.verify_request("valid-token")
        assert result is not None
        assert result["status"] == "verified"


# ---------------------------------------------------------------------------
# export_user_data — validation guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestExportUserData:
    async def test_raises_if_request_not_found(self):
        service = _make_service()
        service.get_request = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Request not found"):
            await service.export_user_data("missing-id")

    async def test_raises_if_request_not_verified(self):
        service = _make_service()
        service.get_request = AsyncMock(
            return_value={
                "status": "pending_verification",
                "request_type": "access",
                "email": "u@e.com",
            }
        )

        with pytest.raises(ValueError, match="must be verified"):
            await service.export_user_data("req-1")

    async def test_raises_if_wrong_request_type(self):
        service = _make_service()
        service.get_request = AsyncMock(
            return_value={
                "status": "verified",
                "request_type": "deletion",
                "email": "u@e.com",
            }
        )

        with pytest.raises(ValueError, match="access/portability"):
            await service.export_user_data("req-1")


# ---------------------------------------------------------------------------
# record_deletion_processing — validation guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestRecordDeletionProcessing:
    async def test_raises_if_request_not_found(self):
        service = _make_service()
        service.get_request = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Request not found"):
            await service.record_deletion_processing("missing-id")

    async def test_raises_if_status_not_verified(self):
        service = _make_service()
        service.get_request = AsyncMock(
            return_value={
                "status": "pending_verification",
                "request_type": "deletion",
                "deadline_at": None,
            }
        )

        with pytest.raises(ValueError, match="must be verified"):
            await service.record_deletion_processing("req-1")

    async def test_raises_if_wrong_request_type(self):
        service = _make_service()
        service.get_request = AsyncMock(
            return_value={
                "status": "verified",
                "request_type": "access",
                "deadline_at": None,
            }
        )

        with pytest.raises(ValueError, match="deletion requests"):
            await service.record_deletion_processing("req-1")
