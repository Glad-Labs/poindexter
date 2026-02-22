# Phase 1: OAuth Token Security Audit & Remediation Plan

**Status:** In Progress  
**Date:** February 22, 2026  
**Focus:** Centralize token lifecycle management and eliminate security gaps  

---

## Current OAuth Architecture Review

### Existing Components ✅
- **OAuth Provider Base Class** (`oauth_provider.py`): Clean abstract interface
- **OAuth Manager** (`oauth_manager.py`): Factory pattern for multiple providers
- **Concrete Providers**: GitHub, Google, Facebook, Microsoft (all implement OAuthProvider)
- **Auth Routes** (`auth_unified.py`): Callback handling with CSRF protection via state tokens
- **Token Validator** (`token_validator.py`): JWT token validation logic

---

## Security Audit Findings

### Issue 1: No Centralized Token Manager ⚠️
**Problem:**
- Tokens are obtained in `auth_unified.py` routes and passed around
- No single place controlling token lifecycle (creation, expiration, refresh, revocation)
- Multiple places potentially storing/using tokens inconsistently

**Risk Level:** Medium
**Impact:** Token reuse vulnerabilities, hard to track token leakage, difficult to revoke

**Recommendation:**
Create dedicated `TokenManager` class to centralize:
- Token generation and storage
- Expiration validation
- Refresh logic  
- Revocation
- Audit logging

---

### Issue 2: Token Logging & Audit Trail Missing ⚠️
**Problem:**
- No audit logging for token operations (generation, validation, revocation)
- Difficult to investigate token security issues post-incident
- No way to know which tokens are active

**Risk Level:** Medium
**Impact:** Cannot detect token compromise, audit compliance failures

**Recommendation:**
Add audit logging for all token operations:
- `token_generated(user_id, token_id, provider, expiry)`
- `token_validated(user_id, token_id, validation_result)`
- `token_revoked(user_id, token_id, reason)`
- `token_refreshed(user_id, old_token_id, new_token_id)`

---

### Issue 3: Token Expiration Not Enforced ⚠️
**Problem:**
- Exchange code → access token happens, but no explicit expiration check before use
- Tokens might be used after expiration without detection

**Risk Level:** High
**Impact:** Expired tokens could grant unauthorized access

**Recommendation:**
Implement middleware that:
- Checks token expiration timestamp before processing request
- Automatically refreshes if supported by provider
- Logs expired token use attempts

---

### Issue 4: No Token Refresh Implementation ❌
**Problem:**
- GitHub/Google provide refresh_token in response, but we're not storing/using it
- When token expires, user must re-authenticate
  
**Risk Level:** Medium  
**Impact:** Poor UX, potential for users to copy/paste tokens across devices

**Recommendation:**
- Store refresh tokens securely (encrypted in DB, not in JWT)
- Implement automatic token refresh flow
- Handle refresh token expiration gracefully

---

### Issue 5: Token Storage Security? 🤔
**Problem:**
- Where are OAuth tokens stored after exchange?
- Are they encrypted at rest?
- How long are they kept?

**Risk Level:** High (if plaintext storage) / Low (if encrypted)
**Impact:** Token compromise if database breached

**Recommendation:**
- Implement encrypted token storage
- Redis for short-lived session tokens
- PostgreSQL (encrypted field) for refresh tokens
- Clear retention policy (delete after X days)

---

### Issue 6: No Token Rotation ❌
**Problem:**
- Same token used for entire session
- If token compromised, attacker has full session access

**Risk Level:** Medium
**Impact:** Extended breach window

**Recommendation:**
Implement short-lived token rotation:
- Exchange OAuth token for short-lived JWT (15min expiry)
- Use refresh tokens to get new JWT
- Invalidate old JWT on refresh

---

## Remediation Plan (6 hours)

### Hour 1-2: Create TokenManager Class
**File:** `services/token_manager.py`

```python
class TokenManager:
    """Centralized OAuth & session token manager"""
    
    async def create_session_token(
        self, 
        user_id: str, 
        provider: str,
        oauth_token: str,
        expires_in_seconds: int = 3600
    ) -> SessionToken:
        """Create short-lived session token for user"""
        
    async def validate_token(self, token: str) -> bool:
        """Check if token is valid and not expired"""
        
    async def refresh_token(self, token: str) -> str:
        """Get new token from refresh token"""
        
    async def revoke_token(self, token: str, reason: str) -> None:
        """Invalidate a token immediately"""
        
    async def get_user_tokens(self, user_id: str) -> List[TokenInfo]:
        """Get all active tokens for user"""
```

**Deliverable:** Token Manager class with full lifecycle management

---

### Hour 2-3: Add Audit Logging
**File:** New `middleware/token_audit.py`

```python
async def audit_log_token_operation(
    operation: str,  # "created", "validated", "revoked", "refreshed"
    user_id: str,
    token_id: str,
    provider: str,
    status: str,  # "success", "failed", "expired"
    details: Dict[str, Any] = None
) -> None:
    """Log token operations for audit trail"""
```

**Schema Addition:**
```sql
CREATE TABLE token_audit_log (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    token_id TEXT NOT NULL,
    operation VARCHAR(50),
    provider VARCHAR(50),
    status VARCHAR(20),
    ip_address INET,
    user_agent TEXT,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    INDEX (user_id, created_at),
    INDEX (token_id)
);
```

**Deliverable:** Complete audit trail for token operations

---

### Hour 3-4: Implement Token Expiration Enforcement
**File:** Modify `auth_unified.py` + new `middleware/token_validation.py`

```python
@app.middleware("http")
async def token_validation_middleware(request: Request, call_next):
    """Validate token expiration before processing request"""
    
    if "Authorization" in request.headers:
        token = request.headers["Authorization"].replace("Bearer ", "")
        is_valid = await token_manager.validate_token(token)
        
        if not is_valid:
            # Try to refresh
            refreshed = await token_manager.refresh_token(token)
            if refreshed:
                request.headers["Authorization"] = f"Bearer {refreshed}"
            else:
                return JSONResponse(status_code=401, detail="Token expired")
    
    return await call_next(request)
```

**Deliverable:** Automatic token expiration validation on every request

---

### Hour 4-5: Secure Token Storage
**File:** Add to `services/database_service.py`

```python
async def store_oauth_token(
    self,
    user_id: str,
    provider: str,
    access_token: str,
    refresh_token: Optional[str],
    expires_at: datetime
) -> str:
    """Store OAuth token encrypted in database"""
    # Encrypt token before storage
    encrypted_token = self.encryption_service.encrypt(access_token)
    
    # Store in oauth_tokens table
    token_id = await self.db.query(
        """INSERT INTO oauth_tokens 
           (user_id, provider, encrypted_token, refresh_token, expires_at)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING id
        """,
        user_id, provider, encrypted_token, refresh_token, expires_at
    )
    
    return token_id
```

**Schema Addition:**
```sql
CREATE TABLE oauth_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    provider VARCHAR(50) NOT NULL,
    encrypted_token TEXT NOT NULL,
    refresh_token TEXT,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    revoked_at TIMESTAMP,
    rotated_at TIMESTAMP,
    UNIQUE (user_id, provider),
    INDEX (user_id),
    INDEX (expires_at)
);
```

**Deliverable:** Encrypted token storage with provider per-user uniqueness

---

### Hour 5-6: Update Auth Routes to Use TokenManager
**File:** Modify `auth_unified.py`

**Changes:**
1. OAuth callback now uses `TokenManager.create_session_token()`
2. All token validations go through `TokenManager.validate_token()`
3. Audit logging on every token operation
4. Automatic refresh on expiration

```python
@router.post("/api/auth/callback/{provider}")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    token_manager: TokenManager = Depends(get_token_manager),
    db_service: DatabaseService = Depends(get_database_dependency)
):
    # Validate CSRF state
    validate_csrf_state(state)
    
    # Exchange code for OAuth token
    oauth_token = await OAuthManager.exchange_code_for_token(provider, code)
    
    # Get user info from provider
    user_info = await OAuthManager.get_user_info(provider, oauth_token)
    
    # Create/update user in database
    user = await db_service.get_or_create_user(user_info)
    
    # Store OAuth token securely
    token_id = await db_service.store_oauth_token(
        user_id=user.id,
        provider=provider,
        access_token=oauth_token.get("access_token"),
        refresh_token=oauth_token.get("refresh_token"),
        expires_at=datetime.utcnow() + timedelta(seconds=oauth_token.get("expires_in", 3600))
    )
    
    # Create session token
    session_token = await token_manager.create_session_token(
        user_id=user.id,
        provider=provider,
        oauth_token=oauth_token,
        expires_in_seconds=3600
    )
    
    # Log token creation
    await audit_log_token_operation(
        operation="created",
        user_id=user.id,
        token_id=token_id,
        provider=provider,
        status="success"
    )
    
    # Return session token (not OAuth token!)
    return {
        "access_token": session_token.token,
        "token_type": "bearer",
        "expires_in": 3600,
        "user": user
    }
```

**Deliverable:** Auth routes use centralized TokenManager for all operations

---

## Implementation Checklist

- [ ] Create `TokenManager` class with full lifecycle
- [ ] Add `token_audit_log` table to PostgreSQL
- [ ] Implement audit logging middleware
- [ ] Add `oauth_tokens` encrypted storage table
- [ ] Implement token expiration validation middleware
- [ ] Implement encryption service for tokens
- [ ] Update auth routes to use TokenManager
- [ ] Add tests for token lifecycle
- [ ] Document OAuth security practices

---

## Success Criteria

✅ **Security:**
- All tokens stored encrypted at rest
- Complete audit trail for token operations
- Tokens automatically validated on every request
- Expired tokens rejected with proper error

✅ **Compliance:**
- Token audit logs for 90 days
- Revocation capability verified
- Refresh token rotation working
- CSRF protection maintained

✅ **Operations:**
- Token operations centralized in TokenManager
- Clear error messages for token issues
- Health check includes token expiration monitoring

✅ **Testing:**
- Unit tests for TokenManager
- Integration tests for OAuth callback
- Token expiration scenarios covered
- Refresh token flow tested

---

## Timeline

- **Hour 1-2:** TokenManager class implementation
- **Hour 2-3:** Audit logging + schema
- **Hour 3-4:** Token expiration enforcement
- **Hour 4-5:** Secure storage implementation
- **Hour 5-6:** Route updates + testing

**Total:** 6 hours (can be paired with other Phase 1 work)

---

## Next Steps After Phase 1

1. **Token Rotation:** Implement automatic short-lived token rotation (15min JWT from long-lived OAuth)
2. **Multi-Device Security:** Detect and alert on token use from unusual locations
3. **Token Scoping:** Use OAuth scopes more granularly (read-only vs write vs sensitive)
4. **Refresh Token Rotation:** Implement refresh token rotation on each use

---

Status: ReadyForImplementation  
Effort: 6 hours  
Priority: CRITICAL - Security  
Review: None yet
