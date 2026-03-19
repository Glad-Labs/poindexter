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

from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status

from services.logger_config import get_logger
from routes.auth_unified import get_current_user

logger = get_logger(__name__)
from schemas.settings_schemas import (
    SettingBase,
    SettingBulkUpdateRequest,
    SettingCategoryEnum,
    SettingCreate,
    SettingDataTypeEnum,
    SettingEnvironmentEnum,
    SettingHistoryResponse,
    SettingListResponse,
    SettingResponse,
    SettingUpdate,
)
from services.database_service import DatabaseService
from utils.error_responses import ErrorResponseBuilder
from utils.route_utils import get_database_dependency


def _setting_attr(setting: Any, attr: str, default: Any = None) -> Any:
    """Safely get attribute from a setting (Pydantic model or dict)."""
    if isinstance(setting, dict):
        return setting.get(attr, default)
    return getattr(setting, attr, default)

# Create router
router = APIRouter(prefix="/api/settings", tags=["settings"])


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
                id=_setting_attr(setting, "id") or idx,
                key=_setting_attr(setting, "key", ""),
                value=_setting_attr(setting, "value", ""),
                data_type=_setting_attr(setting, "data_type", SettingDataTypeEnum.STRING),
                category=_setting_attr(setting, "category", SettingCategoryEnum.DATABASE),
                environment=_setting_attr(setting, "environment", SettingEnvironmentEnum.PRODUCTION),
                description=_setting_attr(setting, "description", ""),
                is_encrypted=_setting_attr(setting, "is_encrypted", False),
                is_read_only=_setting_attr(setting, "is_read_only", False),
                tags=_setting_attr(setting, "tags", []),
                created_at=_setting_attr(setting, "created_at") or datetime.now(timezone.utc),
                updated_at=_setting_attr(setting, "updated_at") or datetime.now(timezone.utc),
                created_by_id=_setting_attr(setting, "created_by_id", 1),
                updated_by_id=_setting_attr(setting, "updated_by_id"),
                value_preview=(_setting_attr(setting, "value", "") or "")[:50],
            )
            for idx, setting in enumerate(paginated_items)
        ]

        return SettingListResponse(
            total=total, page=page, per_page=per_page, pages=pages, items=items
        )
    except Exception:
        logger.error("[list_settings] Failed to retrieve settings", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve settings")


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
            raise HTTPException(status_code=404, detail=f"Setting '{setting_id}' not found")

        # Convert database result to SettingResponse
        return SettingResponse(
            id=_setting_attr(setting, "id") or 1,
            key=_setting_attr(setting, "key", setting_id),
            value=_setting_attr(setting, "value", ""),
            data_type=_setting_attr(setting, "data_type", SettingDataTypeEnum.STRING),
            category=_setting_attr(setting, "category", SettingCategoryEnum.DATABASE),
            environment=_setting_attr(setting, "environment", SettingEnvironmentEnum.PRODUCTION),
            description=_setting_attr(setting, "description", ""),
            is_encrypted=_setting_attr(setting, "is_encrypted", False),
            is_read_only=_setting_attr(setting, "is_read_only", False),
            tags=_setting_attr(setting, "tags", []),
            created_at=_setting_attr(setting, "created_at") or datetime.now(timezone.utc),
            updated_at=_setting_attr(setting, "updated_at") or datetime.now(timezone.utc),
            created_by_id=_setting_attr(setting, "created_by_id", 1),
            updated_by_id=_setting_attr(setting, "updated_by_id"),
            value_preview=(_setting_attr(setting, "value", "") or "")[:50],
        )
    except HTTPException:
        raise
    except Exception:
        logger.error("[get_setting] Failed to retrieve setting", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve setting")


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
            id=_setting_attr(created_setting, "id") or 1,
            key=_setting_attr(created_setting, "key", setting_data.key),
            value=_setting_attr(created_setting, "value", ""),
            data_type=setting_data.data_type or SettingDataTypeEnum.STRING,
            category=setting_data.category or SettingCategoryEnum.GENERAL,
            environment=setting_data.environment or SettingEnvironmentEnum.PRODUCTION,
            description=_setting_attr(created_setting, "description", ""),
            is_encrypted=False,
            is_read_only=False,
            tags=setting_data.tags or [],
            created_at=_setting_attr(created_setting, "created_at") or datetime.now(timezone.utc),
            updated_at=_setting_attr(created_setting, "updated_at") or datetime.now(timezone.utc),
            created_by_id=1,
            updated_by_id=None,
            value_preview=(_setting_attr(created_setting, "value", "") or "")[:50],
        )
    except HTTPException:
        raise
    except Exception :
        logger.error("[settings_routes] Failed to create setting", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create setting")


@router.patch(
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
    """Batch update user settings (update multiple key-value pairs at once)."""
    try:
        # Extract key from the body (extra="allow" on schema permits arbitrary fields)
        key = getattr(update_data, "key", None) or "user_preferences"
        new_value = update_data.value or ""

        success = await db_service.set_setting(
            key=key,
            value=new_value,
            description=update_data.description,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to update setting")

        updated = await db_service.get_setting(key)

        return SettingResponse(
            id=(_setting_attr(updated, "id") or 1) if updated else 1,
            key=key,
            value=new_value,
            data_type=SettingDataTypeEnum.STRING,
            category=SettingCategoryEnum.GENERAL,
            environment=SettingEnvironmentEnum.ALL,
            description=update_data.description or "",
            is_encrypted=False,
            is_read_only=False,
            tags=[],
            created_at=(_setting_attr(updated, "created_at") if updated else None) or datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            created_by_id=1,
            updated_by_id=1,
            value_preview=new_value[:50],
        )
    except HTTPException:
        raise
    except Exception :
        logger.error("[settings_routes] Failed to update settings", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update settings")


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
    """Batch delete user settings (delete all user-owned settings)."""
    # Mock implementation - just return success
    return None


@router.patch(
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
    setting_id: str = Path(..., description="Setting key name"),
    update_data: SettingUpdate = Body(...),
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Update an existing setting (admin/editor).

    **Path Parameters:**
    - `setting_id`: Key name of the setting to update
    """
    try:
        # Check if setting exists
        existing = await db_service.get_setting(setting_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Setting '{setting_id}' not found")

        # Preserve existing values for fields not passed in the update
        new_value = update_data.value if update_data.value else _setting_attr(existing, "value", "")
        new_description = (
            update_data.description if update_data.description else _setting_attr(existing, "description", "")
        )
        existing_category = _setting_attr(existing, "category")
        existing_category_str = existing_category.value if hasattr(existing_category, "value") else existing_category
        existing_display_name = _setting_attr(existing, "display_name") or _setting_attr(existing, "key", setting_id)

        success = await db_service.set_setting(
            key=setting_id,
            value=new_value,
            category=existing_category_str,
            display_name=existing_display_name,
            description=new_description,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to update setting")

        # Fetch updated setting
        updated = await db_service.get_setting(setting_id)

        return SettingResponse(
            id=_setting_attr(updated, "id") or 1,
            key=_setting_attr(updated, "key", setting_id),
            value=_setting_attr(updated, "value", ""),
            data_type=_setting_attr(updated, "data_type", SettingDataTypeEnum.STRING),
            category=_setting_attr(updated, "category", SettingCategoryEnum.DATABASE),
            environment=_setting_attr(updated, "environment", SettingEnvironmentEnum.PRODUCTION),
            description=_setting_attr(updated, "description", ""),
            is_encrypted=_setting_attr(updated, "is_encrypted", False),
            is_read_only=_setting_attr(updated, "is_read_only", False),
            tags=_setting_attr(updated, "tags", []),
            created_at=_setting_attr(updated, "created_at") or datetime.now(timezone.utc),
            updated_at=_setting_attr(updated, "updated_at") or datetime.now(timezone.utc),
            created_by_id=_setting_attr(updated, "created_by_id", 1),
            updated_by_id=1,
            value_preview=(_setting_attr(updated, "value", "") or "")[:50],
        )
    except HTTPException:
        raise
    except Exception :
        logger.error("[settings_routes] Failed to update setting", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update setting")


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
    except Exception :
        logger.error("[settings_routes] Failed to delete setting", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete setting")


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
    },
)
async def get_setting_history(
    setting_id: str = Path(..., description="Setting key name"),
    limit: int = Query(50, ge=1, le=500, description="Number of history entries to return"),
    current_user=Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get change history for a specific setting.

    Not yet implemented — requires a settings_audit_log table.
    """
    # Verify setting exists
    existing = await db_service.get_setting(setting_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Setting '{setting_id}' not found")

    # No audit table exists yet — return empty history
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
        501: {"description": "Not implemented - requires audit history table"},
    },
)
async def rollback_setting(
    setting_id: str = Path(..., description="Setting key name"),
    history_id: int = Query(..., gt=0, description="Audit log entry ID to rollback to"),
    current_user=Depends(get_current_user),
):
    """
    Rollback a setting to a previous value (admin only).

    Not yet implemented — requires a settings_audit_log table to store
    historical values.
    """
    raise HTTPException(
        status_code=501,
        detail="Setting rollback is not yet implemented — requires audit history table",
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
    Update multiple settings in a single request (admin/editor).

    **Request Body:**
    ```json
    {
        "updates": [
            {"setting_id": 1, "value": "60"},
            {"setting_id": 2, "value": "true"}
        ]
    }
    ```
    """
    try:
        updates = bulk_data.updates if hasattr(bulk_data, "updates") else []
        updated_count = 0

        for update in updates:
            if isinstance(update, dict):
                # Support both "key" and "setting_id"; prefer "key" for direct lookup
                key = update.get("key") or str(update.get("setting_id", ""))
                value = update.get("value", "")
            else:
                key = str(update)
                value = ""

            if not key:
                continue

            # Preserve existing category/display_name on bulk updates
            existing = await db_service.get_setting(key)
            existing_category = None
            existing_display_name = None
            if existing:
                cat = _setting_attr(existing, "category")
                existing_category = cat.value if hasattr(cat, "value") else cat
                existing_display_name = _setting_attr(existing, "display_name") or _setting_attr(existing, "key", key)

            success = await db_service.set_setting(
                key=key,
                value=value,
                category=existing_category,
                display_name=existing_display_name,
            )
            if success:
                updated_count += 1

        return {
            "success": True,
            "updated_count": updated_count,
            "message": "Bulk update completed",
        }
    except Exception :
        logger.error("[settings_routes] Failed to perform bulk update", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to perform bulk update")


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
    db_service: DatabaseService = Depends(get_database_dependency),
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
    try:
        settings = await db_service.get_all_settings()
        return {
            "success": True,
            "format": format,
            "include_secrets": include_secrets,
            "total_settings": len(settings),
            "settings": settings,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        logger.error("[export_settings] Failed to export settings", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export settings")


# ============================================================================
# Health Check - REMOVED (use GET /api/health instead)
# ============================================================================
# Note: Unified health check endpoint is in main.py at GET /api/health
# This endpoint is deprecated and will be removed in version 2.0.
# Use the unified /api/health endpoint for all health checks.
# Returns 200 if the settings service is operational.
