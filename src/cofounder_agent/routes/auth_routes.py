"""
Authentication API Routes for GLAD Labs AI Co-Founder

SIMPLIFIED FOR ASYNCPG MIGRATION:
- Removed SQLAlchemy dependencies
- Using mock/stub responses for now
- Will be enhanced after asyncpg integration is complete
"""

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr, Field

from services.database_service import DatabaseService
from services.auth import validate_access_token


router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================================
# Pydantic Models (Response Schemas)
# ============================================================================

class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    accessToken: str
    refreshToken: Optional[str] = None
    user: dict


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str
    password_confirm: str


class RegisterResponse(BaseModel):
    success: bool
    message: str
    user: Optional[dict] = None


class RefreshTokenResponse(BaseModel):
    success: bool
    accessToken: str


class ChangePasswordResponse(BaseModel):
    success: bool
    message: str


class UserProfile(BaseModel):
    id: str
    email: str
    username: str
    is_active: bool
    created_at: str


# ============================================================================
# Dependency: Get Current User
# ============================================================================

async def get_current_user(request: Request) -> dict:
    """
    Async dependency to get current authenticated user from request.
    
    Validates JWT token from Authorization header.
    Returns user dict from database.
    
    For now, returns mock user for development.
    """
    # Get token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    # Validate token
    is_valid, claims = validate_access_token(token)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # For development: return mock user
    user_id = claims.get("user_id")
    return {
        "id": user_id,
        "email": claims.get("email", "dev@example.com"),
        "username": claims.get("username", "dev-user"),
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# Authentication Endpoints (Minimal/Stub Implementations)
# ============================================================================

@router.post("/login", response_model=LoginResponse)
async def login(login_req: LoginRequest) -> LoginResponse:
    """
    Login with email and password (STUB IMPLEMENTATION).
    
    Returns:
        LoginResponse with access token and mock user
    """
    # TODO: Implement proper login with password verification
    user_id = f"user_{hash(login_req.email) % 10000}"
    return LoginResponse(
        success=True,
        accessToken=f"mock_jwt_token_{user_id}",
        refreshToken=None,
        user={
            "id": user_id,
            "email": login_req.email,
            "username": login_req.email.split("@")[0],
            "is_active": True,
        },
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(register_req: RegisterRequest) -> RegisterResponse:
    """
    Create a new user account (STUB IMPLEMENTATION).
    
    Returns:
        RegisterResponse with success status
    """
    # TODO: Implement proper user registration with email/username checks
    if register_req.password != register_req.password_confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match",
        )
    
    user_id = f"user_{hash(register_req.email) % 10000}"
    return RegisterResponse(
        success=True,
        message="User registered successfully",
        user={
            "id": user_id,
            "email": register_req.email,
            "username": register_req.username,
            "is_active": True,
        },
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh(request: Request) -> RefreshTokenResponse:
    """
    Get new access token from refresh token (STUB).
    """
    # TODO: Implement token refresh logic
    return RefreshTokenResponse(
        success=True,
        accessToken="mock_jwt_token_refreshed",
    )


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Logout and revoke current session (STUB).
    """
    return {"success": True, "message": "Logged out successfully"}


@router.get("/me", response_model=UserProfile)
async def get_me(current_user: dict = Depends(get_current_user)) -> UserProfile:
    """
    Get current user profile.
    """
    return UserProfile(
        id=current_user["id"],
        email=current_user["email"],
        username=current_user["username"],
        is_active=current_user["is_active"],
        created_at=current_user.get("created_at", datetime.now(timezone.utc).isoformat()),
    )


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    request: dict,  # TODO: Define proper request model
    current_user: dict = Depends(get_current_user)
) -> ChangePasswordResponse:
    """
    Change user password (STUB).
    """
    # TODO: Implement password change
    return ChangePasswordResponse(
        success=True,
        message="Password changed successfully",
    )


# ============================================================================
# 2FA Endpoints (Stubs - not implemented yet)
# ============================================================================

@router.post("/setup-2fa")
async def setup_2fa(current_user: dict = Depends(get_current_user)) -> dict:
    """Setup 2FA (STUB)"""
    return {"success": True, "message": "2FA setup not yet implemented"}


@router.post("/verify-2fa-setup")
async def verify_2fa_setup(request: dict) -> dict:
    """Verify 2FA setup (STUB)"""
    return {"success": True, "message": "2FA verification not yet implemented"}


@router.post("/disable-2fa")
async def disable_2fa(current_user: dict = Depends(get_current_user)) -> dict:
    """Disable 2FA (STUB)"""
    return {"success": True, "message": "2FA disable not yet implemented"}


@router.get("/backup-codes")
async def get_backup_codes(current_user: dict = Depends(get_current_user)) -> dict:
    """Get backup codes (STUB)"""
    return {"success": True, "codes": []}


@router.post("/regenerate-backup-codes")
async def regenerate_backup_codes(current_user: dict = Depends(get_current_user)) -> dict:
    """Regenerate backup codes (STUB)"""
    return {"success": True, "message": "Backup codes regenerated"}
