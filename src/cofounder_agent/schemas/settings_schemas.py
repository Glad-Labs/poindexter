"""Application Settings and Configuration Models

Consolidated schemas for settings management and configuration.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .database_response_models import ListResponse


class SettingDataTypeEnum(str, Enum):
    """Setting data type"""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    JSON = "json"


class SettingEnvironmentEnum(str, Enum):
    """Environment scope"""

    ALL = "all"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class SettingBase(BaseModel):
    """Base model for setting data"""

    key: str = Field(..., min_length=1, max_length=255, description="Unique setting identifier")
    value: str = Field(..., description="Setting value (can be complex JSON)")
    data_type: SettingDataTypeEnum = Field(
        default=SettingDataTypeEnum.STRING, description="Data type of value"
    )
    # Free-form string validated against the canonical taxonomy at the route
    # layer (services.settings_categories.CATEGORY_IDS). The old strict
    # SettingCategoryEnum was retired — the DB long outgrew its 8 values, and
    # SettingResponse already overrode it to a plain string for the same reason.
    category: str = Field(..., description="Setting category for organization")
    environment: SettingEnvironmentEnum = Field(
        default=SettingEnvironmentEnum.ALL, description="Environment applicability"
    )
    description: str | None = Field(
        None, max_length=1000, description="Human-readable description"
    )
    is_encrypted: bool = Field(
        default=False, description="Whether value is encrypted (secrets, passwords)"
    )
    is_read_only: bool = Field(default=False, description="Whether this setting can be modified")
    tags: list[str] = Field(default_factory=list, description="Tags for filtering and organization")


class SettingCreate(BaseModel):
    """Model for creating new settings - supports both detailed and simple formats"""

    key: str | None = Field(
        None, min_length=1, max_length=255, description="Unique setting identifier"
    )
    value: str | None = Field(None, description="Setting value (can be complex JSON)")
    data_type: SettingDataTypeEnum | None = Field(
        default=SettingDataTypeEnum.STRING, description="Data type of value"
    )
    category: str | None = Field(
        None, description="Setting category for organization"
    )
    environment: SettingEnvironmentEnum | None = Field(
        default=SettingEnvironmentEnum.ALL, description="Environment applicability"
    )
    description: str | None = Field(
        None, max_length=1000, description="Human-readable description"
    )
    is_encrypted: bool | None = Field(
        default=False, description="Whether value is encrypted (secrets, passwords)"
    )
    is_read_only: bool | None = Field(
        default=False, description="Whether this setting can be modified"
    )
    tags: list[str] | None = Field(
        default_factory=list, description="Tags for filtering and organization"
    )

    model_config = ConfigDict(extra="allow")


class SettingUpdate(BaseModel):
    """Model for updating settings (partial update allowed)"""

    value: str | None = Field(None, description="New setting value")
    description: str | None = Field(None, max_length=1000, description="Updated description")
    is_encrypted: bool | None = Field(None, description="Update encryption flag")
    is_read_only: bool | None = Field(None, description="Update read-only flag")
    tags: list[str] | None = Field(None, description="Updated tags")

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    def has_updates(self) -> bool:
        """Check if any fields have been provided for update"""
        return any(
            [
                self.value is not None,
                self.description is not None,
                self.is_encrypted is not None,
                self.is_read_only is not None,
                self.tags is not None,
            ]
        )


class SettingResponse(SettingBase):
    """Model for returning setting data"""

    # Relax the required base field to optional+nullable for read responses:
    # legacy rows may carry a NULL category before the boot reconcile stamps them.
    category: str | None = Field(None, description="Setting category for organization")  # type: ignore[assignment]
    id: int = Field(..., description="Setting database ID")
    # Nullable by contract (poindexter#954). `app_settings.created_at` /
    # `updated_at` are `timestamptz DEFAULT now()` with no NOT NULL, so the
    # value can genuinely be absent. These fields are how an operator tells a
    # deliberate override from stale seed drift, so an absent timestamp is
    # reported as `null` — never backfilled with `now()`, which reads as "just
    # changed" and misleads exactly when the field matters most.
    created_at: datetime | None = Field(None, description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")
    # No `created_by_id` / `updated_by_id` — do NOT reintroduce them
    # (poindexter#955). Fossils of the abandoned `settings` schema, same
    # generation as `modified_at` / `display_name`, they reported a hardcoded
    # user ID 1 as the author of every row: `app_settings` has no such column,
    # `admin_db._APP_SETTINGS_COLUMNS` does not select one, and the DB-layer
    # model does not declare one, so the route's default fired on 100% of
    # responses.
    #
    # Removed rather than nulled (how #954 handled an absent timestamp) because
    # the cases differ: the timestamps are real columns that are genuinely
    # sometimes NULL, so `null` honestly reports a real unknown. These had no
    # column and no possible source — `null` forever would advertise an
    # authorship field the system does not have and cannot add as typed
    # (`users.id` is a uuid; these were `int`).
    value_preview: str | None = Field(
        None, description="Preview of value (for encrypted values)"
    )

    model_config = ConfigDict(from_attributes=True)


class SettingListResponse(ListResponse[SettingResponse]):
    """Settings list — canonical offset envelope (poindexter#745).

    ``{items, total, limit, offset}`` via ``ListResponse[SettingResponse]``.
    Completes the #635 offset/limit canonicalization on the response side (the
    request side already accepted offset/limit); the page-based response fields
    (page/per_page/pages) were retired.
    """


class SettingHistoryResponse(BaseModel):
    """Model for audit log entry"""

    id: int
    setting_id: int
    changed_by_id: int
    changed_by_email: str
    change_description: str
    old_value: str | None
    new_value: str | None
    timestamp: datetime


class SettingBulkUpdateRequest(BaseModel):
    """Model for bulk updating multiple settings"""

    updates: list[dict] = Field(..., description="List of {setting_id, value} objects")


class SettingsErrorResponse(BaseModel):
    """Standard error response for settings endpoints.

    Named SettingsErrorResponse to avoid collision with the canonical ErrorResponse
    defined in schemas/database_response_models.py.
    """

    status: str = Field(..., description="Error status")
    message: str = Field(..., description="Error message")
    code: str | None = Field(None, description="Error code for debugging")
