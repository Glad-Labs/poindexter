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
from schemas.model_converter import ModelConverter
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
        assert "limit" in data
        assert "offset" in data

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
        assert data["limit"] == 20
        assert data["offset"] == 0

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
        assert data["limit"] == 3
        assert data["offset"] == 2

    def test_custom_limit_via_query(self):
        # ``per_page`` stays accepted as a legacy REQUEST param (#635); the
        # RESPONSE now echoes the canonical ``limit`` (poindexter#745).
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=[SETTING_DICT] * 10)
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/settings?per_page=5").json()
        assert data["limit"] == 5

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


def _row(key: str, description: str = "", **extra) -> dict:
    """A dict-shaped settings row (the shape admin_db rows expose)."""
    return {**SETTING_DICT, "key": key, "description": description, **extra}


@pytest.mark.unit
class TestListSettingsFilters:
    """search/environment/tags were declared but never applied (dead Query
    params) — ?search=X silently returned page 1 of the full alphabetical
    list, breaking the console's needle-in-haystack lookups
    (PX.api.voiceJoinUrl / PX.api.electricityRateKwh in console/js/api.js)."""

    def _client(self, rows):
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=rows)
        return TestClient(_build_app(mock_db))

    # ── search ──────────────────────────────────────────────────────────

    def test_search_filters_by_key_substring(self):
        rows = [_row("log_level"), _row("voice_agent_public_join_url")]
        data = self._client(rows).get("/api/settings?search=voice").json()
        assert data["total"] == 1
        assert data["items"][0]["key"] == "voice_agent_public_join_url"

    def test_search_matches_description_case_insensitive(self):
        rows = [_row("log_level", "Log VERBOSITY level"), _row("other_key", "unrelated")]
        data = self._client(rows).get("/api/settings?search=verbosity").json()
        assert data["total"] == 1
        assert data["items"][0]["key"] == "log_level"

    def test_search_miss_returns_empty(self):
        rows = [_row("log_level"), _row("other_key")]
        data = self._client(rows).get("/api/settings?search=zz_no_such_needle").json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_search_total_is_filtered_count_and_paginates(self):
        """`total` must be the FILTERED count; offset/limit slice the
        filtered list, not the raw one."""
        rows = [_row(f"aaa_filler_{i:03d}") for i in range(20)]
        rows += [_row(f"zzz_match_{i}") for i in range(3)]
        client = self._client(rows)

        page1 = client.get("/api/settings?search=zzz_match&limit=2").json()
        assert page1["total"] == 3
        assert [s["key"] for s in page1["items"]] == ["zzz_match_0", "zzz_match_1"]

        page2 = client.get("/api/settings?search=zzz_match&limit=2&offset=2").json()
        assert page2["total"] == 3
        assert [s["key"] for s in page2["items"]] == ["zzz_match_2"]

    def test_console_voice_join_url_lookup_hits_first_page(self):
        """Mirror of PX.api.voiceJoinUrl(): the key sorts far past the first
        alphabetical page, so the lookup only works if search is applied."""
        rows = [_row(f"aaa_setting_{i:03d}") for i in range(30)]
        rows.append(_row("voice_agent_public_join_url", "Tap-to-join voice URL"))
        data = (
            self._client(rows)
            .get("/api/settings?search=voice_agent_public_join_url&limit=10")
            .json()
        )
        assert any(s["key"] == "voice_agent_public_join_url" for s in data["items"])

    def test_console_electricity_rate_lookup_returns_value(self):
        """Mirror of PX.api.electricityRateKwh(): the matching row (and its
        value) must land in the first page."""
        rows = [_row(f"aaa_setting_{i:03d}") for i in range(30)]
        rows.append(_row("electricity_rate_kwh", value="0.2579"))
        data = (
            self._client(rows)
            .get("/api/settings?search=electricity_rate_kwh&limit=10")
            .json()
        )
        hits = [s for s in data["items"] if s["key"] == "electricity_rate_kwh"]
        assert hits and hits[0]["value"] == "0.2579"

    # ── environment ─────────────────────────────────────────────────────

    def test_environment_filter_excludes_nonmatching(self):
        """Rows without an environment column are effectively production —
        same default the response serializer renders."""
        rows = [_row("log_level"), _row("other_key")]
        client = self._client(rows)
        assert client.get("/api/settings?environment=development").json()["total"] == 0
        assert client.get("/api/settings?environment=production").json()["total"] == 2

    def test_environment_all_matches_from_either_side(self):
        rows = [_row("log_level"), _row("dev_only", environment="development")]
        client = self._client(rows)
        # Filter value "all" → no narrowing.
        assert client.get("/api/settings?environment=all").json()["total"] == 2
        # Row value "all" → matches any requested environment.
        rows_all = [_row("everywhere", environment="all")]
        assert (
            self._client(rows_all).get("/api/settings?environment=development").json()["total"]
            == 1
        )

    def test_environment_explicit_row_value_matches(self):
        rows = [_row("log_level"), _row("dev_only", environment="development")]
        data = self._client(rows).get("/api/settings?environment=development").json()
        assert [s["key"] for s in data["items"]] == ["dev_only"]

    def test_environment_invalid_value_is_422(self):
        assert (
            self._client([_row("log_level")]).get("/api/settings?environment=bogus").status_code
            == 422
        )

    # ── tags ────────────────────────────────────────────────────────────

    def test_tags_filter_requires_all_requested_tags(self):
        rows = [
            _row("voice_url", tags=["voice", "infra"]),
            _row("voice_flag", tags=["voice"]),
            _row("untagged"),
        ]
        client = self._client(rows)
        assert client.get("/api/settings?tags=voice").json()["total"] == 2
        data = client.get("/api/settings?tags=voice,infra").json()
        assert [s["key"] for s in data["items"]] == ["voice_url"]

    def test_tags_filter_case_insensitive_and_trims(self):
        rows = [_row("voice_url", tags=["Voice", "Infra"]), _row("untagged")]
        data = self._client(rows).get("/api/settings?tags=%20voice%20,INFRA").json()
        assert data["total"] == 1
        assert data["items"][0]["key"] == "voice_url"

    # ── composition ─────────────────────────────────────────────────────

    def test_search_and_tags_compose(self):
        rows = [
            _row("voice_agent_public_join_url", tags=["voice"]),
            _row("voice_agent_model", tags=["models"]),
            _row("log_level", tags=["voice"]),
        ]
        data = self._client(rows).get("/api/settings?search=voice_agent&tags=voice").json()
        assert data["total"] == 1
        assert data["items"][0]["key"] == "voice_agent_public_join_url"


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
        "category": "observability",
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

    def test_create_noncanonical_category_returns_400(self):
        """An explicit off-taxonomy category is a client error. Validated
        against services.settings_categories.CATEGORY_IDS before the
        existence check, so it 400s even for a brand-new key."""
        client = TestClient(_build_app())
        resp = client.post(
            "/api/settings",
            json={"key": "new_setting_key", "value": "v", "category": "bogus_bucket"},
        )
        assert resp.status_code == 400
        assert "bogus_bucket" in resp.json()["detail"]

    def test_create_omitted_category_defers_to_resolver(self):
        """Omitting category is fine — admin_db.set_setting resolves it from
        the key via resolve_category(), so the create still succeeds (201)."""
        client = TestClient(_build_app())
        resp = client.post(
            "/api/settings", json={"key": "new_setting_key", "value": "v"}
        )
        assert resp.status_code == 201

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


# ---------------------------------------------------------------------------
# Timestamp provenance (poindexter#954)
# ---------------------------------------------------------------------------

STORED_AT = datetime(2026, 6, 19, 23, 49, 20, tzinfo=timezone.utc)


def _real_setting_model(**overrides):
    """Build the object the DB layer ACTUALLY returns.

    The fixtures above hand the routes a plain `dict`, which `_setting_attr`
    reads via `.get()` — so a dict passes `updated_at` straight through and the
    fabrication path never runs. That fidelity gap is why #954 shipped: in
    production `db_service.get_setting` returns a Pydantic model from
    `ModelConverter.to_setting_response`, which was silently dropping the
    field. These tests must go through the real converter, not a dict.
    """
    row = {
        "id": 42,
        "key": "image_render_timeout_seconds",
        "value": "600",
        "category": "media",
        "description": "Render timeout",
        "is_secret": False,
        "is_active": True,
        "created_at": STORED_AT,
        "updated_at": STORED_AT,
    }
    row.update(overrides)
    return ModelConverter.to_setting_response(row)


@pytest.mark.unit
class TestTimestampProvenance:
    """`updated_at` must report the stored column, never the current time.

    This is the field an operator uses to tell a deliberate override from
    stale seed drift, so a fabricated value is worse than an absent one.
    """

    def test_get_returns_stored_updated_at(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=_real_setting_model())
        client = TestClient(_build_app(mock_db))

        body = client.get("/api/settings/image_render_timeout_seconds").json()

        assert body["updated_at"] is not None
        assert datetime.fromisoformat(body["updated_at"]) == STORED_AT

    def test_get_updated_at_is_not_now(self):
        """The original symptom: two calls returned two different times."""
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=_real_setting_model())
        client = TestClient(_build_app(mock_db))

        first = client.get("/api/settings/image_render_timeout_seconds").json()
        second = client.get("/api/settings/image_render_timeout_seconds").json()

        assert first["updated_at"] == second["updated_at"]
        now = datetime.now(timezone.utc)
        assert abs((datetime.fromisoformat(first["updated_at"]) - now).days) > 1

    def test_get_returns_stored_created_at(self):
        """Same four sites fabricated `created_at`; it just happened to be
        masked because the DB model did declare that field."""
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=_real_setting_model())
        client = TestClient(_build_app(mock_db))

        body = client.get("/api/settings/image_render_timeout_seconds").json()

        assert datetime.fromisoformat(body["created_at"]) == STORED_AT

    def test_list_returns_stored_updated_at(self):
        mock_db = _make_settings_db()
        mock_db.get_all_settings = AsyncMock(return_value=[_real_setting_model()])
        client = TestClient(_build_app(mock_db))

        item = client.get("/api/settings").json()["items"][0]

        assert datetime.fromisoformat(item["updated_at"]) == STORED_AT

    def test_update_returns_stored_updated_at(self):
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(return_value=_real_setting_model())
        client = TestClient(_build_app(mock_db))

        resp = client.put(
            "/api/settings/image_render_timeout_seconds", json={"value": "900"}
        )

        assert resp.status_code == 200
        assert datetime.fromisoformat(resp.json()["updated_at"]) == STORED_AT

    def test_create_returns_stored_updated_at(self):
        mock_db = _make_settings_db()
        mock_db.setting_exists = AsyncMock(return_value=False)
        mock_db.get_setting = AsyncMock(return_value=_real_setting_model())
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/settings",
            json={"key": "image_render_timeout_seconds", "value": "600"},
        )

        assert resp.status_code == 201
        assert datetime.fromisoformat(resp.json()["updated_at"]) == STORED_AT

    def test_null_timestamp_reported_as_null(self):
        """A genuinely absent timestamp is reported as null — not backfilled
        with now(), which would read as 'just changed'."""
        mock_db = _make_settings_db()
        mock_db.get_setting = AsyncMock(
            return_value=_real_setting_model(created_at=None, updated_at=None)
        )
        client = TestClient(_build_app(mock_db))

        body = client.get("/api/settings/image_render_timeout_seconds").json()

        assert body["updated_at"] is None
        assert body["created_at"] is None
