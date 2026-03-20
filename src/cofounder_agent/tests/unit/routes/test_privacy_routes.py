"""
Unit tests for routes/privacy_routes.py (Issue #608).

The actual privacy_routes.py is a simplified implementation with three endpoints:
- POST /api/privacy/data-requests        — submit_data_request (public, no auth)
- GET  /api/privacy/gdpr-rights          — get_gdpr_rights (static, public)
- GET  /api/privacy/data-processing      — get_data_processing_info (static, public)

These routes do NOT use GDPRService, database dependencies, or authentication.
All validation is inline.  Tests cover happy paths and error paths per Issue #608.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.privacy_routes import router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


VALID_SUBMIT_PAYLOAD = {
    "request_type": "access",
    "email": "gdpr@example.com",
    "name": "GDPR Test User",
    "details": "Please send me all my data.",
}


# ---------------------------------------------------------------------------
# POST /api/privacy/data-requests — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubmitDataRequest:
    def test_valid_access_request_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_request_id(self):
        client = TestClient(_build_app())
        data = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD).json()
        assert "request_id" in data
        assert data["status"] == "success"

    def test_response_has_next_steps(self):
        client = TestClient(_build_app())
        data = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD).json()
        assert "next_steps" in data
        assert isinstance(data["next_steps"], list)
        assert len(data["next_steps"]) > 0

    def test_response_has_support_email(self):
        client = TestClient(_build_app())
        data = client.post("/api/privacy/data-requests", json=VALID_SUBMIT_PAYLOAD).json()
        assert "support_email" in data
        assert "@" in data["support_email"]

    def test_deletion_request_type_returns_200(self):
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "deletion"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200

    def test_portability_request_type_returns_200(self):
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "portability"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200

    def test_correction_request_type_returns_200(self):
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "correction"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200

    def test_objection_request_type_returns_200(self):
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "objection"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200

    def test_other_request_type_returns_200(self):
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "other"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200

    def test_optional_fields_omitted_still_200(self):
        """name and details are optional; omitting them should still work."""
        client = TestClient(_build_app())
        payload = {"request_type": "access", "email": "min@example.com"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200

    def test_with_data_categories_returns_200(self):
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "data_categories": ["analytics", "cookies"]}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/privacy/data-requests — error paths (Issue #608)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubmitDataRequestErrors:
    def test_invalid_request_type_returns_400(self):
        """Unknown request_type should be rejected with 400."""
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "hacking"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 400

    def test_invalid_email_returns_400(self):
        """Malformed email address should return 400."""
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "email": "not-an-email"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 400

    def test_empty_email_returns_400(self):
        """Empty email should fail validation."""
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "email": ""}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 400

    def test_missing_email_returns_422(self):
        """email is a required field — Pydantic returns 422 when absent."""
        client = TestClient(_build_app())
        payload = {"request_type": "access"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 422

    def test_missing_request_type_returns_422(self):
        """request_type is required — Pydantic returns 422 when absent."""
        client = TestClient(_build_app())
        payload = {"email": "test@example.com"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        assert resp.status_code == 422

    def test_empty_body_returns_422(self):
        """Empty JSON body should fail Pydantic validation."""
        client = TestClient(_build_app())
        resp = client.post("/api/privacy/data-requests", json={})
        assert resp.status_code == 422

    def test_error_response_does_not_leak_stacktrace(self):
        """400 error body must not contain any stack-trace details."""
        client = TestClient(_build_app())
        payload = {**VALID_SUBMIT_PAYLOAD, "request_type": "injection"}
        resp = client.post("/api/privacy/data-requests", json=payload)
        body = resp.text.lower()
        assert "traceback" not in body
        assert "file " not in body  # Python traceback lines start with "File"

    def test_unauthenticated_post_accepted(self):
        """These routes are public — no auth header needed."""
        client = TestClient(_build_app())
        resp = client.post(
            "/api/privacy/data-requests",
            json=VALID_SUBMIT_PAYLOAD,
            # Deliberately no Authorization header
        )
        assert resp.status_code == 200

    def test_malformed_json_returns_422(self):
        """Sending non-JSON body should return 422."""
        client = TestClient(_build_app())
        resp = client.post(
            "/api/privacy/data-requests",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/privacy/gdpr-rights (static — no DB/service)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetGdprRights:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/privacy/gdpr-rights")
        assert resp.status_code == 200

    def test_response_has_rights(self):
        client = TestClient(_build_app())
        data = client.get("/api/privacy/gdpr-rights").json()
        assert "rights" in data
        assert "access" in data["rights"]
        assert "erasure" in data["rights"]

    def test_response_has_contact_email(self):
        client = TestClient(_build_app())
        data = client.get("/api/privacy/gdpr-rights").json()
        assert "contact" in data
        assert "@" in data["contact"]

    def test_response_has_applicable_articles(self):
        client = TestClient(_build_app())
        data = client.get("/api/privacy/gdpr-rights").json()
        assert "applicable_articles" in data
        assert isinstance(data["applicable_articles"], list)

    def test_unauthenticated_access_returns_200(self):
        """Static endpoint is public — no auth needed."""
        client = TestClient(_build_app())
        resp = client.get("/api/privacy/gdpr-rights")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/privacy/data-processing (static — no DB/service)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDataProcessingInfo:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/privacy/data-processing")
        assert resp.status_code == 200

    def test_response_has_legal_bases(self):
        client = TestClient(_build_app())
        data = client.get("/api/privacy/data-processing").json()
        assert "legal_bases" in data
        assert "consent" in data["legal_bases"]

    def test_response_has_data_categories(self):
        client = TestClient(_build_app())
        data = client.get("/api/privacy/data-processing").json()
        assert "data_categories" in data

    def test_response_has_processors(self):
        client = TestClient(_build_app())
        data = client.get("/api/privacy/data-processing").json()
        assert "processors" in data
        assert isinstance(data["processors"], list)

    def test_unauthenticated_access_returns_200(self):
        """Static endpoint is public — no auth needed."""
        client = TestClient(_build_app())
        resp = client.get("/api/privacy/data-processing")
        assert resp.status_code == 200
