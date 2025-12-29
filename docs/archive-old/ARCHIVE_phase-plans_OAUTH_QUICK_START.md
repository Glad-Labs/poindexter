# OAuth Implementation - Quick Start (Copy-Paste Ready)

**Time:** 2 hours | **Complexity:** Medium | **Result:** Zero user management

---

## Before You Start

1. Create GitHub OAuth App:
   - Go: https://github.com/settings/developers
   - Click: "New OAuth App"
   - Name: "Glad Labs"
   - Callback: `http://localhost:8000/api/auth/github/callback`
   - Copy: Client ID and Client Secret

2. Update `.env`:

```bash
GITHUB_CLIENT_ID=your_client_id_here
GITHUB_CLIENT_SECRET=your_client_secret_here
REDIRECT_URI=http://localhost:8000/api/auth/github/callback
FRONTEND_URL=http://localhost:3000

JWT_SECRET_KEY=super-secret-key-change-in-prod
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
```

---

## Implementation Checklist

- [ ] Step 1: Update User model (20 min)
- [ ] Step 2: Implement OAuth routes (40 min)
- [ ] Step 3: Add RBAC (10 min)
- [ ] Step 4: Initialize roles (10 min)
- [ ] Step 5: Test OAuth (20 min)
- [ ] Step 6: Protect endpoints (20 min)
- [ ] Step 7: Clean up old code (20 min)

---

## Step 1: Update User Model

**File:** `src/cofounder_agent/models.py`

Replace `class User(Base):` with:

```python
class User(Base):
    """GitHub OAuth user - no password management."""

    __tablename__ = "users"
    __table_args__ = (
        Index('idx_users_github_id', 'github_id'),
        Index('idx_users_github_username', 'github_username'),
    )

    # Primary Key
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)

    # OAuth Data from GitHub
    github_id = Column(Integer, unique=True, nullable=False, index=True)
    github_username = Column(String(255), unique=True, nullable=False, index=True)
    github_email = Column(String(255))
    github_avatar_url = Column(String(500))

    # Tracking
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(github_username={self.github_username})>"
```

---

## Step 2: Implement GitHub OAuth

**File:** `src/cofounder_agent/routes/auth_routes.py`

Replace entire file with:

```python
"""GitHub OAuth authentication - no passwords, no user management."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timedelta
import requests
import os
from typing import Optional
from uuid import UUID

from src.cofounder_agent.database import get_db
from src.cofounder_agent.models import User, Role, UserRole
from src.cofounder_agent.services.auth import create_access_token, verify_token

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# Config
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/api/auth/github/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


@router.get("/github/login")
async def github_login():
    """Redirect to GitHub OAuth authorization."""
    github_auth_url = (
        f"https://github.com/login/oauth/authorize?"
        f"client_id={GITHUB_CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"scope=read:user,read:org_membership&"
        f"allow_signup=true"
    )
    return RedirectResponse(url=github_auth_url)


@router.get("/github/callback")
async def github_callback(code: str, db: Session = Depends(get_db)):
    """Handle GitHub OAuth callback."""

    try:
        # Exchange code for token
        token_response = requests.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=10
        )

        if token_response.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to get token")

        access_token = token_response.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="No token in response")

        # Fetch user from GitHub
        user_response = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=10
        )

        if user_response.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user")

        github_user = user_response.json()

        # Get or create user
        user = db.query(User).filter(User.github_id == github_user["id"]).first()

        if not user:
            # Create new user with VIEWER role
            user = User(
                github_id=github_user["id"],
                github_username=github_user["login"],
                github_email=github_user.get("email"),
                github_avatar_url=github_user.get("avatar_url"),
            )

            viewer_role = db.query(Role).filter(Role.name == "VIEWER").first()
            if viewer_role:
                user.roles.append(UserRole(role_id=viewer_role.id))

            db.add(user)

        # Update last login
        user.last_login = datetime.utcnow()
        db.commit()

        # Create JWT
        jwt_token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=timedelta(hours=24)
        )

        # Redirect to frontend with token
        return RedirectResponse(
            url=f"{FRONTEND_URL}/?token={jwt_token}",
            status_code=302
        )

    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UserResponse(BaseModel):
    id: UUID
    github_username: str
    github_email: Optional[str]
    github_avatar_url: Optional[str]
    last_login: Optional[datetime]

    class Config:
        from_attributes = True


@router.get("/me", response_model=UserResponse)
async def get_me(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Get current user."""
    if not token:
        raise HTTPException(status_code=401, detail="No token")

    payload = verify_token(token)
    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


@router.post("/logout")
async def logout():
    """Logout (stateless - just discard token on frontend)."""
    return {"message": "Logged out"}


# RBAC Dependency
def require_role(*allowed_roles: str):
    """Check if user has required role."""
    async def check_role(
        token: str = Depends(oauth2_scheme),
        db: Session = Depends(get_db),
    ) -> User:
        if not token:
            raise HTTPException(status_code=401, detail="No token")

        payload = verify_token(token)
        user_id = payload.get("sub")

        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user_roles = [role.name for role in user.roles]
        if not any(r in allowed_roles for r in user_roles):
            raise HTTPException(
                status_code=403,
                detail=f"Requires: {', '.join(allowed_roles)}"
            )

        return user

    return check_role
```

---

## Step 3: Initialize Roles

**File:** `scripts/init_roles.py` (create new)

```python
"""Initialize default roles."""

from sqlalchemy.orm import Session
from src.cofounder_agent.database import SessionLocal
from src.cofounder_agent.models import Role

def init_roles():
    db = SessionLocal()

    for name, desc in [
        ("ADMIN", "Full access"),
        ("EDITOR", "Can create content"),
        ("VIEWER", "Read-only"),
    ]:
        if not db.query(Role).filter(Role.name == name).first():
            db.add(Role(name=name, description=desc, is_system_role=True))
            print(f"✅ Created: {name}")

    db.commit()
    db.close()

if __name__ == "__main__":
    init_roles()
```

**Run:**

```bash
cd src/cofounder_agent
python ../../scripts/init_roles.py
```

---

## Step 4: Test It

**Terminal 1: Start backend**

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

**Terminal 2: Test**

```bash
# Test that endpoint exists
curl http://localhost:8000/api/auth/github/login -v

# You'll see a redirect to GitHub
# Click that link, authorize, and GitHub redirects back with token
```

**In browser:**

1. Visit: `http://localhost:8000/api/auth/github/login`
2. GitHub will ask to authorize
3. Click "Authorize"
4. You'll be redirected to: `http://localhost:3000/?token=eyJ...`
5. Extract token and test:

```bash
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer eyJ..."
```

**Expected response:**

```json
{
  "id": "uuid-here",
  "github_username": "your_github_username",
  "github_email": "your_email@example.com",
  "github_avatar_url": "https://avatars.githubusercontent.com/...",
  "last_login": "2024-..."
}
```

---

## Step 5: Protect Endpoints

**Example:**

```python
from src.cofounder_agent.routes.auth_routes import require_role

# Requires ADMIN role only
@router.delete("/admin/settings")
async def delete_settings(current_user = Depends(require_role("ADMIN"))):
    return {"deleted": True}

# Requires EDITOR or ADMIN
@router.post("/content/create")
async def create_content(current_user = Depends(require_role("EDITOR", "ADMIN"))):
    return {"created_by": current_user.github_username}

# Public endpoint (no role required)
@router.get("/public/info")
async def public_info():
    return {"message": "Public access"}
```

---

## Step 6: Verify It Works

**Test authenticated request:**

```bash
TOKEN="eyJ..."  # from step 4

# Admin endpoint (no role assigned yet, will fail)
curl -X POST http://localhost:8000/api/admin/settings \
  -H "Authorization: Bearer $TOKEN"
# Expected: 403 Forbidden
```

**Grant admin role:**

```bash
# Connect to database
psql glad_labs_dev

# Find user ID
SELECT id, github_username FROM users LIMIT 1;

# Get ADMIN role ID
SELECT id FROM roles WHERE name = 'ADMIN';

# Grant role
INSERT INTO user_role (user_id, role_id)
VALUES ('user-uuid-here', 'role-uuid-here');
```

**Test again:**

```bash
curl -X POST http://localhost:8000/api/admin/settings \
  -H "Authorization: Bearer $TOKEN"
# Expected: 200 OK
```

---

## Step 7: Clean Up Old Code

Delete or disable:

```bash
# Remove password service
rm src/cofounder_agent/services/password_service.py 2>/dev/null

# Remove 2FA service
rm src/cofounder_agent/services/totp_service.py 2>/dev/null

# Remove old session routes
rm src/cofounder_agent/routes/session_routes.py 2>/dev/null

# Remove API key routes
rm src/cofounder_agent/routes/api_keys.py 2>/dev/null
```

---

## Database Cleanup (Optional)

Delete password/2FA columns from existing User table:

```sql
-- BACKUP FIRST!
ALTER TABLE users DROP COLUMN IF EXISTS password_hash CASCADE;
ALTER TABLE users DROP COLUMN IF EXISTS is_locked CASCADE;
ALTER TABLE users DROP COLUMN IF EXISTS failed_login_attempts CASCADE;
ALTER TABLE users DROP COLUMN IF EXISTS totp_secret CASCADE;
ALTER TABLE users DROP COLUMN IF EXISTS totp_enabled CASCADE;
ALTER TABLE users DROP COLUMN IF EXISTS backup_codes CASCADE;
ALTER TABLE users DROP COLUMN IF EXISTS created_by CASCADE;
ALTER TABLE users DROP COLUMN IF EXISTS updated_by CASCADE;
```

---

## ✅ You're Done!

What you have:

- ✅ GitHub OAuth login
- ✅ JWT token authentication
- ✅ Role-based access control
- ✅ Zero user management burden
- ✅ No passwords to maintain
- ✅ No 2FA to manage
- ✅ No account locking to handle

What GitHub handles:

- ✅ Password security
- ✅ Account lockout
- ✅ Email verification
- ✅ 2FA if users want it
- ✅ Account recovery
- ✅ Session management

---

## 🆘 Troubleshooting

**"GitHub redirect not working"**

- Check GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in .env
- Verify OAuth App callback URL matches REDIRECT_URI

**"Token validation fails"**

- Check JWT_SECRET_KEY is set in .env
- Verify JWT_ALGORITHM matches (default: HS256)

**"User not found after OAuth"**

- Check database user was created: `SELECT * FROM users;`
- Check roles were initialized: `SELECT * FROM roles;`

**"RBAC check always fails"**

- Check user has role assigned: `SELECT * FROM user_role;`
- Use database insert to manually assign role

---

## 📚 Full Documentation

For more details, see: `OAUTH_ONLY_IMPLEMENTATION.md`
