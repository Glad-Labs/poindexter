# Strapi v5 Plugin Incompatibility Issue

**Last Updated:** October 23, 2025  
**Status:** ⚠️ Known Issue - Workaround Available  
**Severity:** HIGH (blocks Strapi CMS admin)  
**Component:** Strapi v5 Backend

---

## 🔴 Problem

When running `npm run dev` or starting Strapi v5, you may encounter:

```
Error: "unstable_tours" is not exported by "@strapi/admin/dist/admin/index.mjs"
Cannot find module '@strapi/strapi/package.json'
```

**Root Cause:** `@strapi/content-type-builder` plugin (v5.x) expects an API export from `@strapi/admin` that doesn't exist in the current version.

**Impact:**
- ❌ Strapi CMS admin won't build
- ❌ Cannot access [http://localhost:1337/admin](http://localhost:1337/admin)
- ✅ Frontend services still work fine (Next.js, React)

---

## ✅ Workaround 1: Skip Strapi During Development (RECOMMENDED)

**Why:** Frontend services work perfectly without Strapi running locally

**Steps:**

```bash
# Instead of npm run dev (which tries to start Strapi)
npm run dev:frontend

# This runs only:
# - Public Site: http://localhost:3000
# - Oversight Hub: http://localhost:3001
```

**Result:** ✅ Frontend services running, frontend development unblocked

**Use Case:** Best for frontend development when you don't need local Strapi

---

## ✅ Workaround 2: Use Cloud Strapi Instance

**Why:** Access Strapi admin without local build issues

**Steps:**

```bash
# Configure .env to point to cloud Strapi
NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
STRAPI_API_TOKEN=your-production-token
```

**Result:** ✅ Frontend connects to production Strapi, use admin there

**Use Case:** Best for full-stack development using production data

---

## ✅ Workaround 3: Downgrade Strapi Plugins

**Why:** Older plugin versions may be compatible

**Steps:**

```bash
cd cms/strapi-v5-backend

# Downgrade problematic plugin
npm install @strapi/content-type-builder@5.0.0

# Rebuild
npm run build

# Start
npm run develop
```

**Note:** May lose newer features, test thoroughly

**Use Case:** If you need local Strapi admin for CMS management

---

## ✅ Workaround 4: Fresh Strapi Installation

**Why:** Start with known-good plugin versions

**Steps:**

```bash
cd cms/strapi-v5-backend

# Backup current
cp -r node_modules node_modules.backup
cp package-lock.json package-lock.json.backup

# Clear and reinstall
rm -rf node_modules package-lock.json
npm install

# Build from scratch
npm run build

# Start
npm run develop
```

**Note:** May take 10+ minutes

**Use Case:** If downgrades don't work

---

## 📊 Recommended Solution by Use Case

| Use Case | Solution | Setup Time | Maintenance |
|----------|----------|-----------|-------------|
| **Frontend dev only** | Workaround 1 (skip Strapi) | 5 min | None ✅ |
| **Full-stack dev** | Workaround 2 (cloud Strapi) | 10 min | Low ✅ |
| **CMS management** | Workaround 3 (downgrade) | 20 min | Medium ⚠️ |
| **Nuclear option** | Workaround 4 (fresh install) | 30+ min | High ⚠️ |

---

## 🔧 Current Configuration

**Current Script:** `npm run dev:frontend` (skips Strapi)

```json
{
  "scripts": {
    "dev": "npx npm-run-all --parallel dev:frontend",
    "dev:frontend": "npx npm-run-all --parallel dev:public dev:oversight",
    "dev:public": "npm run dev --workspace=web/public-site",
    "dev:oversight": "npm start --workspace=web/oversight-hub"
  }
}
```

**Status:** ✅ Working (frontend only)

---

## 🚀 Next Steps

**Option A: Continue with frontend only** (RECOMMENDED for now)
```bash
npm run dev:frontend
# Frontend works great, just no local Strapi admin
```

**Option B: Try plugin downgrade**
```bash
cd cms/strapi-v5-backend
npm install @strapi/content-type-builder@5.0.0
npm run build
npm run develop
```

**Option C: Use production Strapi**
- Access CMS at: https://cms.railway.app/admin
- Update .env to point to production
- Continue frontend development

---

## 📞 Getting Help

1. **Check logs:** `npm run dev 2>&1 | grep -A 5 "unstable_tours"`
2. **Clear cache:** `rm -rf node_modules .next build && npm install`
3. **Try workaround:** Start with Workaround 1 (skip Strapi)
4. **Escalate:** If none work, document error and escalate to team

---

## 📚 Related Documentation

- **[03-DEPLOYMENT_AND_INFRASTRUCTURE.md](../../03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Production Strapi setup
- **[01-SETUP_AND_OVERVIEW.md](../../01-SETUP_AND_OVERVIEW.md)** - Initial setup guide
- **[Component: Strapi CMS README](../README.md)** - Main Strapi documentation

---

**Status:** 🔄 Known Issue with Documented Workarounds  
**Last Updated:** October 23, 2025  
**Next Review:** When Strapi plugin updates (v5.1+)
