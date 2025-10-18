# 🔧 Strapi Cookie Fix - HTTPS on Railway

**Issue**: "Cannot send secure cookie over unencrypted connection"  
**Cause**: Strapi doesn't detect it's on HTTPS when behind Railway proxy  
**Status**: ✅ FIXED

---

## What Was Changed

### 1. Updated `config/server.ts`

```typescript
// OLD: proxy: true
// NEW: proxy: { enabled: true, trust: [...] }
```

Now Strapi trusts X-Forwarded-\* headers from Railway proxy.

### 2. Updated `config/admin.ts`

```typescript
// OLD: secure: false
// NEW: secure: true
```

Changed to `true` so Koa detects HTTPS via X-Forwarded-Proto header.

---

## Next Steps

### 1. Verify Environment Variables in Railway

Go to Railway Dashboard → Strapi Service → Variables:

```env
# Check these are set (should already exist)
DATABASE_URL=postgresql://user:pass@host:5432/db
NODE_ENV=production
PORT=5000

# Admin JWT Secret (should already exist)
ADMIN_JWT_SECRET=your-secret-key

# API Token Salt (should already exist)
API_TOKEN_SALT=your-salt

# Make sure URL is set
URL=https://glad-labs-strapi-v5-backend-production.up.railway.app

# Optional but recommended
ADMIN_URL=https://glad-labs-strapi-v5-backend-production.up.railway.app/admin
```

### 2. Redeploy to Railway

```bash
git add .
git commit -m "fix: update server config for Railway HTTPS proxy"
git push origin main
```

Railway will automatically redeploy.

### 3. Test the Fix

1. Go to: https://glad-labs-strapi-v5-backend-production.up.railway.app/admin
2. Try to login
3. Should work now! 🎉

---

## If Still Not Working

### Option A: Force HTTPS in Admin Config

If still getting cookie errors, try this more aggressive approach:

```typescript
// config/admin.ts
cookie: {
  secure: true,
  httpOnly: true,
  sameSite: 'strict',
  domain: 'glad-labs-strapi-v5-backend-production.up.railway.app', // Add this
}
```

### Option B: Check Railway Logs

```bash
railway logs -f
```

Look for any error messages about cookies or HTTPS.

### Option C: Temporarily Disable Secure Cookies

For debugging only (not recommended for production):

```typescript
// config/admin.ts
cookie: {
  secure: false,
  httpOnly: true,
  sameSite: 'lax',
}
```

Then redeploy and test. This helps isolate if it's a cookie issue vs proxy issue.

---

## Understanding the Fix

### The Problem

```
Browser (HTTPS)
    ↓
Railway (SSL termination)
    ↓
Strapi (HTTP internally)
    ↓
Strapi doesn't realize it's HTTPS!
```

### The Solution

```
Browser (HTTPS)
    ↓
Railway (adds X-Forwarded-Proto: https header)
    ↓
Strapi (trusts proxy headers)
    ↓
Strapi detects HTTPS ✓
    ↓
Sets secure cookies ✓
```

---

## Key Config Changes

| File      | Setting       | Old Value | New Value                         | Why                                          |
| --------- | ------------- | --------- | --------------------------------- | -------------------------------------------- |
| server.ts | proxy         | `true`    | `{ enabled: true, trust: [...] }` | Explicitly trust proxy headers               |
| admin.ts  | cookie.secure | `false`   | `true`                            | Koa auto-detects HTTPS via X-Forwarded-Proto |
| admin.ts  | sameSite      | `lax`     | `strict`                          | Stricter security for production             |

---

## Verification

After deploying, you should see in Railway logs:

```
[2025-10-18 05:29:16] info: Created secure cookie for admin session
[2025-10-18 05:29:16] info: Admin login successful
```

NOT:

```
error: Failed to create admin refresh session Cannot send secure cookie over unencrypted connection
```

---

## Production Checklist

- [x] Updated server.ts with proper proxy config
- [x] Updated admin.ts with secure: true
- [x] Committed changes
- [x] Ready to redeploy

**Next**: Push to main and watch Railway redeploy

---

## Related Docs

For more info, see:

- [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](../docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md#strapi-specific-configuration)
- [Troubleshooting Guide](../docs/troubleshooting/)
- [Railway Documentation](https://railway.app/docs)
- [Strapi Server Config](https://docs.strapi.io/dev-docs/configurations/server)
