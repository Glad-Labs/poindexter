# Environment Consolidation - Quick Reference

## ✅ Consolidation Status: COMPLETE

Your monorepo now uses **a single root `.env.local`** as the source of truth.

---

## File Structure Now

```
glad-labs-website/
├── .env.local                          ← SINGLE SOURCE OF TRUTH ✅
│   ├── Backend vars (DATABASE_, OLLAMA_, JWT_, etc.)
│   ├── Shared vars (ALLOWED_ORIGINS, GITHUB_, etc.)
│   ├── NEXT_PUBLIC_* (auto-exposed to Next.js)
│   └── REACT_APP_* (auto-exposed to React)
│
├── web/oversight-hub/
│   └── .env.local                      ← DELETE (no longer needed)
│
└── web/public-site/
    └── .env.local                      ← DELETE (no longer needed)
```

---

## What To Do Now

### Option 1: Clean Up (Recommended)

```bash
# Delete service-specific .env files (they're no longer needed)
rm web/oversight-hub/.env.local
rm web/public-site/.env.local

# Confirm only root .env.local exists
ls -la .env.local
ls -la web/oversight-hub/.env.local 2>/dev/null || echo "✅ Deleted"
ls -la web/public-site/.env.local 2>/dev/null || echo "✅ Deleted"
```

### Option 2: Leave As-Is (Safe)

The `.env` files are in `.gitignore`, so leaving them won't hurt. React/Next.js will just use the root values.

---

## How It Works

### React (Oversight Hub)

React's `react-scripts` build tool automatically:

- ✅ Loads root `.env.local`
- ✅ Exposes `REACT_APP_*` variables
- ✅ Makes them available in `process.env`

```javascript
// Any React component - automatically works!
const apiUrl = process.env.REACT_APP_API_URL; // http://localhost:8000
```

### Next.js (Public Site)

Next.js automatically:

- ✅ Loads root `.env.local`
- ✅ Exposes `NEXT_PUBLIC_*` variables (visible in browser)
- ✅ Other vars only in server-side code

```javascript
// In Next.js component
const apiUrl = process.env.NEXT_PUBLIC_API_BASE_URL; // Auto-available!
```

### Backend (FastAPI)

The Python backend:

- ✅ Already configured to load `../../.env.local`
- ✅ Reads all backend variables (DATABASE*, OLLAMA*, JWT\_, etc.)

```python
# In main.py or services
load_dotenv('../../.env.local')  # Loads from root
db_url = os.getenv('DATABASE_URL')
```

---

## Test It

```bash
# Start all services from root directory
npm run dev

# Expected output:
# ✅ Backend starts on port 8000 with correct config
# ✅ Oversight Hub loads on port 3001 with REACT_APP_* vars
# ✅ Public Site loads on port 3000 with NEXT_PUBLIC_* vars
# ✅ No "missing variable" errors
```

---

## Deployment: Same Process

When deploying:

1. Copy root `.env.local` → `.env.production`
2. Update values for production
3. Deploy normally (all services use same root config)

```bash
# No need for service-specific .env.production files!
cp .env.local .env.production
# Edit .env.production with production values
npm run build  # All services use root config
```

---

## Variables Included

### Backend Variables

- `DATABASE_URL` - PostgreSQL connection
- `OLLAMA_HOST`, `OLLAMA_MODEL` - Local AI
- `JWT_SECRET` - Token signing
- `ALLOWED_ORIGINS` - CORS
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` - OAuth
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` - Optional AI providers

### React Variables (Oversight Hub)

- `REACT_APP_API_URL` - Backend URL
- `REACT_APP_GITHUB_CLIENT_ID` - OAuth
- `REACT_APP_USE_MOCK_AUTH` - Dev mode flag
- `REACT_APP_LOG_LEVEL` - Logging
- All other `REACT_APP_*` flags

### Next.js Variables (Public Site)

- `NEXT_PUBLIC_API_BASE_URL` - Backend URL
- `NEXT_PUBLIC_SITE_URL` - Canonical URL
- `NEXT_PUBLIC_GA_ID` - Google Analytics
- `NEXT_PUBLIC_ADSENSE_CLIENT_ID` - AdSense

---

## Key Changes Made

✅ **Updated root `.env.local`**

- Added all `REACT_APP_*` variables for Oversight Hub
- Added all `NEXT_PUBLIC_*` variables for Public Site
- Consolidated GitHub OAuth configuration
- Removed duplicate variables
- Fixed Client ID mismatch

✅ **Created documentation**

- `ENV_CONSOLIDATION_GUIDE.md` - Detailed guide
- `CONSOLIDATION_QUICK_REFERENCE.md` - This file

✅ **Ready to delete (optional)**

- `web/oversight-hub/.env.local` - No longer needed
- `web/public-site/.env.local` - No longer needed

---

## Done! 🎉

Your monorepo is now fully consolidated to a single root `.env.local` configuration.

**Next steps:**

1. Delete service `.env.local` files (optional)
2. Test with `npm run dev`
3. Create `.env.production` for production deployment
