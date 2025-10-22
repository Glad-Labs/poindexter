# Strapi Railway Secure Cookie Fix

**Issue:** `Failed to create admin refresh session Cannot send secure cookie over unencrypted connection`

**Root Cause:** When using npm (instead of yarn) on Railway, the admin cookie security configuration was using incorrect logic.

## The Problem

In `cms/strapi-main/config/admin.ts`, the cookie security flag had:

```typescript
secure: env.bool('NODE_ENV', 'development') === 'production',  // ❌ WRONG
```

This tried to convert `NODE_ENV` (a string like "production") to a boolean, which always returned `false`, causing:

- ✅ Local: Works fine with `secure: false`
- ❌ Production Railway: HTTPS connection, but cookie marked as `secure: false` → Security error

## The Fix

Changed the logic to:

```typescript
secure: env('NODE_ENV') === 'production',  // ✅ CORRECT
```

Now:

- Checks if `NODE_ENV` string equals `'production'`
- Returns `true` in production (Railway) → `secure: true` → HTTPS cookies work ✅
- Returns `false` in development (local) → `secure: false` → localhost works ✅

## Changes Made

**File:** `cms/strapi-main/config/admin.ts` (Line 23)

```diff
      cookie: {
-       secure: env.bool('NODE_ENV', 'development') === 'production',
+       secure: env('NODE_ENV') === 'production',
        httpOnly: true,
        sameSite: 'lax',
      },
```

## Required Railway Environment Variables

**To deploy, ensure Railway has these environment variables set:**

1. **NODE_ENV** = `production`
   - Controls secure cookie behavior
   - Must be set in Railway dashboard

2. **ADMIN_JWT_SECRET** = (same value as local)
   - Used to sign admin JWT tokens
   - Set in Railway dashboard or GitHub Secrets

3. **API_TOKEN_SALT** = (same value as local)
   - Used to salt API tokens
   - Set in Railway dashboard

**How to set in Railway:**

1. Go to https://railway.app → Select project → Settings tab
2. Click "Environment" or "Variables"
3. Add/verify these variables are set

## Deployment Steps

1. ✅ Commit the fix:

   ```bash
   git add cms/strapi-main/config/admin.ts
   git commit -m "fix: correct Strapi admin cookie security logic for Railway production"
   ```

2. ✅ Verify Railway environment variables set (NODE_ENV=production, API tokens)

3. ✅ Push to main:

   ```bash
   git push github main
   ```

4. ✅ Railway redeploys automatically → Admin login should work over HTTPS

5. ✅ Test login at: `https://glad-labs-website-production.up.railway.app/admin`

## Verification

After deployment, verify with:

```bash
# 1. Check logs in Railway dashboard
# Look for: "✅ Admin panel available at..." (no cookie error)

# 2. Try admin login
# https://glad-labs-website-production.up.railway.app/admin
# Login should work without "Cannot send secure cookie" error

# 3. Check application works
# https://glad-labs-website-production.up.railway.app
# Content should load normally
```

## Why This Happened

- **With yarn:** Yarn's lock file had a pre-existing working configuration that masked the bug
- **With npm:** When we switched to npm (for consistency), the configuration issue surfaced
- **The actual bug:** Was always in the `admin.ts` logic, just hidden by yarn's behavior

## Related Files

- `cms/strapi-main/config/admin.ts` - Fixed ✅
- `cms/strapi-main/config/server.ts` - Already correct (has `proxy: true`)
- `.env.production` - Already has `NODE_ENV=production`

## Additional Context

Railway's HTTPS/SSL setup:

- Railway terminates SSL at the proxy layer
- Internal connection (Railway → Strapi container) is HTTP
- External connection (Browser → Railway) is HTTPS
- Strapi must trust proxy headers (already configured with `proxy: true`)
- Strapi must know it's in production (now fixed with `NODE_ENV=production`)
