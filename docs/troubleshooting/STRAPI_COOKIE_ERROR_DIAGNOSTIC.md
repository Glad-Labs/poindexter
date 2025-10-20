# 🔍 Strapi Cookie Error Diagnostic Guide

## Current Error

```
[2025-10-18 06:02:29.759] error: Failed to create admin refresh session
Cannot send secure cookie over unencrypted connection
```

---

## 🎯 Root Causes (In Order of Likelihood)

### ❌ Issue #1: `URL` Environment Variable Not Set on Railway

**Symptom**: Error happens right after deployment  
**Why**: Strapi can't determine the public URL, so it defaults to `http://localhost`

**Fix**:

1. Go to Railway dashboard
2. Select your Strapi service
3. Go to **Variables**
4. Add: `URL=https://${{RAILWAY_PUBLIC_DOMAIN}}`
5. Redeploy

**Verify**:

```bash
# In Railway logs, should see:
[strapi] ⏳ Initializing server...
[strapi] ✓ Server initialized successfully
# And URL should be your domain, NOT localhost
```

---

### ❌ Issue #2: Deployment Not Updated

**Symptom**: Error persists after code changes  
**Why**: Your git push didn't trigger a redeploy yet

**Fix**:

```powershell
# Commit all changes
git add .
git commit -m "fix: Railway deployment configuration"
git push origin main

# Wait 2-3 minutes for Railway to redeploy
# Check: railway logs -f
```

---

### ❌ Issue #3: Koa/Node.js Not Restarted

**Symptom**: Error continues even after environment variable change  
**Why**: Process still running with old config

**Fix** (from Railway Dashboard):

1. Go to your Strapi service
2. Click **Settings**
3. Scroll to **Deployments**
4. Click **Redeploy latest**
5. Wait for deployment to complete

---

### ❌ Issue #4: Railway Not Sending Proxy Headers

**Symptom**: Error even with URL set correctly  
**Why**: Reverse proxy not configured to forward HTTPS headers

**Fix**: Update `server.ts` to explicitly trust proxy headers:

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
    trust: [
      '127.0.0.1',
      'loopback',
      'linklocal',
      'uniquelocal',
      '::ffff:127.0.0.1',
    ],
  },
});
```

---

## 🔧 Complete Fix Checklist

### Step 1: Verify Git Deployment ✅

```powershell
cd c:\Users\mattm\glad-labs-website
git log --oneline -5  # Check recent commits
git push origin main  # Ensure latest pushed
```

### Step 2: Set URL on Railway ✅

Go to Railway Dashboard → Strapi Service → Variables

**Must have:**

```
URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
DATABASE_CLIENT=postgres
```

**Verify it's set:**

```bash
railway secret list | grep URL
# Should show: URL=https://your-domain.up.railway.app
```

### Step 3: Check Current Config

```bash
railway logs -f | grep -i "url\|proxy\|initialization"
```

**Should see:**

```
[strapi] ⏳ Initializing strapi...
[strapi] URL configured as: https://your-domain.up.railway.app
[strapi] Proxy enabled: true
[strapi] ✓ Strapi fully loaded
```

### Step 4: Trigger Redeploy

Railway Dashboard → Strapi Service → Settings → Deployments → **Redeploy latest**

### Step 5: Test After Redeploy

```bash
# Wait 2-3 minutes for deployment
railway logs -f

# When you see "Strapi fully loaded", test:
curl https://YOUR_DOMAIN/admin
```

---

## 🔬 Deep Diagnostic Commands

### Check if Strapi sees HTTPS

```bash
# SSH into Railway pod
railway shell

# Once inside:
curl -I http://localhost:1337/admin

# Should see in response:
# Set-Cookie: ... Secure
# (NOT "Cannot send secure cookie")
```

### Check Environment Variables

```bash
railway secret list
```

**Must have:**

```
ADMIN_JWT_SECRET=<value>
APP_KEYS=<value>
DATABASE_URL=postgresql://...
DATABASE_CLIENT=postgres
URL=https://<domain>
```

**Missing any? Add them:**

```bash
railway secret set URL="https://glad-labs-strapi-v5-backend-production.up.railway.app"
```

### Check Recent Logs

```bash
railway logs --follow 2025-10-18 | tail -50
# Look for error patterns
```

---

## 📊 Expected vs Actual

### ✅ Expected Flow (Working)

```
1. Browser sends: GET /admin/login
2. Railway reverse proxy receives HTTPS request
3. Railway converts to HTTP, adds: X-Forwarded-Proto: https
4. Strapi receives HTTP but reads header → knows it's HTTPS
5. Sets: Set-Cookie: ... Secure
6. Browser receives cookie over HTTPS ✅
7. Login succeeds
```

### ❌ Actual Flow (Broken)

```
1. Browser sends: GET /admin/login
2. Railway reverse proxy receives HTTPS request
3. Railway converts to HTTP, but...
4. Strapi thinks it's HTTP (no X-Forwarded-Proto header?)
5. Tries to: Set-Cookie: ... Secure
6. Error: "Cannot send secure cookie over unencrypted connection" ❌
```

---

## 🎯 Quickest Fix (Most Likely to Work)

**This resolves 90% of cases:**

```bash
# 1. On Railway dashboard, go to your Strapi service

# 2. Add this environment variable if missing:
URL=https://${{RAILWAY_PUBLIC_DOMAIN}}

# 3. Scroll down and click "Redeploy latest"

# 4. Wait 2-3 minutes

# 5. Test: https://your-domain/admin
```

---

## ✨ If Still Broken After Checklist

**Try the explicit proxy config:**

Update `server.ts`:

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
    trust: [
      '127.0.0.1',
      'loopback',
      'linklocal',
      'uniquelocal',
      '::ffff:127.0.0.1',
    ],
  },
});
```

Then:

```powershell
git add cms/strapi-v5-backend/config/server.ts
git commit -m "fix: explicit proxy trust for Railway"
git push origin main
# Wait for redeploy
```

---

## 📞 When to Escalate

If issue persists after all steps:

1. Check Railway status: https://railway.app/status
2. Review Railway PostgreSQL logs (might be database connection issue)
3. Check Strapi v5 docs: https://docs.strapi.io/dev-docs/deployment/railway
4. Post in Railway Discord with: `railway logs --follow | head -100`

---

## 🚨 Common Mistakes

| Mistake             | Impact                       | Fix                          |
| ------------------- | ---------------------------- | ---------------------------- |
| Forgot to set `URL` | Strapi defaults to localhost | Set it in Railway variables  |
| Changes not pushed  | Old code still running       | `git push origin main`       |
| Forgot to redeploy  | Old process still running    | Click Redeploy in Railway UI |
| `proxy: false`      | Headers not trusted          | Set `proxy: true`            |
| Mixed HTTP/HTTPS    | Cookie conflict              | All traffic over HTTPS       |
| No ADMIN_JWT_SECRET | Login fails silently         | Regenerate secrets           |

---

## ✅ Success Indicators

When it's working:

```bash
# 1. Logs show no "Cannot send secure cookie" error
railway logs -f | grep -i cookie
# (Should be empty or show success messages)

# 2. Can access admin
curl -I https://YOUR_DOMAIN/admin
# HTTP 200 or 302 (redirect to login)

# 3. Can login
# Go to https://YOUR_DOMAIN/admin
# Enter credentials
# Dashboard loads ✅
```

---

## 🔄 Next Steps

1. **Right now**: Check if `URL` is set on Railway
2. **Then**: Verify deployment completed
3. **Then**: Check logs for the error
4. **If still broken**: Try explicit proxy config
5. **Last resort**: Redeploy entire service

Let me know the results of `railway secret list` and I can pinpoint the exact issue!
