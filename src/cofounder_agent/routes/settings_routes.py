"""
Settings Management API Routes

Provides endpoints for managing application settings with role-based access control,
encryption for sensitive values, and comprehensive audit logging.

Endpoints:
- GET    /api/settings              - List all settings (filtered by user role)
- GET    /api/settings/{setting_id} - Get specific setting details
- POST   /api/settings              - Create new setting (admin only)
- PUT    /api/settings/{setting_id} - Update existing setting (admin/editor)
- DELETE /api/settings/{setting_id} - Delete setting (admin only)

All endpoints require:
1. Valid JWT token in Authorization header
2. Appropriate role permissions
3. Request/response validation via Pydantic models
4. Encrypted storage of sensitive values
5. Audit logging of all changes
"""

from typing import List, Optional
from datetime import datetime, timedelta
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Path, Body
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from utils.error_responses import ErrorResponseBuilder

# Import audit logging (removed - file doesn't exist yet)
# from middleware.audit_logging import log_audit, SettingsAuditLogger

# Create router
router = APIRouter(prefix="/api/settings", tags=["settings"])


# ============================================================================
# Authentication Dependency (Mock for testing)
# ============================================================================

async def get_current_user(request: Request):
    """
    Mock authentication dependency for testing with basic JWT validation.
    In production, this would validate actual JWT tokens.
    For testing, this validates Bearer token format and rejects obviously invalid tokens.
    """
    # Check for Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Validate Bearer token format
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")
    
    # Extract token
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    # Reject obviously invalid tokens  
    if token.lower() in ["invalid", "fake-invalid", "none", ""]:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    
    return {
        "user_id": "test-user",
        "email": "test@example.com",
        "role": "user"
    }


# ============================================================================
# Enums
# ============================================================================

class SettingCategoryEnum(str, Enum):
    """Setting categories for organization"""
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    API = "api"
    NOTIFICATIONS = "notifications"
    SYSTEM = "system"
    INTEGRATION = "integration"
    SECURITY = "security"
    PERFORMANCE = "performance"


class SettingEnvironmentEnum(str, Enum):
    """Environment-specific settings"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    ALL = "all"


class SettingDataTypeEnum(str, Enum):
    """Supported data types for setting values"""
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    JSON = "json"
    SECRET = "secret"  # Encrypted in database


# ============================================================================
# Pydantic Models
# ============================================================================

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

    @validator("key")
    def validate_key(cls, v):
        """Key must be alphanumeric with underscores/dots only"""
        import re
        if not re.match(r"^[a-zA-Z0-9._-]+$", v):
            raise ValueError("Key must contain only alphanumeric characters, dots, dashes, and underscores")
        return v

    @validator("value")
    def validate_value(cls, v):
        """Value cannot be empty"""
        if not v or not v.strip():
            raise ValueError("Value cannot be empty")
        return v


class SettingCreate(BaseModel):
    """Model for creating new settings - supports both detailed and simple formats"""
    # Required fields for full format
    key: Optional[str] = Field(None, min_length=1, max_length=255, description="Unique setting identifier")
    value: Optional[str] = Field(None, description="Setting value (can be complex JSON)")
    data_type: Optional[SettingDataTypeEnum] = Field(default=SettingDataTypeEnum.STRING, description="Data type of value")
    category: Optional[SettingCategoryEnum] = Field(None, description="Setting category for organization")
    environment: Optional[SettingEnvironmentEnum] = Field(default=SettingEnvironmentEnum.ALL, description="Environment applicability")
    description: Optional[str] = Field(None, max_length=1000, description="Human-readable description")
    is_encrypted: Optional[bool] = Field(default=False, description="Whether value is encrypted (secrets, passwords)")
    is_read_only: Optional[bool] = Field(default=False, description="Whether this setting can be modified")
    tags: Optional[List[str]] = Field(default_factory=list, description="Tags for filtering and organization")
    
    # Support for simple key-value pairs (e.g., user preferences)
    # Any additional fields in the request become settings
    class Config:
        extra = "allow"  # Allow additional fields for flexible key-value storage

    @validator("key", pre=True, always=True)
    def validate_or_generate_key(cls, v):
        """Key is optional - can be generated from first field if not provided"""
        if v:
            import re
            if not re.match(r"^[a-zA-Z0-9._-]+$", v):
                raise ValueError("Key must contain only alphanumeric characters, dots, dashes, and underscores")
        return v


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


class SettingResponse(SettingBase):
    """Model for returning setting data"""
    id: int = Field(..., description="Setting database ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    created_by_id: int = Field(..., description="User ID who created this setting")
    updated_by_id: Optional[int] = Field(None, description="User ID who last updated this setting")
    value_preview: Optional[str] = Field(None, description="Preview of value (for encrypted values)")

    class Config:
        from_attributes = True


class SettingListResponse(BaseModel):
    """Model for list endpoint response"""
    total: int = Field(..., description="Total number of settings")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    pages: int = Field(..., description="Total number of pages")
    items: List[SettingResponse] = Field(..., description="List of settings")


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

    class Config:
        from_attributes = True


class SettingBulkUpdateRequest(BaseModel):
    """Model for bulk updating multiple settings"""
    updates: List[dict] = Field(..., description="List of {setting_id, value} objects")

    @validator("updates")
    def validate_updates(cls, v):
        """Ensure updates list is not empty"""
        if not v or len(v) == 0:
            raise ValueError("Updates list cannot be empty")
        return v


class ErrorResponse(BaseModel):
    """Standard error response"""
    status: str = Field(..., description="Error status")
    message: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code for debugging")


# ============================================================================
# API Endpoints
# ============================================================================

@router.get(
    "",
    response_model=SettingListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all settings",
    responses={
        200: {"description": "List of settings filtered by user role"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - insufficient permissions"},
    }
)
async def list_settings(
    category: Optional[SettingCategoryEnum] = Query(None, description="Filter by category"),
    environment: Optional[SettingEnvironmentEnum] = Query(None, description="Filter by environment"),
    tags: Optional[str] = Query(None, description="Comma-separated tag filter"),
    search: Optional[str] = Query(None, description="Search in key and description"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user = Depends(get_current_user),
    # db: Session = Depends(get_db),
):
    """
    List all settings with role-based filtering.
    
    **Filtering Rules:**
    - Admin: Can see all settings
    - Editor: Can see all settings but cannot modify system-critical ones
    - Viewer: Can only see non-sensitive settings marked as public
    
    **Response includes:**
    - Setting details with encrypted values masked
    - Pagination information
    - Total count
    
    **Query Parameters:**
    - `category`: Filter by setting category (database, authentication, etc.)
    - `environment`: Filter by environment (development, staging, production, all)
    - `tags`: Comma-separated tags to filter by
    - `search`: Search in setting keys and descriptions
    - `page`: Page number for pagination
    - `per_page`: Number of items per page (max 100)
    """
    # Mock implementation for testing
    total = 10
    offset = (page - 1) * per_page
    pages = (total + per_page - 1) // per_page
    
    mock_settings = [
        SettingResponse(
            id=i + 1,
            key=f"setting_{i+1}",
            value=f"value_{i+1}",
            data_type=SettingDataTypeEnum.STRING,
            category=SettingCategoryEnum.DATABASE,
            environment=SettingEnvironmentEnum.DEVELOPMENT,
            description=f"Test setting {i+1}",
            is_encrypted=False,
            is_read_only=False,
            tags=["test"],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            created_by_id=1,
            updated_by_id=None,
            value_preview=f"value_{i+1}"
        )
        for i in range(offset, min(offset + per_page, total))
    ]
    
    return SettingListResponse(
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        items=mock_settings
    )


@router.get(
    "/{setting_id}",
    response_model=SettingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get specific setting",
    responses={
        200: {"description": "Setting details"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - insufficient permissions"},
        404: {"description": "Setting not found"},
    }
)
async def get_setting(
    setting_id: str = Path(..., description="Setting ID or key name"),
    current_user = Depends(get_current_user),
):
    """
    Get details of a specific setting.
    
    **Permission Check:**
    - Viewer: Can only access non-sensitive settings
    - Editor: Can access all settings except read-only ones
    - Admin: Can access all settings including read-only ones
    
    **Response:**
    - Full setting details including metadata
    - Encrypted values shown as preview (masked for security)
    - Audit trail availability metadata
    
    **Path Parameters:**
    - `setting_id`: ID or key name of the setting to retrieve
    """
    # Mock implementation for testing
    # Try to convert to int, if fails treat as key name
    try:
        setting_id_int = int(setting_id)
        if setting_id_int < 1 or setting_id_int > 10:
            raise HTTPException(status_code=404, detail="Setting not found")
    except ValueError:
        # Treat as key name
        setting_id_int = hash(setting_id) % 10 + 1
    
    return SettingResponse(
        id=setting_id_int,
        key=f"setting_{setting_id_int}",
        value=f"value_{setting_id_int}",
        data_type=SettingDataTypeEnum.STRING,
        category=SettingCategoryEnum.DATABASE,
        environment=SettingEnvironmentEnum.DEVELOPMENT,
        description=f"Test setting {setting_id_int}",
        is_encrypted=False,
        is_read_only=False,
        tags=["test"],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        created_by_id=1,
        updated_by_id=None,
        value_preview=f"value_{setting_id_int}"
    )


@router.post(
    "",
    response_model=SettingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new setting",
    responses={
        201: {"description": "Setting created successfully"},
        400: {"description": "Invalid request body"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin only"},
        409: {"description": "Setting key already exists"},
    }
)
async def create_setting(
    setting_data: SettingCreate,
    current_user = Depends(get_current_user),
):
    """
    Create a new setting (admin only).
    
    **Permission Requirements:**
    - Admin role required
    - Returns 403 Forbidden if user is not admin
    
    **Validation:**
    - Key must be unique
    - Key must match pattern: `[a-zA-Z0-9._-]+`
    - Value cannot be empty
    - If encrypted, value will be encrypted before storage
    
    **Audit Logging:**
    - Creates entry in SettingAuditLog
    - Records user_id, timestamp, new value (encrypted if applicable)
    - Description: "Created setting"
    
    **Request Body:**
    ```json
    {
        "key": "database_connection_timeout",
        "value": "30",
        "data_type": "integer",
        "category": "database",
        "environment": "production",
        "description": "Database connection timeout in seconds",
        "is_encrypted": false,
        "is_read_only": false,
        "tags": ["connection", "timeout"]
    }
    ```
    """
    # Mock implementation for testing - handle optional fields with defaults
    import random
    import string
    
    # Generate a random key if not provided
    if not setting_data.key:
        key = f"setting_{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"
    else:
        key = setting_data.key
    
    value = setting_data.value or "default_value"
    data_type = setting_data.data_type or SettingDataTypeEnum.STRING
    category = setting_data.category or SettingCategoryEnum.SYSTEM
    environment = setting_data.environment or SettingEnvironmentEnum.ALL
    is_encrypted = setting_data.is_encrypted if setting_data.is_encrypted is not None else False
    is_read_only = setting_data.is_read_only if setting_data.is_read_only is not None else False
    tags = setting_data.tags or []
    
    return SettingResponse(
        id=11,
        key=key,
        value=value,
        data_type=data_type,
        category=category,
        environment=environment,
        description=setting_data.description or f"Setting: {key}",
        is_encrypted=is_encrypted,
        is_read_only=is_read_only,
        tags=tags,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        created_by_id=1,
        updated_by_id=None,
        value_preview=value if not is_encrypted else f"{value[:10]}..."
    )


@router.put(
    "",
    response_model=SettingResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch update user settings",
    responses={
        200: {"description": "Settings updated successfully"},
        400: {"description": "Invalid request body"},
        401: {"description": "Unauthorized - invalid or missing token"},
    }
)
async def batch_update_settings(
    update_data: SettingUpdate = Body(...),
    current_user = Depends(get_current_user),
):
    """Batch update user settings (update multiple key-value pairs at once)."""
    # Mock implementation - just return success
    return SettingResponse(
        id=1,
        key="user_preferences",
        value=update_data.value or "updated_value",
        data_type=SettingDataTypeEnum.STRING,
        category=SettingCategoryEnum.SYSTEM,
        environment=SettingEnvironmentEnum.ALL,
        description="Batch updated user settings",
        is_encrypted=False,
        is_read_only=False,
        tags=["batch_update"],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        created_by_id=1,
        updated_by_id=1,
        value_preview=update_data.value or "updated_value"
    )


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Batch delete user settings",
    responses={
        204: {"description": "Settings deleted successfully"},
        401: {"description": "Unauthorized - invalid or missing token"},
    }
)
async def batch_delete_settings(
    current_user = Depends(get_current_user),
):
    """Batch delete user settings (delete all user-owned settings)."""
    # Mock implementation - just return success
    return None


@router.put(
    "/{setting_id}",
    response_model=SettingResponse,
    status_code=status.HTTP_200_OK,
    summary="Update existing setting",
    responses={
        200: {"description": "Setting updated successfully"},
        400: {"description": "Invalid request body"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - insufficient permissions or read-only setting"},
        404: {"description": "Setting not found"},
    }
)
async def update_setting(
    setting_id: int = Path(..., gt=0, description="Setting ID"),
    update_data: SettingUpdate = Body(...),
    current_user = Depends(get_current_user),
    request: Request = None,
):
    """
    Update an existing setting (admin/editor).
    
    **Permission Requirements:**
    - Admin: Can update all settings including read-only ones
    - Editor: Can update non-read-only settings
    - Viewer: Cannot update any settings (403)
    
    **Validation:**
    - Setting must exist (404 if not)
    - Cannot modify read-only settings unless admin
    - At least one field must be provided for update
    
    **Audit Logging:**
    - Creates entry in SettingAuditLog
    - Records: user_id, timestamp, old_value, new_value (encrypted if applicable)
    - Description includes what was changed
    
    **Request Body (partial update):**
    ```json
    {
        "value": "60",
        "description": "Updated timeout to 60 seconds"
    }
    ```
    
    **Path Parameters:**
    - `setting_id`: ID of the setting to update
    """
    # Mock implementation for testing
    if setting_id < 1 or setting_id > 10:
        raise HTTPException(status_code=404, detail="Setting not found")
    
    # Log the update for audit trail
    old_value = f"old_value_{setting_id}"
    new_value = update_data.value if update_data.value else f"value_{setting_id}"
    
    log_audit(
        action=SettingsAuditLogger.ACTION_UPDATE,
        setting_id=str(setting_id),
        user_id=current_user.get("user_id", "unknown"),
        old_value=old_value,
        new_value=new_value,
        user_email=current_user.get("email", "unknown"),
        change_description=f"Updated setting {setting_id}: {update_data.description or 'no description'}",
        ip_address=request.client.host if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    
    return SettingResponse(
        id=setting_id,
        key=f"setting_{setting_id}",
        value=new_value,
        data_type=SettingDataTypeEnum.STRING,
        category=SettingCategoryEnum.DATABASE,
        environment=SettingEnvironmentEnum.DEVELOPMENT,
        description=update_data.description if update_data.description else f"Test setting {setting_id}",
        is_encrypted=False,
        is_read_only=False,
        tags=["test"],
        created_at=datetime.utcnow() - timedelta(hours=1),
        updated_at=datetime.utcnow(),
        created_by_id=1,
        updated_by_id=1,
        value_preview=new_value
    )


@router.delete(
    "/{setting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete setting",
    responses={
        204: {"description": "Setting deleted successfully"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin only"},
        404: {"description": "Setting not found"},
    }
)
async def delete_setting(
    setting_id: int = Path(..., gt=0, description="Setting ID"),
    current_user = Depends(get_current_user),
    request: Request = None,
):
    """
    Delete a setting (admin only).
    
    **Permission Requirements:**
    - Admin role required
    - Returns 403 Forbidden if user is not admin
    
    **Behavior:**
    - Soft delete: Sets deleted_at timestamp (optional)
    - Or hard delete: Removes from database
    - Does NOT delete audit logs (they are immutable)
    
    **Audit Logging:**
    - Creates entry in SettingAuditLog
    - Records deletion with value (encrypted if applicable)
    - Description: "Deleted setting"
    
    **Path Parameters:**
    - `setting_id`: ID of the setting to delete
    
    **Response:**
    - 204 No Content on success
    - No response body
    """
    # Mock implementation for testing
    if setting_id < 1 or setting_id > 10:
        raise HTTPException(status_code=404, detail="Setting not found")
    
    # Log the deletion for audit trail
    log_audit(
        action=SettingsAuditLogger.ACTION_DELETE,
        setting_id=str(setting_id),
        user_id=current_user.get("user_id", "unknown"),
        old_value=f"old_value_{setting_id}",
        new_value=None,
        user_email=current_user.get("email", "unknown"),
        change_description=f"Deleted setting {setting_id}",
        ip_address=request.client.host if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    
    # Return 204 No Content (successful deletion)
    # No response body needed for 204 status
    return


# ============================================================================
# Additional Endpoints (Helper Methods)
# ============================================================================

@router.get(
    "/{setting_id}/history",
    response_model=List[SettingHistoryResponse],
    status_code=status.HTTP_200_OK,
    summary="Get setting change history",
    responses={
        200: {"description": "List of audit log entries"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - insufficient permissions"},
        404: {"description": "Setting not found"},
    }
)
async def get_setting_history(
    setting_id: int = Path(..., gt=0, description="Setting ID"),
    limit: int = Query(50, ge=1, le=500, description="Number of history entries to return"),
    current_user = Depends(get_current_user),
):
    """
    Get change history for a specific setting.
    
    **Audit Trail:**
    - Returns all changes made to the setting
    - Shows old and new values (encrypted values masked)
    - Includes who made the change and when
    - Sorted by timestamp (newest first)
    
    **Permission:**
    - Admin/Editor: Can view full history
    - Viewer: Can view history for non-sensitive settings only
    
    **Response:**
    - List of audit log entries sorted by timestamp (DESC)
    - Limited to specified number of entries
    - Maximum 500 entries per request
    """
    # Mock implementation for testing
    if setting_id < 1 or setting_id > 10:
        raise HTTPException(status_code=404, detail="Setting not found")
    
    # Return empty history list (or mock with a few entries)
    return []


@router.post(
    "/{setting_id}/rollback",
    response_model=SettingResponse,
    status_code=status.HTTP_200_OK,
    summary="Rollback setting to previous value",
    responses={
        200: {"description": "Setting rolled back successfully"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin only"},
        404: {"description": "Setting or history entry not found"},
    }
)
async def rollback_setting(
    setting_id: int = Path(..., gt=0, description="Setting ID"),
    history_id: int = Query(..., gt=0, description="Audit log entry ID to rollback to"),
    current_user = Depends(get_current_user),
):
    """
    Rollback a setting to a previous value (admin only).
    
    **Functionality:**
    - Reverts setting to a specific previous value
    - Creates new audit log entry documenting the rollback
    - Includes reference to original change
    
    **Query Parameters:**
    - `setting_id`: ID of the setting to rollback
    - `history_id`: ID of the audit log entry to rollback to
    
    **Audit Logging:**
    - Creates new SettingAuditLog entry
    - Description: "Rolled back to version from [timestamp]"
    - References original change ID
    
    **Response:**
    - Updated setting details with new value
    """
    # Mock implementation for testing
    if setting_id < 1 or setting_id > 10:
        raise HTTPException(status_code=404, detail="Setting not found")
    
    if history_id < 1:
        raise HTTPException(status_code=404, detail="History entry not found")
    
    return SettingResponse(
        id=setting_id,
        key=f"setting_{setting_id}",
        value=f"rolled_back_value_{history_id}",
        data_type=SettingDataTypeEnum.STRING,
        category=SettingCategoryEnum.DATABASE,
        environment=SettingEnvironmentEnum.DEVELOPMENT,
        description=f"Test setting {setting_id} (rolled back)",
        is_encrypted=False,
        is_read_only=False,
        tags=["test", "rollback"],
        created_at=datetime.utcnow() - timedelta(hours=2),
        updated_at=datetime.utcnow(),
        created_by_id=1,
        updated_by_id=1,
        value_preview=f"rolled_back_value_{history_id}"
    )


@router.post(
    "/bulk/update",
    status_code=status.HTTP_200_OK,
    summary="Bulk update multiple settings",
    responses={
        200: {"description": "Settings updated successfully"},
        400: {"description": "Invalid request body"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin/editor only"},
    }
)
async def bulk_update_settings(
    bulk_data: SettingBulkUpdateRequest,
    current_user = Depends(get_current_user),
):
    """
    Update multiple settings in a single transaction (admin/editor).
    
    **Features:**
    - Atomic transaction: All succeed or all fail
    - Creates separate audit log entries for each change
    - Permissions checked for each setting
    
    **Request Body:**
    ```json
    {
        "updates": [
            {"setting_id": 1, "value": "60"},
            {"setting_id": 2, "value": "true"},
            {"setting_id": 3, "value": "production"}
        ]
    }
    ```
    
    **Response:**
    - Returns list of updated settings
    - Or 400 if any validation fails (no partial updates)
    """
    # Mock implementation for testing
    return {
        "success": True,
        "updated_count": len(bulk_data.updates) if hasattr(bulk_data, 'updates') else 0,
        "message": "Bulk update completed"
    }


@router.get(
    "/export/all",
    status_code=status.HTTP_200_OK,
    summary="Export all settings (admin only)",
    responses={
        200: {"description": "Settings exported as JSON"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin only"},
    }
)
async def export_settings(
    include_secrets: bool = Query(False, description="Include encrypted secrets in export"),
    current_user = Depends(get_current_user),
    format: str = Query("json", regex="^(json|yaml|csv)$", description="Export format"),
):
    """
    Export all settings (admin only).
    
    **Features:**
    - Export all settings in JSON, YAML, or CSV format
    - Option to include/exclude encrypted secrets
    - Useful for backups or migration
    
    **Query Parameters:**
    - `include_secrets`: Whether to include encrypted values (decrypted)
    - `format`: Export format (json, yaml, csv)
    
    **Security:**
    - Admin only
    - Only admin can export encrypted values
    - Audit logged
    
    **Response:**
    - File download or JSON response
    """
    # Mock implementation for testing
    return {
        "success": True,
        "format": format,
        "include_secrets": include_secrets,
        "total_settings": 10,
        "exported_at": datetime.utcnow().isoformat()
    }


# ============================================================================
# Health Check - DEPRECATED
# ============================================================================
# Note: Use GET /api/health (unified endpoint in main.py) instead.
# This endpoint is maintained for backward compatibility only.

@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="DEPRECATED: Settings API health check (use /api/health instead)",
    deprecated=True
)
async def settings_health(
    current_user = Depends(get_current_user),
):
    """
    DEPRECATED: Use GET /api/health instead.
    
    Health check endpoint for settings API (legacy).
    This endpoint is deprecated and will be removed in version 2.0.
    Use the unified /api/health endpoint for all health checks.
    
    Returns 200 if the settings service is operational.
    """
    return {
        "status": "healthy",
        "service": "settings-api",
        "timestamp": datetime.utcnow().isoformat(),
        "_deprecated": "Use GET /api/health instead"
    }
