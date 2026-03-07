"""Tests for GDPR privacy routes."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.privacy_routes import router
from utils.route_utils import get_database_dependency

pytestmark = [pytest.mark.unit, pytest.mark.api]


class _FakeGDPRService:
    """Simple fake GDPR service for route testing."""

    def __init__(self, _db):
        self.deadline = datetime.now(timezone.utc) + timedelta(days=30)

    async def create_request(self, **kwargs):
        return {
            "id": "req-123",
            "verification_token": "token-abc",
            "deadline_at": self.deadline,
        }

    async def mark_verification_sent(self, _request_id):
        return None

    async def verify_request(self, token):
        if token == "bad-token":
            return None
        return {
            "id": "req-123",
            "request_type": "access",
            "verified_at": datetime.now(timezone.utc),
            "deadline_at": self.deadline,
        }

    async def get_request(self, request_id):
        if request_id == "missing":
            return None
        return {
            "id": request_id,
            "request_type": "deletion",
            "status": "verified",
            "created_at": datetime.now(timezone.utc),
            "verified_at": datetime.now(timezone.utc),
            "deadline_at": self.deadline,
            "completed_at": None,
        }

    async def export_user_data(self, request_id, fmt="json"):
        if request_id == "not-verified":
            raise ValueError("Request must be verified before export")
        return {"request_id": request_id, "format": fmt, "data": {"ok": True}}

    async def record_deletion_processing(self, request_id):
        return {
            "id": request_id,
            "status": "processing",
            "request_type": "deletion",
            "deadline_at": self.deadline,
        }


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: MagicMock()
    return TestClient(app)


def test_submit_data_request_success():
    client = _build_client()

    with patch("routes.privacy_routes.GDPRService", _FakeGDPRService):
        response = client.post(
            "/api/privacy/data-requests",
            json={
                "request_type": "access",
                "email": "user@example.com",
                "details": "Please export my data",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["request_id"] == "req-123"
    assert payload["verification_required"] is True


def test_verify_data_request_invalid_token():
    client = _build_client()

    with patch("routes.privacy_routes.GDPRService", _FakeGDPRService):
        response = client.get("/api/privacy/data-requests/verify/bad-token")

    assert response.status_code == 404
    assert "Invalid or expired verification token" in response.json()["detail"]


def test_export_data_request_requires_verified_request():
    client = _build_client()

    with patch("routes.privacy_routes.GDPRService", _FakeGDPRService):
        response = client.get("/api/privacy/data-requests/not-verified/export?format=json")

    assert response.status_code == 400
    assert "verified" in response.json()["detail"]


def test_process_deletion_request_success():
    client = _build_client()

    with patch("routes.privacy_routes.GDPRService", _FakeGDPRService):
        response = client.post("/api/privacy/data-requests/req-123/process-deletion")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert payload["request_type"] == "deletion"
