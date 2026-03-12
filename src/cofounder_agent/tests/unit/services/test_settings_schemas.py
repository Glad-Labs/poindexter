"""
Unit tests for settings_schemas.py

Tests field validation and model behaviour for settings schemas.
"""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from schemas.settings_schemas import (
    SettingBulkUpdateRequest,
    SettingCategoryEnum,
    SettingCreate,
    SettingDataTypeEnum,
    SettingEnvironmentEnum,
    SettingListResponse,
    SettingResponse,
    SettingUpdate,
    SettingsErrorResponse,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingEnums:
    def test_data_type_enum(self):
        assert SettingDataTypeEnum.STRING == "string"
        assert SettingDataTypeEnum.INT == "int"
        assert SettingDataTypeEnum.FLOAT == "float"
        assert SettingDataTypeEnum.BOOL == "bool"
        assert SettingDataTypeEnum.JSON == "json"

    def test_category_enum(self):
        assert SettingCategoryEnum.GENERAL == "general"
        assert SettingCategoryEnum.API == "api"
        assert SettingCategoryEnum.DATABASE == "database"
        assert SettingCategoryEnum.SECURITY == "security"
        assert SettingCategoryEnum.FEATURE_FLAGS == "feature_flags"

    def test_environment_enum(self):
        assert SettingEnvironmentEnum.ALL == "all"
        assert SettingEnvironmentEnum.DEVELOPMENT == "development"
        assert SettingEnvironmentEnum.STAGING == "staging"
        assert SettingEnvironmentEnum.PRODUCTION == "production"


# ---------------------------------------------------------------------------
# SettingCreate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingCreate:
    def test_valid_minimal(self):
        setting = SettingCreate()  # type: ignore[call-arg]
        assert setting.data_type == SettingDataTypeEnum.STRING
        assert setting.environment == SettingEnvironmentEnum.ALL
        assert setting.is_encrypted is False
        assert setting.is_read_only is False

    def test_with_key_and_value(self):
        setting = SettingCreate(  # type: ignore[call-arg]
            key="FEATURE_DARK_MODE",
            value="true",
            category="feature_flags",
            data_type="bool",
        )
        assert setting.key == "FEATURE_DARK_MODE"
        assert setting.data_type == SettingDataTypeEnum.BOOL

    def test_key_too_long_raises(self):
        with pytest.raises(ValidationError):
            SettingCreate(key="x" * 256)  # type: ignore[call-arg]

    def test_description_too_long_raises(self):
        with pytest.raises(ValidationError):
            SettingCreate(description="x" * 1001)  # type: ignore[call-arg]

    def test_allows_extra_fields(self):
        # SettingCreate has extra="allow" for flexible key-value storage
        setting = SettingCreate(custom_field="custom_value")  # type: ignore[call-arg]
        assert setting.custom_field == "custom_value"  # type: ignore[attr-defined]

    def test_encrypted_setting(self):
        setting = SettingCreate(  # type: ignore[call-arg]
            key="API_SECRET",
            value="supersecret",
            category="security",
            is_encrypted=True,
        )
        assert setting.is_encrypted is True

    def test_readonly_setting(self):
        setting = SettingCreate(  # type: ignore[call-arg]
            key="VERSION",
            value="3.0.43",
            is_read_only=True,
        )
        assert setting.is_read_only is True


# ---------------------------------------------------------------------------
# SettingUpdate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingUpdate:
    def test_valid_empty_update(self):
        update = SettingUpdate()  # type: ignore[call-arg]
        assert update.has_updates() is False

    def test_with_value_has_updates(self):
        update = SettingUpdate(value="new_value")  # type: ignore[call-arg]
        assert update.has_updates() is True

    def test_with_description_has_updates(self):
        update = SettingUpdate(description="Updated description")  # type: ignore[call-arg]
        assert update.has_updates() is True

    def test_with_encrypted_flag_has_updates(self):
        update = SettingUpdate(is_encrypted=True)  # type: ignore[call-arg]
        assert update.has_updates() is True

    def test_with_read_only_flag_has_updates(self):
        update = SettingUpdate(is_read_only=True)  # type: ignore[call-arg]
        assert update.has_updates() is True

    def test_with_tags_has_updates(self):
        update = SettingUpdate(tags=["production", "security"])  # type: ignore[call-arg]
        assert update.has_updates() is True

    def test_description_too_long_raises(self):
        with pytest.raises(ValidationError):
            SettingUpdate(description="x" * 1001)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SettingsErrorResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsErrorResponse:
    def test_valid(self):
        resp = SettingsErrorResponse(  # type: ignore[call-arg]
            status="error",
            message="Setting not found",
        )
        assert resp.code is None

    def test_with_code(self):
        resp = SettingsErrorResponse(
            status="error",
            message="Validation failed",
            code="VALIDATION_ERROR",
        )
        assert resp.code == "VALIDATION_ERROR"

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            SettingsErrorResponse(status="error")  # missing message  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SettingBulkUpdateRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingBulkUpdateRequest:
    def test_valid(self):
        req = SettingBulkUpdateRequest(
            updates=[
                {"setting_id": 1, "value": "new_value_1"},
                {"setting_id": 2, "value": "new_value_2"},
            ]
        )
        assert len(req.updates) == 2

    def test_empty_updates_list(self):
        req = SettingBulkUpdateRequest(updates=[])
        assert len(req.updates) == 0

    def test_missing_updates_raises(self):
        with pytest.raises(ValidationError):
            SettingBulkUpdateRequest()  # type: ignore[call-arg]
