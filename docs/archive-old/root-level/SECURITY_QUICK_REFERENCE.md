# 🔒 Security Features Quick Reference Guide

**Last Updated:** December 6, 2025  
**For:** Developers using Glad Labs security features  
**Status:** ✅ Production Ready

---

## 🚀 Quick Start: Using Security Features

### 1. Input Validation (Prevent XSS & Injection)

#### Example: Create a Blog Post

```python
from fastapi import HTTPException
from src.cofounder_agent.services.validation_service import InputValidator, ValidationError

@app.post("/api/posts")
async def create_post(request: dict):
    try:
        # Validate title (3-200 chars, no XSS, no SQL injection)
        title = InputValidator.validate_string(
            request["title"],
            "title",
            min_length=3,
            max_length=200,
            allow_html=False
        )

        # Validate content (allow markdown but not scripts)
        content = InputValidator.validate_string(
            request["content"],
            "content",
            max_length=50000,
            allow_html=False  # No <script> tags allowed
        )

        # Validate slug (alphanumeric + dashes only)
        slug = InputValidator.validate_string(
            request["slug"],
            "slug",
            pattern=r"^[a-z0-9\-]+$"
        )

        # Safe to use - no injection possible
        post = Post(title=title, content=content, slug=slug)
        db.session.add(post)
        db.session.commit()

        return {"id": post.id}

    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

#### Available Validators

```python
InputValidator.validate_string()     # With XSS/SQL injection detection
InputValidator.validate_email()      # RFC 5322 compliant
InputValidator.validate_url()        # Scheme and domain validation
InputValidator.validate_integer()    # With min/max bounds
InputValidator.validate_dict()       # With key whitelisting
InputValidator.validate_list()       # With item type checking
```

---

### 2. Webhook Security (Prevent Spoofing & Tampering)

#### Example: Handle Stripe Webhook

```python
import json
from fastapi import Request, HTTPException
from src.cofounder_agent.services.webhook_security import WebhookSecurity, WebhookSignatureError

STRIPE_WEBHOOK_SECRET = "whsec_test123..."

@app.post("/api/webhooks/stripe")
async def handle_stripe_webhook(request: Request):
    # Get signature from header
    signature = request.headers.get("stripe-signature")

    # Get timestamp from header
    timestamp = request.headers.get("stripe-timestamp")

    # Read the raw body (important for signature verification)
    body = await request.body()

    # Verify signature
    try:
        WebhookSecurity.verify_signature(
            payload=body,
            signature=signature,
            secret=STRIPE_WEBHOOK_SECRET,
            timestamp=timestamp,
            check_timestamp=True  # Prevent replay attacks
        )
    except WebhookSignatureError as e:
        # Signature verification failed - reject webhook
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Signature verified - safe to process
    data = json.loads(body)

    if data["type"] == "payment_intent.succeeded":
        process_payment(data["data"]["object"])

    return {"status": "ok"}
```

#### What Signature Verification Does

```
✅ Verifies webhook came from Stripe (not an attacker)
✅ Detects if webhook was modified in transit
✅ Prevents replay attacks (timestamp check)
✅ Requires webhook secret key (must be stored securely)
```

---

### 3. Rate Limiting (Prevent DDoS)

#### Example: Limit Webhook Processing

```python
from src.cofounder_agent.services.webhook_security import WebhookRateLimiter

# Create limiter: 100 requests per minute per source
webhook_limiter = WebhookRateLimiter(max_requests_per_minute=100)

@app.post("/api/webhooks/github")
async def handle_github_webhook(request: Request):
    # Get source identifier (IP, user ID, etc.)
    source = request.client.host

    # Check rate limit
    if not webhook_limiter.is_allowed(source):
        # Too many requests from this source
        raise HTTPException(status_code=429, detail="Too many requests")

    # Process webhook safely
    body = await request.body()
    data = json.loads(body)

    process_webhook(data)

    return {"status": "ok"}
```

#### Common Rate Limits

```python
WebhookRateLimiter(max_requests_per_minute=10)    # Strict (critical ops)
WebhookRateLimiter(max_requests_per_minute=100)   # Normal (webhooks)
WebhookRateLimiter(max_requests_per_minute=1000)  # High volume (APIs)
```

---

### 4. Authentication (Verify User Identity)

#### Example: Create JWT Token

```python
from src.cofounder_agent.services.auth_service import JWTService

jwt_service = JWTService(secret_key="your-secret-key", algorithm="HS256")

@app.post("/api/login")
async def login(email: str, password: str):
    user = find_user_by_email(email)

    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Create token that expires in 1 hour
    token = jwt_service.create_token(
        subject=user.id,
        expires_delta=timedelta(hours=1)
    )

    return {"access_token": token, "token_type": "bearer"}
```

#### Example: Use JWT in Protected Routes

```python
from fastapi import Depends, HTTPException, status

@app.get("/api/profile")
async def get_profile(token: str = Header(None)):
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    # Verify token
    try:
        payload = jwt_service.verify_token(token)
        user_id = payload["sub"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = get_user(user_id)
    return {"id": user.id, "email": user.email}
```

---

### 5. Authorization (Verify User Permissions)

#### Example: Role-Based Access Control

```python
from enum import Enum

class Role(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

def require_role(required_role: Role):
    async def check_role(user: User):
        if user.role != required_role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return Depends(check_role)

# Only admins can delete posts
@app.delete("/api/posts/{id}")
async def delete_post(id: int, user: User = require_role(Role.ADMIN)):
    post = get_post(id)
    db.session.delete(post)
    db.session.commit()
    return {"status": "deleted"}

# Editors can create posts
@app.post("/api/posts")
async def create_post(data: dict, user: User = require_role(Role.EDITOR)):
    # Create post
    pass

# Everyone can read posts
@app.get("/api/posts")
async def list_posts():
    # Return posts
    pass
```

---

## 🛡️ Common Attack Scenarios & How Security Features Prevent Them

### Scenario 1: SQL Injection Attack

**Attack:** User enters `"admin' OR '1'='1"` in login form  
**Without Protection:** Query becomes `SELECT * FROM users WHERE email = 'admin' OR '1'='1'` → All users returned!  
**With InputValidator:** Detects SQL pattern, rejects with error

```python
# ❌ BAD - Vulnerable
query = f"SELECT * FROM users WHERE email = '{user_email}'"

# ✅ GOOD - Protected
email = InputValidator.validate_email(user_email)  # Detects injection
# Then use parameterized query
query = "SELECT * FROM users WHERE email = ?"
cursor.execute(query, (email,))
```

### Scenario 2: XSS (Cross-Site Scripting)

**Attack:** User enters `<script>alert('xss')</script>` in comment  
**Without Protection:** Script executes when comment is viewed  
**With InputValidator:** Detects HTML/JavaScript, rejects

```python
# ❌ BAD - Vulnerable
comment = request.data["comment"]
save_to_db(comment)

# ✅ GOOD - Protected
comment = InputValidator.validate_string(
    request.data["comment"],
    allow_html=False  # Detects <script>, javascript:, etc.
)
```

### Scenario 3: Fake Webhook from Attacker

**Attack:** Attacker sends webhook pretending to be Stripe  
**Without Protection:** System processes it as real order  
**With WebhookSecurity:** Signature verification fails

```python
# ❌ BAD - Vulnerable
@app.post("/api/webhooks/stripe")
async def handle_webhook(data: dict):
    if data["type"] == "payment_intent.succeeded":
        mark_order_paid(data["order_id"])

# ✅ GOOD - Protected
@app.post("/api/webhooks/stripe")
async def handle_webhook(request: Request):
    body = await request.body()
    signature = request.headers["stripe-signature"]

    # Verify signature - will fail for fake webhooks
    WebhookSecurity.verify_signature(body, signature, STRIPE_SECRET)

    data = json.loads(body)
    mark_order_paid(data["order_id"])
```

### Scenario 4: DDoS Attack via Webhooks

**Attack:** Attacker sends 10,000 webhooks/second  
**Without Protection:** System crashes from overload  
**With WebhookRateLimiter:** Excess requests rejected

```python
limiter = WebhookRateLimiter(max_requests_per_minute=100)

@app.post("/api/webhooks/events")
async def handle_event(request: Request):
    if not limiter.is_allowed(request.client.host):
        return 429  # Too Many Requests
```

### Scenario 5: Session Hijacking

**Attack:** Attacker steals user's session cookie, uses it forever  
**Without Protection:** Attacker has permanent access  
**With JWT Expiration:** Token expires after 1 hour

```python
token = jwt_service.create_token(
    subject=user_id,
    expires_delta=timedelta(hours=1)  # Token expires after 1 hour
)
# After 1 hour, token is invalid - attacker can no longer use it
```

### Scenario 6: Unauthorized Access

**Attack:** Non-admin user tries to delete all posts  
**Without Protection:** User can delete any post  
**With RBAC:** Route checks user role

```python
@app.delete("/api/posts/{id}")
async def delete_post(id: int, user: User = require_role(Role.ADMIN)):
    # This function only runs if user.role == "admin"
    # Non-admin users get 403 Forbidden error
```

---

## 📋 Integration Checklist

### Before Using in Production

- [ ] **Input Validation**
  - [ ] All user input validated with InputValidator
  - [ ] No raw SQL queries (all parameterized)
  - [ ] HTML input has `allow_html=False`
  - [ ] Email inputs use `validate_email()`
  - [ ] URLs use `validate_url()`

- [ ] **Webhook Security**
  - [ ] All webhooks have signature verification
  - [ ] Webhook secret stored in environment (not code)
  - [ ] Timestamp checking enabled
  - [ ] Rate limiting configured
  - [ ] Error handling for invalid webhooks

- [ ] **Authentication**
  - [ ] All protected routes require JWT token
  - [ ] Token expiration set (1 hour recommended)
  - [ ] JWT secret stored securely (environment)
  - [ ] Password hashing uses bcrypt with salt
  - [ ] No plaintext passwords in logs

- [ ] **Authorization**
  - [ ] All admin routes check `role == ADMIN`
  - [ ] All editor routes check `role >= EDITOR`
  - [ ] Consistent role checks across all routes
  - [ ] User ID verified (can't modify other users' data)

- [ ] **Testing**
  - [ ] All 50+ security tests passing
  - [ ] Run tests before each deployment
  - [ ] No new vulnerabilities in dependencies

---

## 🚀 Common Code Patterns

### Pattern 1: Validate & Store

```python
# Always validate before storing
title = InputValidator.validate_string(request["title"], min_length=3)
task = Task(title=title)
db.save(task)
```

### Pattern 2: Verify Webhook

```python
# Always verify webhook signature first
WebhookSecurity.verify_signature(body, signature, secret)
data = json.loads(body)
# Then process
```

### Pattern 3: Protect Sensitive Routes

```python
# Always check auth and role
@app.delete("/api/admin/users/{id}")
async def delete_user(id: int, user: User = require_role(Role.ADMIN)):
    # User must be admin
    pass
```

### Pattern 4: Rate Limit External Input

```python
# Always rate limit webhooks
if not limiter.is_allowed(request.client.host):
    raise HTTPException(status_code=429)
# Then process
```

---

## 🔧 Configuration Recommendations

### Development

```python
InputValidator.MAX_STRING_LENGTH = 50000
WebhookRateLimiter.max_requests_per_minute = 1000  # Lenient for testing
JWT_EXPIRATION_MINUTES = 1440  # 1 day for development
```

### Production

```python
InputValidator.MAX_STRING_LENGTH = 10000  # Stricter
WebhookRateLimiter.max_requests_per_minute = 100  # Strict
JWT_EXPIRATION_MINUTES = 60  # 1 hour
PAYLOAD_MAX_SIZE_MB = 10  # Prevent memory exhaustion
```

---

## ⚠️ Common Mistakes to Avoid

### ❌ DON'T: Trust User Input Directly

```python
# WRONG - Vulnerable to XSS
comment = request.data["comment"]
html = f"<p>{comment}</p>"  # User can inject HTML/JS
```

### ✅ DO: Always Validate

```python
# CORRECT - Protected
comment = InputValidator.validate_string(
    request.data["comment"],
    allow_html=False
)
html = f"<p>{comment}</p>"  # Safe - no HTML/JS possible
```

### ❌ DON'T: Skip Signature Verification

```python
# WRONG - Accepts fake webhooks
@app.post("/api/webhooks/stripe")
def webhook(data: dict):
    process_payment(data["order_id"])  # Could be fake!
```

### ✅ DO: Always Verify Signatures

```python
# CORRECT - Rejects fake webhooks
@app.post("/api/webhooks/stripe")
def webhook(request: Request):
    WebhookSecurity.verify_signature(...)  # Verifies authenticity
    process_payment(data["order_id"])  # Definitely real
```

### ❌ DON'T: Store Plaintext Passwords

```python
# WRONG - Huge security risk
user.password = raw_password
db.save(user)
```

### ✅ DO: Hash with Bcrypt

```python
# CORRECT - Secure hashing
user.password = bcrypt.hashpw(raw_password, salt)
db.save(user)
```

### ❌ DON'T: Trust Token Without Verification

```python
# WRONG - Token could be forged
user_id = jwt.decode(token)  # Without verification!
```

### ✅ DO: Always Verify Tokens

```python
# CORRECT - Verified token
payload = jwt_service.verify_token(token)  # Signature checked
user_id = payload["sub"]
```

---

## 🚨 Emergency Response

### If You Suspect a Security Breach

1. **Immediately Stop Processing**

   ```python
   # Disable webhook endpoints
   @app.post("/api/webhooks/stripe")
   async def handle_webhook():
       return 503  # Service Unavailable
   ```

2. **Check Logs**

   ```bash
   # Look for suspicious activity
   grep "invalid signature" logs/webhook.log
   grep "injection" logs/app.log
   ```

3. **Rotate Secrets**

   ```python
   # Update webhook secret in environment
   NEW_WEBHOOK_SECRET = "new-secret-generated-securely"
   # Notify webhook provider to update secret
   ```

4. **Review & Patch**
   - Run security tests: `pytest tests/test_*security.py`
   - Identify vulnerability
   - Apply fix
   - Deploy new version

---

## 📚 Additional Resources

- **Full Security Test Suite:** `src/cofounder_agent/tests/`
- **Security Documentation:** `src/cofounder_agent/tests/SECURITY_TESTING_DOCUMENTATION.md`
- **OWASP Top 10:** https://owasp.org/www-project-top-ten/
- **CWE/SANS Top 25:** https://cwe.mitre.org/top25/

---

**Version:** 1.0  
**Last Updated:** December 6, 2025  
**Status:** ✅ Production Ready
