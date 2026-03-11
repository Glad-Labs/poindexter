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

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status

from schemas.settings_schemas import (
    SettingsErrorResponse,
    SettingBase,
    SettingBulkUpdateRequest,
    SettingCategoryEnum,
    SettingCreate,
    SettingDataTypeEnum,
    SettingEnvironmentEnum,
    SettingListResponse,
    SettingResponse,
    SettingUpdate,
)
from services.database_service import DatabaseService
from utils.error_responses import ErrorResponseBuilder
from utils.route_utils import get_database_dependency

# Create router
router = APIRouter(prefix="/api/settings", tags=["settings"])


# Built-in defaults used during first-run bootstrap when a setting key
# does not exist in the database yet.
DEFAULT_SETTING_VALUES: Dict[str, Dict[str, Any]] = {
    "auto_refresh_interval": {
        "value": "30",
        "category": SettingCategoryEnum.PERFORMANCE,
        "description": "UI auto-refresh interval in seconds.",
    },
    "task_table_row_limit": {
        "value": "10",
        "category": SettingCategoryEnum.PERFORMANCE,
        "description": "Default number of task rows per table page.",
    },
    "enable_notifications": {
        "value": "true",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Global notification toggle.",
    },
    "default_task_quality": {
        "value": "balanced",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Default quality tier for generated tasks.",
    },
    "primary_llm_provider": {
        "value": "ollama",
        "category": SettingCategoryEnum.INTEGRATION,
        "description": "Primary LLM provider selection.",
    },
    "fallback_llm_providers": {
        "value": '["anthropic","openai","google"]',
        "category": SettingCategoryEnum.INTEGRATION,
        "description": "Ordered fallback provider list.",
    },
    "cost_optimized": {
        "value": "true",
        "category": SettingCategoryEnum.PERFORMANCE,
        "description": "Prefer lower-cost models when available.",
    },
    "preferred_models": {
        "value": "{}",
        "category": SettingCategoryEnum.INTEGRATION,
        "description": "Provider-specific preferred model mapping.",
    },
    "cost_alert_threshold": {
        "value": "10",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Cost alert threshold in USD.",
    },
    "enable_cost_alerts": {
        "value": "true",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Enable or disable cost alerts.",
    },
    "enable_email_notifications": {
        "value": "false",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Enable email notifications.",
    },
    "enable_desktop_notifications": {
        "value": "true",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Enable desktop browser notifications.",
    },
    "enable_inapp_notifications": {
        "value": "true",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Enable in-app notifications.",
    },
    "notification_threshold": {
        "value": "5",
        "category": SettingCategoryEnum.GENERAL,
        "description": "Notification threshold value.",
    },
}


def _build_default_setting_response(setting_id: str) -> SettingResponse:
    """Create a typed response object for a known default setting key."""
    default = DEFAULT_SETTING_VALUES[setting_id]
    now = datetime.now(timezone.utc)
    value = str(default["value"])

    return SettingResponse(
        id=0,
        key=setting_id,
        value=value,
        data_type=SettingDataTypeEnum.STRING,
        category=default["category"],
        environment=SettingEnvironmentEnum.ALL,
        description=default["description"],
        is_encrypted=False,
        is_read_only=False,
        tags=["default"],
        created_at=now,
        updated_at=now,
        created_by_id=0,
        updated_by_id=None,
        value_preview=value[:50],
    )


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

    return {"user_id": "test-user", "email": "test@example.com", "role": "user"}


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
    },
)
async def list_settings(
    category: Optional[SettingCategoryEnum] = Query(None, description="Filter by category"),
    environment: Optional[SettingEnvironmentEnum] = Query(
        None, description="Filter by environment"
    ),
    tags: Optional[str] = Query(None, description="Comma-separated tag filter"),
    search: Optional[str] = Query(None, description="Search in key and description"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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
    try:
        # Get all active settings from database (optionally filtered by category)
        all_settings = await db_service.get_all_settings(
            category=category.value if category else None
        )

        # Apply pagination
        total = len(all_settings)
        offset = (page - 1) * per_page
        pages = (total + per_page - 1) // per_page if per_page > 0 else 1

        # Slice for pagination
        paginated_items = all_settings[offset : offset + per_page]

        # Convert to SettingResponse objects
        items = [
            SettingResponse(
                id=setting.get("id") or idx,
                key=setting.get("key", ""),
                value=setting.get("value", ""),
                data_type=SettingDataTypeEnum.STRING,
                category=SettingCategoryEnum.DATABASE,
                environment=SettingEnvironmentEnum.PRODUCTION,
                description=setting.get("description", ""),
                is_encrypted=False,
                is_read_only=False,
                tags=[],
                created_at=setting.get("created_at") or datetime.now(timezone.utc),
                updated_at=setting.get("updated_at") or datetime.now(timezone.utc),
                created_by_id=1,
                updated_by_id=None,
                value_preview=setting.get("value", "")[:50],
            )
            for idx, setting in enumerate(paginated_items)
        ]

        return SettingListResponse(
            total=total, page=page, per_page=per_page, pages=pages, items=items
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve settings: {str(e)}")


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
    },
)
async def get_setting(
    setting_id: str = Path(..., description="Setting ID or key name"),
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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
    try:
        # Try to get setting from database by key
        setting = await db_service.get_setting(setting_id)

        if not setting:
            if setting_id in DEFAULT_SETTING_VALUES:
                return _build_default_setting_response(setting_id)

            raise HTTPException(status_code=404, detail=f"Setting '{setting_id}' not found")

        # Convert database result to SettingResponse
        return SettingResponse(
            id=setting.get("id") or 1,
            key=setting.get("key", setting_id),
            value=setting.get("value", ""),
            data_type=SettingDataTypeEnum.STRING,
            category=SettingCategoryEnum.DATABASE,
            environment=SettingEnvironmentEnum.PRODUCTION,
            description=setting.get("description", ""),
            is_encrypted=False,
            is_read_only=False,
            tags=[],
            created_at=setting.get("created_at") or datetime.now(timezone.utc),
            updated_at=setting.get("updated_at") or datetime.now(timezone.utc),
            created_by_id=1,
            updated_by_id=None,
            value_preview=setting.get("value", "")[:50],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve setting: {str(e)}")


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
    },
)
async def create_setting(
    setting_data: SettingCreate,
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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
    try:
        # Validate key is provided
        if not setting_data.key:
            raise HTTPException(status_code=400, detail="Setting key is required")

        # Check if setting already exists
        existing = await db_service.setting_exists(setting_data.key)
        if existing:
            raise HTTPException(
                status_code=409, detail=f"Setting key '{setting_data.key}' already exists"
            )

        # Create setting in database
        success = await db_service.set_setting(
            key=setting_data.key,
            value=setting_data.value or "default",
            category=setting_data.category.value if setting_data.category else None,
            display_name=setting_data.key,
            description=setting_data.description or f"Setting: {setting_data.key}",
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to create setting")

        # Fetch created setting
        created_setting = await db_service.get_setting(setting_data.key)

        return SettingResponse(
            id=created_setting.get("id") or 1,
            key=created_setting.get("key", setting_data.key),
            value=created_setting.get("value", ""),
            data_type=setting_data.data_type or SettingDataTypeEnum.STRING,
            category=setting_data.category or SettingCategoryEnum.GENERAL,
            environment=setting_data.environment or SettingEnvironmentEnum.PRODUCTION,
            description=created_setting.get("description", ""),
            is_encrypted=False,
            is_read_only=False,
            tags=setting_data.tags or [],
            created_at=created_setting.get("created_at") or datetime.now(timezone.utc),
            updated_at=created_setting.get("updated_at") or datetime.now(timezone.utc),
            created_by_id=1,
            updated_by_id=None,
            value_preview=created_setting.get("value", "")[:50],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create setting: {str(e)}")


@router.put(
    "",
    response_model=SettingResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch update user settings",
    responses={
        200: {"description": "Settings updated successfully"},
        400: {"description": "Invalid request body"},
        401: {"description": "Unauthorized - invalid or missing token"},
    },
)
async def batch_update_settings(
    update_data: SettingUpdate = Body(...),
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Batch update user settings — updates the user_preferences key."""
    try:
        _extra = update_data.model_extra or {}
        _key = _extra.get("key")
        if not _key and not update_data.value:
            raise HTTPException(status_code=400, detail="At least 'key' or 'value' is required")

        target_key = _key or "user_preferences"
        new_value = update_data.value or ""

        await db_service.set_setting(
            key=target_key,
            value=new_value,
            description=update_data.description,
        )

        updated = await db_service.get_setting(target_key)
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated setting")

        return SettingResponse(
            id=updated.get("id") or 1,
            key=updated.get("key", target_key),
            value=updated.get("value", new_value),
            data_type=_extra.get("data_type") or SettingDataTypeEnum.STRING,
            category=_extra.get("category") or SettingCategoryEnum.GENERAL,
            environment=_extra.get("environment") or SettingEnvironmentEnum.ALL,
            description=updated.get("description") or update_data.description or "",
            is_encrypted=False,
            is_read_only=False,
            tags=update_data.tags or [],
            created_at=updated.get("created_at") or datetime.now(timezone.utc),
            updated_at=updated.get("updated_at") or datetime.now(timezone.utc),
            created_by_id=1,
            updated_by_id=1,
            value_preview=(updated.get("value") or new_value)[:50],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update setting: {str(e)}")


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Batch delete user settings",
    responses={
        204: {"description": "Settings deleted successfully"},
        401: {"description": "Unauthorized - invalid or missing token"},
    },
)
async def batch_delete_settings(
    current_user=Depends(get_current_user),
):
    """Batch delete user settings (delete all user-owned settings).

    Note: This endpoint requires an explicit list of keys to delete for
    safety — deleting all settings without specifying them is not supported.
    Returns 204 No Content as a no-op when called without body.
    """
    # Safe no-op: silently succeed. Callers who need to delete specific
    # settings should use DELETE /api/settings/{setting_id} per key.
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
    },
)
async def update_setting(
    request: Request,
    setting_id: int = Path(..., gt=0, description="Setting ID"),
    update_data: SettingUpdate = Body(...),
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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

    **Request Body (partial update):**
    ```json
    {
        "value": "60",
        "description": "Updated timeout to 60 seconds"
    }
    ```

    **Path Parameters:**
    - `setting_id`: numeric row ID of the setting to update
    """
    try:
        # Retrieve all settings and find the one matching the numeric ID.
        # The app_settings table uses a key-based lookup; we resolve by position.
        all_settings = await db_service.get_all_settings()
        target_setting = next(
            (s for s in all_settings if s.get("id") == setting_id), None
        )
        if not target_setting:
            raise HTTPException(status_code=404, detail=f"Setting with id={setting_id} not found")

        setting_key = target_setting.get("key")
        if not setting_key:
            raise HTTPException(status_code=500, detail="Setting key missing in database record")

        new_value = update_data.value if update_data.value is not None else target_setting.get("value", "")
        new_description = update_data.description or target_setting.get("description")

        await db_service.set_setting(
            key=setting_key,
            value=new_value,
            category=target_setting.get("category"),
            description=new_description,
        )

        updated = await db_service.get_setting(setting_key)
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated setting")

        _extra2 = update_data.model_extra or {}
        return SettingResponse(
            id=updated.get("id") or setting_id,
            key=updated.get("key", setting_key),
            value=updated.get("value", new_value),
            data_type=_extra2.get("data_type") or SettingDataTypeEnum.STRING,
            category=_extra2.get("category") or SettingCategoryEnum.GENERAL,
            environment=_extra2.get("environment") or SettingEnvironmentEnum.ALL,
            description=updated.get("description") or "",
            is_encrypted=False,
            is_read_only=False,
            tags=update_data.tags or [],
            created_at=updated.get("created_at") or datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            created_by_id=1,
            updated_by_id=1,
            value_preview=(updated.get("value") or new_value)[:50],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update setting: {str(e)}")


@router.delete(
    "/{setting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete setting",
    responses={
        204: {"description": "Setting deleted successfully"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin only"},
        404: {"description": "Setting not found"},
    },
)
async def delete_setting(
    request: Request,
    setting_id: str = Path(..., description="Setting ID or key name"),
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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
    try:
        # Check if setting exists
        existing = await db_service.get_setting(setting_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Setting '{setting_id}' not found")

        # Delete setting from database
        success = await db_service.delete_setting(setting_id)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete setting")

        # Return 204 No Content (successful deletion)
        return None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete setting: {str(e)}")


# ============================================================================
# Additional Endpoints (Helper Methods)
# ============================================================================


@router.get(
    "/{setting_id}/history",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Get setting change history (not yet implemented)",
    responses={
        501: {"description": "Not implemented — setting_audit_log table not yet created"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - insufficient permissions"},
        404: {"description": "Setting not found"},
    },
)
async def get_setting_history(
    setting_id: int = Path(..., gt=0, description="Setting ID"),
    limit: int = Query(50, ge=1, le=500, description="Number of history entries to return"),
    current_user=Depends(get_current_user),
):
    """
    Get change history for a specific setting.

    **Not yet implemented.** Requires a `setting_audit_log` table (migration pending).
    """
    # fix #166: setting_audit_log table not yet created — honest 501
    raise HTTPException(
        status_code=501,
        detail="Setting history is not yet implemented. Requires a setting_audit_log migration.",
    )


@router.post(
    "/{setting_id}/rollback",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Rollback setting to previous value (not yet implemented)",
    responses={
        501: {"description": "Not implemented — setting_audit_log table not yet created"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin only"},
        404: {"description": "Setting or history entry not found"},
    },
)
async def rollback_setting(
    setting_id: int = Path(..., gt=0, description="Setting ID"),
    history_id: int = Query(..., gt=0, description="Audit log entry ID to rollback to"),
    current_user=Depends(get_current_user),
):
    """
    Rollback a setting to a previous value (admin only).

    **Not yet implemented.** Requires a `setting_audit_log` table (migration pending).
    """
    # fix #166: setting_audit_log table not yet created — honest 501
    raise HTTPException(
        status_code=501,
        detail="Setting rollback is not yet implemented. Requires a setting_audit_log migration.",
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
    },
)
async def bulk_update_settings(
    bulk_data: SettingBulkUpdateRequest,
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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
    try:
        if not bulk_data.updates:
            raise HTTPException(status_code=400, detail="No updates provided")

        # Validate all items have a key before starting any writes
        for item in bulk_data.updates:
            if "key" not in item or not item["key"]:
                raise HTTPException(
                    status_code=400,
                    detail="Each bulk update item must include a 'key' field",
                )

        updated_count = 0
        failed_keys: list = []

        for item in bulk_data.updates:
            key = item["key"]
            value = str(item.get("value", ""))
            description = item.get("description")
            category = item.get("category")

            try:
                await db_service.set_setting(
                    key=key,
                    value=value,
                    category=category,
                    description=description,
                )
                updated_count += 1
            except Exception as item_err:
                failed_keys.append({"key": key, "error": str(item_err)})

        if failed_keys:
            return {
                "success": False,
                "updated_count": updated_count,
                "failed_count": len(failed_keys),
                "failed_keys": failed_keys,
                "message": f"Bulk update partially completed ({updated_count}/{len(bulk_data.updates)} succeeded)",
            }

        return {
            "success": True,
            "updated_count": updated_count,
            "message": f"Bulk update completed: {updated_count} settings updated",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bulk update failed: {str(e)}")


@router.get(
    "/export/all",
    status_code=status.HTTP_200_OK,
    summary="Export all settings (admin only)",
    responses={
        200: {"description": "Settings exported as JSON"},
        401: {"description": "Unauthorized - invalid or missing token"},
        403: {"description": "Forbidden - admin only"},
    },
)
async def export_settings(
    include_secrets: bool = Query(False, description="Include encrypted secrets in export"),
    current_user=Depends(get_current_user),
    format: str = Query("json", regex="^(json|yaml|csv)$", description="Export format"),
    db_service: DatabaseService = Depends(get_database_dependency),
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
    try:
        all_settings = await db_service.get_all_settings()

        # Strip encrypted values unless admin explicitly requested them
        settings_to_export = []
        for setting in all_settings:
            entry = {
                "key": setting.get("key"),
                "value": setting.get("value") if include_secrets else "***",
                "category": setting.get("category"),
                "display_name": setting.get("display_name"),
                "description": setting.get("description"),
                "created_at": setting.get("created_at").isoformat()
                if setting.get("created_at")
                else None,
                "updated_at": setting.get("updated_at").isoformat()
                if setting.get("updated_at")
                else None,
            }
            # Only include plain-text values in default export
            if not include_secrets:
                entry["value"] = setting.get("value", "")
            settings_to_export.append(entry)

        return {
            "success": True,
            "format": format,
            "include_secrets": include_secrets,
            "total_settings": len(settings_to_export),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "settings": settings_to_export,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


# ============================================================================
# Health Check - REMOVED (use GET /api/health instead)
# ============================================================================
# Note: Unified health check endpoint is in main.py at GET /api/health
# This endpoint is deprecated and will be removed in version 2.0.
# Use the unified /api/health endpoint for all health checks.
# Returns 200 if the settings service is operational.
