"""Application Settings and Configuration Models

Consolidated schemas for settings management and configuration.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


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
    data_type: SettingDataTypeEnum = Field(default=SettingDataTypeEnum.STRING, description="Data type of value")
    category: SettingCategoryEnum = Field(..., description="Setting category for organization")
    environment: SettingEnvironmentEnum = Field(default=SettingEnvironmentEnum.ALL, description="Environment applicability")
    description: Optional[str] = Field(None, max_length=1000, description="Human-readable description")
    is_encrypted: bool = Field(default=False, description="Whether value is encrypted (secrets, passwords)")
    is_read_only: bool = Field(default=False, description="Whether this setting can be modified")
    tags: List[str] = Field(default_factory=list, description="Tags for filtering and organization")


class SettingCreate(BaseModel):
    """Model for creating new settings - supports both detailed and simple formats"""
    key: Optional[str] = Field(None, min_length=1, max_length=255, description="Unique setting identifier")
    value: Optional[str] = Field(None, description="Setting value (can be complex JSON)")
    data_type: Optional[SettingDataTypeEnum] = Field(default=SettingDataTypeEnum.STRING, description="Data type of value")
    category: Optional[SettingCategoryEnum] = Field(None, description="Setting category for organization")
    environment: Optional[SettingEnvironmentEnum] = Field(default=SettingEnvironmentEnum.ALL, description="Environment applicability")
    description: Optional[str] = Field(None, max_length=1000, description="Human-readable description")
    is_encrypted: Optional[bool] = Field(default=False, description="Whether value is encrypted (secrets, passwords)")
    is_read_only: Optional[bool] = Field(default=False, description="Whether this setting can be modified")
    tags: Optional[List[str]] = Field(default_factory=list, description="Tags for filtering and organization")
    
    class Config:
        extra = "allow"  # Allow additional fields for flexible key-value storage


class SettingUpdate(BaseModel):
    """Model for updating settings (partial update allowed)"""
    value: Optional[str] = Field(None, description="New setting value")
    description: Optional[str] = Field(None, max_length=1000, description="Updated description")
    is_encrypted: Optional[bool] = Field(None, description="Update encryption flag")
    is_read_only: Optional[bool] = Field(None, description="Update read-only flag")
    tags: Optional[List[str]] = Field(None, description="Updated tags")
    
    class Config:
        extra = "allow"  # Allow additional fields for simple key-value updates
        validate_assignment = True

    def has_updates(self) -> bool:
        """Check if any fields have been provided for update"""
        return any([
            self.value is not None,
            self.description is not None,
            self.is_encrypted is not None,
            self.is_read_only is not None,
            self.tags is not None,
        ])


class SettingListResponse(BaseModel):
    """Model for list endpoint response"""
    total: int = Field(..., description="Total number of settings")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    pages: int = Field(..., description="Total number of pages")
    items: List[dict] = Field(..., description="List of settings")


class SettingHistoryResponse(BaseModel):
    """Model for audit log entry"""
    id: int
    setting_id: int
    changed_by_id: int
    changed_by_email: str
    change_description: str
    old_value: Optional[str]
    new_value: Optional[str]
    timestamp: datetime


class SettingBulkUpdateRequest(BaseModel):
    """Model for bulk updating multiple settings"""
    updates: List[dict] = Field(..., description="List of {setting_id, value} objects")


class ErrorResponse(BaseModel):
    """Standard error response"""
    status: str = Field(..., description="Error status")
    message: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code for debugging")
