"""
Unit tests for routes/writing_style_routes.py.

Tests cover:
- GET  /api/writing-style/samples              — list_writing_samples
- GET  /api/writing-style/active               — get_active_writing_sample
- POST /api/writing-style/{id}/activate        — activate_writing_sample
- PUT  /api/writing-style/{id}                 — update_writing_sample
- DELETE /api/writing-style/{id}               — delete_writing_sample

Auth and DB are overridden so no real I/O occurs.
The POST /upload endpoint requires multipart form data and is tested separately
with a focus on validation logic.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from routes.auth_unified import get_current_user
from utils.route_utils import get_database_dependency
from routes.writing_style_routes import router

from tests.unit.routes.conftest import TEST_USER, make_mock_db


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_DICT = {
    "id": "sample-001",
    "user_id": TEST_USER["id"],
    "title": "My Writing Style",
    "description": "How I write",
    "content": "This is my writing style content. It is detailed and technical.",
    "is_active": True,
    "word_count": 12,
    "char_count": 65,
    "metadata": {},
    "created_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-01T10:00:00Z",
}

INACTIVE_SAMPLE_DICT = {**SAMPLE_DICT, "id": "sample-002", "is_active": False}


def _build_app(mock_db=None) -> FastAPI:
    if mock_db is None:
        mock_db = _make_writing_style_db()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


def _make_writing_style_db():
    """Return a DatabaseService mock with writing_style sub-service configured."""
    db = MagicMock()
    ws = MagicMock()

    ws.create_writing_sample = AsyncMock(return_value=SAMPLE_DICT)
    ws.get_user_writing_samples = AsyncMock(return_value=[SAMPLE_DICT])
    ws.get_active_writing_sample = AsyncMock(return_value=SAMPLE_DICT)
    ws.get_writing_sample = AsyncMock(return_value=SAMPLE_DICT)
    ws.set_active_writing_sample = AsyncMock(return_value=SAMPLE_DICT)
    ws.update_writing_sample = AsyncMock(return_value=SAMPLE_DICT)
    ws.delete_writing_sample = AsyncMock(return_value=True)

    db.writing_style = ws
    return db


# ---------------------------------------------------------------------------
# GET /api/writing-style/samples
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWritingSamples:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/writing-style/samples")
        assert resp.status_code == 200

    def test_response_has_standard_envelope(self):
        client = TestClient(_build_app())
        data = client.get("/api/writing-style/samples").json()
        assert "samples" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data

    def test_total_matches_sample_count(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_user_writing_samples = AsyncMock(
            return_value=[SAMPLE_DICT, INACTIVE_SAMPLE_DICT]
        )
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/writing-style/samples").json()
        assert data["total"] == 2

    def test_active_sample_id_is_set(self):
        client = TestClient(_build_app())
        data = client.get("/api/writing-style/samples").json()
        assert data["active_sample_id"] == "sample-001"

    def test_no_active_sample_returns_none_id(self):
        mock_db = _make_writing_style_db()
        inactive = {**SAMPLE_DICT, "is_active": False}
        mock_db.writing_style.get_user_writing_samples = AsyncMock(return_value=[inactive])
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/writing-style/samples").json()
        assert data["active_sample_id"] is None

    def test_default_pagination(self):
        client = TestClient(_build_app())
        data = client.get("/api/writing-style/samples").json()
        assert data["offset"] == 0
        assert data["limit"] == 20  # Default lowered to 20 (project standard, issue #587)

    def test_custom_offset_and_limit(self):
        mock_db = _make_writing_style_db()
        samples = [SAMPLE_DICT, INACTIVE_SAMPLE_DICT]
        mock_db.writing_style.get_user_writing_samples = AsyncMock(return_value=samples)
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/writing-style/samples?offset=1&limit=1").json()
        assert data["offset"] == 1
        assert data["limit"] == 1
        # Should only return 1 sample (the second one)
        assert len(data["samples"]) == 1

    def test_db_error_returns_500(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_user_writing_samples = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/writing-style/samples")
        assert resp.status_code == 500

    def test_empty_sample_list(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_user_writing_samples = AsyncMock(return_value=[])
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/writing-style/samples").json()
        assert data["total"] == 0
        assert data["samples"] == []


# ---------------------------------------------------------------------------
# GET /api/writing-style/active
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetActiveWritingSample:
    def test_returns_200_when_active_sample_exists(self):
        client = TestClient(_build_app())
        resp = client.get("/api/writing-style/active")
        assert resp.status_code == 200

    def test_returns_sample_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/writing-style/active").json()
        assert data["id"] == "sample-001"
        assert data["title"] == "My Writing Style"
        assert data["is_active"] is True

    def test_returns_null_when_no_active_sample(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_active_writing_sample = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/writing-style/active")
        assert resp.status_code == 200
        assert resp.json() is None

    def test_db_error_returns_500(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_active_writing_sample = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/writing-style/active")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/writing-style/{sample_id}/activate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestActivateWritingSample:
    def test_activate_own_sample_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post("/api/writing-style/sample-001/activate")
        assert resp.status_code == 200

    def test_activate_returns_sample_with_is_active_true(self):
        client = TestClient(_build_app())
        data = client.post("/api/writing-style/sample-001/activate").json()
        assert data["is_active"] is True
        assert data["id"] == "sample-001"

    def test_activate_nonexistent_sample_returns_404(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_writing_sample = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))
        resp = client.post("/api/writing-style/nonexistent/activate")
        assert resp.status_code == 404

    def test_activate_other_users_sample_returns_403(self):
        mock_db = _make_writing_style_db()
        other_user_sample = {**SAMPLE_DICT, "user_id": "other-user-id"}
        mock_db.writing_style.get_writing_sample = AsyncMock(return_value=other_user_sample)
        client = TestClient(_build_app(mock_db))
        resp = client.post("/api/writing-style/sample-001/activate")
        assert resp.status_code == 403

    def test_db_error_returns_500(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.set_active_writing_sample = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        client = TestClient(_build_app(mock_db))
        resp = client.post("/api/writing-style/sample-001/activate")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# PUT /api/writing-style/{sample_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateWritingSample:
    UPDATE_PAYLOAD = {
        "title": "Updated Title",
        "description": "Updated description",
        "content": "Updated writing content with more words.",
        "set_as_active": False,
    }

    def test_update_own_sample_returns_200(self):
        client = TestClient(_build_app())
        resp = client.put("/api/writing-style/sample-001", json=self.UPDATE_PAYLOAD)
        assert resp.status_code == 200

    def test_update_returns_sample_data(self):
        client = TestClient(_build_app())
        data = client.put("/api/writing-style/sample-001", json=self.UPDATE_PAYLOAD).json()
        assert "id" in data
        assert "title" in data

    def test_update_nonexistent_sample_returns_404(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_writing_sample = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/writing-style/nonexistent", json=self.UPDATE_PAYLOAD)
        assert resp.status_code == 404

    def test_update_other_users_sample_returns_403(self):
        mock_db = _make_writing_style_db()
        other_user_sample = {**SAMPLE_DICT, "user_id": "other-user-id"}
        mock_db.writing_style.get_writing_sample = AsyncMock(return_value=other_user_sample)
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/writing-style/sample-001", json=self.UPDATE_PAYLOAD)
        assert resp.status_code == 403

    def test_update_requires_title_and_content(self):
        client = TestClient(_build_app())
        resp = client.put("/api/writing-style/sample-001", json={"title": "Only title"})
        # Missing 'content' — should fail validation
        assert resp.status_code == 422

    def test_db_error_returns_500(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.update_writing_sample = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/writing-style/sample-001", json=self.UPDATE_PAYLOAD)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /api/writing-style/{sample_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteWritingSample:
    def test_delete_own_sample_returns_204(self):
        client = TestClient(_build_app())
        resp = client.delete("/api/writing-style/sample-001")
        assert resp.status_code == 204

    def test_delete_nonexistent_sample_returns_404(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.get_writing_sample = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))
        resp = client.delete("/api/writing-style/nonexistent")
        assert resp.status_code == 404

    def test_delete_other_users_sample_returns_403(self):
        mock_db = _make_writing_style_db()
        other_user_sample = {**SAMPLE_DICT, "user_id": "other-user-id"}
        mock_db.writing_style.get_writing_sample = AsyncMock(return_value=other_user_sample)
        client = TestClient(_build_app(mock_db))
        resp = client.delete("/api/writing-style/sample-001")
        assert resp.status_code == 403

    def test_delete_db_returns_false_gives_404(self):
        """If delete_writing_sample returns False, that's a 404."""
        mock_db = _make_writing_style_db()
        mock_db.writing_style.delete_writing_sample = AsyncMock(return_value=False)
        client = TestClient(_build_app(mock_db))
        resp = client.delete("/api/writing-style/sample-001")
        assert resp.status_code == 404

    def test_db_error_returns_500(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.delete_writing_sample = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        client = TestClient(_build_app(mock_db))
        resp = client.delete("/api/writing-style/sample-001")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/writing-style/upload (form-based)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUploadWritingSample:
    def test_upload_with_content_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/writing-style/upload",
            data={
                "title": "Test Sample",
                "content": "This is my writing style with some words.",
                "set_as_active": "false",
            },
        )
        assert resp.status_code == 200

    def test_upload_returns_sample_data(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/writing-style/upload",
            data={
                "title": "Test Sample",
                "content": "My writing style content.",
            },
        ).json()
        assert "id" in data
        assert "title" in data

    def test_upload_without_content_returns_400(self):
        """Content is required; omitting it should return 400."""
        client = TestClient(_build_app())
        resp = client.post(
            "/api/writing-style/upload",
            data={
                "title": "Test Sample",
                # No 'content', no 'file'
            },
        )
        assert resp.status_code == 400

    def test_upload_whitespace_only_content_returns_400(self):
        """Content that is only whitespace should be rejected."""
        client = TestClient(_build_app())
        resp = client.post(
            "/api/writing-style/upload",
            data={
                "title": "Test Sample",
                "content": "   \n\t  ",
            },
        )
        assert resp.status_code == 400

    def test_db_error_returns_500(self):
        mock_db = _make_writing_style_db()
        mock_db.writing_style.create_writing_sample = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        client = TestClient(_build_app(mock_db))
        resp = client.post(
            "/api/writing-style/upload",
            data={
                "title": "Test Sample",
                "content": "Some writing content here.",
            },
        )
        assert resp.status_code == 500
