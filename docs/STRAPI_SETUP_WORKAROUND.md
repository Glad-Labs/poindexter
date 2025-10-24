# Strapi v5 Setup Workaround

**Issue:** Strapi build fails with plugin version mismatch error  
**Status:** Frontend + Python Backend ✅ Working  
**Impact:** Strapi admin can be accessed but requires manual setup  
**Date:** October 23, 2025

---

## ⚠️ Current Issue

```
Error: "unstable_tours" is not exported by "@strapi/admin"
```

This is a known Strapi v5 plugin incompatibility issue where the content-type-builder plugin expects an export that's not available in the current @strapi/admin version.

---

## ✅ Working Services

**Running Successfully:**

- ✅ Public Site (Next.js): http://localhost:3000
- ✅ Oversight Hub (React): http://localhost:3001
- ✅ Python Backend (FastAPI): http://localhost:8000 (ready to start)

**Not Running:**

- ❌ Strapi CMS (build error - see solutions below)

---

## 🔧 Solution Options

### **Option 1: Skip Strapi, Use Frontend Only (RECOMMENDED FOR NOW)**

Strapi is for content management. If you're testing the frontend/backend:

```bash
# Start just the frontends
npm run dev

# Or start specific services
npm run dev:public          # Public site only
npm run dev:oversight       # Oversight hub only
npm run dev:cofounder       # Python backend only
```

**When to use:** During frontend development, API testing, agent implementation

---

### **Option 2: Downgrade Strapi to v4 (If You Need CMS)**

If you need Strapi now, downgrade to v4 which is stable:

```bash
cd cms/strapi-main

# Remove v5
npm remove @strapi/strapi @strapi/core @strapi/plugins

# Install v4
npm install @strapi/strapi@4.x @strapi/core@4.x @strapi/plugin-users-permissions@4.x

# Build and start
npm run build
npm run develop
```

**Trade-off:** v4 is stable but older. v5 is newer but this plugin issue.

---

### **Option 3: Fix the Plugin (Advanced)**

The issue is in `@strapi/content-type-builder`. You can patch it:

```bash
# Create a patch file to skip the problematic export
cd cms/strapi-main

# Edit node_modules/@strapi/content-type-builder/dist/admin/pages/ListView/EmptyState.mjs
# Line 2: Change from:
#   import { unstable_tours } from '@strapi/admin/strapi-admin';
# To:
#   import { unstable_tours } from '@strapi/admin/dist/admin/index.mjs';
```

Then start Strapi:

```bash
npm run develop
```

**Note:** This might break the tours feature but CMS will work.

---

### **Option 4: Fresh Strapi v5 Setup (Nuclear Option)**

Remove and reinstall Strapi completely:

```bash
cd cms/strapi-main

# Remove everything
npm run clean  # or rm -rf node_modules .cache dist build

# Reinstall fresh
npm install

# Build
npm run build

# Start
npm run develop
```

---

## 📋 Recommended Action

**For current development:**

1. **Keep frontend + Python backend running** (working perfectly)
2. **Skip Strapi until needed** for content management
3. **When you need CMS:**
   - Either use Option 2 (downgrade to v4)
   - Or use Option 3 (patch the plugin)

**Why:**

- Frontend is production-ready
- Python backend is ready for agent implementation
- Strapi issue is isolated to the build/admin panel
- You can add/test content later once CMS is fixed

---

## 🚀 Current Working Setup

```bash
# Terminal 1: Frontend
npm run dev:public
# Output: http://localhost:3000

# Terminal 2: Python Backend (when ready)
npm run dev:cofounder
# Output: http://localhost:8000/docs

# Terminal 3: Oversight Hub
npm run dev:oversight
# Output: http://localhost:3001
```

All three work independently! 🎉

---

## 📝 Long-term Solution

This should be resolved in next Strapi update. Track the issue:

- **Strapi Repo:** https://github.com/strapi/strapi/issues
- **Content-Type-Builder:** The culprit plugin

When Strapi v5 is patched, just run:

```bash
npm install --latest
npm run build
npm run develop
```

---

**Status:** ✅ Frontend Development Ready  
**Blocker:** CMS Admin (workarounds available)  
**Recommendation:** Continue with Option 1 for now
