"""
Authentication API Routes for Glad Labs AI Co-Founder

SIMPLIFIED FOR ASYNCPG MIGRATION:
- Removed SQLAlchemy dependencies
- Using mock/stub responses for now
- Will be enhanced after asyncpg integration is complete
"""

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from services.database_service import DatabaseService
from services.auth import validate_access_token


router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================================
# Pydantic Models (Response Schemas)
# ============================================================================

class LoginRequest(BaseModel):
    email: str = Field(..., pattern="^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$",
                      description="Valid email address")
    password: str = Field(..., min_length=6, max_length=128,
                         description="Password (6-128 chars)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "secure_password"
            }
        }


class LoginResponse(BaseModel):
    success: bool
    accessToken: str
    refreshToken: Optional[str] = None
    user: dict


class RegisterRequest(BaseModel):
    email: str = Field(..., pattern="^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$",
                      description="Valid email address")
    username: str = Field(..., min_length=3, max_length=50,
                         pattern="^[a-zA-Z0-9._-]+$",
                         description="Username (3-50 chars, alphanumeric + . - _)")
    password: str = Field(..., min_length=8, max_length=128,
                         description="Password (8-128 chars, strong)")
    password_confirm: str = Field(..., min_length=8, max_length=128,
                                 description="Password confirmation")
    
    @field_validator("password_confirm")
    @classmethod
    def passwords_match(cls, v, info):
        """Validate passwords match"""
        if info.data.get("password") != v:
            raise ValueError("Passwords do not match")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "newuser@example.com",
                "username": "newuser",
                "password": "SecurePassword123!",
                "password_confirm": "SecurePassword123!"
            }
        }


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
    In development mode, allows requests without auth for easier testing.
    Returns user dict from database.
    """
    import os
    
    # Development mode: allow access without auth
    if os.getenv("ENVIRONMENT", "development").lower() == "development":
        # Check if token is provided
        auth_header = request.headers.get("Authorization", "")
        
        # If no token, return mock dev user for development
        if not auth_header.startswith("Bearer "):
            return {
                "id": "dev-user-123",
                "email": "dev@localhost",
                "username": "dev-user",
                "is_active": True,
                "created_at": "2025-01-01T00:00:00Z",
            }
        
        # If token provided, validate it
        token = auth_header[7:]  # Remove "Bearer " prefix
        is_valid, claims = validate_access_token(token)
        if is_valid and claims:
            user_id = claims.get("user_id", "dev-user-123")
            return {
                "id": user_id,
                "email": claims.get("email", "dev@example.com"),
                "username": claims.get("username", "dev-user"),
                "is_active": True,
                "created_at": claims.get("created_at", "2025-01-01T00:00:00Z"),
            }
        # Invalid token in dev mode still returns dev user
        return {
            "id": "dev-user-123",
            "email": "dev@localhost",
            "username": "dev-user",
            "is_active": True,
            "created_at": "2025-01-01T00:00:00Z",
        }
    
    # Production mode: require valid auth
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
    if not is_valid or not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # For production: return authenticated user
    user_id = claims.get("user_id", "unknown")
    return {
        "id": user_id,
        "email": claims.get("email", "user@example.com"),
        "username": claims.get("username", "user"),
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


# NOTE: POST /logout and GET /me endpoints moved to routes/auth_unified.py
# (unified endpoint that works for all auth types: JWT, OAuth, GitHub)
# See: routes/auth_unified.py for consolidated implementation


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
