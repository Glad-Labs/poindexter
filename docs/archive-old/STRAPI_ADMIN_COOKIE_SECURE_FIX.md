# Fix Strapi Admin Cookie Secure Error on Railway

## The Error

```
error: Failed to create admin refresh session Cannot send secure cookie over unencrypted connection
http: POST /admin/login (159 ms) 500
```

## Root Cause

Strapi is trying to set **secure cookies** (HTTPS only) but the connection is HTTP. This happens because:

1. **Railway terminates SSL externally** - Users connect via HTTPS
2. **Internal connection is HTTP** - Railway → Strapi container
3. **Strapi config missing** - Doesn't know it's behind HTTPS proxy
4. **Admin cookie set to secure** - Requires HTTPS, but internally it's HTTP

---

## The Fix

### Step 1: Update Admin Configuration

**File:** `cms/strapi-v5-backend/config/admin.ts`

Already fixed! Changed:

```typescript
// BEFORE (broken)
secure: env.bool('NODE_ENV', 'development') === 'production';

// AFTER (fixed)
secure: env.bool(
  'STRAPI_ADMIN_COOKIE_SECURE',
  env('NODE_ENV') === 'production'
);
```

This allows:

- **Local dev:** `NODE_ENV=development` → cookies NOT secure (works with HTTP)
- **Production:** `STRAPI_ADMIN_COOKIE_SECURE=true` → cookies ARE secure (works with Railway HTTPS)

### Step 2: Add Environment Variables in Railway

Go to [railway.app](https://railway.app):

1. Select **strapi-production** service
2. Click **"Variables"** tab
3. Add these two variables:
   ```
   NODE_ENV = production
   STRAPI_ADMIN_COOKIE_SECURE = true
   ```
4. Click **"Redeploy"** button

**Reference file:** `cms/strapi-v5-backend/.env.railway` (for your records)

---

## How It Works

### Railway SSL/HTTPS Flow

```
┌─────────────────────────────────────────────┐
│           External User (HTTPS)             │
│  https://strapi-production-b234...          │
└────────────────┬────────────────────────────┘
                 │
         ┌───────▼──────────┐
         │ Railway Proxy    │ ← Terminates SSL
         │ (HTTPS ↔ HTTP)   │
         └───────┬──────────┘
                 │
         ┌───────▼──────────────────┐
         │ Strapi Container (HTTP)  │
         │ NODE_ENV=production      │
         │ PORT=1337                │
         └──────────────────────────┘
```

With `NODE_ENV=production` and `STRAPI_ADMIN_COOKIE_SECURE=true`:

- Strapi knows it's behind HTTPS proxy
- Sets cookies with `Secure` flag (even though internal connection is HTTP)
- Browser receives HTTPS cookies correctly
- Admin login works ✅

---

## Verification Steps

### 1. Redeploy on Railway

```powershell
# Option A: Push new code (auto-redeploys)
git push origin dev

# Option B: Manual redeploy in Railway UI
# Click service → "Redeploy" button
```

### 2. Check Logs

```powershell
railway logs --service strapi-production --tail 20
```

Look for:

```
[INFO] Server is running at http://0.0.0.0:1337
[INFO] Admin panel available at /admin
```

**NOT** the cookie error anymore.

### 3. Test Admin Login

1. Visit: `https://strapi-production-b234.up.railway.app/admin`
2. Try to log in
3. Should work without the "secure cookie" error ✅

---

## Local Development (Still Works)

Your local `.env` already has:

```bash
NODE_ENV [not set - defaults to 'development']
DATABASE_CLIENT=sqlite
```

This means locally:

- SQLite database (no PostgreSQL needed)
- Cookies NOT secure (HTTP only)
- Works at `http://localhost:1337/admin` ✅

---

## Complete Environment Variable Summary

### Local Development (`.env`)

```bash
DATABASE_CLIENT=sqlite
# NODE_ENV defaults to 'development'
# Cookies are NOT secure (HTTP works)
```

### Production on Railway (Add via UI or copy from `.env.railway`)

```bash
DATABASE_CLIENT=postgres
DATABASE_URL=[auto-provided by Railway]
NODE_ENV=production
STRAPI_ADMIN_COOKIE_SECURE=true
```

---

## Why This Is Important

Without this fix:

- ❌ Admin login fails with 500 error
- ❌ Can't access content management UI
- ❌ Can't create posts/categories

With this fix:

- ✅ Admin login works
- ✅ Full content management available
- ✅ Cookies properly secured
- ✅ Production-ready setup

---

## Troubleshooting

### Still Getting Cookie Error After Redeploy?

1. **Clear Railway cache:**
   - In Railway UI: Service → settings → "Clear build cache"
   - Then click "Redeploy"

2. **Verify variables are set:**

   ```powershell
   railway vars --service strapi-production
   ```

   Should show:

   ```
   NODE_ENV=production
   STRAPI_ADMIN_COOKIE_SECURE=true
   ```

3. **Check build completed:**
   ```powershell
   railway logs --service strapi-production | grep -i "build\|ready\|error"
   ```

### Admin Panel Loads But Login Still Fails

1. **Wrong credentials?** Check you set admin password on first login
2. **Token expired?** Try clearing browser cookies and refresh
3. **Check API logs:**
   ```powershell
   railway logs --service strapi-production --tail 50
   ```

---

## Next Steps

1. ✅ Add `NODE_ENV=production` to Railway Variables
2. ✅ Add `STRAPI_ADMIN_COOKIE_SECURE=true` to Railway Variables
3. ✅ Click "Redeploy"
4. ✅ Wait 2-3 minutes
5. ✅ Visit admin panel and try logging in
6. ✅ Create posts/categories
7. ✅ Vercel build will now succeed (API has data)
