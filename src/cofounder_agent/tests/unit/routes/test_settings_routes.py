"""
Unit tests for routes/settings_routes.py.

Tests cover:
- GET    /api/settings              — list_settings
- GET    /api/settings/{id}         — get_setting
- POST   /api/settings              — create_setting
- PUT    /api/settings/{id}        — update_setting
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.settings_routes import router
from utils.route_utils import get_database_dependency

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
    app.dependency_overrides[verify_api_token] = lambda: "test-token"

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

    def test_secret_value_is_masked(self):
        """#642 — secret values (and enc: ciphertext) must not round-trip
        through the read API; both value and value_preview are masked."""
        secret = {
            **SETTING_DICT,
            "key": "openai_api_key",
            "value": "enc:v1:c2VjcmV0Y2lwaGVydGV4dA==",
            "is_secret": True,
        }
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=[secret])
        item = TestClient(_build_app(mock_db)).get("/api/settings").json()["items"][0]
        assert item["value"] == "********"
        assert item["value_preview"] == "********"

    def test_non_secret_value_not_masked(self):
        """Non-secret settings still expose their value (unchanged)."""
        item = TestClient(_build_app()).get("/api/settings").json()["items"][0]
        assert item["value"] == "debug"

    def test_offset_limit_override_page(self):
        """#635 — offset/limit (the canonical API params) override page/per_page
        and the response reflects the effective window."""
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=[SETTING_DICT] * 10)
        data = TestClient(_build_app(mock_db)).get("/api/settings?offset=2&limit=3").json()
        assert data["total"] == 10
        assert len(data["items"]) == 3
        assert data["per_page"] == 3
        assert data["pages"] == 4

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
        """Without dependency override, verify_api_token checks Bearer token."""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_settings_db()
        # No auth override — let the real verify_api_token run
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/settings")
        # No Authorization header → 401
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self):
        """verify_api_token rejects invalid tokens."""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_settings_db()
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
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
# PUT /api/settings/{setting_id}  (update_setting)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateSetting:
    def test_existing_key_returns_200(self):
        """Update existing setting by key name."""
        client = TestClient(_build_app())
        resp = client.put(
            "/api/settings/log_level",
            json={"value": "new_value"},
        )
        assert resp.status_code == 200

    def test_response_has_key_field(self):
        client = TestClient(_build_app())
        data = client.put("/api/settings/log_level", json={"value": "x"}).json()
        assert "key" in data

    def test_missing_key_returns_404(self):
        """Non-existent setting key returns 404."""
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/settings/nonexistent_key", json={"value": "x"})
        assert resp.status_code == 404

    def test_value_reflected_in_response(self):
        mock_db = _make_settings_db()
        # After update, get_setting returns the updated value
        updated_setting = {**SETTING_DICT, "value": "my_value"}
        mock_db.get_setting = AsyncMock(side_effect=[SETTING_DICT, updated_setting])
        client = TestClient(_build_app(mock_db))
        data = client.put("/api/settings/log_level", json={"value": "my_value"}).json()
        assert data["value"] == "my_value"

    def test_set_setting_called_with_correct_key(self):
        mock_db = _make_settings_db()
        client = TestClient(_build_app(mock_db))
        client.put("/api/settings/log_level", json={"value": "info"})
        mock_db.set_setting.assert_awaited_once()
        call_kwargs = mock_db.set_setting.call_args
        assert (
            call_kwargs.kwargs.get("key") == "log_level" or call_kwargs[1].get("key") == "log_level"
        )

    def test_db_failure_returns_500(self):
        mock_db = _make_settings_db()
        mock_db.set_setting = AsyncMock(return_value=False)
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/settings/log_level", json={"value": "x"})
        assert resp.status_code == 500

    def test_empty_string_value_is_accepted_not_silently_dropped(self):
        """'' is the system unset sentinel — PUT {"value": ""} must persist
        the empty string, not silently fall back to the existing value.

        Regression for poindexter#751: the old `if update_data.value` falsy
        check treated "" the same as None/missing and replaced it with the
        existing value, making 'clear this setting' a silent no-op.
        """
        mock_db = _make_settings_db()
        updated_setting = {**SETTING_DICT, "value": ""}
        # First call: get existing; second call: get after update
        mock_db.get_setting = AsyncMock(side_effect=[SETTING_DICT, updated_setting])
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/settings/log_level", json={"value": ""})
        assert resp.status_code == 200
        # The response must carry back the empty string, not the old "debug"
        assert resp.json()["value"] == ""
        # set_setting must have been called with value="" (not the old "debug")
        call_kwargs = mock_db.set_setting.call_args
        assert call_kwargs.kwargs.get("value") == "" or call_kwargs[0][1] == ""

    def test_empty_string_description_is_accepted(self):
        """Same falsy-check bug applied to description: '' should clear it."""
        mock_db = _make_settings_db()
        updated_setting = {**SETTING_DICT, "description": ""}
        mock_db.get_setting = AsyncMock(side_effect=[SETTING_DICT, updated_setting])
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/settings/log_level", json={"description": ""})
        assert resp.status_code == 200
        call_kwargs = mock_db.set_setting.call_args
        # Use explicit key lookup — avoid `or` which would treat "" as falsy
        assert call_kwargs.kwargs["description"] == ""

    def test_none_value_falls_back_to_existing(self):
        """Omitting 'value' from the payload (None default) should still fall
        back to the existing value — the is-not-None fix must not break this."""
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=SETTING_DICT)
        client = TestClient(_build_app(mock_db))
        resp = client.put("/api/settings/log_level", json={"description": "updated desc"})
        assert resp.status_code == 200
        call_kwargs = mock_db.set_setting.call_args
        # value should be the existing "debug", not empty/None
        assert call_kwargs.kwargs.get("value") == "debug" or (
            call_kwargs[0] and call_kwargs[0][1] == "debug"
        )
