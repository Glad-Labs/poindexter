# ⚡ CRITICAL FIX: Strapi Cookie Error Root Cause Found

## 🎯 The Problem

Your `force-https` middleware is **a workaround** that indicates Strapi isn't properly detecting HTTPS from Railway's proxy headers.

**Current flow:**

```
1. Railway sends: X-Forwarded-Proto: https
2. Your middleware checks for it manually
3. Sets ctx.scheme = 'https'
4. But this only works if middleware runs FIRST ✓ (and it does)
5. YET: Error still occurs ❌
```

**Why it's still failing:**

- The middleware sets `ctx.scheme` but Koa session middleware might not respect it
- Or the `proxy` configuration in `server.ts` is NOT properly reading the header

---

## 🔨 THE REAL FIX

The issue is that `proxy: true` alone doesn't work on Railway. We need to be more explicit.

### Solution 1: Update server.ts (RECOMMENDED)

Replace `proxy: true` with explicit configuration:

**File: `cms/strapi-v5-backend/config/server.ts`**

```typescript
export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  app: {
    keys: env.array('APP_KEYS'),
  },
  webhooks: {
    populateRelations: env.bool('WEBHOOKS_POPULATE_RELATIONS', false),
  },
  url: env('URL'),
  proxy: {
    enabled: true,
    trust: ['127.0.0.1'], // Railway's internal IP
  },
});
```

**Why this works:**

- Tells Koa to trust proxy headers from 127.0.0.1 (Railway's internal network)
- Koa then properly respects X-Forwarded-Proto header
- Sessions middleware uses the correct scheme
- Cookies are set with proper security flags

---

### Solution 2: Alternative - Update middlewares.ts

If Solution 1 doesn't work, ensure the force-https middleware is truly first AND verify Rails behavior.

Add this to your force-https middleware:

```typescript
export default () => {
  return async (ctx, next) => {
    // Detect HTTPS from proxy
    if (ctx.request.header['x-forwarded-proto'] === 'https') {
      ctx.scheme = 'https';
      ctx.secure = true; // IMPORTANT: Also set secure flag
    }

    // Also check the default - if URL starts with https, we're on HTTPS
    if (ctx.URL?.href?.startsWith('https')) {
      ctx.scheme = 'https';
      ctx.secure = true;
    }

    await next();
  };
};
```

---

## 🚀 IMMEDIATE ACTION

**Step 1: Update server.ts with explicit proxy config**

```powershell
# This sets up proper proxy trusting
```

Then update the file from `proxy: true` to:

```typescript
  proxy: {
    enabled: true,
    trust: ['127.0.0.1'],
  },
```

**Step 2: Deploy**

```powershell
git add cms/strapi-v5-backend/config/server.ts
git commit -m "fix: explicit proxy configuration for Railway HTTPS"
git push origin main
```

**Step 3: Redeploy on Railway**

```bash
railway logs -f  # Monitor deployment
```

**Step 4: Test**

```
https://YOUR_DOMAIN/admin
```

---

## 📋 Why This Works

Koa has a built-in trust mechanism. When you set `proxy: { enabled: true, trust: ['127.0.0.1'] }`:

1. ✅ Koa reads `X-Forwarded-Proto` header
2. ✅ Koa reads `X-Forwarded-For` header
3. ✅ Koa properly sets `ctx.scheme` to 'https' or 'http'
4. ✅ Session middleware sees correct scheme
5. ✅ Cookies are set with correct secure flag
6. ✅ No "secure cookie over unencrypted connection" error

---

## 🔍 If Still Broken

Check these in order:

**1. Is URL set?**

```bash
railway secret list | grep URL
```

Should show: `URL=https://your-domain.up.railway.app`

**2. Are headers being sent?**

```bash
railway shell
curl -I -H "X-Forwarded-Proto: https" http://localhost:1337/admin
```

Should NOT error about secure cookies

**3. Check exact error**

```bash
railway logs -f | grep -A5 "Cannot send secure cookie"
```

Look for the exact circumstances

**4. Nuclear option - disable cookie security temporarily**

Add to `src/index.ts` or middleware:

```typescript
// TEMPORARY DEBUG ONLY
app.use(async (ctx, next) => {
  console.log('Request scheme:', ctx.scheme);
  console.log('X-Forwarded-Proto:', ctx.request.header['x-forwarded-proto']);
  console.log('Secure:', ctx.secure);
  await next();
});
```

---

## 📚 Reference

- **Koa Proxy Trust**: https://koajs.com/#app-proxy
- **Railway HTTPS**: https://docs.railway.app/deploy/deployments#https-and-ssl
- **Strapi Proxy Docs**: https://docs.strapi.io/dev-docs/configurations/server#proxy

The explicit `proxy: { enabled: true, trust: ['127.0.0.1'] }` is the correct approach for Railway.
