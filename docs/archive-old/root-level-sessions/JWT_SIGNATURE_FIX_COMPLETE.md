# JWT Token Signature Verification Fix - Complete Solution

## Problem Summary

You were still getting **401 Unauthorized** errors even after implementing JWT token support. The root cause:

**The mock JWT tokens were being created but the signatures were NOT being verified correctly by the backend.**

### Error Message

```
[verify_token] Invalid token error: Signature verification failed
INFO: 127.0.0.1:57208 - "GET /api/tasks?limit=100&offset=0 HTTP/1.1" 401 Unauthorized
```

The backend was checking token signatures using PyJWT's `jwt.decode()` function with HMAC-SHA256, but the mock tokens had placeholder signatures that didn't match the cryptographic signature.

---

## Root Cause Analysis

### What Was Happening

1. **Frontend (mockTokenGenerator.js)** was creating tokens with:

   ```
   Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWI...
           .mock_signature_xxxxx  ← FAKE signature
   ```

2. **Backend (token_validator.py)** was validating with:

   ```python
   payload = jwt.decode(
       token,
       AuthConfig.SECRET_KEY,  # 'development-secret-key-change-in-production'
       algorithms=[AuthConfig.ALGORITHM]  # 'HS256'
   )
   ```

   This uses **cryptographic verification** - it recalculates the HMAC-SHA256 signature and compares it to the signature in the token. A fake signature always fails.

### The Solution

Implement **proper HMAC-SHA256 signing** in the browser using the **Web Crypto API** with the **same secret** the backend uses.

---

## Changes Made

### 1. Fixed Redis Import Bug (CRITICAL - Blocking)

**File:** `src/cofounder_agent/services/redis_cache.py`

```python
# OLD - Redis type only imported in try block
try:
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# NEW - Type is available even when Redis not installed
try:
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    Redis = None  # ← Type placeholder
```

**Why:** Without this, the backend wouldn't start if Redis wasn't installed.

### 2. Updated JWT Secret in Token Generator

**File:** `web/oversight-hub/src/utils/mockTokenGenerator.js`

```javascript
// OLD
const DEV_JWT_SECRET = 'dev-secret-change-in-production';

// NEW
const DEV_JWT_SECRET = 'development-secret-key-change-in-production';
```

**Why:** Must match the exact secret in `.env.local` line 77: `JWT_SECRET=development-secret-key-change-in-production`

### 3. Implemented Proper HMAC-SHA256 Signing

**File:** `web/oversight-hub/src/utils/mockTokenGenerator.js`

```javascript
// OLD - Fake signature
const signature =
  'mock_signature_' + Math.random().toString(36).substring(2, 15);
return `${headerEncoded}.${payloadEncoded}.${signature}`;

// NEW - Cryptographically signed
const signatureEncoded = await hmacSha256Sign(
  `${headerEncoded}.${payloadEncoded}`,
  DEV_JWT_SECRET
);
return `${headerEncoded}.${payloadEncoded}.${signatureEncoded}`;

// Uses Web Crypto API (browser native)
async function hmacSha256Sign(message, secret) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    encoder.encode(message)
  );
  // Convert to base64url format
  return base64UrlEncode(binaryString);
}
```

**Why:** This creates signatures that match what the backend expects when it runs `jwt.decode()`.

### 4. Made Token Generation Async

**File:** `web/oversight-hub/src/utils/mockTokenGenerator.js`

```javascript
// OLD - Synchronous
export const createMockJWTToken = (userData = {}) => { ... }

// NEW - Asynchronous (because crypto.subtle.sign is async)
export const createMockJWTToken = async (userData = {}) => { ... }
```

**Why:** The Web Crypto API's `crypto.subtle.sign()` returns a Promise, so we must await it.

### 5. Updated All Token Generation Calls

**File:** `web/oversight-hub/src/services/authService.js`

```javascript
// OLD
const mockToken = createMockJWTToken(mockUser);

// NEW
const mockToken = await createMockJWTToken(mockUser);
```

Updated in 2 locations:

- `exchangeCodeForToken()` (line ~47)
- `initializeDevToken()` (line ~210)

### 6. Updated Auth Context

**File:** `web/oversight-hub/src/context/AuthContext.jsx`

```javascript
// OLD
initializeDevToken();

// NEW
await initializeDevToken();
```

**Why:** Must await the async function to ensure token is created before continuing.

### 7. Improved Buffer Handling

**File:** `web/oversight-hub/src/utils/mockTokenGenerator.js`

```javascript
// OLD - Can fail with large buffers
String.fromCharCode.apply(null, new Uint8Array(signature));

// NEW - Robust iteration
const signatureArray = new Uint8Array(signature);
let binaryString = '';
for (let i = 0; i < signatureArray.length; i++) {
  binaryString += String.fromCharCode(signatureArray[i]);
}
```

---

## Token Generation Flow

### Before (Broken)

```
Frontend: 'mock_jwt_token_xxxxx'
          ↓
Backend: jwt.decode(token, secret, algorithms=['HS256'])
         Signature validation: ❌ FAILS
         Response: 401 Unauthorized
```

### After (Fixed)

```
Frontend:
  1. Header: {"alg":"HS256","typ":"JWT"}
  2. Payload: {"sub":"dev-user","type":"access","exp":...}
  3. Message: header.payload
  4. Signature = HMAC-SHA256(message, secret)
  5. Token: header.payload.signature
  ↓
Backend: jwt.decode(token, secret, algorithms=['HS256'])
         Signature validation: ✅ PASSES
         Response: 200 OK with tasks
```

---

## Environment Configuration

### .env.local (Backend Secret)

```dotenv
JWT_SECRET=development-secret-key-change-in-production
JWT_ALGORITHM=HS256
```

### Frontend Code (Must Match Backend Secret)

```javascript
const DEV_JWT_SECRET = 'development-secret-key-change-in-production';
```

If you change the JWT_SECRET in `.env.local`, you **MUST** update the frontend constant too!

---

## Testing

### Option 1: Test HTML File

```bash
cd c:/Users/mattm/glad-labs-website
# Open test-token-generator.html in browser
# Click "Generate Token" then "Test Backend"
```

### Option 2: Test with curl

```bash
# Generate token in Node.js (see test-token-generator.html for code)
BACKEND_STARTED=true
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/tasks?limit=5
# Should return 200 OK with tasks data
```

### Option 3: Test with Browser DevTools

```javascript
// In browser console (after page loads)
const response = await fetch('http://localhost:8000/api/tasks', {
  headers: { Authorization: 'Bearer <token>' },
});
console.log(response.status); // Should be 200
```

---

## Production Considerations

### For Production Deployment

**Do NOT use the development secret in production:**

1. Generate a strong, random secret:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Set it in your production environment:

   ```bash
   export JWT_SECRET="your-generated-secret-here"
   # Or in .env file
   JWT_SECRET=your-generated-secret-here
   ```

3. For real OAuth (GitHub), tokens come from GitHub servers:

   ```javascript
   // No need to generate tokens - GitHub provides them
   const response = await fetch(`${API_BASE_URL}/api/auth/github-callback`, {
     method: 'POST',
     body: JSON.stringify({ code }),
   });
   const { token } = await response.json();
   // token is now a real, GitHub-verified JWT
   ```

4. Update mockTokenGenerator backend secret:
   ```python
   # Only used in development mode
   if os.getenv("ENVIRONMENT") == "development":
       _secret = os.getenv("JWT_SECRET") or "dev-secret-change-in-production"
   ```

---

## Verification Checklist

✅ **Backend is running:**

```bash
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Check: http://localhost:8000/docs (API docs)
```

✅ **JWT secret matches:**

- `.env.local`: `JWT_SECRET=development-secret-key-change-in-production`
- Frontend code: `const DEV_JWT_SECRET = 'development-secret-key-change-in-production'`

✅ **Token format is valid:**

```
Header: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9
Payload: eyJzdWIiOiJkZXYtdXNlciIsInR5cGUiOiJhY2Nlc3MiLCJleHAiOjE3NjUx...
Signature: S-PTwaeg3LpMzxeN53qHwDYlCS1P7AsNUrTyE8crUFI
           ↑ Real HMAC-SHA256 signature, not 'mock_...'
```

✅ **Backend accepts tokens:**

```bash
curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/tasks?limit=5
# Returns: {"tasks": [...]} with 200 OK
```

---

## Summary of Fixes

| Issue                        | Fix                                 | Impact                          |
| ---------------------------- | ----------------------------------- | ------------------------------- |
| Token signature not verified | Implemented HMAC-SHA256 signing     | ✅ Backend now accepts tokens   |
| Redis import error           | Added `Redis = None` fallback       | ✅ Backend starts without Redis |
| Wrong JWT secret             | Updated to match .env.local         | ✅ Signatures match             |
| Async signature generation   | Made createMockJWTToken async       | ✅ Proper Web Crypto API usage  |
| Buffer overflow risk         | Implemented loop instead of apply() | ✅ Robust signature conversion  |

---

## Next Steps

1. **Clear browser cache** (`Ctrl+Shift+R`)
2. **Restart frontend** to load new code
3. **Test authentication** - should see 200 OK responses
4. **Verify token in localStorage** - should have proper JWT format
5. **Check network tab** - Authorization header should include token

**Expected result:** All API calls return 200 OK instead of 401 Unauthorized.
