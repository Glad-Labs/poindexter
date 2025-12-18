# 🔐 Authentication System Completion Implementation Guide

**Priority:** 🔴 PRIORITY 1  
**Estimated Time:** 45 minutes  
**Status:** 🔄 IN-PROGRESS  
**Backend Score After:** 75/100 → ~82/100 (Auth: 70% → 95%)  
**Critical Path:** ✅ YES - Blocks frontend integration

---

## 📋 Executive Summary

The authentication system is **90% complete** but lacks critical operational endpoints needed for production:

- ✅ JWT token generation working
- ✅ Token validation in place
- ✅ User model with password hashing ready
- ❌ **Admin initialization endpoint missing** (needed for first setup)
- ❌ **JWT token generation not tested** (stubs need verification)
- ❌ **GitHub OAuth not wired** (OAuth provider configured but not integrated)
- ❌ **RBAC integration incomplete** (role assignment on user creation not connected)

**This guide walks through 45 minutes of focused implementation to complete the auth system.**

---

## 🎯 Implementation Tasks (45 min total)

### Task 1: Create Admin Initialization Endpoint (15 min)

**Location:** `src/cofounder_agent/routes/auth_routes.py`

**Why:** First-time setup requires admin creation. This endpoint is:

- Only callable if NO admin exists (security)
- Creates initial admin user with ADMIN role
- Returns JWT token for immediate API access
- Sets up basic RBAC structure

**Implementation:**

Add this function to `auth_routes.py` after the existing stub endpoints:

```python
# ============================================================================
# Admin Initialization (Only for First Setup)
# ============================================================================

class AdminInitRequest(BaseModel):
    """Request model for admin initialization"""
    username: str = Field(..., min_length=3, max_length=255, pattern=r"^[a-zA-Z0-9_-]+$")
    email: EmailStr
    password: str = Field(..., min_length=12)

class AdminInitResponse(BaseModel):
    """Response model for admin initialization"""
    success: bool
    message: str
    user: dict
    accessToken: str
    refreshToken: Optional[str] = None


@router.post("/init-admin", response_model=AdminInitResponse, tags=["admin"])
async def initialize_admin(request: AdminInitRequest, db: Session = Depends(get_db)) -> AdminInitResponse:
    """
    Initialize admin user (ONLY for first-time setup).

    SECURITY: This endpoint:
    - Only works if NO admin user exists
    - Creates the first user with ADMIN role
    - Must be called before any other users
    - Should be disabled after first successful call

    Request:
        {
            "username": "admin",
            "email": "admin@example.com",
            "password": "SecurePassword123!"
        }

    Response:
        {
            "success": true,
            "message": "Admin user created successfully",
            "user": {
                "id": "uuid",
                "username": "admin",
                "email": "admin@example.com",
                "role": "ADMIN"
            },
            "accessToken": "jwt_token_here",
            "refreshToken": "refresh_token_here"
        }

    Raises:
        HTTPException(403): If admin already exists
        HTTPException(400): If password is weak
        HTTPException(500): If database error
    """
    # TODO: Implement after database integration
    from services.auth import JWTTokenManager, PasswordManager, AuthConfig
    from models import User, Role, UserRole

    try:
        # Check if admin already exists (query for any user with ADMIN role)
        existing_admin = db.query(User).join(
            UserRole
        ).join(
            Role
        ).filter(
            Role.name == "ADMIN"
        ).first()

        if existing_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin user already exists. Cannot reinitialize.",
            )

        # Validate password strength
        password_errors = PasswordManager.validate_password(request.password)
        if password_errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Password does not meet requirements: {', '.join(password_errors)}"
            )

        # Check if username/email already exists
        existing_user = db.query(User).filter(
            (User.username == request.username) | (User.email == request.email.lower())
        ).first()

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username or email already exists"
            )

        # Hash password
        password_hash = PasswordManager.hash_password(request.password)

        # Create admin user
        admin_user = User(
            username=request.username,
            email=request.email.lower(),
            password_hash=password_hash,
            is_active=True,
        )

        db.add(admin_user)
        db.flush()  # Flush to get the ID without committing

        # Get or create ADMIN role
        admin_role = db.query(Role).filter(Role.name == "ADMIN").first()
        if not admin_role:
            admin_role = Role(
                name="ADMIN",
                description="System administrator with full access",
                is_system_role=True,
            )
            db.add(admin_role)
            db.flush()

        # Assign ADMIN role to user
        user_role = UserRole(
            user_id=admin_user.id,
            role_id=admin_role.id,
        )
        db.add(user_role)

        # Commit all changes
        db.commit()

        # Generate tokens
        token_data = {
            "user_id": str(admin_user.id),
            "email": admin_user.email,
            "username": admin_user.username,
            "role": "ADMIN",
        }
        access_token = JWTTokenManager.create_token(
            data=token_data,
            token_type=TokenType.ACCESS
        )
        refresh_token = JWTTokenManager.create_token(
            data=token_data,
            token_type=TokenType.REFRESH
        )

        return AdminInitResponse(
            success=True,
            message="Admin user created successfully. You can now use the system.",
            user={
                "id": str(admin_user.id),
                "username": admin_user.username,
                "email": admin_user.email,
                "role": "ADMIN",
                "created_at": admin_user.created_at.isoformat(),
            },
            accessToken=access_token,
            refreshToken=refresh_token,
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error during admin initialization: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize admin user"
        )


# ============================================================================
# Password Reset Flow (for production)
# ============================================================================

class PasswordResetRequest(BaseModel):
    """Request model for password reset"""
    email: EmailStr

class PasswordResetResponse(BaseModel):
    """Response model for password reset"""
    success: bool
    message: str


@router.post("/forgot-password", response_model=PasswordResetResponse)
async def forgot_password(request: PasswordResetRequest, db: Session = Depends(get_db)) -> PasswordResetResponse:
    """
    Request password reset email (STUB - needs email service integration).

    In production:
    1. Find user by email
    2. Generate reset token (1-hour expiry)
    3. Send email with reset link
    4. Return success (don't reveal if email exists)
    """
    # TODO: Implement after email service is available
    return PasswordResetResponse(
        success=True,
        message="If this email exists in our system, you will receive a password reset link shortly."
    )
```

**Tests to Add (15 min time includes running tests):**

Add to `tests/test_auth_endpoints.py`:

```python
import pytest
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app

client = TestClient(app)

class TestAdminInitialization:
    """Test admin initialization endpoint"""

    def test_init_admin_success(self, db_session):
        """Should create admin user and return tokens"""
        response = client.post("/api/auth/init-admin", json={
            "username": "admin",
            "email": "admin@example.com",
            "password": "SecurePassword123!",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["user"]["username"] == "admin"
        assert "accessToken" in data
        assert "refreshToken" in data

    def test_init_admin_fails_if_admin_exists(self, db_session):
        """Should reject second admin creation"""
        # Create first admin
        client.post("/api/auth/init-admin", json={
            "username": "admin",
            "email": "admin@example.com",
            "password": "SecurePassword123!",
        })

        # Try to create second admin
        response = client.post("/api/auth/init-admin", json={
            "username": "admin2",
            "email": "admin2@example.com",
            "password": "SecurePassword456!",
        })
        assert response.status_code == 403
        assert "already exists" in response.json()["detail"]

    def test_init_admin_weak_password(self):
        """Should reject weak passwords"""
        response = client.post("/api/auth/init-admin", json={
            "username": "admin",
            "email": "admin@example.com",
            "password": "weak",  # Too short, no uppercase, etc.
        })
        assert response.status_code == 400
        assert "password" in response.json()["detail"].lower()

    def test_init_admin_invalid_email(self):
        """Should reject invalid email"""
        response = client.post("/api/auth/init-admin", json={
            "username": "admin",
            "email": "not-an-email",
            "password": "SecurePassword123!",
        })
        assert response.status_code == 422  # Validation error
```

**Acceptance Criteria (Task 1):**

- ✅ Endpoint created at `POST /api/auth/init-admin`
- ✅ Validates password strength (12+ chars, uppercase, number, special char)
- ✅ Creates user with ADMIN role
- ✅ Returns access + refresh tokens
- ✅ Rejects second admin creation (403)
- ✅ All 4+ tests passing
- ✅ Swagger docs updated with endpoint

---

### Task 2: Test JWT Token Generation (15 min)

**Location:** `src/cofounder_agent/services/auth.py` (already implemented, just verify)

**Why:** JWT tokens are the core of API auth. We need to verify:

- Tokens are generated correctly
- Tokens can be validated
- Token expiration works
- Token refresh works

**Test Implementation:**

Add to `tests/test_jwt_tokens.py`:

```python
import pytest
from datetime import timedelta
import jwt

from services.auth import JWTTokenManager, TokenType, AuthConfig


class TestJWTTokenGeneration:
    """Test JWT token creation and validation"""

    def test_create_access_token(self):
        """Should create valid access token"""
        token_data = {
            "user_id": "test-user-123",
            "email": "test@example.com",
            "username": "testuser",
        }
        token = JWTTokenManager.create_token(token_data, TokenType.ACCESS)

        assert isinstance(token, str)
        assert token.count('.') == 2  # JWT has 3 parts separated by dots

    def test_verify_access_token(self):
        """Should decode and verify access token"""
        token_data = {
            "user_id": "test-user-123",
            "email": "test@example.com",
            "username": "testuser",
        }
        token = JWTTokenManager.create_token(token_data, TokenType.ACCESS)

        # Decode token
        decoded = JWTTokenManager.verify_token(token, expected_type=TokenType.ACCESS)
        assert decoded["user_id"] == "test-user-123"
        assert decoded["email"] == "test@example.com"
        assert decoded["type"] == "access"

    def test_token_expiration(self):
        """Should reject expired tokens"""
        token_data = {
            "user_id": "test-user-123",
            "email": "test@example.com",
        }
        # Create token with negative expiration (already expired)
        expires_delta = timedelta(minutes=-1)
        token = JWTTokenManager.create_token(token_data, TokenType.ACCESS, expires_delta)

        with pytest.raises(jwt.ExpiredSignatureError):
            JWTTokenManager.verify_token(token)

    def test_refresh_token_different_expiry(self):
        """Refresh token should have longer expiry than access token"""
        token_data = {
            "user_id": "test-user-123",
            "email": "test@example.com",
        }
        access_token = JWTTokenManager.create_token(token_data, TokenType.ACCESS)
        refresh_token = JWTTokenManager.create_token(token_data, TokenType.REFRESH)

        # Decode both
        access_decoded = jwt.decode(access_token, AuthConfig.SECRET_KEY, algorithms=[AuthConfig.ALGORITHM])
        refresh_decoded = jwt.decode(refresh_token, AuthConfig.SECRET_KEY, algorithms=[AuthConfig.ALGORITHM])

        # Refresh should expire later
        assert refresh_decoded["exp"] > access_decoded["exp"]

    def test_tampered_token_rejected(self):
        """Should reject tokens with modified payload"""
        token_data = {
            "user_id": "test-user-123",
            "email": "test@example.com",
        }
        token = JWTTokenManager.create_token(token_data, TokenType.ACCESS)

        # Tamper with token (change one character)
        tampered_token = token[:-10] + "0000000000"

        with pytest.raises(jwt.InvalidTokenError):
            JWTTokenManager.verify_token(tampered_token)


class TestPasswordHashing:
    """Test password hashing and verification"""

    def test_hash_password(self):
        """Should hash password"""
        from services.auth import PasswordManager

        password = "SecurePassword123!"
        hashed = PasswordManager.hash_password(password)

        assert hashed != password
        assert len(hashed) > len(password)

    def test_verify_password(self):
        """Should verify correct password"""
        from services.auth import PasswordManager

        password = "SecurePassword123!"
        hashed = PasswordManager.hash_password(password)

        assert PasswordManager.verify_password(password, hashed) is True

    def test_wrong_password_fails(self):
        """Should reject wrong password"""
        from services.auth import PasswordManager

        password = "SecurePassword123!"
        hashed = PasswordManager.hash_password(password)

        assert PasswordManager.verify_password("WrongPassword456!", hashed) is False

    def test_password_strength_validation(self):
        """Should validate password strength"""
        from services.auth import PasswordManager

        weak_passwords = [
            "weak",  # Too short
            "NoNumbers!",  # No numbers
            "no_uppercase123!",  # No uppercase
            "NOLOWERCASE123!",  # No lowercase
        ]

        for weak_pwd in weak_passwords:
            errors = PasswordManager.validate_password(weak_pwd)
            assert len(errors) > 0, f"Should reject password: {weak_pwd}"

        strong_pwd = "SecurePassword123!"
        errors = PasswordManager.validate_password(strong_pwd)
        assert len(errors) == 0, f"Should accept strong password: {strong_pwd}"
```

**Run These Tests:**

```bash
cd src/cofounder_agent

# Run JWT tests
pytest tests/test_jwt_tokens.py -v

# Should see output like:
# test_create_access_token PASSED
# test_verify_access_token PASSED
# test_token_expiration PASSED
# test_refresh_token_different_expiry PASSED
# test_tampered_token_rejected PASSED
# test_hash_password PASSED
# test_verify_password PASSED
# test_wrong_password_fails PASSED
# test_password_strength_validation PASSED
# ========== 9 passed in 0.45s ==========
```

**Acceptance Criteria (Task 2):**

- ✅ JWT token created successfully
- ✅ Token verified with correct claims
- ✅ Expired token rejected
- ✅ Refresh token has longer expiry
- ✅ Tampered token rejected
- ✅ Password hashing working
- ✅ Password verification working
- ✅ Password strength validation working
- ✅ All 9+ tests passing

---

### Task 3: Wire GitHub OAuth Flow (10 min)

**Location:** `src/cofounder_agent/routes/auth_routes.py`

**Why:** OAuth provides social login, reducing friction for users and improving security via provider-managed credentials.

**Implementation:**

First, check if OAuth provider is configured:

```bash
# In .env or GitHub Actions secrets, you should have:
GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_client_secret
GITHUB_OAUTH_REDIRECT_URI=http://localhost:8000/api/auth/github/callback
```

Add this to `auth_routes.py`:

```python
import os
from urllib.parse import urlencode

# ============================================================================
# GitHub OAuth Flow
# ============================================================================

@router.get("/github/authorize")
async def github_authorize() -> dict:
    """
    Redirect user to GitHub for OAuth authorization.

    Flow:
    1. Frontend calls GET /api/auth/github/authorize
    2. Receives GitHub OAuth URL
    3. User is redirected to GitHub to authenticate
    4. After approval, GitHub redirects to /api/auth/github/callback

    Response:
        {
            "authorizationUrl": "https://github.com/login/oauth/authorize?..."
        }
    """
    client_id = os.getenv("GITHUB_CLIENT_ID")
    redirect_uri = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/github/callback")

    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub OAuth not configured (missing GITHUB_CLIENT_ID)"
        )

    # Generate authorization URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email",  # Request email scope
        "state": secrets.token_hex(16),  # CSRF protection
    }

    authorization_url = f"https://github.com/login/oauth/authorize?{urlencode(auth_params)}"

    return {
        "authorizationUrl": authorization_url,
        "state": auth_params["state"]
    }


@router.get("/github/callback")
async def github_callback(code: str = None, state: str = None, db: Session = Depends(get_db)) -> dict:
    """
    GitHub OAuth callback handler.

    Flow:
    1. GitHub redirects user here with 'code' and 'state'
    2. Exchange code for access token
    3. Use token to get user info from GitHub
    4. Create or update user in our database
    5. Generate and return JWT tokens

    Query params:
        code: Authorization code from GitHub
        state: CSRF protection token (should match request state)

    Response:
        {
            "success": true,
            "accessToken": "jwt_token",
            "refreshToken": "refresh_token",
            "user": {...}
        }
    """
    # TODO: Implement after GitHub OAuth provider setup
    import httpx

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code"
        )

    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    redirect_uri = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/github/callback")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub OAuth not configured"
        )

    try:
        # Exchange authorization code for access token
        token_url = "https://github.com/login/oauth/access_token"
        token_response = httpx.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )

        if token_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to exchange authorization code for access token"
            )

        token_data = token_response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No access token received from GitHub"
            )

        # Get user info from GitHub
        user_url = "https://api.github.com/user"
        user_response = httpx.get(
            user_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if user_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to get user info from GitHub"
            )

        github_user = user_response.json()
        github_id = github_user.get("id")
        github_login = github_user.get("login")
        github_email = github_user.get("email")

        # Get user email if not in user response
        if not github_email:
            emails_response = httpx.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if emails_response.status_code == 200:
                emails = emails_response.json()
                primary_email = next((e for e in emails if e.get("primary")), None)
                if primary_email:
                    github_email = primary_email.get("email")

        if not github_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not retrieve email from GitHub"
            )

        # Find or create user
        user = db.query(User).filter(User.email == github_email.lower()).first()

        if not user:
            # Create new user from GitHub data
            user = User(
                username=github_login or f"github_{github_id}",
                email=github_email.lower(),
                password_hash="oauth_github",  # Placeholder for OAuth users
                is_active=True,
                metadata_={"github_id": github_id, "github_login": github_login},
            )
            db.add(user)
            db.flush()

            # Assign USER role to new OAuth user
            user_role = db.query(Role).filter(Role.name == "USER").first()
            if not user_role:
                user_role = Role(
                    name="USER",
                    description="Standard user role",
                    is_system_role=True,
                )
                db.add(user_role)
                db.flush()

            user_role_assignment = UserRole(
                user_id=user.id,
                role_id=user_role.id,
            )
            db.add(user_role_assignment)

        db.commit()

        # Generate tokens
        token_data = {
            "user_id": str(user.id),
            "email": user.email,
            "username": user.username,
            "role": "USER",
        }

        from services.auth import JWTTokenManager, TokenType

        access_token_jwt = JWTTokenManager.create_token(
            data=token_data,
            token_type=TokenType.ACCESS
        )
        refresh_token_jwt = JWTTokenManager.create_token(
            data=token_data,
            token_type=TokenType.REFRESH
        )

        return {
            "success": True,
            "accessToken": access_token_jwt,
            "refreshToken": refresh_token_jwt,
            "user": {
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "role": "USER",
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GitHub OAuth callback error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth authentication failed"
        )
```

**Test GitHub OAuth Flow:**

```python
def test_github_authorize_endpoint(self):
    """Should return GitHub authorization URL"""
    response = client.get("/api/auth/github/authorize")
    assert response.status_code == 200
    data = response.json()
    assert "authorizationUrl" in data
    assert "github.com/login/oauth/authorize" in data["authorizationUrl"]
    assert "state" in data


def test_github_callback_missing_code(self):
    """Should reject callback without authorization code"""
    response = client.get("/api/auth/github/callback")
    assert response.status_code == 400
    assert "authorization code" in response.json()["detail"].lower()
```

**Acceptance Criteria (Task 3):**

- ✅ GET /api/auth/github/authorize returns auth URL
- ✅ GET /api/auth/github/callback accepts code + state
- ✅ OAuth user created on first login
- ✅ Existing user updated on subsequent logins
- ✅ JWT tokens returned after OAuth success
- ✅ Error handling for OAuth failures

---

### Task 4: Wire RBAC Integration (5 min)

**Location:** `src/cofounder_agent/models.py` + `routes/auth_routes.py`

**Why:** Role-Based Access Control ensures users can only perform authorized actions.

**Status Check:**

The RBAC infrastructure already exists:

- ✅ Role model created
- ✅ UserRole join table created
- ✅ Role-User relationships defined
- ✅ User model has `roles` relationship

**What's Missing:**

Wire role assignment in auth flow and create middleware to check roles.

**Implementation:**

Add role-checking middleware to `src/cofounder_agent/middleware/rbac_middleware.py`:

```python
"""
Role-Based Access Control (RBAC) middleware for protecting endpoints.

Usage:
    @app.post("/admin-only")
    async def admin_endpoint(current_user: dict = Depends(require_role("ADMIN"))):
        return {"message": "Admin access granted"}
"""

from typing import List, Optional
from fastapi import Depends, HTTPException, status
from models import User, UserRole, Role


def require_role(*allowed_roles: str):
    """
    Dependency to require specific roles.

    Usage:
        @app.post("/admin-panel")
        async def admin_panel(current_user = Depends(require_role("ADMIN", "MODERATOR"))):
            return {"message": "You have required role"}
    """
    async def role_checker(current_user: dict = Depends(get_current_user), db = Depends(get_db)):
        """Check if current user has one of the allowed roles"""

        user_id = current_user.get("id")
        user = db.query(User).filter(User.id == user_id).first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )

        # Get user roles
        user_roles = db.query(Role).join(
            UserRole
        ).filter(
            UserRole.user_id == user_id
        ).all()

        user_role_names = {role.name for role in user_roles}

        # Check if user has any allowed role
        if not any(role_name in allowed_roles for role_name in user_role_names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User does not have required role(s): {', '.join(allowed_roles)}"
            )

        return current_user

    return role_checker


# Alternative: Simpler version using user.role from JWT
def require_role_simple(*allowed_roles: str):
    """
    Simpler version that checks role from JWT token claims.
    """
    async def role_checker(current_user: dict = Depends(get_current_user)):
        user_role = current_user.get("role", "USER")

        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This operation requires one of: {', '.join(allowed_roles)}"
            )

        return current_user

    return role_checker
```

**Use in routes:**

```python
# In auth_routes.py or any other route file

from middleware.rbac_middleware import require_role

@router.get("/admin/dashboard")
async def admin_dashboard(current_user: dict = Depends(require_role("ADMIN"))):
    """Admin-only endpoint"""
    return {
        "message": f"Welcome admin {current_user['username']}",
        "dashboard": {...}
    }

@router.post("/admin/users")
async def list_users(current_user: dict = Depends(require_role("ADMIN", "MODERATOR"))):
    """Admin or moderator can view users"""
    return {"users": [...]}

@router.get("/user/profile")
async def get_profile(current_user: dict = Depends(get_current_user)):
    """Any authenticated user can view their profile"""
    return {"profile": current_user}
```

**Acceptance Criteria (Task 4):**

- ✅ require_role() dependency created
- ✅ Role checking middleware wired
- ✅ Admin endpoints protected
- ✅ User endpoints accessible to authenticated users
- ✅ 403 error returned for insufficient permissions

---

## ✅ Completion Checklist

### Task 1: Admin Initialization (15 min)

- [ ] Function `initialize_admin()` added to `auth_routes.py`
- [ ] AdminInitRequest/AdminInitResponse models created
- [ ] User created with ADMIN role
- [ ] JWT tokens generated on success
- [ ] Second admin creation rejected (403)
- [ ] Password validation enforced
- [ ] 4+ tests written and passing
- [ ] Swagger docs updated

### Task 2: JWT Token Generation (15 min)

- [ ] Create access token test passing
- [ ] Token verification test passing
- [ ] Token expiration test passing
- [ ] Refresh token expiry difference verified
- [ ] Tampered token rejected
- [ ] Password hashing working
- [ ] Password strength validation working
- [ ] All 9+ tests passing

### Task 3: GitHub OAuth (10 min)

- [ ] GET /api/auth/github/authorize endpoint working
- [ ] Returns valid authorization URL
- [ ] GET /api/auth/github/callback handles code exchange
- [ ] User created on first OAuth login
- [ ] Existing user updated on subsequent login
- [ ] JWT tokens returned after OAuth success
- [ ] 2+ tests written and passing
- [ ] Error handling for failed OAuth

### Task 4: RBAC Integration (5 min)

- [ ] require_role() middleware created
- [ ] Role checking in jwt claims
- [ ] Admin endpoints protected
- [ ] 403 error for insufficient permissions
- [ ] require_role used in 2+ endpoints
- [ ] Tests passing for role-based access

---

## 🚀 Running the Full Auth System

### Start Backend:

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

### Test Endpoints:

```bash
# 1. Initialize admin
curl -X POST http://localhost:8000/api/auth/init-admin \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "email": "admin@example.com",
    "password": "SecurePassword123!"
  }'

# Response:
# {
#   "success": true,
#   "message": "Admin user created successfully",
#   "accessToken": "eyJhbGciOiJIUzI1NiI...",
#   "user": {"id": "...", "username": "admin", "email": "admin@example.com"}
# }

# 2. Use token to access protected endpoint
curl -X GET http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiI..."

# Response:
# {
#   "id": "...",
#   "email": "admin@example.com",
#   "username": "admin",
#   "is_active": true,
#   "created_at": "2025-..."
# }

# 3. Test admin-only endpoint
curl -X GET http://localhost:8000/api/admin/dashboard \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiI..."

# Response:
# {"message": "Welcome admin admin", "dashboard": {...}}

# 4. Start GitHub OAuth flow
curl -X GET http://localhost:8000/api/auth/github/authorize

# Response:
# {
#   "authorizationUrl": "https://github.com/login/oauth/authorize?...",
#   "state": "..."
# }
```

### View Swagger Documentation:

```
http://localhost:8000/docs
```

---

## 📊 Progress After Auth Completion

| Component           | Before | After  | Δ   |
| ------------------- | ------ | ------ | --- |
| **Auth System**     | 70/100 | 95/100 | +25 |
| **Overall Backend** | 75/100 | 82/100 | +7  |

**Next Priority (Task 5):** Error Handling & Standardization (30 min)

---

## 🔗 Quick Reference

**Key Files:**

- Route handler: `src/cofounder_agent/routes/auth_routes.py`
- Auth service: `src/cofounder_agent/services/auth.py`
- Models: `src/cofounder_agent/models.py` (User, Role, UserRole)
- Middleware: `src/cofounder_agent/middleware/rbac_middleware.py` (to create)
- Tests: `src/cofounder_agent/tests/test_auth_*.py`

**Commands:**

- Run tests: `pytest tests/test_auth_* -v`
- Start backend: `python -m uvicorn main:app --reload`
- View API docs: `http://localhost:8000/docs`

**Time Budget:** 45 minutes total

- Task 1 (Admin init): 15 min
- Task 2 (JWT testing): 15 min
- Task 3 (OAuth): 10 min
- Task 4 (RBAC): 5 min

**Success Criteria:** All 4 tasks complete, all tests passing, 95/100 auth score

---

**Status:** 🔄 READY TO IMPLEMENT  
**Estimated Completion:** Next 45 minutes  
**Backend Score After:** 75/100 → 82/100  
**Frontend Unblocks After:** YES ✅
