# 🎯 Action Plan - Fix Auth Endpoint Shadowing

## Problem Summary

Three route files all register endpoints on `/api/auth` prefix, causing shadowing:

- `POST /api/auth/logout` registered 3 times
- `GET /api/auth/me` registered 2 times

Only the FIRST registration works. Later ones are silently ignored.

---

## Current Registration Order (main.py lines 310-330)

```python
# Order matters - first registration wins!

1️⃣  app.include_router(github_oauth_router)      # routes/auth.py
    ├─ POST /api/auth/logout                     ✅ ACTIVE
    ├─ POST /api/auth/github-callback
    ├─ GET  /api/auth/verify
    └─ GET  /api/auth/health

2️⃣  app.include_router(auth_router)              # routes/auth_routes.py
    ├─ POST /api/auth/login
    ├─ POST /api/auth/register
    ├─ POST /api/auth/logout                     ❌ SHADOWED (duplicate of #1)
    ├─ GET  /api/auth/me                         ✅ ACTIVE
    └─ ... 2FA endpoints

3️⃣  app.include_router(oauth_routes_router)      # routes/oauth_routes.py
    ├─ GET  /{provider}/login
    ├─ GET  /{provider}/callback
    ├─ GET  /api/auth/me                         ❌ SHADOWED (duplicate of #2)
    ├─ POST /api/auth/logout                     ❌ SHADOWED (duplicate of #1)
    └─ ... provider linking endpoints
```

---

## What's Broken?

### 1. Logout Endpoint (POST /api/auth/logout)

Only GitHub logout works (from `routes/auth.py`)

- ❌ Traditional JWT logout broken (from `routes/auth_routes.py`)
- ❌ OAuth logout broken (from `routes/oauth_routes.py`)

### 2. Me Endpoint (GET /api/auth/me)

Only traditional auth works (from `routes/auth_routes.py`)

- ❌ OAuth me endpoint broken (from `routes/oauth_routes.py`)

### 3. Frontend Impact

Frontend clients don't know which one is active:

- Logout might call GitHub implementation even if using traditional auth
- User profile might return wrong data

---

## Fix Options

### Option A: Unified Endpoints (RECOMMENDED)

Single endpoint for each function, handles all auth types

**Implementation:**

```python
# routes/auth_unified.py (NEW FILE)

@router.post("/logout")
async def unified_logout(current_user: User = Depends(get_current_user)):
    """
    Logout endpoint for ALL auth types.

    Auto-detects auth provider from JWT token:
    - Traditional JWT: Invalidate token
    - OAuth: Revoke refresh token
    - GitHub: Invalidate session

    Returns: {"status": "logged_out", "message": "Successfully logged out"}
    """
    auth_provider = current_user.get("auth_provider", "jwt")

    if auth_provider == "github":
        # GitHub-specific logout
        await revoke_github_session(current_user["id"])
    elif auth_provider == "oauth":
        # OAuth-specific logout
        await revoke_oauth_refresh_token(current_user["token_id"])
    else:
        # Traditional JWT logout
        await add_to_token_blacklist(current_user["token"])

    return {"status": "logged_out", "message": "Successfully logged out"}


@router.get("/me", response_model=UserProfile)
async def unified_get_current_user(current_user: User = Depends(get_current_user)):
    """
    Get current user profile for ALL auth types.

    Returns user profile with auth provider info:
    - name, email, profile_picture
    - auth_provider: "jwt", "oauth", "github"
    - last_login_at
    """
    return UserProfile(
        id=str(current_user["id"]),
        email=current_user["email"],
        name=current_user.get("name", ""),
        profile_picture=current_user.get("picture", ""),
        auth_provider=current_user.get("auth_provider", "jwt"),
        last_login_at=current_user.get("last_login_at"),
    )
```

**Migration Steps:**

1. Create `routes/auth_unified.py` with consolidated endpoints
2. Register unified router in main.py (single registration)
3. Remove duplicate endpoints from `auth_routes.py` and `oauth_routes.py`
4. Remove duplicate registrations from main.py
5. Test all three auth flows

**Registration (main.py):**

```python
from routes.auth_unified import router as auth_router  # ✅ Single source

app.include_router(auth_router)  # Only one registration!
```

---

### Option B: Route Priority with Path Parameters

Different endpoints for different auth types

**Implementation:**

```python
# Keep separate, use different paths

POST /api/auth/logout?provider=github
POST /api/auth/logout?provider=oauth
POST /api/auth/logout                  # Default = JWT

# Pro: No shadowing
# Con: Complex client logic, unclear which to use
```

**Not Recommended** - confusing for clients

---

### Option C: Lazy Consolidation

Keep shadowing but document it

**Implementation:**

- Remove duplicate registrations, keep only GitHub
- Force all clients to use GitHub implementation
- Document: "GitHub logout is canonical for all auth types"

**Pro:** Minimal code changes  
**Con:** Confusing naming, not maintainable

---

## Recommended Solution: Option A

**Why?**

- ✅ Single source of truth for each endpoint
- ✅ Auto-detects auth type from JWT claims
- ✅ Clear, maintainable code
- ✅ Works for all auth flows
- ✅ Easy to extend (add new providers)

---

## Implementation Checklist

- [ ] Create `routes/auth_unified.py` with consolidated endpoints
- [ ] Implement `unified_logout()` handling all auth types
- [ ] Implement `unified_get_current_user()` returning auth provider info
- [ ] Update JWT token schema to include `auth_provider` claim
- [ ] Register unified router in main.py
- [ ] Remove duplicate logout/me endpoints from:
  - [ ] routes/auth_routes.py
  - [ ] routes/oauth_routes.py
  - [ ] routes/auth.py
- [ ] Remove old registrations from main.py
- [ ] Test logout with each auth type:
  - [ ] Traditional JWT login → logout
  - [ ] OAuth login → logout
  - [ ] GitHub OAuth login → logout
- [ ] Test /me endpoint with each auth type
- [ ] Update frontend to use unified endpoints
- [ ] Update API documentation

---

## Code to Remove

### From main.py (lines ~310-330)

```python
# ❌ REMOVE THESE LINES - will be replaced by single unified registration

# Include route routers
app.include_router(github_oauth_router)    # ❌ Remove
app.include_router(auth_router)            # ❌ Remove (only keep unified)
app.include_router(oauth_routes_router)    # ❌ Remove
```

### Replace With:

```python
# ✅ Add this single line
from routes.auth_unified import router as unified_auth_router
app.include_router(unified_auth_router)
```

---

## Files to Modify

1. **NEW:** `routes/auth_unified.py` (create)
   - Consolidated logout endpoint
   - Consolidated me endpoint
   - Unified error handling

2. **EDIT:** `routes/auth_routes.py`
   - Remove `@router.post("/logout")`
   - Remove `@router.get("/me")`

3. **EDIT:** `routes/oauth_routes.py`
   - Remove `@router.get("/me")`
   - Remove `@router.post("/logout")`

4. **EDIT:** `routes/auth.py`
   - Remove `@router.post("/logout")`

5. **EDIT:** `main.py`
   - Add import of unified router
   - Register unified router
   - Remove 3 separate auth router registrations

---

## Testing Endpoints

After fix, verify:

```bash
# Test traditional JWT logout
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer {jwt_token}" \
  -H "Content-Type: application/json"

# Test OAuth logout
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer {oauth_token}" \
  -H "Content-Type: application/json"

# Test GitHub logout
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer {github_token}" \
  -H "Content-Type: application/json"

# Test /me for each auth type
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer {token}"
```

---

## Priority

**🔴 HIGH** - This is actively breaking auth for non-GitHub users

Would you like me to implement Option A (unified endpoints)?
