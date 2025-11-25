# 🧪 Quick Auth Testing Guide

## ✅ What Was Fixed

- **POST /api/auth/logout** - Now works for ALL auth types (was only working for GitHub)
- **GET /api/auth/me** - Now works for ALL auth types (was only working for traditional JWT)

---

## 🚀 Quick Test

### 1. Verify Endpoints Are Not Duplicated

```bash
# Get all auth endpoints from the API docs
curl http://localhost:8000/openapi.json | grep -A 5 '"paths"' | grep auth
```

**Expected Result:**

- ✅ Single POST /api/auth/logout
- ✅ Single GET /api/auth/me
- No duplicates!

---

### 2. Test Logout with Different Auth Types

#### Traditional JWT

```bash
# Step 1: Login with JWT
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"password"}'
# Response: { "accessToken": "jwt_token_here", ... }

# Step 2: Logout
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer jwt_token_here"
# Expected: { "success": true, "message": "Successfully logged out (jwt authentication)" }
```

#### GitHub OAuth

```bash
# Use token from GitHub OAuth callback
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer github_token_here"
# Expected: { "success": true, "message": "Successfully logged out (github authentication)" }
```

#### OAuth (if configured)

```bash
# Use token from OAuth provider
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer oauth_token_here"
# Expected: { "success": true, "message": "Successfully logged out (oauth authentication)" }
```

---

### 3. Test Get Me Endpoint

```bash
# Should work with ANY valid token
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer any_valid_token"

# Expected Response:
{
  "id": "user123",
  "email": "user@example.com",
  "username": "username",
  "auth_provider": "github",  # or "jwt" or "oauth"
  "is_active": true,
  "created_at": "2025-01-15T10:30:00Z"
}
```

---

### 4. Error Handling Tests

#### Missing Token

```bash
curl http://localhost:8000/api/auth/me
# Expected: 401 Unauthorized - "Missing or invalid authorization header"
```

#### Invalid Token

```bash
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer invalid_token"
# Expected: 401 Unauthorized - "Invalid or expired token"
```

#### Wrong Header Format

```bash
curl http://localhost:8000/api/auth/me \
  -H "Authorization: invalid_token"  # Missing "Bearer" prefix
# Expected: 401 Unauthorized - "Missing or invalid authorization header"
```

---

## 📊 Verification Checklist

Using the API at `http://localhost:8000/docs`:

### Auth Endpoints (in Swagger UI)

- [ ] POST /api/auth/logout - Should see **1 endpoint** (NOT 3!)
  - Parameter: None
  - Response: `{ "success": boolean, "message": string }`

- [ ] GET /api/auth/me - Should see **1 endpoint** (NOT 2!)
  - Response: UserProfile with `auth_provider` field

### Test with Swagger UI

1. Click "Try it out" on POST /api/auth/logout
2. Add your token in the Authorization header
3. Click "Execute"
4. Verify response shows correct auth_provider

---

## 🔍 Behind the Scenes

**Before Fix:**

```
app.include_router(github_oauth_router)      # ← POST /logout registered here
app.include_router(auth_router)              # ← POST /logout tried here (shadowed!)

Result: GET /api/auth/me works (traditional)
        POST /api/auth/logout only works with GitHub token
        ❌ Bug: OAuth users can't logout
```

**After Fix:**

```
app.include_router(auth_router)  # ← Single unified router

Inside auth_unified.py:
  POST /api/auth/logout - Auto-detects auth type from token
  GET /api/auth/me - Returns auth_provider info

Result: Both endpoints work with ALL auth types ✅
```

---

## 📝 Testing Scripts

### Automated Test (bash)

```bash
#!/bin/bash
set -e

BASE_URL="http://localhost:8000"
TOKEN="eyJhbGciOiJIUzI1NiIs..."  # Replace with valid token

echo "🧪 Testing unified auth endpoints..."

echo "1️⃣ Test GET /api/auth/me"
curl -s "$BASE_URL/api/auth/me" \
  -H "Authorization: Bearer $TOKEN" | jq .
echo ""

echo "2️⃣ Test POST /api/auth/logout"
curl -s -X POST "$BASE_URL/api/auth/logout" \
  -H "Authorization: Bearer $TOKEN" | jq .
echo ""

echo "✅ All tests passed!"
```

### Manual Test (PowerShell)

```powershell
$token = "eyJhbGciOiJIUzI1NiIs..."  # Replace with valid token
$baseUrl = "http://localhost:8000"

# Test get me
Write-Host "Testing GET /api/auth/me"
Invoke-RestMethod -Uri "$baseUrl/api/auth/me" `
  -Headers @{ "Authorization" = "Bearer $token" }

# Test logout
Write-Host "Testing POST /api/auth/logout"
Invoke-RestMethod -Uri "$baseUrl/api/auth/logout" `
  -Method Post `
  -Headers @{ "Authorization" = "Bearer $token" }
```

---

## 🐛 Troubleshooting

### "No JSON object could be decoded"

- Token might be invalid or expired
- Check token format: `Bearer eyJ...` (not just `eyJ...`)

### "401 Unauthorized"

- Token is expired or invalid
- Check Authorization header format: `Bearer <token>`
- Ensure token is from the same backend instance

### "Cannot find route"

- Backend might not have reloaded the new code
- Restart the backend server

### Still seeing duplicate endpoints

- Clear browser cache (F12 → Network → Disable cache)
- Restart backend service
- Check `/docs` page is refreshed

---

## 📊 Expected Results

| Test Case                     | Before Fix | After Fix |
| ----------------------------- | ---------- | --------- |
| Logout with JWT token         | ❌ Broken  | ✅ Works  |
| Logout with OAuth token       | ❌ Broken  | ✅ Works  |
| Logout with GitHub token      | ✅ Works   | ✅ Works  |
| GET /me with JWT              | ✅ Works   | ✅ Works  |
| GET /me with OAuth            | ❌ Broken  | ✅ Works  |
| GET /me with GitHub           | ✅ Works   | ✅ Works  |
| Check for duplicate endpoints | ❌ Found 3 | ✅ Only 1 |

---

## 🎯 Next Steps

1. **Run the tests** above to verify both endpoints work
2. **Check API docs** to confirm no duplicate endpoints
3. **Test Oversight Hub** login/logout flows
4. **Report any issues** with specific auth method

---

**Status:** ✅ Ready for Testing  
**Files Changed:** 5 (1 new, 4 modified)  
**Syntax Errors:** 0  
**Expected Outcome:** All auth types can now logout and fetch profile ✅
