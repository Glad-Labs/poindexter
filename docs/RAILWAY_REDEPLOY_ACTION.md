# 🔧 Action Required: Redeploy Railway to Use New Config

## Current Situation

✅ **Code is pushed to GitHub** - your `config/admin.ts` with `secure: false` is in main branch

❌ **Railway is still running old code** - showing error from previous build

## The Fix

Railway doesn't automatically detect pushes to main branch (if you're using the template or have it configured differently). You need to manually redeploy.

### Step 1: Go to Railway UI

1. Visit [railway.app](https://railway.app)
2. Select your project
3. Click on **glad-labs-strapi-v5-backend** service (or however it's named)

### Step 2: Redeploy

You should see one of these:

**Option A: If you see a "Redeploy" button**

- Click **"Redeploy"** button directly

**Option B: If you see "Deployments" tab**

- Click **"Deployments"** tab
- Click **"Deploy"** or **"Redeploy"** button

**Option C: Using Railway CLI**

```powershell
railway up
```

### Step 3: Wait for Build

Railway will:

1. Pull latest code from GitHub (main branch)
2. Run `npm run build` (via Procfile)
3. Run `npm run start`
4. Deploy updated service

Takes ~3-5 minutes.

### Step 4: Verify

```powershell
# Check logs for the new build
railway logs --service glad-labs-strapi-v5-backend --tail 30

# Should show:
# ✔ Compiling TS
# [2025-10-18 XX:XX:XX.XXX] info: Strapi started successfully
# NOT the cookie error anymore
```

---

## About Switching to Yarn

**No, switching to yarn won't help.** The issue is the configuration, not the package manager. Your npm setup is fine.

The Railway template uses yarn, but:

- ✅ npm works just as well
- ✅ Procfile works with both
- ✅ The real fix is `secure: false` in config, not the package manager

Stick with npm - it's working fine.

---

## Why This Happens

```
You: Push code → GitHub ✅
GitHub: Code updated ✅
Railway: Still running old deployed build ❌
         (Doesn't auto-pull from GitHub for Strapi service)

Solution: Manually trigger redeploy
```

Railway redeploys automatically when you:

- Click "Redeploy" button
- Use `railway up` CLI
- Or if auto-deploy is configured (you might not have it)

---

## After Redeploy

Once Railway rebuilds with new code:

1. ✅ Admin panel loads
2. ✅ Login page appears
3. ✅ No cookie error when submitting login
4. ✅ Can access admin dashboard
5. ✅ Can create posts/categories

---

## Quick Checklist

- [ ] Push code to main branch (already done ✅)
- [ ] Go to railway.app
- [ ] Click service → "Redeploy"
- [ ] Wait 3-5 minutes
- [ ] Check logs for success
- [ ] Try logging in to admin
