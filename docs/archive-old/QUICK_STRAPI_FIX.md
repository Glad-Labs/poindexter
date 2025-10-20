# 🔧 Quick Fix: Strapi Admin Cookie Error

## The Problem

```
error: Failed to create admin refresh session Cannot send secure cookie over unencrypted connection
```

## The Root Cause

Railway uses HTTPS externally but HTTP internally. Strapi needs to know it's behind an HTTPS proxy.

## The Fix (2 Steps)

### Step 1: ✅ Already Done

Updated `config/admin.ts` to properly handle Railway's SSL termination.

### Step 2: Add Variables in Railway

Go to [railway.app](https://railway.app):

1. **strapi-production** service
2. **Variables** tab
3. **Add these two:**
   ```
   NODE_ENV = production
   STRAPI_ADMIN_COOKIE_SECURE = true
   ```
4. **Redeploy**

---

## Verify It Works

```powershell
# Check logs (should NOT show cookie error)
railway logs --service strapi-production --tail 10

# Then visit
https://strapi-production-b234.up.railway.app/admin
```

Should work without errors! ✅

---

## Why This Fixes It

- **Local dev:** `NODE_ENV=development` → HTTP cookies (localhost works)
- **Production:** `NODE_ENV=production` + `STRAPI_ADMIN_COOKIE_SECURE=true` → HTTPS cookies (Railway works)

That's it!
