"""
Authentication API Routes for GLAD Labs AI Co-Founder

Provides REST endpoints for user authentication, token management, and 2FA setup.

Endpoints:
- POST /api/auth/login - Login with email/password
- POST /api/auth/register - Create new user account
- POST /api/auth/refresh - Get new access token from refresh token
- POST /api/auth/logout - Revoke current session
- GET /api/auth/me - Get current user profile
- POST /api/auth/change-password - Change user password
- POST /api/auth/setup-2fa - Enable 2FA for user
- POST /api/auth/verify-2fa-setup - Verify TOTP code during setup
- POST /api/auth/verify-2fa - Verify TOTP code during login
- POST /api/auth/disable-2fa - Disable 2FA for user
- POST /api/auth/use-backup-code - Use backup code for login
- GET /api/auth/backup-codes - Get backup codes info
- POST /api/auth/regenerate-backup-codes - Generate new backup codes

Error Responses:
- 400: Bad request (invalid input, validation error)
- 401: Unauthorized (invalid credentials, expired token)
- 403: Forbidden (insufficient permissions)
- 404: Not found (user, endpoint)
- 409: Conflict (duplicate email, 2FA already enabled)
- 429: Too many requests (rate limited)
- 500: Server error
"""

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from database import get_session
from models import User
from services.auth import (
    authenticate_user,
    validate_access_token,
    refresh_access_token_request,
    logout_user,
    validate_password_strength,
    PasswordValidator,
    AuthConfig,
)
from services.totp import (
    setup_totp_for_user,
    enable_totp,
    disable_totp,
    verify_totp_code,
    verify_backup_code,
)
from encryption import get_encryption_service


# ============================================================================
# Router Setup
# ============================================================================

router = APIRouter(prefix="/api/auth", tags=["authentication"])


# ============================================================================
# Pydantic Models (Request/Response Schemas)
# ============================================================================

class LoginRequest(BaseModel):
    """Login request schema"""
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=1, description="User password")
    device_name: Optional[str] = Field(None, description="Optional device name for tracking")


class LoginResponse(BaseModel):
    """Login response schema"""
    success: bool
    message: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None  # Seconds
    token_type: Optional[str] = None


class RegisterRequest(BaseModel):
    """User registration request schema"""
    email: EmailStr = Field(..., description="User email address")
    username: str = Field(..., min_length=3, max_length=255, description="Username")
    password: str = Field(..., description="Password (must meet strength requirements)")
    password_confirm: str = Field(..., description="Password confirmation")


class RegisterResponse(BaseModel):
    """User registration response schema"""
    success: bool
    message: str
    user_id: Optional[str] = None
    email: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    """Refresh token request schema"""
    refresh_token: str = Field(..., description="Refresh token from login response")


class RefreshTokenResponse(BaseModel):
    """Refresh token response schema"""
    success: bool
    message: str
    access_token: Optional[str] = None
    expires_in: Optional[int] = None


class ChangePasswordRequest(BaseModel):
    """Change password request schema"""
    current_password: str = Field(..., description="Current password for verification")
    new_password: str = Field(..., description="New password")
    new_password_confirm: str = Field(..., description="New password confirmation")


class ChangePasswordResponse(BaseModel):
    """Change password response schema"""
    success: bool
    message: str


class UserProfile(BaseModel):
    """User profile response schema"""
    id: str
    email: str
    username: str
    is_active: bool
    totp_enabled: bool
    created_at: str
    last_login: Optional[str] = None


class SetupTwoFAResponse(BaseModel):
    """Setup 2FA response schema"""
    success: bool
    message: str
    secret: Optional[str] = None
    qr_code_url: Optional[str] = None
    backup_codes: Optional[list] = None


class VerifyTwoFASetupRequest(BaseModel):
    """Verify 2FA setup request schema"""
    secret: str = Field(..., description="TOTP secret from setup response")
    totp_code: str = Field(..., pattern="^[0-9]{6}$", description="6-digit TOTP code")
    backup_codes: list = Field(..., description="Backup codes from setup response")


class VerifyTwoFASetupResponse(BaseModel):
    """Verify 2FA setup response schema"""
    success: bool
    message: str


class VerifyTwoFARequest(BaseModel):
    """Verify 2FA code request schema"""
    totp_code: Optional[str] = Field(None, pattern="^[0-9]{6}$", description="6-digit TOTP code")
    backup_code: Optional[str] = Field(None, description="Backup code (if TOTP unavailable)")


class VerifyTwoFAResponse(BaseModel):
    """Verify 2FA response schema"""
    success: bool
    message: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None


class BackupCodesResponse(BaseModel):
    """Backup codes info response schema"""
    success: bool
    message: str
    remaining_codes: Optional[int] = None
    codes: Optional[list] = None  # Only in regenerate response


# ============================================================================
# Helper Functions
# ============================================================================

def get_current_user(
    request: Request,
    db: Session = Depends(get_session)
) -> User:
    """
    Dependency to get current authenticated user from request.
    
    Validates JWT token from Authorization header.
    
    Raises:
        HTTPException(401): If token is missing or invalid
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
    
    # Get user from database
    user = db.query(User).filter_by(id=claims["user_id"]).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    return user


def get_client_ip(request: Request) -> str:
    """Get client IP address from request"""
    # Check for X-Forwarded-For header (for proxies)
    if request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    
    # Fallback to client connection IP
    return request.client.host if request.client else "unknown"


# ============================================================================
# Authentication Endpoints
# ============================================================================

@router.post("/login", response_model=LoginResponse)
def login(
    request: Request,
    login_req: LoginRequest,
    db: Session = Depends(get_session)
) -> LoginResponse:
    """
    Login with email and password.
    
    Returns access and refresh tokens on success.
    
    Status Codes:
    - 200: Login successful
    - 400: Invalid request
    - 401: Invalid credentials, account locked, or account inactive
    - 429: Too many failed attempts
    """
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "Unknown")
    
    success, message, tokens = authenticate_user(
        db,
        login_req.email,
        login_req.password,
        ip_address,
        user_agent,
        login_req.device_name
    )
    
    if success:
        return LoginResponse(
            success=True,
            message=message,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_in=tokens["expires_in"],
            token_type=tokens["token_type"],
        )
    else:
        # Map error messages to HTTP status codes
        if "account_locked" in message:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=message,
            )
        elif "account_inactive" in message:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=message,
            )
        else:  # invalid_credentials
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(
    register_req: RegisterRequest,
    db: Session = Depends(get_session)
) -> RegisterResponse:
    """
    Create a new user account.
    
    Status Codes:
    - 201: Account created
    - 400: Invalid request or weak password
    - 409: Email already registered
    """
    # Validate passwords match
    if register_req.password != register_req.password_confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match",
        )
    
    # Validate password strength
    is_strong, error_msg = validate_password_strength(register_req.password)
    if not is_strong:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password too weak: {error_msg}",
        )
    
    # Check if email already exists
    existing_user = db.query(User).filter_by(email=register_req.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    
    # Check if username already exists
    existing_username = db.query(User).filter_by(username=register_req.username).first()
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    
    # Create new user
    encryption = get_encryption_service()
    password_hash, password_salt = encryption.hash_password(register_req.password)
    
    new_user = User(
        email=register_req.email,
        username=register_req.username,
        password_hash=password_hash,
        password_salt=password_salt,
        is_active=True,
    )
    
    # TODO: Assign default role (VIEWER)
    # from models import Role
    # viewer_role = db.query(Role).filter_by(name="VIEWER").first()
    # if viewer_role:
    #     from models import UserRole
    #     user_role = UserRole(user=new_user, role=viewer_role)
    #     db.add(user_role)
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return RegisterResponse(
        success=True,
        message="Account created successfully",
        user_id=str(new_user.id),
        email=new_user.email,
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
def refresh(
    request: Request,
    refresh_req: RefreshTokenRequest,
    db: Session = Depends(get_session)
) -> RefreshTokenResponse:
    """
    Get a new access token using a refresh token.
    
    Status Codes:
    - 200: New access token issued
    - 400: Invalid request
    - 401: Invalid or expired refresh token
    """
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "Unknown")
    
    success, message, new_access_token = refresh_access_token_request(
        db,
        refresh_req.refresh_token,
        ip_address,
        user_agent
    )
    
    if success:
        return RefreshTokenResponse(
            success=True,
            message=message,
            access_token=new_access_token,
            expires_in=AuthConfig.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=message,
        )


@router.post("/logout")
def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
) -> dict:
    """
    Log out current user by revoking their session.
    
    Status Codes:
    - 200: Logout successful
    - 401: Not authenticated
    """
    # Get token JTI from request
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    
    if token:
        is_valid, claims = validate_access_token(token)
        if is_valid:
            logout_user(db, claims["jti"])
    
    return {
        "success": True,
        "message": "Logged out successfully",
    }


@router.get("/me", response_model=UserProfile)
def get_profile(
    current_user: User = Depends(get_current_user)
) -> UserProfile:
    """
    Get current user's profile.
    
    Status Codes:
    - 200: Profile returned
    - 401: Not authenticated
    """
    return UserProfile(
        id=str(current_user.id),
        email=current_user.email,
        username=current_user.username,
        is_active=current_user.is_active,
        totp_enabled=current_user.totp_enabled,
        created_at=current_user.created_at.isoformat(),
        last_login=current_user.last_login.isoformat() if current_user.last_login else None,
    )


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    change_req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
) -> ChangePasswordResponse:
    """
    Change current user's password.
    
    Status Codes:
    - 200: Password changed
    - 400: Invalid request or weak password
    - 401: Not authenticated or invalid current password
    """
    encryption = get_encryption_service()
    
    # Verify current password
    if not encryption.verify_password(
        change_req.current_password,
        current_user.password_hash,
        current_user.password_salt
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    
    # Validate new passwords match
    if change_req.new_password != change_req.new_password_confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New passwords do not match",
        )
    
    # Validate password strength
    is_strong, error_msg = validate_password_strength(change_req.new_password)
    if not is_strong:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password too weak: {error_msg}",
        )
    
    # Prevent reusing same password
    if change_req.new_password == change_req.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password",
        )
    
    # Hash and store new password
    new_hash, new_salt = encryption.hash_password(change_req.new_password)
    current_user.password_hash = new_hash
    current_user.password_salt = new_salt
    current_user.last_password_change = datetime.now(timezone.utc)
    
    db.commit()
    
    return ChangePasswordResponse(
        success=True,
        message="Password changed successfully",
    )


# ============================================================================
# 2FA Endpoints
# ============================================================================

@router.post("/setup-2fa", response_model=SetupTwoFAResponse)
def setup_2fa(
    current_user: User = Depends(get_current_user)
) -> SetupTwoFAResponse:
    """
    Initiate 2FA setup for current user.
    
    Returns TOTP secret and QR code URL for scanning with authenticator app.
    User must verify the code before 2FA is actually enabled.
    
    Status Codes:
    - 200: Setup initiated
    - 409: 2FA already enabled
    """
    # Check if 2FA already enabled
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA is already enabled for this account",
        )
    
    secret, qr_code_url, backup_codes = setup_totp_for_user(current_user)
    
    return SetupTwoFAResponse(
        success=True,
        message="2FA setup initiated. Please scan QR code with authenticator app.",
        secret=secret,
        qr_code_url=qr_code_url,
        backup_codes=backup_codes,  # User must save these!
    )


@router.post("/verify-2fa-setup", response_model=VerifyTwoFASetupResponse)
def verify_2fa_setup(
    verify_req: VerifyTwoFASetupRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
) -> VerifyTwoFASetupResponse:
    """
    Complete 2FA setup by verifying TOTP code.
    
    User must provide valid 6-digit code from their authenticator app.
    
    Status Codes:
    - 200: 2FA enabled
    - 400: Invalid code or bad request
    - 401: Not authenticated
    - 409: 2FA already enabled
    """
    # Check if 2FA already enabled
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA is already enabled for this account",
        )
    
    # Verify TOTP code matches secret
    import pyotp
    totp = pyotp.TOTP(verify_req.secret)
    
    if not totp.verify(verify_req.totp_code, valid_window=1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code",
        )
    
    # Enable 2FA for user
    enable_totp(current_user, verify_req.secret, verify_req.backup_codes, db)
    
    return VerifyTwoFASetupResponse(
        success=True,
        message="2FA enabled successfully. Please save your backup codes in a secure location.",
    )


@router.post("/disable-2fa")
def disable_2fa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
) -> dict:
    """
    Disable 2FA for current user.
    
    Status Codes:
    - 200: 2FA disabled
    - 401: Not authenticated
    - 409: 2FA not enabled
    """
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA is not enabled for this account",
        )
    
    disable_totp(current_user, db)
    
    return {
        "success": True,
        "message": "2FA disabled successfully",
    }


@router.get("/backup-codes", response_model=BackupCodesResponse)
def get_backup_codes_info(
    current_user: User = Depends(get_current_user)
) -> BackupCodesResponse:
    """
    Get information about backup codes.
    
    Returns count of remaining unused backup codes.
    Does NOT return the actual codes.
    
    Status Codes:
    - 200: Info returned
    - 401: Not authenticated
    """
    from services.totp import BackupCodeManager
    
    remaining = BackupCodeManager.get_remaining_backup_codes_count(current_user)
    
    return BackupCodesResponse(
        success=True,
        message=f"You have {remaining} backup codes remaining",
        remaining_codes=remaining,
    )


@router.post("/regenerate-backup-codes")
def regenerate_backup_codes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
) -> dict:
    """
    Generate new backup codes for current user.
    
    Old backup codes are invalidated.
    
    Status Codes:
    - 200: Codes regenerated
    - 401: Not authenticated
    - 409: 2FA not enabled
    """
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA is not enabled for this account",
        )
    
    from services.totp import BackupCodeManager
    
    backup_codes = BackupCodeManager.generate_backup_codes()
    BackupCodeManager.store_backup_codes(current_user, backup_codes)
    db.commit()
    
    return {
        "success": True,
        "message": "Backup codes regenerated. Please save these in a secure location.",
        "codes": backup_codes,
    }
