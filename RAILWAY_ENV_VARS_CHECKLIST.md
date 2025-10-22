# ⚡ IMMEDIATE ACTION: Railway Environment Variables Checklist

**Status:** Production Strapi deployment - Latest fixes applied (Oct 22, 2025)  
**Action Required:** Verify Railway environment variables + trigger rebuild

---

## ✅ FIXES ALREADY APPLIED

- ✅ Fixed Strapi package version mismatch (all 5.18.1)
- ✅ Downgraded Node to 18.20.3 (more stable)
- ✅ Created proper minimal yarn.lock
- ✅ Updated .nvmrc format for Railway

**Status:** Code fixes pushed to GitHub → Railway should auto-redeploy now

---

## 🚨 LIKELY REMAINING ISSUE

Railway dashboard **environment variables are still missing or blank**.

## ✅ DO THIS NOW

1. **Go to:** https://railway.app
2. **Select:** Your GLAD Labs project
3. **Click:** The "Strapi CMS" service (or similar name)
4. **Go to:** "Settings" tab
5. **Click:** "Environment" section
6. **Verify these 6 variables are set:**

| Variable              | Status   | Value                       |
| --------------------- | -------- | --------------------------- |
| `NODE_ENV`            | [ ] Set? | `production`                |
| `DATABASE_URL`        | [ ] Set? | Auto-provided by Railway ✅ |
| `DATABASE_CLIENT`     | [ ] Set? | `postgres`                  |
| `ADMIN_JWT_SECRET`    | [ ] Set? | (any long random string)    |
| `API_TOKEN_SALT`      | [ ] Set? | (any long random string)    |
| `TRANSFER_TOKEN_SALT` | [ ] Set? | (any long random string)    |

**If ANY are missing or blank:**

1. Click "Edit" button
2. Add missing variables
3. Click "Save"
4. Railway will **auto-redeploy** with new variables

---

## 🔍 How to Check Build Status

1. Go to https://railway.app → Your Project → Strapi
2. Click "Deployments" tab
3. Click the latest deployment (should show recent timestamp)
4. Scroll through logs looking for:
   - ✅ "server has started successfully" - SUCCESS!
   - 🟢 "Using yarn1 package manager" - expected
   - 🟢 "yarn install --frozen-lockfile" - expected
   - 🟢 "yarn run build" - expected
   - 🔴 "error" (in red) - note the exact message
   - 🟠 "failed" - note what failed

**Common error messages to look for:**

```
❌ "Cannot send secure cookie" → NODE_ENV not set to production
❌ "cannot find module" or "ENOENT" → Dependency missing
❌ "Failed to connect to database" → DATABASE_URL not set or wrong
❌ "Cannot create admin" → ADMIN_JWT_SECRET blank
❌ "yarn install --frozen-lockfile" error → Strapi package mismatch (already fixed)
```

---

## ⚡ Quick Fixes by Error Type

### If you see "Cannot send secure cookie"

→ Set: `NODE_ENV=production`

### If you see dependency or module errors

→ Already fixed! Package.json mismatch resolved on Oct 22

### If you see "yarn: command not found"

→ Check `Procfile` contains exactly: `web: yarn start`

### If you see "Cannot create admin session"

→ Set all three JWT/salt secrets:

- `ADMIN_JWT_SECRET=` (e.g., `your-secret-key-here`)
- `API_TOKEN_SALT=` (e.g., `another-secret-here`)
- `TRANSFER_TOKEN_SALT=` (e.g., `third-secret-here`)

### If you see database errors

→ Verify `DATABASE_CLIENT=postgres` is set

---

## 🚀 After Setting Variables

1. Wait for Railway to redeploy automatically (2-5 minutes)
2. Watch the logs in Railway dashboard
3. When you see "server has started successfully" → it worked! ✅
4. Visit your Strapi URL to verify admin panel loads

---

## 📞 If Still Not Working

1. Check: `docs/guides/troubleshooting/RAILWAY_PRODUCTION_DEPLOYMENT_DEBUG.md`
2. Look up your error message in the error reference section
3. Apply the corresponding fix


2. Check all 4 Railway config files exist locally:
   - `Procfile`
   - `.nvmrc`
   - `.yarnrc.yml`
   - `yarn.lock`
3. Verify `package.json` has `"packageManager": "yarn@1.22.22"`

---

**Next:** Check your Railway dashboard environment variables NOW - this is most likely the issue!
