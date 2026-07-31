"""Provenance contract for the settings response (poindexter#954, #955).

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

The same file also pins the *authorship* half of the contract (poindexter#955).
`created_by_id` / `updated_by_id` reported a hardcoded user ID 1 as the author
of every setting: `app_settings` has no such column, the SELECT never fetched
one, and the DB-layer model never declared one, so the route's
`_setting_attr(..., 1)` default fired on 100% of responses. Both fields are
removed — not nulled — because unlike the timestamps there is no column behind
them and no way to add one (`users.id` is a uuid; these were `int`).
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
    # Lifecycle metadata (#756), added to the SELECT by poindexter#956.
    "owner": "image_pipeline",
    "value_type": "integer",
    "deprecated": False,
    "superseded_by": None,
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
        )
        assert resp.updated_at is None


class TestNoFabricatedAuthorship:
    """No `created_by_id` / `updated_by_id` in the settings contract (#955).

    They named a user ID that cannot exist. `app_settings` has no such column,
    `admin_db._APP_SETTINGS_COLUMNS` does not select one, and the DB-layer model
    does not declare one — so `_setting_attr(setting, "created_by_id", 1)` in
    `routes/settings_routes.py` was not a fallback for a NULL column, it
    manufactured `1` on every response for all ~1,345 keys.
    """

    @pytest.mark.parametrize("field", ["created_by_id", "updated_by_id"])
    def test_field_absent_from_api_model(self, field):
        """Reintroducing the field is the regression — fail here, not in prod."""
        assert field not in ApiSettingResponse.model_fields

    @pytest.mark.parametrize("field", ["created_by_id", "updated_by_id"])
    def test_field_absent_from_db_model(self, field):
        """Nothing below the route could supply one either."""
        assert field not in DbSettingResponse.model_fields

    @pytest.mark.parametrize("field", ["created_by_id", "updated_by_id"])
    def test_field_not_selected(self, field):
        """The SELECT is the upstream source — it never fetched these.

        Pinned against the real constant so adding the column to the query
        without also deciding what the API should report fails here.
        """
        from services.admin_db import AdminDatabase

        assert field not in AdminDatabase._APP_SETTINGS_COLUMNS

    @pytest.mark.parametrize("field", ["created_by_id", "updated_by_id"])
    def test_field_not_serialized(self, field):
        """The response body must not carry the key at all.

        `model_fields` alone would miss a field re-added via `extra="allow"`,
        which is how a well-meaning "keep it for back-compat" patch would most
        likely sneak it back in.
        """
        resp = ApiSettingResponse(
            id=1,
            key="image_render_timeout_seconds",
            value="600",
            category="media",
            **{field: 1},
        )
        assert field not in resp.model_dump()

    def test_route_passes_no_authorship_kwarg(self):
        """The route is where the fabrication lived — keep it clean.

        The schema checks above still pass while the route passes the kwarg,
        because `SettingResponse` inherits Pydantic's default `extra="ignore"`
        and drops an undeclared kwarg silently rather than raising (the same
        mechanic that hid #954). So assert on the route's call sites too, or the
        fabrication can be half-restored invisibly.

        Parsed with `ast` rather than grepped: the module's own docstrings name
        these fields to warn against them, and prose must not fail the test that
        enforces the warning.
        """
        import ast
        from pathlib import Path

        import routes.settings_routes as settings_routes

        tree = ast.parse(Path(settings_routes.__file__).read_text(encoding="utf-8"))
        offenders = [
            f"line {node.lineno}: {kw.arg}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg in {"created_by_id", "updated_by_id"}
        ]
        assert offenders == [], (
            "fabricated authorship kwarg is back in settings_routes: " + "; ".join(offenders)
        )


class TestLifecycleMetadataReachesTheApi:
    """`owner` / `value_type` / `deprecated` / `superseded_by` (poindexter#956).

    These are REAL `app_settings` columns (unlike the #955 fields), so the bar
    is the #954 one: the stored value must survive the SELECT → converter →
    response chain, and an unset column must report `null` rather than a
    stand-in. `owner` names the module that reads the key, not who set it.
    """

    LIFECYCLE = ["owner", "value_type", "deprecated", "superseded_by"]

    @pytest.mark.parametrize("field", LIFECYCLE)
    def test_selected_by_the_query(self, field):
        """The chain starts at the SELECT — if it isn't fetched, nothing else matters."""
        from services.admin_db import AdminDatabase

        assert field in AdminDatabase._APP_SETTINGS_COLUMNS

    @pytest.mark.parametrize("field", LIFECYCLE)
    def test_declared_on_db_model(self, field):
        """A selected column the model omits is dropped by `extra="ignore"` (#954)."""
        assert field in DbSettingResponse.model_fields

    @pytest.mark.parametrize("field", LIFECYCLE)
    def test_declared_on_api_model(self, field):
        assert field in ApiSettingResponse.model_fields

    def test_stored_values_survive_conversion(self):
        """The #954 regression shape, applied to the new columns."""
        converted = ModelConverter.to_setting_response(APP_SETTINGS_ROW)
        assert converted.owner == "image_pipeline"
        assert converted.value_type == "integer"
        assert converted.deprecated is False

    def test_deprecation_pair_survives_conversion(self):
        """A superseded key must carry its replacement — the pair is the payload."""
        row = {**APP_SETTINGS_ROW, "deprecated": True, "superseded_by": "new_key"}
        converted = ModelConverter.to_setting_response(row)
        assert converted.deprecated is True
        assert converted.superseded_by == "new_key"

    def test_unset_columns_report_null_not_a_placeholder(self):
        """160 of ~1,345 rows carry owner/value_type, so unset is the common case.

        Reporting anything but `null` here would repeat #955 on real columns —
        inventing provenance for the 88% of keys that have none.
        """
        row = {**APP_SETTINGS_ROW, "owner": None, "value_type": None}
        converted = ModelConverter.to_setting_response(row)
        assert converted.owner is None
        assert converted.value_type is None

    def test_route_passes_no_default_for_nullable_lifecycle_fields(self):
        """`_lifecycle_metadata` must not hand `_setting_attr` a non-None default.

        Only `deprecated` may (it is NOT NULL DEFAULT false, so `False` is the
        column's real value). A default on the three nullable ones would
        manufacture data exactly as `created_by_id=1` did — and the schema
        assertions above would stay green while it happened.
        """
        import ast
        import inspect

        from routes.settings_routes import _lifecycle_metadata

        tree = ast.parse(inspect.getsource(_lifecycle_metadata))
        offenders = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_setting_attr"):
                continue
            field = node.args[1].value if len(node.args) > 1 else "?"
            if len(node.args) > 2 and field != "deprecated":
                offenders.append(field)
        assert offenders == [], f"non-None default on nullable lifecycle field(s): {offenders}"


class TestDeadHistorySchemaStaysDeleted:
    """`SettingHistoryResponse` was deleted (poindexter#956).

    It modelled `changed_by_id: int` for the abandoned `settings` table and no
    endpoint ever returned it — a worked example of the shape #955 had to
    remove from the live response, sitting in the file a new author would copy.
    """

    def test_not_exported_from_schemas(self):
        import schemas

        assert not hasattr(schemas, "SettingHistoryResponse")

    def test_not_defined_in_settings_schemas(self):
        import schemas.settings_schemas as settings_schemas

        assert not hasattr(settings_schemas, "SettingHistoryResponse")
