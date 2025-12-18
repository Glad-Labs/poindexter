# OAuth + RBAC Only Architecture (No Traditional User Management)

**Status:** Alternative approach for zero user management burden  
**Complexity:** Simplified (remove password hashing, account locking, 2FA setup)  
**Benefit:** Delegated authentication + role-based access control  
**Best For:** Single-team/organization access (GitHub OAuth team membership = roles)

---

## 🎯 Core Concept

Instead of managing users, passwords, and accounts:

- **Authentication**: Delegated to OAuth provider (GitHub)
- **Authorization**: RBAC based on OAuth team membership
- **Burden**: Zero - OAuth handles user lifecycle, you only assign roles

```
GitHub User Logs In
    ↓
GitHub OAuth Flow (handled by GitHub)
    ↓
Get GitHub User + Team Info
    ↓
Check Database: Does user exist?
    ├─ YES → Update last_login, return JWT
    └─ NO → Create user record, assign roles based on GitHub teams
    ↓
Return JWT Token + User Info
    ↓
User makes API calls with token
    ↓
Middleware checks: User has required role?
    ├─ YES → Allow request
    └─ NO → Return 403 Forbidden
```

---

## 📊 Simplified Data Model

### What You NEED (Keep)

```
✅ User (minimal - just OAuth data)
   - id (UUID)
   - github_id (unique identifier from GitHub)
   - github_username
   - github_email
   - github_avatar_url
   - last_login
   - created_at

✅ Role (RBAC)
   - id
   - name (ADMIN, EDITOR, VIEWER)
   - is_system_role (built-in roles)

✅ UserRole (Join Table)
   - user_id → Role
   - assigned_at
```

### What You DELETE (Remove)

```
❌ password_hash (no passwords = no hashing)
❌ is_locked, failed_login_attempts (OAuth handles this)
❌ totp_secret, totp_enabled, backup_codes (OAuth handles 2FA)
❌ email, username (use github_username instead)
❌ is_active (GitHub controls this)
❌ Session management (use JWT tokens instead)
❌ APIKey model (tokens from OAuth)
```

---

## 🗄️ Minimal User Model

```python
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

class User(Base):
    """
    Minimal user model - OAuth only.
    No password management, account locking, 2FA, or email field.
    GitHub OAuth is the single source of truth.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index('idx_users_github_id', 'github_id'),
        Index('idx_users_github_username', 'github_username'),
    )

    # Primary Key
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # OAuth Data (from GitHub)
    github_id = Column(Integer, unique=True, nullable=False)  # GitHub's numeric ID
    github_username = Column(String(255), unique=True, nullable=False)
    github_email = Column(String(255))  # Optional, may not be public
    github_avatar_url = Column(String(500))

    # Tracking
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(github_username={self.github_username})>"
```

**That's it!** 7 fields total. No complexity.

---

## 🔐 OAuth Flow (GitHub)

### Step 1: Redirect to GitHub

```python
@router.get("/auth/github/login")
async def github_login():
    """Redirect user to GitHub OAuth authorization"""
    github_auth_url = (
        "https://github.com/login/oauth/authorize?"
        f"client_id={GITHUB_CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        "scope=read:user,read:org_membership&"
        "state={random_state}"
    )
    return RedirectResponse(url=github_auth_url)
```

### Step 2: Handle GitHub Callback

```python
@router.get("/auth/github/callback")
async def github_callback(code: str, db: Session = Depends(get_db)):
    """
    GitHub redirects here after user authorizes.
    Exchange code for access token.
    """

    # 1. Exchange code for access token
    token_response = requests.post(
        "https://github.com/login/oauth/access_token",
        json={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
        },
        headers={"Accept": "application/json"},
    )

    access_token = token_response.json()["access_token"]

    # 2. Fetch user data from GitHub
    user_response = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    github_user = user_response.json()
    github_id = github_user["id"]
    github_username = github_user["login"]

    # 3. Check if user exists in database
    user = db.query(User).filter(User.github_id == github_id).first()

    if not user:
        # Create new user
        user = User(
            github_id=github_id,
            github_username=github_username,
            github_email=github_user.get("email"),
            github_avatar_url=github_user.get("avatar_url"),
        )

        # Assign default role
        default_role = db.query(Role).filter(Role.name == "VIEWER").first()
        user_role = UserRole(role=default_role)
        user.roles.append(user_role)

        db.add(user)

    # 4. Update last login
    user.last_login = datetime.utcnow()
    db.commit()

    # 5. Generate JWT token
    jwt_token = create_access_token({"sub": str(user.id)})

    # 6. Redirect to frontend with token
    return RedirectResponse(
        url=f"{FRONTEND_URL}/?token={jwt_token}"
    )
```

**That's the entire OAuth flow!** No password hashing, no account management, no 2FA setup.

---

## 🎯 Role Assignment Strategies

### Strategy 1: Static Assignment

```python
# On first login, assign VIEWER role
# Admin manually upgrades users to EDITOR/ADMIN in database
```

### Strategy 2: GitHub Team-Based

```python
# Fetch user's GitHub teams
teams_response = requests.get(
    "https://api.github.com/user/teams",
    headers={"Authorization": f"Bearer {access_token}"},
)

teams = teams_response.json()

# Map GitHub teams to roles
team_to_role = {
    "glad-labs/admins": "ADMIN",
    "glad-labs/editors": "EDITOR",
    "glad-labs/viewers": "VIEWER",
}

# Assign roles based on team membership
for team in teams:
    team_slug = team["slug"]
    org_slug = team["organization"]["login"]
    full_team = f"{org_slug}/{team_slug}"

    if full_team in team_to_role:
        role_name = team_to_role[full_team]
        role = db.query(Role).filter(Role.name == role_name).first()

        # Remove old roles and assign new one
        user.roles.clear()
        user_role = UserRole(role=role)
        user.roles.append(user_role)

db.commit()
```

### Strategy 3: Organization Admin = Glad Labs Admin

```python
# Check if user is org admin
org_response = requests.get(
    "https://api.github.com/user/orgs",
    headers={"Authorization": f"Bearer {access_token}"},
)

orgs = org_response.json()

# If user is admin in "glad-labs" org, make them ADMIN
for org in orgs:
    if org["login"] == "glad-labs":
        # User is org member
        role = db.query(Role).filter(Role.name == "ADMIN").first()
        user.roles.clear()
        user_role = UserRole(role=role)
        user.roles.append(user_role)
        break

db.commit()
```

---

## 🛡️ RBAC Middleware (Same as Before)

```python
from fastapi import Depends, HTTPException, status
from typing import List

def require_role(*allowed_roles: str):
    """
    Dependency to check if user has required role.

    Usage:
        @app.get("/admin/dashboard")
        async def admin_dashboard(current_user = Depends(require_role("ADMIN"))):
            return {"message": "Admin access"}
    """
    async def check_role(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        # Get user's roles
        user_roles = db.query(Role).join(UserRole).filter(
            UserRole.user_id == current_user.id
        ).all()

        user_role_names = [role.name for role in user_roles]

        # Check if user has any of the allowed roles
        if not any(role in allowed_roles for role in user_role_names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(allowed_roles)}"
            )

        return current_user

    return check_role
```

---

## 📝 Protected Endpoints

```python
@router.get("/admin/dashboard")
async def admin_dashboard(
    current_user: User = Depends(require_role("ADMIN"))
):
    """Only ADMIN role can access"""
    return {"message": f"Welcome {current_user.github_username}"}

@router.post("/content/create")
async def create_content(
    current_user: User = Depends(require_role("ADMIN", "EDITOR"))
):
    """ADMIN or EDITOR can create content"""
    return {"created_by": current_user.github_username}

@router.get("/content/view")
async def view_content(
    current_user: User = Depends(require_role("ADMIN", "EDITOR", "VIEWER"))
):
    """Anyone with a role can view"""
    return {"viewed_by": current_user.github_username}
```

---

## 🗑️ What Gets Deleted/Simplified

### Delete These from User Model

```python
# ❌ REMOVE: password_hash
# ❌ REMOVE: is_locked, failed_login_attempts, locked_until
# ❌ REMOVE: totp_secret, totp_enabled, backup_codes
# ❌ REMOVE: email (use github_email instead)
# ❌ REMOVE: username (use github_username instead)
# ❌ REMOVE: is_active (GitHub controls)
# ❌ REMOVE: created_by, updated_by (audit not needed)
# ❌ REMOVE: metadata_ (if not using)

# ✅ KEEP: id, created_at, last_login
# ✅ KEEP: github_id, github_username, github_email, github_avatar_url
# ✅ KEEP: roles relationship
```

### Delete These Services/Routes

```
❌ Password hashing service (no passwords!)
❌ Account locking logic
❌ 2FA setup/verification
❌ Email verification
❌ Password reset flow
❌ Traditional login endpoint
❌ Session management
❌ API key management
```

### Keep These

```
✅ JWT token generation/validation
✅ RBAC middleware and role checking
✅ OAuth callback handler
✅ GitHub API integration
✅ Role assignment logic
```

---

## 🚀 Database Migration

### Minimal SQL to Convert Existing Model

```sql
-- Drop password-related columns
ALTER TABLE users DROP COLUMN password_hash;
ALTER TABLE users DROP COLUMN is_locked;
ALTER TABLE users DROP COLUMN failed_login_attempts;
ALTER TABLE users DROP COLUMN locked_until;
ALTER TABLE users DROP COLUMN totp_secret;
ALTER TABLE users DROP COLUMN totp_enabled;
ALTER TABLE users DROP COLUMN backup_codes;
ALTER TABLE users DROP COLUMN created_by;
ALTER TABLE users DROP COLUMN updated_by;
ALTER TABLE users DROP COLUMN metadata;

-- Add GitHub OAuth columns
ALTER TABLE users ADD COLUMN github_id INTEGER UNIQUE NOT NULL;
ALTER TABLE users ADD COLUMN github_username VARCHAR(255) UNIQUE NOT NULL;
ALTER TABLE users ADD COLUMN github_email VARCHAR(255);
ALTER TABLE users ADD COLUMN github_avatar_url VARCHAR(500);

-- Update existing users (if any)
-- You'll need to manually map existing users to GitHub accounts
-- Or delete existing users and start fresh

-- Drop unused tables
DROP TABLE sessions;
DROP TABLE api_keys;
```

---

## 📋 Implementation Checklist

### Phase 1: Database Changes (20 min)

- [ ] Review current User model
- [ ] Decide: Keep existing users or start fresh?
- [ ] If starting fresh: Delete users and related data
- [ ] Run migration SQL
- [ ] Update SQLAlchemy models
- [ ] Create new User model (7 fields)

### Phase 2: OAuth Implementation (30 min)

- [ ] Remove password/2FA-related endpoints
- [ ] Implement GitHub OAuth login redirect
- [ ] Implement GitHub callback handler
- [ ] Implement role assignment logic
- [ ] Generate JWT tokens from GitHub user data

### Phase 3: RBAC Setup (15 min)

- [ ] Ensure require_role() middleware works
- [ ] Protect endpoints with role checking
- [ ] Test role-based access
- [ ] Verify 403 responses for unauthorized access

### Phase 4: Testing (15 min)

- [ ] Test complete OAuth flow
- [ ] Test role checking on protected routes
- [ ] Test role assignment strategies
- [ ] Test JWT token validation

### Phase 5: Cleanup (15 min)

- [ ] Remove password hashing service
- [ ] Remove account locking logic
- [ ] Remove 2FA service
- [ ] Remove unused auth routes
- [ ] Update Swagger docs
- [ ] Remove unused tests

**TOTAL: 1.5 hours to complete**

---

## ✅ Benefits

| Aspect              | Traditional       | OAuth Only            |
| ------------------- | ----------------- | --------------------- |
| Password Management | You manage        | GitHub manages        |
| Account Locking     | You manage        | GitHub manages        |
| 2FA Setup           | You manage        | GitHub manages        |
| Email Verification  | You manage        | N/A (GitHub verified) |
| Password Reset      | You manage        | GitHub handles        |
| User Lifecycle      | You manage        | GitHub manages        |
| RBAC                | You manage        | You manage            |
| Burden              | HIGH              | ZERO                  |
| Security            | You responsible   | GitHub responsible    |
| Complexity          | HIGH (20+ fields) | LOW (7 fields)        |

---

## ⚠️ Tradeoffs

**Pros:**

- ✅ Zero user management overhead
- ✅ GitHub handles all authentication complexity
- ✅ Built-in 2FA support (at GitHub level)
- ✅ Simpler database schema
- ✅ Smaller User model (7 fields vs 20+)
- ✅ OAuth tokens refresh automatically
- ✅ User can revoke access in GitHub settings

**Cons:**

- ❌ Only works with GitHub (not email/password)
- ❌ User must have GitHub account
- ❌ GitHub outage = no access (but rare)
- ❌ OAuth token revocation = immediate lockout (actually a pro!)
- ❌ Can't have role-based access without GitHub team setup

---

## 🎯 Recommended Setup

```
DEFAULT ROLES:
├─ ADMIN (system role, manually assigned)
├─ EDITOR (for content creators)
└─ VIEWER (default role for new users)

ROLE ASSIGNMENT:
├─ Everyone gets VIEWER on first login
├─ Admin manually promotes to EDITOR/ADMIN
│  (or set up GitHub team mapping)
└─ Revoke role = immediate access loss

GITHUB SETUP:
├─ Create "glad-labs/admins" team
├─ Create "glad-labs/editors" team
├─ Map teams to roles in code
└─ Users added to team → automatic role assignment
```

---

## 🔄 Migration Path

If you want to move from traditional auth to OAuth:

```
Step 1: Keep current auth system running
Step 2: Add GitHub OAuth alongside (dual auth)
Step 3: Users migrate to OAuth voluntarily
Step 4: After migration complete, disable traditional auth
Step 5: Delete all password/2FA/session data
```

---

## 💡 Final Answer

**Yes, absolutely!** OAuth + RBAC only is:

- ✅ Fully supported by your architecture
- ✅ Actually simpler than traditional auth
- ✅ Zero user management burden
- ✅ Better security (delegated to GitHub)
- ✅ Perfect for team-based access

**Next steps:**

1. Decide: Keep existing users or start fresh?
2. Choose role assignment strategy (static, team-based, or org-based)
3. Delete all password/2FA fields from User model
4. Implement GitHub OAuth callback
5. Add role assignment logic
6. Test RBAC with protected endpoints

**Time to implement: 1.5-2 hours**
