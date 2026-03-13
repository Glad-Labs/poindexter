"""
Unit tests for routes/privacy_routes.py.

Tests cover:
- POST /api/privacy/data-requests              — submit_data_request (public)
- GET  /api/privacy/data-requests/verify/{tok} — verify_data_request (public)
- GET  /api/privacy/data-requests/{id}         — get_data_request_status (requires auth)
- GET  /api/privacy/data-requests/{id}/export  — export_data_request (requires auth)
- POST /api/privacy/data-requests/{id}/process-deletion — process_deletion_request (requires auth)
- GET  /api/privacy/gdpr-rights               — get_gdpr_rights (static, public)
- GET  /api/privacy/data-processing           — get_data_processing_info (static, public)

GDPRService is patched to avoid real DB/email I/O.
Admin endpoints require authentication — get_current_user overridden with TEST_USER.
"""

import pytest
from datetime import datetime, timezone
from typing import Any, Tuple
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from routes.auth_unified import get_current_user
from utils.route_utils import get_database_dependency
from routes.privacy_routes import router

from tests.unit.routes.conftest import make_mock_db, TEST_USER


SAMPLE_REQUEST = {
    "id": 1,
    "request_type": "access",
    "email": "gdpr@example.com",
    "status": "pending",
    "verification_token": "tok-abc123",
    "verified_at": None,
    "deadline_at": datetime(2026, 4, 11, tzinfo=timezone.utc),
    "created_at": datetime(2026, 3, 12, tzinfo=timezone.utc),
    "completed_at": None,
}


def _make_gdpr_svc(
    created=None,
    verified=None,
    request=None,
    exported=None,
    updated=None,
):
    svc = MagicMock()
    svc.create_request = AsyncMock(return_value=created or SAMPLE_REQUEST)
    svc.mark_verification_sent = AsyncMock(return_value=None)
    svc.verify_request = AsyncMock(return_value=verified or SAMPLE_REQUEST)
    svc.get_request = AsyncMock(return_value=request or SAMPLE_REQUEST)
    svc.export_user_data = AsyncMock(
        return_value=exported
        or {
            "request_id": "1",
            "format": "json",
            "data": {},
        }
    )
    svc.record_deletion_processing = AsyncMock(
        return_value=updated
        or {
            **SAMPLE_REQUEST,
            "status": "processing",
            "request_type": "deletion",
        }
    )
    return svc


def _build_app(gdpr_svc=None) -> Tuple[FastAPI, Any]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: make_mock_db()
    # Override auth for admin-facing endpoints (get_data_request_status, export, process-deletion)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app, gdpr_svc or _make_gdpr_svc()


VALID_SUBMIT_PAYLOAD = {
    "request_type": "access",
    "email": "gdpr@example.com",
    "name": "GDPR Test User",
    "details": "Please send me all my data.",
}


# ---------------------------------------------------------------------------
# POST /api/privacy/data-requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubmitDataRequest:
    def test_valid_access_request_returns_200(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_request_id(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            data = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD).json()
        assert "request_id" in data
        assert data["status"] == "success"

    def test_deletion_request_type_returns_200(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "deletion"}
            resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200

    def test_invalid_request_type_returns_400(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "hacking"}
            resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 400

    def test_invalid_email_returns_400(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            payload = {**VALID_SUBMIT_PAYLOAD, "email": "not-an-email"}
            resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 400

    def test_response_has_next_steps(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            data = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD).json()
        assert "next_steps" in data
        assert isinstance(data["next_steps"], list)

    def test_db_error_returns_500(self):
        svc = _make_gdpr_svc()
        svc.create_request = AsyncMock(side_effect=KeyError("missing_column"))
        app, _ = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/privacy/data-requests/verify/{token}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyDataRequest:
    def test_valid_token_returns_200(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/verify/tok-abc123")
        assert resp.status_code == 200

    def test_valid_token_response_has_status_verified(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            data = client.get("/api/privacy/data-requests/verify/tok-abc123").json()
        assert data["status"] == "verified"

    def test_invalid_token_returns_404(self):
        svc = _make_gdpr_svc()
        svc.verify_request = AsyncMock(return_value=None)
        app, _ = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/verify/bad-token")
        assert resp.status_code == 404

    def test_db_error_returns_500(self):
        svc = _make_gdpr_svc()
        svc.verify_request = AsyncMock(side_effect=KeyError("db"))
        app, _ = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/privacy/data-requests/verify/tok")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/privacy/data-requests/{request_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDataRequestStatus:
    def test_existing_request_returns_200(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/1")
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            data = client.get("/api/privacy/data-requests/1").json()
        for field in ["request_id", "request_type", "status", "deadline_status"]:
            assert field in data

    def test_not_found_returns_404(self):
        svc = _make_gdpr_svc()
        svc.get_request = AsyncMock(return_value=None)
        app, _ = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/privacy/data-requests/{request_id}/export
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExportDataRequest:
    def test_json_export_returns_200(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/1/export?format=json")
        assert resp.status_code == 200

    def test_csv_export_returns_200(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/1/export?format=csv")
        assert resp.status_code == 200

    def test_invalid_format_returns_422(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/1/export?format=xml")
        assert resp.status_code == 422

    def test_value_error_returns_400(self):
        svc = _make_gdpr_svc()
        svc.export_user_data = AsyncMock(
            side_effect=ValueError("Request not verified")
        )
        app, _ = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.get("/api/privacy/data-requests/1/export")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/privacy/data-requests/{request_id}/process-deletion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProcessDeletionRequest:
    def test_returns_200(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.post("/api/privacy/data-requests/1/process-deletion")
        assert resp.status_code == 200

    def test_response_has_request_id_and_status(self):
        app, svc = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            data = client.post("/api/privacy/data-requests/1/process-deletion").json()
        assert "request_id" in data
        assert "status" in data

    def test_value_error_returns_400(self):
        svc = _make_gdpr_svc()
        svc.record_deletion_processing = AsyncMock(
            side_effect=ValueError("Not a deletion request")
        )
        app, _ = _build_app()
        with patch("routes.privacy_routes.GDPRService", return_value=svc):
            client = TestClient(app)
            resp = client.post("/api/privacy/data-requests/1/process-deletion")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/privacy/gdpr-rights (static — no DB/service)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetGdprRights:
    def test_returns_200(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/privacy/gdpr-rights")
        assert resp.status_code == 200

    def test_response_has_rights(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        data = client.get("/api/privacy/gdpr-rights").json()
        assert "rights" in data
        assert "access" in data["rights"]
        assert "erasure" in data["rights"]

    def test_response_has_contact_email(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        data = client.get("/api/privacy/gdpr-rights").json()
        assert "contact" in data
        assert "@" in data["contact"]


# ---------------------------------------------------------------------------
# GET /api/privacy/data-processing (static — no DB/service)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDataProcessingInfo:
    def test_returns_200(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/privacy/data-processing")
        assert resp.status_code == 200

    def test_response_has_legal_bases(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        data = client.get("/api/privacy/data-processing").json()
        assert "legal_bases" in data
        assert "consent" in data["legal_bases"]

    def test_response_has_data_categories(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        data = client.get("/api/privacy/data-processing").json()
        assert "data_categories" in data
