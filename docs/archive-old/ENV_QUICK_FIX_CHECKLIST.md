# Environment Configuration - QUICK FIX CHECKLIST

**Time to Complete**: 30-45 minutes  
**Difficulty**: Easy (no coding required, just config updates)

---

## 🔴 CRITICAL FIXES (Do These First!)

### Fix #1: Revoke GitHub Client Secret (5 min)

**Status**: ☐ TODO

**Your exposed secret**:

```
a2b98d4eb47ba4b657b214a1ad494cb692c111c7
```

**Steps**:

```bash
# 1. Go to GitHub
open https://github.com/settings/developers

# 2. Find your OAuth app (Glad Labs or similar)
# 3. Click on it
# 4. Scroll to "Client secrets"
# 5. Click the trash icon next to:
#    a2b98d4eb47ba4b657b214a1ad494cb692c111c7
# 6. Click "Generate a new client secret"
# 7. Copy the new secret (you'll see it once)

# 8. Update your .env.local file
nano .env.local
# Find: GITHUB_CLIENT_SECRET=a2b98d4eb...
# Replace with: GITHUB_CLIENT_SECRET=<NEW_SECRET_FROM_GITHUB>
# Save: Ctrl+O, Enter, Ctrl+X

# 9. Verify the change
grep GITHUB_CLIENT_SECRET .env.local
```

---

### Fix #2: Generate New JWT Secret (3 min)

**Status**: ☐ TODO

**Your current default secret**:

```
dev-jwt-secret-change-in-production-to-random-64-chars
```

**Steps**:

```bash
# 1. Generate random secret
openssl rand -base64 32

# You'll see something like:
# aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890+/=

# 2. Copy that output

# 3. Update .env.local
nano .env.local
# Find: JWT_SECRET=dev-jwt-secret-...
# Replace with: JWT_SECRET=<PASTE_OUTPUT_ABOVE>
# Save: Ctrl+O, Enter, Ctrl+X

# 4. Verify the change
grep JWT_SECRET .env.local
```

---

## 🟡 HIGH PRIORITY FIXES

### Fix #3: Remove Duplicate Variable (2 min)

**Status**: ☐ TODO

**File**: `web/oversight-hub/.env.local`

**Problem**: REACT_APP_API_URL appears on lines 9 and 32

**Steps**:

```bash
# 1. Open the file
nano web/oversight-hub/.env.local

# 2. Find and delete the duplicate (line 32):
# REACT_APP_API_URL=http://localhost:8000

# 3. Save: Ctrl+O, Enter, Ctrl+X

# 4. Verify only one instance
grep -c "REACT_APP_API_URL" web/oversight-hub/.env.local
# Should output: 1
```

---

### Fix #4: Align GitHub Client IDs (2 min)

**Status**: ☐ TODO

**Problem**: Two different GitHub Client IDs!

- Root has: `Ov23liMUM5PuVfu7F4kB`
- Oversight Hub has: `Ov23liAcCMWrS5DihFnl`

**Steps**:

```bash
# Choose one GitHub app to use (recommend the root one)
# This assumes you're using: Ov23liMUM5PuVfu7F4kB

# 1. Update oversight-hub/.env.local
nano web/oversight-hub/.env.local
# Find: REACT_APP_GITHUB_CLIENT_ID=Ov23liAcCMWrS5DihFnl
# Replace with: REACT_APP_GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB
# Save: Ctrl+O, Enter, Ctrl+X

# 2. Verify both match now
echo "Root:" && grep GITHUB_CLIENT_ID .env.local
echo "Oversight Hub:" && grep REACT_APP_GITHUB_CLIENT_ID web/oversight-hub/.env.local
# Both should show: Ov23liMUM5PuVfu7F4kB
```

---

## 🟢 MEDIUM PRIORITY FIXES

### Fix #5: Create Root Production Config (5 min)

**Status**: ☐ TODO

**Create**: `.env.production` (in root directory)

**Steps**:

```bash
# 1. Create file
touch .env.production

# 2. Edit it
nano .env.production

# 3. Paste this content (adjust values for your production setup):
```

```dotenv
# PRODUCTION ENVIRONMENT
NODE_ENV=production
ENVIRONMENT=production
LOG_LEVEL=INFO

# DATABASE
DATABASE_URL=postgresql://user:password@prod-host:5432/glad_labs_prod
DATABASE_HOST=prod-host
DATABASE_PORT=5432
DATABASE_NAME=glad_labs_prod
DATABASE_USER=prod-user
DATABASE_PASSWORD=YOUR_PROD_PASSWORD

# SECURITY
JWT_SECRET=<GENERATE_NEW: openssl rand -base64 32>
ALLOWED_ORIGINS=https://yourdomain.com,https://oversight.yourdomain.com

# GITHUB OAUTH (Create separate GitHub app for production!)
GITHUB_CLIENT_ID=<PRODUCTION_APP_ID>
GITHUB_CLIENT_SECRET=<PRODUCTION_APP_SECRET>
GITHUB_REDIRECT_URI=https://oversight.yourdomain.com/auth/callback

# BACKEND
OLLAMA_HOST=http://ollama:11434
COFOUNDER_AGENT_PORT=8000

# FRONTEND
NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com
NEXT_PUBLIC_COFOUNDER_AGENT_URL=https://api.yourdomain.com
REACT_APP_API_URL=https://api.yourdomain.com

# OPTIONAL
PEXELS_API_KEY=YOUR_PEXELS_API_KEY
ENABLE_ANALYTICS=true
ENABLE_ERROR_REPORTING=true
```

```bash
# 4. Save: Ctrl+O, Enter, Ctrl+X
# 5. Verify file created
ls -la .env.production
```

---

### Fix #6: Create Oversight Hub Production Config (3 min)

**Status**: ☐ TODO

**Create**: `web/oversight-hub/.env.production`

**Steps**:

```bash
# 1. Create file
touch web/oversight-hub/.env.production

# 2. Edit it
nano web/oversight-hub/.env.production

# 3. Paste this content:
```

```dotenv
# Oversight Hub - Production Configuration

# API
REACT_APP_API_URL=https://api.yourdomain.com

# GITHUB OAUTH (Same as root production)
REACT_APP_GITHUB_CLIENT_ID=<PRODUCTION_APP_ID>
REACT_APP_GITHUB_REDIRECT_URI=https://oversight.yourdomain.com/auth/callback

# AUTHENTICATION - MUST BE FALSE IN PRODUCTION!
REACT_APP_USE_MOCK_AUTH=false

# SETTINGS
REACT_APP_LOG_LEVEL=info
REACT_APP_DEBUG_MODE=false
REACT_APP_API_TIMEOUT=30000
REACT_APP_AUTO_REFRESH_INTERVAL=5000

# ANALYTICS
REACT_APP_SENTRY_DSN=
REACT_APP_ENABLE_ANALYTICS=true
```

```bash
# 4. Save: Ctrl+O, Enter, Ctrl+X
# 5. Verify file created
ls -la web/oversight-hub/.env.production
```

---

### Fix #7: Create Public Site Production Config (3 min)

**Status**: ☐ TODO

**Create**: `web/public-site/.env.production`

**Steps**:

```bash
# 1. Create file
touch web/public-site/.env.production

# 2. Edit it
nano web/public-site/.env.production

# 3. Paste this content:
```

```dotenv
# Public Site - Production Configuration

# API
NEXT_PUBLIC_FASTAPI_URL=https://api.yourdomain.com
NEXT_PUBLIC_SITE_URL=https://yourdomain.com

# ANALYTICS (Get actual IDs from Google Analytics)
NEXT_PUBLIC_GA_ID=G-YOUR_ACTUAL_GA_ID

# ADSENSE (Get actual ID from Google AdSense)
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-YOUR_ACTUAL_ID
```

```bash
# 4. Save: Ctrl+O, Enter, Ctrl+X
# 5. Verify file created
ls -la web/public-site/.env.production
```

---

## ✅ VERIFICATION CHECKLIST

**Do these after all fixes**:

```bash
# 1. Verify GitHub secret changed
echo "GitHub Secret (should NOT be a2b98d4eb...):"
grep GITHUB_CLIENT_SECRET .env.local

# 2. Verify JWT secret changed
echo "JWT Secret (should NOT contain 'dev-jwt-secret'):"
grep JWT_SECRET .env.local | head -c 50

# 3. Verify no duplicates
echo "REACT_APP_API_URL count (should be 1):"
grep -c "REACT_APP_API_URL" web/oversight-hub/.env.local

# 4. Verify GitHub Client IDs match
echo "Root GitHub Client ID:"
grep "^GITHUB_CLIENT_ID=" .env.local
echo "Oversight Hub GitHub Client ID:"
grep "^REACT_APP_GITHUB_CLIENT_ID=" web/oversight-hub/.env.local

# 5. Verify production files exist
echo "Production files:"
ls -la .env.production
ls -la web/oversight-hub/.env.production
ls -la web/public-site/.env.production

# 6. Verify .gitignore protects .env files
echo ".env files in .gitignore:"
grep ".env" .gitignore
```

---

## 🧪 TEST YOUR SETUP

**After making all fixes**:

```bash
# 1. Restart services
npm run dev

# Expected output:
# ✅ Backend starting on port 8000
# ✅ Frontend (oversight-hub) on port 3001
# ✅ Frontend (public-site) on port 3000
# ✅ No JWT_SECRET warnings
# ✅ No security warnings

# 2. Check health
curl http://localhost:8000/health
# Should return: OK

# 3. Test frontend loads
open http://localhost:3001
# Should load without errors

# 4. Test OAuth
# Click "Sign in" button
# For development: Should show "Sign in (Mock)"
# (Because REACT_APP_USE_MOCK_AUTH=true in .env.local)
```

---

## 📋 SUMMARY TABLE

| Fix | File                            | Old Value                                 | New Value                    | Status |
| --- | ------------------------------- | ----------------------------------------- | ---------------------------- | ------ |
| 1   | `.env.local`                    | `GITHUB_CLIENT_SECRET=a2b98d4eb...`       | `GITHUB_CLIENT_SECRET=<NEW>` | ☐      |
| 2   | `.env.local`                    | `JWT_SECRET=dev-jwt-secret...`            | `JWT_SECRET=<RANDOM>`        | ☐      |
| 3   | `oversight-hub/.env.local`      | Duplicate line 32                         | Remove duplicate             | ☐      |
| 4   | `oversight-hub/.env.local`      | `REACT_APP_GITHUB_CLIENT_ID=Ov23liAcC...` | `Ov23liMUM5Puf...`           | ☐      |
| 5   | `.env.production`               | (Missing)                                 | Create file                  | ☐      |
| 6   | `oversight-hub/.env.production` | (Missing)                                 | Create file                  | ☐      |
| 7   | `public-site/.env.production`   | (Missing)                                 | Create file                  | ☐      |

---

## ⏱️ TIME ESTIMATE

- Fix #1 (GitHub Secret): 5 minutes
- Fix #2 (JWT Secret): 3 minutes
- Fix #3 (Remove Duplicate): 2 minutes
- Fix #4 (GitHub Client ID): 2 minutes
- Fix #5 (Root Prod Config): 5 minutes
- Fix #6 (Oversight Prod Config): 3 minutes
- Fix #7 (Public Site Prod Config): 3 minutes
- Verification: 5 minutes
- Testing: 10 minutes

**Total**: 30-45 minutes

---

## 🎯 SUCCESS CRITERIA

After completing all fixes:

- ✅ No GitHub secret exposed in `.env.local`
- ✅ Unique JWT secret generated
- ✅ No duplicate variables
- ✅ GitHub Client IDs consistent
- ✅ `.env.production` files created
- ✅ Services start without errors
- ✅ No security warnings in logs
- ✅ Ready for production deployment

---

## 📚 REFERENCE

- Full details: `CONFIGURATION_FIXES_DETAILED.md`
- Analysis: `ENV_CONFIGURATION_REVIEW.md`
- Summary: `ENV_CONFIGURATION_SUMMARY.md`

---

**Start with Fix #1 NOW!** 🚀
