"""
Unit tests for routes/settings_routes.py.

Tests cover:
- GET    /api/settings              — list_settings
- GET    /api/settings/{id}         — get_setting
- POST   /api/settings              — create_setting
- PATCH  /api/settings             — batch_update_settings
- DELETE /api/settings             — batch_delete_settings
- PUT    /api/settings/{id}        — update_setting
- DELETE /api/settings/{id}        — delete_setting (single)
- GET    /api/settings/{id}/history — get_setting_history
- POST   /api/settings/{id}/rollback — rollback_setting
- POST   /api/settings/bulk/update  — bulk_update_settings
- GET    /api/settings/export/all   — export_settings

Auth uses the local get_current_user in settings_routes (not the shared one)
which validates Bearer token format. The DB dependency is overridden via
the shared get_database_dependency.
"""

import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from utils.route_utils import get_database_dependency
import routes.settings_routes as settings_module
from routes.settings_routes import router

from tests.unit.routes.conftest import make_mock_db


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SETTING_DICT = {
    "id": 1,
    "key": "log_level",
    "value": "debug",
    "description": "Log verbosity level",
    "category": "logging",
    "created_at": datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
    "updated_at": datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
}

VALID_AUTH = "Bearer valid-test-token"
INVALID_AUTH_BLANK = "Bearer "
INVALID_AUTH_KEYWORD = "Bearer invalid"


def _build_app(mock_db=None) -> FastAPI:
    if mock_db is None:
        mock_db = _make_settings_db()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_database_dependency] = lambda: mock_db
    # Note: settings_routes uses its OWN local get_current_user, not the shared one.
    # We override via the module-level function reference.
    app.dependency_overrides[settings_module.get_current_user] = lambda: {
        "user_id": "test-user",
        "email": "test@example.com",
        "role": "admin",
    }

    return app


def _make_settings_db():
    """Return a DB mock with settings methods configured."""
    db = MagicMock()
    db.get_all_settings = AsyncMock(return_value=[SETTING_DICT])
    db.get_setting = AsyncMock(return_value=SETTING_DICT)
    db.setting_exists = AsyncMock(return_value=False)
    db.set_setting = AsyncMock(return_value=True)
    db.delete_setting = AsyncMock(return_value=True)
    return db


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListSettings:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings")
        assert resp.status_code == 200

    def test_response_has_list_envelope(self):
        client = TestClient(_build_app())
        data = client.get("/api/settings").json()
        assert "total" in data
        assert "items" in data
        assert "page" in data
        assert "per_page" in data
        assert "pages" in data

    def test_total_matches_db_count(self):
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=[SETTING_DICT, SETTING_DICT])
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/settings").json()
        assert data["total"] == 2

    def test_empty_settings_list(self):
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=[])
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/settings").json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_default_pagination(self):
        client = TestClient(_build_app())
        data = client.get("/api/settings").json()
        assert data["per_page"] == 20
        assert data["page"] == 1

    def test_custom_limit_via_query(self):
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=[SETTING_DICT] * 10)
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/settings?per_page=5").json()
        assert data["per_page"] == 5

    def test_db_error_returns_500(self):
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/settings")
        assert resp.status_code == 500

    def test_auth_required_when_no_override(self):
        """Without dependency override, local auth validates Bearer token format."""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_settings_db()
        # Do NOT override get_current_user — let it run with no token

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/settings")
        # No Authorization header → 401
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self):
        """The local auth function rejects 'invalid' and other keyword tokens."""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_settings_db()

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/settings", headers={"Authorization": INVALID_AUTH_KEYWORD})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/settings/{setting_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSetting:
    def test_found_setting_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings/log_level")
        assert resp.status_code == 200

    def test_found_setting_has_key(self):
        client = TestClient(_build_app())
        data = client.get("/api/settings/log_level").json()
        assert data["key"] == "log_level"

    def test_missing_setting_returns_404(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/settings/nonexistent_key")
        assert resp.status_code == 404

    def test_db_error_returns_500(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/settings/some_key")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSetting:
    VALID_PAYLOAD = {
        "key": "new_setting_key",
        "value": "new_value",
        "category": "logging",
        "description": "A new test setting",
    }

    def test_create_new_setting_returns_201(self):
        client = TestClient(_build_app())
        resp = client.post("/api/settings", json=self.VALID_PAYLOAD)
        assert resp.status_code == 201

    def test_create_returns_setting_with_key(self):
        client = TestClient(_build_app())
        data = client.post("/api/settings", json=self.VALID_PAYLOAD).json()
        assert "key" in data
        assert "value" in data

    def test_create_duplicate_key_returns_409(self):
        mock_db = _make_settings_db()
        mock_db.setting_exists = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))
        resp = client.post("/api/settings", json=self.VALID_PAYLOAD)
        assert resp.status_code == 409

    def test_create_without_key_returns_400(self):
        client = TestClient(_build_app())
        resp = client.post("/api/settings", json={"value": "some_value"})
        # No key → 400
        assert resp.status_code == 400

    def test_db_set_failure_returns_500(self):
        mock_db = _make_settings_db()
        mock_db.setting_exists = AsyncMock(return_value=False)
        mock_db.set_setting = AsyncMock(return_value=False)  # Failure
        client = TestClient(_build_app(mock_db))
        resp = client.post("/api/settings", json=self.VALID_PAYLOAD)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# PATCH /api/settings (batch update)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchUpdateSettings:
    def test_patch_returns_200(self):
        client = TestClient(_build_app())
        resp = client.patch("/api/settings", json={"value": "updated_value"})
        assert resp.status_code == 200

    def test_patch_returns_setting_response(self):
        client = TestClient(_build_app())
        data = client.patch("/api/settings", json={"value": "updated_value"}).json()
        assert "key" in data
        assert "value" in data


# ---------------------------------------------------------------------------
# DELETE /api/settings (batch delete)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchDeleteSettings:
    def test_delete_returns_204(self):
        client = TestClient(_build_app())
        resp = client.delete("/api/settings")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# PUT /api/settings/{setting_id}  (update_setting)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateSetting:
    def test_valid_id_returns_200(self):
        """Mock implementation treats IDs 1-10 as existing."""
        client = TestClient(_build_app())
        resp = client.patch(
            "/api/settings/5",
            json={"value": "new_value"},
        )
        assert resp.status_code == 200

    def test_response_has_key_field(self):
        client = TestClient(_build_app())
        data = client.patch("/api/settings/3", json={"value": "x"}).json()
        assert "key" in data

    def test_out_of_range_id_returns_404(self):
        """Mock implementation returns 404 for IDs > 10."""
        client = TestClient(_build_app())
        resp = client.patch("/api/settings/99", json={"value": "x"})
        assert resp.status_code == 404

    def test_value_reflected_in_response(self):
        client = TestClient(_build_app())
        data = client.patch("/api/settings/2", json={"value": "my_value"}).json()
        assert data["value"] == "my_value"

    def test_zero_id_returns_422(self):
        """Path param has gt=0 constraint."""
        client = TestClient(_build_app())
        resp = client.patch("/api/settings/0", json={"value": "x"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/settings/{setting_id}  (delete_setting single)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteSingleSetting:
    def test_returns_204_on_success(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=SETTING_DICT)
        mock_db.delete_setting = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))
        resp = client.delete("/api/settings/log_level")
        assert resp.status_code == 204

    def test_returns_404_when_not_found(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))
        resp = client.delete("/api/settings/missing_key")
        assert resp.status_code == 404

    def test_delete_service_called_on_success(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=SETTING_DICT)
        mock_db.delete_setting = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))
        client.delete("/api/settings/log_level")
        mock_db.delete_setting.assert_awaited_once()

    def test_db_failure_returns_500(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=SETTING_DICT)
        mock_db.delete_setting = AsyncMock(return_value=False)
        client = TestClient(_build_app(mock_db))
        resp = client.delete("/api/settings/log_level")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/settings/{setting_id}/history  (get_setting_history)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSettingHistory:
    def test_valid_id_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings/1/history")
        assert resp.status_code == 200

    def test_response_is_list(self):
        client = TestClient(_build_app())
        data = client.get("/api/settings/5/history").json()
        assert isinstance(data, list)

    def test_out_of_range_id_returns_404(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings/99/history")
        assert resp.status_code == 404

    def test_zero_id_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings/0/history")
        assert resp.status_code == 422

    def test_limit_param_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings/1/history?limit=10")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/settings/{setting_id}/rollback  (rollback_setting)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRollbackSetting:
    def test_valid_ids_return_200(self):
        client = TestClient(_build_app())
        resp = client.post("/api/settings/5/rollback?history_id=1")
        assert resp.status_code == 200

    def test_response_contains_rollback_in_value(self):
        client = TestClient(_build_app())
        data = client.post("/api/settings/3/rollback?history_id=7").json()
        assert "rolled_back_value_7" in data["value"]

    def test_out_of_range_setting_returns_404(self):
        client = TestClient(_build_app())
        resp = client.post("/api/settings/99/rollback?history_id=1")
        assert resp.status_code == 404

    def test_zero_setting_id_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/settings/0/rollback?history_id=1")
        assert resp.status_code == 422

    def test_missing_history_id_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/settings/5/rollback")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/settings/bulk/update  (bulk_update_settings)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBulkUpdateSettings:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/settings/bulk/update",
            json={"updates": [{"setting_id": 1, "value": "60"}]},
        )
        assert resp.status_code == 200

    def test_response_has_success_true(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/settings/bulk/update",
            json={"updates": [{"setting_id": 1, "value": "true"}]},
        ).json()
        assert data["success"] is True

    def test_updated_count_matches_payload_length(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/settings/bulk/update",
            json={"updates": [
                {"setting_id": 1, "value": "a"},
                {"setting_id": 2, "value": "b"},
            ]},
        ).json()
        assert data["updated_count"] == 2

    def test_missing_updates_field_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/settings/bulk/update", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/settings/export/all  (export_settings)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExportSettings:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings/export/all")
        assert resp.status_code == 200

    def test_response_has_success_true(self):
        client = TestClient(_build_app())
        data = client.get("/api/settings/export/all").json()
        assert data["success"] is True

    def test_format_param_echoed(self):
        client = TestClient(_build_app())
        data = client.get("/api/settings/export/all?format=yaml").json()
        assert data["format"] == "yaml"

    def test_invalid_format_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/settings/export/all?format=xml")
        assert resp.status_code == 422

    def test_include_secrets_echoed(self):
        client = TestClient(_build_app())
        data = client.get("/api/settings/export/all?include_secrets=true").json()
        assert data["include_secrets"] is True
