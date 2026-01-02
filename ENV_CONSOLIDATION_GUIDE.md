# Environment Configuration - Consolidation Guide

**Status**: ✅ CONSOLIDATED TO ROOT `.env.local`

---

## What Changed

### Before (Multiple `.env` files)

```
glad-labs-website/
├── .env.local                          ← Backend + shared config
├── web/oversight-hub/.env.local        ← React-specific vars
└── web/public-site/.env.local          ← Next.js-specific vars
```

**Problems:**

- ❌ Duplicate variables across files
- ❌ GitHub Client IDs don't match
- ❌ Maintenance nightmare (update in 3 places)
- ❌ Variables defined in wrong files
- ❌ Confusion about source of truth

### After (Single Root `.env.local`)

```
glad-labs-website/
└── .env.local                          ← SINGLE SOURCE OF TRUTH
    ├── Backend vars (DATABASE_, OLLAMA_, JWT_, etc.)
    ├── Shared vars (ALLOWED_ORIGINS, CORS, etc.)
    ├── NEXT_PUBLIC_* (auto-exposed to Next.js)
    └── REACT_APP_* (auto-exposed to React)
```

**Benefits:**

- ✅ Single source of truth
- ✅ No duplication
- ✅ Automatic exposure to services
- ✅ Easier to deploy (one file)
- ✅ Clearer variable naming
- ✅ Less confusion

---

## How It Works

### Framework Auto-Exposure

Create Confer uses automatic environment variable exposure:

#### **React (Oversight Hub)**

- Variables prefixed with `REACT_APP_*` are automatically available to React
- `npm start` automatically loads from `.env.local` in parent directory
- ✅ **No need for service-specific `.env` file**

```javascript
// In React code - automatically available!
const apiUrl = process.env.REACT_APP_API_URL; // http://localhost:8000
const mockAuth = process.env.REACT_APP_USE_MOCK_AUTH; // true
```

#### **Next.js (Public Site)**

- Variables prefixed with `NEXT_PUBLIC_*` are automatically available (public)
- Other variables available in server-side code only
- ✅ **No need for service-specific `.env` file**

```javascript
// In Next.js page/component
const apiUrl = process.env.NEXT_PUBLIC_API_BASE_URL; // Auto-available!
const secret = process.env.SECRET_KEY; // Only in server-side code
```

#### **Backend (FastAPI)**

- Reads root `.env.local` using `python-dotenv`
- ✅ **Already configured to use root `.env.local`**

```python
import os
from dotenv import load_dotenv

load_dotenv('../../.env.local')  # Loads from root directory
db_url = os.getenv('DATABASE_URL')
```

---

## New Root `.env.local` Structure

### **Backend Configuration** (for src/cofounder_agent)

```dotenv
# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev

# AI Models
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral:latest

# Authentication
JWT_SECRET=dev-jwt-secret-...
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,http://localhost:8000

# GitHub OAuth (Backend)
GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB
GITHUB_CLIENT_SECRET=a2b98d4eb47ba4b657b214a1ad494cb692c111c7
```

### **Next.js Public Site** (Automatically exposed)

```dotenv
# Prefixed with NEXT_PUBLIC_ - auto-available!
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SITE_URL=http://localhost:3000
NEXT_PUBLIC_GA_ID=G-XXXXXXXXXX
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-XXXXXXXXXX
```

### **React Oversight Hub** (Automatically exposed)

```dotenv
# Prefixed with REACT_APP_ - auto-available!
REACT_APP_API_URL=http://localhost:8000
REACT_APP_LOG_LEVEL=debug
REACT_APP_GITHUB_CLIENT_ID=Ov23liMUM5PuVfu7F4kB
REACT_APP_USE_MOCK_AUTH=true
```

---

## What to Delete

The following service-specific files are **NO LONGER NEEDED**:

```bash
# Delete these files:
rm web/oversight-hub/.env.local
rm web/public-site/.env.local

# Keep root only:
.env.local  ← KEEP THIS
```

---

## How to Deploy This

### **Local Development**

```bash
# 1. You already have root .env.local ✅

# 2. Delete service .env files (optional, they'll be ignored anyway)
rm web/oversight-hub/.env.local
rm web/public-site/.env.local

# 3. Start services normally
npm run dev

# React automatically loads REACT_APP_* from root
# Next.js automatically loads NEXT_PUBLIC_* from root
# Backend automatically loads everything from root
```

### **Production**

1. **Create root `.env.production`** (not in Git)

   ```bash
   cp .env.local .env.production
   # Edit .env.production with production values
   ```

2. **No service-specific `.env.production` files needed**
   - The monorepo build system loads from root automatically

3. **Deploy normally**
   ```bash
   # All three services read from same root config
   npm run build    # Builds all services with root .env
   ```

---

## Environment Variable Reference

### **Backend Variables** (Python FastAPI)

| Variable               | Purpose               | Example                                       |
| ---------------------- | --------------------- | --------------------------------------------- |
| `DATABASE_URL`         | PostgreSQL connection | `postgresql://user:pass@localhost:5432/db`    |
| `OLLAMA_HOST`          | Local AI model server | `http://localhost:11434`                      |
| `JWT_SECRET`           | Token signing secret  | `random-64-char-secret`                       |
| `ALLOWED_ORIGINS`      | CORS origins          | `http://localhost:3000,http://localhost:3001` |
| `GITHUB_CLIENT_ID`     | OAuth app ID          | `Ov23liMUM5PuVfu7F4kB`                        |
| `GITHUB_CLIENT_SECRET` | OAuth app secret      | (hidden)                                      |

### **React Variables** (Oversight Hub)

| Variable                     | Purpose               | Example                        |
| ---------------------------- | --------------------- | ------------------------------ |
| `REACT_APP_API_URL`          | FastAPI backend URL   | `http://localhost:8000`        |
| `REACT_APP_GITHUB_CLIENT_ID` | OAuth app ID          | `Ov23liMUM5PuVfu7F4kB`         |
| `REACT_APP_USE_MOCK_AUTH`    | Use fake login        | `true` (dev) or `false` (prod) |
| `REACT_APP_LOG_LEVEL`        | Console logging level | `debug` or `info`              |

### **Next.js Variables** (Public Site)

| Variable                        | Purpose               | Example                 |
| ------------------------------- | --------------------- | ----------------------- |
| `NEXT_PUBLIC_API_BASE_URL`      | FastAPI backend URL   | `http://localhost:8000` |
| `NEXT_PUBLIC_SITE_URL`          | Website canonical URL | `http://localhost:3000` |
| `NEXT_PUBLIC_GA_ID`             | Google Analytics ID   | `G-XXXXXXXXXX`          |
| `NEXT_PUBLIC_ADSENSE_CLIENT_ID` | AdSense publisher ID  | `ca-pub-XXXXXXXXXX`     |

---

## Troubleshooting

### **React not reading variables?**

**Solution**: Check variable is prefixed with `REACT_APP_`

```javascript
// ❌ WON'T WORK (no prefix)
const apiUrl = process.env.API_URL;

// ✅ WILL WORK (with prefix)
const apiUrl = process.env.REACT_APP_API_URL;
```

### **Next.js showing undefined?**

**Solution**: Variables must be prefixed with `NEXT_PUBLIC_` to be available in browser

```javascript
// ❌ WON'T WORK (server-side only)
export default function Page() {
  const secret = process.env.SECRET_KEY;  // undefined in browser
}

// ✅ WILL WORK (available in browser)
export default function Page() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;  // defined
}
```

### **Backend not reading from root?**

**Solution**: Make sure you're running from root directory

```bash
# ❌ WRONG (running from subdirectory)
cd src/cofounder_agent
python main.py  # Can't find root .env.local

# ✅ CORRECT (from root)
cd glad-labs-website
python -m uvicorn main:app  # Or use npm run dev
```

---

## Summary

| Aspect              | Before             | After                |
| ------------------- | ------------------ | -------------------- |
| **Files**           | 3 × `.env.local`   | 1 × `.env.local`     |
| **Duplicates**      | 🔴 Yes             | ✅ No                |
| **Maintenance**     | 🔴 Update 3 places | ✅ Update 1 place    |
| **Confusion**       | 🔴 High            | ✅ Clear structure   |
| **Deployment**      | 🔴 Complex         | ✅ Simple            |
| **Source of truth** | 🔴 Unclear         | ✅ Root `.env.local` |

---

## Next Steps

1. **Review** the consolidated root `.env.local` structure
2. **Delete** service-specific `.env.local` files (optional)
3. **Test** all services start correctly: `npm run dev`
4. **Create** production configs when ready for deployment

✅ **Done!** Your monorepo now uses a single consolidated root `.env` configuration.
