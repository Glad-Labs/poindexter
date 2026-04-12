"""
Settings Management API Routes

Provides endpoints for managing application settings.

Endpoints:
- GET    /api/settings              - List all settings
- GET    /api/settings/{setting_id} - Get specific setting details
- POST   /api/settings              - Create new setting
- PUT    /api/settings/{setting_id} - Update existing setting
"""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger

logger = get_logger(__name__)
from schemas.settings_schemas import (
    SettingCategoryEnum,
    SettingCreate,
    SettingDataTypeEnum,
    SettingEnvironmentEnum,
    SettingListResponse,
    SettingResponse,
    SettingUpdate,
)
from services.database_service import DatabaseService
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
    # Accept raw strings — the DB has many categories beyond the original
    # enum (pipeline, quality, models, content, tokens, identity, etc.) and
    # locking this to SettingCategoryEnum rejects every real-world filter.
    # Same pattern as SettingResponse.category which overrides the strict
    # enum for the same reason.
    category: str | None = Query(None, description="Filter by category"),
    environment: SettingEnvironmentEnum | None = Query(
        None, description="Filter by environment"
    ),
    tags: str | None = Query(None, description="Comma-separated tag filter"),
    search: str | None = Query(None, description="Search in key and description"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """List all settings with optional category/environment filtering and pagination."""
    try:
        # Get all active settings from database (optionally filtered by category)
        all_settings = await db_service.get_all_settings(
            category=category if category else None
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
                environment=_setting_attr(
                    setting, "environment", SettingEnvironmentEnum.PRODUCTION
                ),
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
    except Exception as exc:
        logger.error("[list_settings] Failed to retrieve settings", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve settings") from exc


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
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Get details of a specific setting by ID or key name."""
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
    except Exception as exc:
        logger.error("[get_setting] Failed to retrieve setting", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve setting") from exc


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
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Create a new setting. Key must be unique."""
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
    except Exception as exc:
        logger.error("[settings_routes] Failed to create setting", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create setting") from exc


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
    token: str = Depends(verify_api_token),
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
            update_data.description
            if update_data.description
            else _setting_attr(existing, "description", "")
        )
        existing_category = _setting_attr(existing, "category")
        existing_category_str = (
            existing_category.value if hasattr(existing_category, "value") else existing_category
        )
        existing_display_name = _setting_attr(existing, "display_name") or _setting_attr(
            existing, "key", setting_id
        )

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
    except Exception as exc:
        logger.error("[settings_routes] Failed to update setting", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update setting") from exc


@router.post(
    "/{setting_id}/activate",
    status_code=status.HTTP_200_OK,
    summary="Toggle is_active on a setting (soft delete / re-enable)",
    responses={
        200: {"description": "Activation state updated"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Setting not found"},
    },
)
async def toggle_setting_active(
    setting_id: str = Path(..., description="Setting key name"),
    active: bool = Body(..., embed=True, description="New is_active value"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Enable or disable a setting without rewriting its value.

    Added for Gitea #192 / Matt 2026-04-12 soft-delete + fallback testing
    work. Flips the `is_active` flag via `admin_db.set_setting_active`,
    which also invalidates the worker's 60s settings cache so the next
    `get_setting` call reflects the change immediately.

    Body:
        {"active": true}  → re-enable
        {"active": false} → soft-delete
    """
    try:
        updated = await db_service.admin.set_setting_active(setting_id, active)
        if not updated:
            raise HTTPException(
                status_code=404, detail=f"Setting '{setting_id}' not found"
            )
        return {
            "key": setting_id,
            "is_active": active,
            "message": f"Setting {'enabled' if active else 'disabled'}",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "[settings_routes] Failed to toggle setting_id=%s active=%s",
            setting_id, active, exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to toggle setting: {exc}"
        ) from exc
