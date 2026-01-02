# GitHub OAuth Production Deployment - Visual Guide

## Your Current Status

```
┌─────────────────────────────────────────────────────────────┐
│                  OAUTH IMPLEMENTATION STATUS                 │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Frontend (React) - Oversight Hub                            │
│  ═══════════════════════════════════════════════════════════ │
│                                                               │
│  ✅ Login Component (Login.jsx)                              │
│     └─ "Sign in with GitHub" button                          │
│     └─ Redirects to GitHub OAuth endpoint                    │
│                                                               │
│  ✅ OAuth Callback (AuthCallback.jsx)                        │
│     └─ Handles GitHub redirect                              │
│     └─ CSRF state validation                                │
│     └─ Sends code to backend                                │
│                                                               │
│  ✅ Auth Service (authService.js)                            │
│     └─ Token storage & validation                            │
│     └─ JWT expiry checking                                  │
│     └─ Session management                                    │
│                                                               │
│  ✅ Auth Context (AuthContext.jsx)                           │
│     └─ Global auth state with Zustand                        │
│     └─ Auto-initialization on app load                       │
│                                                               │
│  ⚠️  Environment Config                                      │
│     └─ REACT_APP_GITHUB_CLIENT_ID = Set (dev value)          │
│     └─ REACT_APP_USE_MOCK_AUTH = true (NEEDS TO CHANGE)     │
│     └─ REACT_APP_API_URL = localhost:8000 (needs prod URL)   │
│                                                               │
│                                                               │
│  Backend (FastAPI) - Cofounder Agent                         │
│  ═══════════════════════════════════════════════════════════ │
│                                                               │
│  ✅ OAuth Routes (auth_unified.py)                           │
│     └─ POST /api/auth/github/callback                        │
│        • Exchanges code for GitHub token                     │
│        • Fetches user data from GitHub                       │
│        • Creates JWT token                                   │
│     └─ POST /api/auth/logout                                 │
│     └─ GET /api/auth/me                                      │
│                                                               │
│  ✅ Security Features                                        │
│     └─ JWT token validation                                  │
│     └─ CORS configuration                                    │
│     └─ Token expiry (15 minutes)                             │
│                                                               │
│  ❌ Environment Config                                       │
│     └─ GITHUB_CLIENT_ID (NOT SET)                            │
│     └─ GITHUB_CLIENT_SECRET (NOT SET)                        │
│     └─ JWT_SECRET (USING DEFAULT DEV VALUE)                  │
│     └─ ALLOWED_ORIGINS (needs production domain)             │
│                                                               │
│                                                               │
│  GitHub OAuth App                                            │
│  ═══════════════════════════════════════════════════════════ │
│                                                               │
│  ❌ Not yet created                                          │
│     └─ Needed: Production OAuth app                          │
│     └─ Currently using: Development app (dev values)         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## What You Need to Do (In Order)

```
STEP 1: Create GitHub OAuth App
════════════════════════════════════════════════════════════════
Time: 5 minutes
1. Go to: https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in:
   • Application name: Glad Labs Oversight Hub
   • Homepage URL: https://yourdomain.com
   • Authorization callback URL: https://yourdomain.com/auth/callback
4. Note your CLIENT_ID and CLIENT_SECRET

Result:
   GITHUB_CLIENT_ID=Ov23li...XXXXX (32 char hex)
   GITHUB_CLIENT_SECRET=abcd1234...XXXXX (40 char hex)


STEP 2: Generate JWT Secret
════════════════════════════════════════════════════════════════
Time: 2 minutes
Run this command:
   $ openssl rand -base64 32
   Result: abc123...XYZ (random 64-char string)

This is your JWT_SECRET


STEP 3: Update Backend Configuration
════════════════════════════════════════════════════════════════
Time: 3 minutes
File: .env.local (root directory)

Add or update these lines:
─────────────────────────────────────────────────────────────
# GitHub OAuth (from Step 1)
GITHUB_CLIENT_ID=Ov23li...XXXXX
GITHUB_CLIENT_SECRET=abcd1234...XXXXX

# Security (from Step 2)
JWT_SECRET=abc123...XYZ

# Production Settings
ALLOWED_ORIGINS=https://yourdomain.com
BACKEND_URL=https://api.yourdomain.com
NODE_ENV=production
─────────────────────────────────────────────────────────────


STEP 4: Update Frontend Configuration
════════════════════════════════════════════════════════════════
Time: 2 minutes
File: web/oversight-hub/.env.local

Update these lines:
─────────────────────────────────────────────────────────────
# API URL (change from localhost)
REACT_APP_API_URL=https://api.yourdomain.com

# GitHub OAuth (from Step 1)
REACT_APP_GITHUB_CLIENT_ID=Ov23li...XXXXX
REACT_APP_GITHUB_REDIRECT_URI=https://yourdomain.com/auth/callback

# DISABLE MOCK AUTH!!! (Critical)
REACT_APP_USE_MOCK_AUTH=false
─────────────────────────────────────────────────────────────


STEP 5: Deploy
════════════════════════════════════════════════════════════════
Time: 10-30 minutes

Option A: Vercel + Railway (Recommended)
   1. Push code to GitHub (or set env vars in Vercel/Railway dashboard)
   2. Vercel auto-deploys frontend
   3. Railway auto-deploys backend
   4. Done!

Option B: Docker
   1. docker build -t glad-labs .
   2. docker run (with env variables set)
   3. Configure nginx for HTTPS
   4. Done!

Option C: Manual Server
   1. SSH into server
   2. git pull latest code
   3. Set environment variables
   4. npm run dev (or systemctl start glad-labs)
   5. Configure SSL certificate
   6. Done!


STEP 6: Test
════════════════════════════════════════════════════════════════
Time: 5 minutes

1. Open: https://yourdomain.com
2. Click "Sign in with GitHub"
3. You should be redirected to GitHub.com
4. Click "Authorize"
5. Should return to your app and show username
6. Try logging out
7. Success!

```

---

## What's Different from Development

```
┌─────────────────────────────┬──────────────┬──────────────────┐
│ Configuration               │ Development  │ Production       │
├─────────────────────────────┼──────────────┼──────────────────┤
│ REACT_APP_USE_MOCK_AUTH     │ true         │ false ⚠️ CHANGE  │
│ REACT_APP_API_URL           │ localhost    │ api.yourdomain   │
│ REACT_APP_GITHUB_CLIENT_ID  │ Ov23liAcC... │ your-prod-id     │
│ GitHub Redirect URL         │ localhost    │ https://yourdom  │
│ GITHUB_CLIENT_SECRET        │ not set      │ your-secret ⚠️   │
│ JWT_SECRET                  │ dev-secret   │ random-64-char ⚠️│
│ ALLOWED_ORIGINS             │ localhost    │ yourdomain.com   │
│ NODE_ENV                    │ development  │ production       │
│ Protocol                    │ HTTP         │ HTTPS ⚠️ CRITICAL│
└─────────────────────────────┴──────────────┴──────────────────┘

⚠️ = Must change for production
```

---

## Security Checklist

```
Priority 1 (CRITICAL - Must Do)
═════════════════════════════════════════════════════════════
☐ Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET
☐ Generate and set JWT_SECRET (not default dev-secret)
☐ Set REACT_APP_USE_MOCK_AUTH=false
☐ Enable HTTPS (no HTTP allowed)
☐ Set GITHUB_CLIENT_SECRET in environment (not in code)
☐ Update redirect URLs to match your production domain

Priority 2 (HIGH - Should Do)
═════════════════════════════════════════════════════════════
☐ Configure CORS to only allow your domain
☐ Set LOG_LEVEL=INFO (reduce verbose logging)
☐ Add rate limiting on /api/auth/github/callback
☐ Enable error monitoring (Sentry optional)
☐ Verify SSL certificate is valid

Priority 3 (MEDIUM - Nice to Have)
═════════════════════════════════════════════════════════════
☐ Move token to httpOnly cookies (better than localStorage)
☐ Implement refresh token rotation
☐ Add OAuth provider audit logging
☐ Setup database backups
☐ Monitor auth logs regularly
```

---

## Environment Variables Quick Reference

```
BACKEND (.env.local - root)
═════════════════════════════════════════════════════════════
# GitHub OAuth (REQUIRED)
GITHUB_CLIENT_ID=Ov23li...
GITHUB_CLIENT_SECRET=<secret-from-github>

# Security (REQUIRED)
JWT_SECRET=<random-64-chars>
JWT_EXPIRY_MINUTES=15

# API (REQUIRED)
ALLOWED_ORIGINS=https://yourdomain.com
BACKEND_URL=https://api.yourdomain.com

# Deployment (REQUIRED)
NODE_ENV=production
LOG_LEVEL=INFO

# Optional
RATE_LIMIT_AUTH=5/minute


FRONTEND (web/oversight-hub/.env.local)
═════════════════════════════════════════════════════════════
# API (REQUIRED)
REACT_APP_API_URL=https://api.yourdomain.com

# GitHub OAuth (REQUIRED)
REACT_APP_GITHUB_CLIENT_ID=Ov23li...
REACT_APP_GITHUB_REDIRECT_URI=https://yourdomain.com/auth/callback

# Auth (REQUIRED)
REACT_APP_USE_MOCK_AUTH=false

# Optional
REACT_APP_LOG_LEVEL=info
```

---

## Testing Before & After Deployment

```
LOCAL TESTING (Before deploying)
═════════════════════════════════════════════════════════════
1. Keep REACT_APP_USE_MOCK_AUTH=true
2. Click "Sign in" button
3. Should redirect to mock login
4. Should show mock user profile
5. Try logout
6. Should return to login

PRODUCTION TESTING (After deploying)
═════════════════════════════════════════════════════════════
1. REACT_APP_USE_MOCK_AUTH=false
2. HTTPS://yourdomain.com loads
3. Click "Sign in with GitHub" (NOT "Sign in (Mock)")
4. Redirected to GitHub.com
5. Click "Authorize" on GitHub
6. Returned to app
7. Shows your real GitHub username
8. API calls work (no 401 errors)
9. Logout works and clears token
10. Can login again
```

---

## Troubleshooting Quick Guide

```
Problem: "GitHub authentication failed"
───────────────────────────────────────
Likely cause: GITHUB_CLIENT_ID or GITHUB_CLIENT_SECRET wrong
Fix:
  1. Go to GitHub.com → Settings → Developers
  2. Check your OAuth App settings match .env.local
  3. Verify redirect URL is exactly right

Problem: "CORS error: No Access-Control-Allow-Origin header"
───────────────────────────────────────
Likely cause: ALLOWED_ORIGINS doesn't include frontend domain
Fix:
  1. Check .env.local ALLOWED_ORIGINS setting
  2. Add your production domain
  3. Restart backend

Problem: "Token expired immediately"
───────────────────────────────────────
Likely cause: JWT_SECRET changed or system time wrong
Fix:
  1. Check JWT_SECRET is same on all backend instances
  2. Sync system time (timedatectl set-ntp true)
  3. Regenerate tokens

Problem: "Mock auth still works in production"
───────────────────────────────────────
Likely cause: REACT_APP_USE_MOCK_AUTH=true in production
Fix:
  1. Set REACT_APP_USE_MOCK_AUTH=false in Vercel/Railway
  2. Redeploy frontend
  3. Verify login button shows GitHub icon, not "Sign in (Mock)"
```

---

## Files to Keep Secure

```
🔐 NEVER commit these to git:
───────────────────────────────────────
.env.local                          (root)
.env.production                     (root)
web/oversight-hub/.env.local
web/oversight-hub/.env.production
web/public-site/.env.local
web/public-site/.env.production

These should ONLY be:
  ✓ In .gitignore
  ✓ Set in environment variable manager
  ✓ Set in deployment platform (Vercel, Railway, etc.)
  ✓ Stored in secrets manager (GitHub Secrets, AWS Secrets Manager)

NEVER in:
  ✗ Version control
  ✗ Config files that get committed
  ✗ Source code
  ✗ Docker images
  ✗ Container registries
```

---

## One-Minute Summary

```
You have: ✅ Complete, working OAuth implementation

You need: ⚠️ 3 environment variables:
  • GITHUB_CLIENT_ID (from GitHub.com)
  • GITHUB_CLIENT_SECRET (from GitHub.com)
  • JWT_SECRET (generate: openssl rand -base64 32)

You change: ⚠️ 2 settings:
  • REACT_APP_USE_MOCK_AUTH=false
  • REACT_APP_API_URL=https://api.yourdomain.com

You deploy: ✅ Push code, watch it auto-deploy

You test: ✅ Click login, authorize GitHub, done!

Time needed: ⏱️ 45-60 minutes total
```

---

## Next Steps

1. **Read**: This guide (you're reading it now ✓)
2. **Create**: GitHub OAuth app (5 min)
3. **Generate**: JWT secret (2 min)
4. **Configure**: .env.local files (5 min)
5. **Deploy**: To Vercel/Railway (10-30 min)
6. **Test**: Login with real GitHub (5 min)
7. **Monitor**: Check logs for errors (ongoing)

---

**Status**: ✅ READY TO DEPLOY
**Effort**: ~1 hour
**Risk**: Low (no code changes needed)
**Impact**: Users can now log in with GitHub in production
