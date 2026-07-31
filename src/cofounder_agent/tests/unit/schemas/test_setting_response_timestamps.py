"""Timestamp-provenance contract for the settings response (poindexter#954).

`GET /api/settings/{key}` reported a fabricated `updated_at` — the current time
on every call instead of the stored column. The visible defect was a
`or datetime.now(timezone.utc)` fallback in `routes/settings_routes.py`, but it
only ever fired because of a name collision one layer down:

- `schemas.database_response_models.SettingResponse` — DB layer, built by
  `ModelConverter.to_setting_response`
- `schemas.settings_schemas.SettingResponse` — API layer, the HTTP body

The DB-layer model declared `modified_at` (the abandoned `settings` table's
name) but not `updated_at` (the live `app_settings` column). The converter
renamed the real value into the alias, and Pydantic's default `extra="ignore"`
dropped the original — so the route read `None` and stamped `now()`.

These tests lock the seam at the layer where the value was actually lost, so a
future edit to either model or to the converter's alias handling fails here
rather than silently resuming the fabrication.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemas.database_response_models import SettingResponse as DbSettingResponse
from schemas.model_converter import ModelConverter
from schemas.settings_schemas import SettingResponse as ApiSettingResponse

pytestmark = pytest.mark.unit

STORED_AT = datetime(2026, 6, 19, 23, 49, 20, tzinfo=timezone.utc)

# Exactly the columns `admin_db._APP_SETTINGS_COLUMNS` selects. Keep this in
# sync with that constant — the bug lived in the gap between what the SELECT
# returned and what the model declared.
APP_SETTINGS_ROW = {
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


class TestDbModelDeclaresUpdatedAt:
    """The DB-layer model must declare every column the SELECT returns."""

    def test_updated_at_is_declared(self):
        """The regression itself: a missing field is dropped, not rejected."""
        assert "updated_at" in DbSettingResponse.model_fields

    def test_modified_at_alias_retained(self):
        """Legacy alias stays for back-compat with the old `settings` schema."""
        assert "modified_at" in DbSettingResponse.model_fields

    @pytest.mark.parametrize("field", ["created_at", "updated_at", "modified_at"])
    def test_timestamps_are_nullable(self, field):
        """`app_settings` timestamps are `timestamptz DEFAULT now()` with no
        NOT NULL. Declaring them required made a NULL row raise inside
        `admin_db.get_setting`, which swallows the error and returns None —
        surfacing as a 404 on a setting that exists."""
        assert not DbSettingResponse.model_fields[field].is_required()


class TestConverterPreservesStoredTimestamp:
    def test_updated_at_survives_conversion(self):
        """The core regression: the stored value must reach the caller."""
        converted = ModelConverter.to_setting_response(APP_SETTINGS_ROW)
        assert converted.updated_at == STORED_AT

    def test_created_at_survives_conversion(self):
        converted = ModelConverter.to_setting_response(APP_SETTINGS_ROW)
        assert converted.created_at == STORED_AT

    def test_modified_at_backfilled_from_updated_at(self):
        """Current `app_settings` row → legacy alias populated."""
        converted = ModelConverter.to_setting_response(APP_SETTINGS_ROW)
        assert converted.modified_at == STORED_AT

    def test_updated_at_backfilled_from_legacy_modified_at(self):
        """Legacy `settings`-shaped row → canonical field populated."""
        legacy_row = {k: v for k, v in APP_SETTINGS_ROW.items() if k != "updated_at"}
        legacy_row["modified_at"] = STORED_AT
        converted = ModelConverter.to_setting_response(legacy_row)
        assert converted.updated_at == STORED_AT

    def test_null_timestamps_do_not_raise(self):
        """A NULL timestamp is legal per the DDL — convert, don't explode."""
        null_row = {**APP_SETTINGS_ROW, "created_at": None, "updated_at": None}
        converted = ModelConverter.to_setting_response(null_row)
        assert converted.updated_at is None
        assert converted.created_at is None


class TestApiModelAcceptsNull:
    """The API contract reports an absent timestamp as null, never as now()."""

    @pytest.mark.parametrize("field", ["created_at", "updated_at"])
    def test_timestamps_are_nullable(self, field):
        assert not ApiSettingResponse.model_fields[field].is_required()

    def test_constructs_with_null_timestamps(self):
        resp = ApiSettingResponse(
            id=1,
            key="image_render_timeout_seconds",
            value="600",
            category="media",
            created_at=None,
            updated_at=None,
            created_by_id=1,
        )
        assert resp.updated_at is None
