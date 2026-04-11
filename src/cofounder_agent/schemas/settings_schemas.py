"""Application Settings and Configuration Models

Consolidated schemas for settings management and configuration.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SettingDataTypeEnum(str, Enum):
    """Setting data type"""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    JSON = "json"


class SettingCategoryEnum(str, Enum):
    """Setting category"""

    GENERAL = "general"
    API = "api"
    DATABASE = "database"
    SECURITY = "security"
    FEATURE_FLAGS = "feature_flags"
    PERFORMANCE = "performance"
    LOGGING = "logging"
    INTEGRATION = "integration"


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
    category: SettingCategoryEnum = Field(..., description="Setting category for organization")
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
    category: SettingCategoryEnum | None = Field(
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

    class Config:
        extra = "allow"  # Allow additional fields for flexible key-value storage


class SettingUpdate(BaseModel):
    """Model for updating settings (partial update allowed)"""

    value: str | None = Field(None, description="New setting value")
    description: str | None = Field(None, max_length=1000, description="Updated description")
    is_encrypted: bool | None = Field(None, description="Update encryption flag")
    is_read_only: bool | None = Field(None, description="Update read-only flag")
    tags: list[str] | None = Field(None, description="Updated tags")

    class Config:
        extra = "allow"  # Allow additional fields for simple key-value updates
        validate_assignment = True

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

    # Override strict enum — DB has many categories beyond the original enum
    category: str | None = Field(None, description="Setting category for organization")
    id: int = Field(..., description="Setting database ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    created_by_id: int = Field(..., description="User ID who created this setting")
    updated_by_id: int | None = Field(None, description="User ID who last updated this setting")
    value_preview: str | None = Field(
        None, description="Preview of value (for encrypted values)"
    )

    class Config:
        from_attributes = True


class SettingListResponse(BaseModel):
    """Model for list endpoint response"""

    total: int = Field(..., description="Total number of settings")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    pages: int = Field(..., description="Total number of pages")
    items: list["SettingResponse"] = Field(..., description="List of settings")


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
