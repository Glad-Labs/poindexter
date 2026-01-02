# `.env` Configuration Review Summary

**Status**: ⚠️ Configuration Issues Found  
**Severity**: 🔴 CRITICAL (GitHub secret exposed)

---

## Quick Summary

Your monorepo has 3 service `.env` files across:

- Root: `.env.local` (backend + root config)
- Frontend: `web/oversight-hub/.env.local` (React admin)
- Frontend: `web/public-site/.env.local` (Next.js public site)

### Issues Found:

| Issue                         | Severity    | File                 | Action                   |
| ----------------------------- | ----------- | -------------------- | ------------------------ |
| GitHub secret exposed         | 🔴 CRITICAL | `.env.local`         | Revoke immediately       |
| JWT secret is dev default     | 🔴 CRITICAL | `.env.local`         | Generate new secret      |
| Mock auth enabled in prod     | ⚠️ MEDIUM   | oversight-hub        | Set to false in prod     |
| Duplicate variables           | ⚠️ MEDIUM   | oversight-hub        | Remove duplicates        |
| GitHub Client IDs don't match | ⚠️ MEDIUM   | root + oversight-hub | Align IDs                |
| No production configs         | ⚠️ MEDIUM   | All                  | Create `.env.production` |

---

## Current Configuration Structure

```
glad-labs-website/
├── .env.example              ✅ Template
├── .env.local               ⚠️ EXPOSED SECRET - FIX NOW!
├── .env.production          ❌ MISSING
├── .gitignore               ✅ Correctly ignores .env files
│
├── web/oversight-hub/
│   ├── .env.example         ✅ Template
│   ├── .env.local          ⚠️ Duplicates, unmatched GitHub ID
│   └── .env.production      ❌ MISSING
│
└── web/public-site/
    ├── .env.example         ✅ Template
    ├── .env.local          ⚠️ Incomplete (GA/AdSense placeholders)
    └── .env.production      ❌ MISSING
```

---

## Critical: GitHub Secret Exposed

### Current Value:

```dotenv
GITHUB_CLIENT_SECRET=a2b98d4eb47ba4b657b214a1ad494cb692c111c7
```

### Why This Is Critical:

- Anyone with this file can impersonate your GitHub OAuth app
- Could be accidentally committed or exposed
- Security risk if repository is compromised

### Fix (5 minutes):

```bash
# 1. Go to GitHub and revoke the secret
# https://github.com/settings/developers
# Delete: a2b98d4eb47ba4b657b214a1ad494cb692c111c7

# 2. Generate new secret on GitHub
# Copy the new secret

# 3. Update .env.local
nano .env.local
# Find: GITHUB_CLIENT_SECRET=a2b98d4eb...
# Replace with new secret
# Save: Ctrl+O, Enter, Ctrl+X

# 4. Restart services
npm run dev
```

---

## Critical: Dev JWT Secret in Production

### Current Value:

```dotenv
JWT_SECRET=dev-jwt-secret-change-in-production-to-random-64-chars
```

### Why This Is Critical:

- Anyone knowing the secret can forge JWT tokens
- Sessions can be hijacked
- Production is vulnerable

### Fix (2 minutes):

```bash
# 1. Generate random secret
openssl rand -base64 32
# Example output: aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890+/=

# 2. Update .env.local
nano .env.local
# Find: JWT_SECRET=dev-jwt-secret-...
# Replace with: JWT_SECRET=<output-from-above>

# 3. Restart backend
npm run dev:cofounder
```

---

## Medium Issues

### 1. Mock Auth Enabled by Default

**File**: `web/oversight-hub/.env.local`

```dotenv
REACT_APP_USE_MOCK_AUTH=true  # ⚠️ Anyone can log in!
```

**Fix for production**: Set to `false`

### 2. Duplicate Variables

**File**: `web/oversight-hub/.env.local`

Lines 9 and 32 both define:

```dotenv
REACT_APP_API_URL=http://localhost:8000
```

**Fix**: Remove one (line 32)

### 3. GitHub Client IDs Don't Match

- Root: `Ov23liMUM5PuVfu7F4kB`
- Oversight Hub: `Ov23liAcCMWrS5DihFnl`

**Fix**: Use same Client ID in both places (choose one GitHub app)

### 4. Missing Production Configs

**Missing Files**:

- `.env.production` (root)
- `web/oversight-hub/.env.production`
- `web/public-site/.env.production`

**Fix**: Create for production deployment

---

## What's Working ✅

- `.gitignore` correctly prevents `.env*` commits
- Environment variable structure is logical
- `.env.example` files are well-documented
- Database configuration is correct
- Ollama configuration is correct
- Development setup works

---

## Recommended Configuration

### Root `.env.local` (Backend)

```dotenv
# Critical variables
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev
JWT_SECRET=<random-64-chars>  # Not dev default!
GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB  # Consistent
GITHUB_CLIENT_SECRET=<new-revoked-secret>

# Optional
OLLAMA_HOST=http://localhost:11434
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,http://localhost:8000
```

### web/oversight-hub/.env.local (React Admin)

```dotenv
# Single source of truth - avoid duplication
REACT_APP_API_URL=http://localhost:8000
REACT_APP_GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB  # Match root
REACT_APP_USE_MOCK_AUTH=true  # Development only
REACT_APP_LOG_LEVEL=debug

# No duplicates, no extra vars
```

### web/public-site/.env.local (Next.js Public)

```dotenv
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
NEXT_PUBLIC_SITE_URL=http://localhost:3000
NEXT_PUBLIC_GA_ID=G-XXXXXXXXXX  # Leave as placeholder
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-XXXXXXXXXX  # Leave as placeholder
```

---

## Action Items (In Priority Order)

### 🔴 Emergency (Now - 5 min)

1. Revoke GitHub Client Secret
2. Generate new GitHub Client Secret
3. Update `.env.local` with new secret

### 🟡 High (Today - 20 min)

4. Generate JWT Secret: `openssl rand -base64 32`
5. Update JWT_SECRET in `.env.local`
6. Remove duplicate REACT_APP_API_URL from oversight-hub
7. Align GitHub Client IDs (pick one, use everywhere)

### 🟢 Medium (This week - 15 min)

8. Create `.env.production` files
9. Fill in production values (domain, database, GitHub app)
10. Document which env variables go where

### 🔵 Low (Before deployment)

11. Set up environment variables in Vercel/Railway
12. Test production config locally
13. Deploy and verify

---

## Testing Your Configuration

### Development Test:

```bash
npm run dev

# Should see:
# ✅ FastAPI backend starting on port 8000
# ✅ React admin on http://localhost:3001
# ✅ Public site on http://localhost:3000
# ✅ No JWT_SECRET warnings
# ✅ No exposed secret warnings
```

### Production Test (Before Deploying):

```bash
# 1. Create .env.production files
# 2. Fill with production values
# 3. Locally:
NODE_ENV=production npm run build
NODE_ENV=production npm run dev

# Should:
# ✅ Use production database
# ✅ Use production JWT secret
# ✅ Use production GitHub app
# ✅ Have mock auth disabled
```

---

## Files Provided

I've created these guides:

1. **`ENV_CONFIGURATION_REVIEW.md`** - Detailed analysis of all issues
2. **`CONFIGURATION_FIXES_DETAILED.md`** - Step-by-step fix instructions
3. **`ENV_CONFIGURATION_SUMMARY.md`** - This quick reference

---

## Key Takeaways

| Config Item        | Current Status     | Production Status            |
| ------------------ | ------------------ | ---------------------------- |
| Database URL       | ✅ Configured      | ⚠️ Needs update              |
| JWT Secret         | ❌ Dev default     | ❌ Needs generation          |
| GitHub Secret      | ❌ EXPOSED         | ❌ Needs revoke + regenerate |
| GitHub Client ID   | ⚠️ Mismatch        | ⚠️ Needs alignment           |
| Mock Auth          | ✅ Dev-appropriate | ❌ Must be false             |
| Ollama             | ✅ Configured      | ⚠️ May need cloud API        |
| Production configs | ❌ Missing         | ❌ Need creation             |

---

## Next Steps

1. **Read**: `CONFIGURATION_FIXES_DETAILED.md` for step-by-step instructions
2. **Execute**: Each step in order (start with GitHub secret)
3. **Test**: Run `npm run dev` and verify no errors
4. **Review**: `ENV_CONFIGURATION_REVIEW.md` for detailed explanation
5. **Deploy**: After all fixes, deploy to Vercel + Railway

---

**Estimated Time to Fix**: 30-45 minutes  
**Risk Level**: Low (configuration changes only, no code changes)  
**Urgency**: 🔴 CRITICAL (GitHub secret exposed)

Start with CONFIGURATION_FIXES_DETAILED.md now!
