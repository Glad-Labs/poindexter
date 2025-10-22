# 🚀 Railway Strapi Deployment Troubleshooting - Production Build Error

**Issue:** Strapi build failing on Railway.app

**Date:** October 22, 2025

---

## 🔍 Diagnosis Checklist

Before deploying to Railway, verify **ALL** of the following are correct:

### 1. ✅ Railway Configuration Files (CRITICAL)

All 4 files MUST exist in `cms/strapi-main/`:

- [ ] **Procfile** exists with: `web: yarn start`
- [ ] **.nvmrc** exists with: `20.19.5` (just version, no `node-version:`)
- [ ] **.yarnrc.yml** exists with: `nodeLinker: node-modules`
- [ ] **yarn.lock** exists (placeholder is fine - Railway will regenerate)
- [ ] **build.sh** exists with yarn install command
- [ ] **railway.json** has `buildCommand: "bash build.sh"`

**Verification:** Run this command to verify all files exist:

```bash
cd cms/strapi-main
ls -la Procfile .nvmrc .yarnrc.yml yarn.lock build.sh railway.json
```

### 2. ✅ package.json Configuration (CRITICAL)

**File:** `cms/strapi-main/package.json`

Must have:

```json
{
  "engines": {
    "node": ">=20.0.0 <=22.x.x",
    "yarn": ">=1.22.0"
  },
  "packageManager": "yarn@1.22.22"
}
```

**Verify:**
```bash
cat cms/strapi-main/package.json | grep -A 5 "engines"
```

### 3. ✅ Railway Environment Variables (CRITICAL)

Log into Railway dashboard and set these variables for your Strapi service:

| Variable | Value | Notes |
|----------|-------|-------|
| `NODE_ENV` | `production` | REQUIRED - controls secure cookie behavior |
| `DATABASE_URL` | PostgreSQL connection | Railway auto-provides this ✅ |
| `DATABASE_CLIENT` | `postgres` | Must be explicit for PostgreSQL |
| `ADMIN_JWT_SECRET` | [random string] | Used for admin tokens - set in Railway secrets |
| `API_TOKEN_SALT` | [random string] | Used for API tokens - set in Railway secrets |
| `TRANSFER_TOKEN_SALT` | [random string] | Used for transfer tokens - set in Railway secrets |

**How to verify in Railway:**
1. Go to railway.app → Your Project → Strapi Service → Settings
2. Click "Environment" or "Variables"
3. Verify all 6 variables are set and NOT empty

**DO NOT leave these blank:**
```
❌ ADMIN_JWT_SECRET=
❌ API_TOKEN_SALT=
❌ TRANSFER_TOKEN_SALT=
```

### 4. ✅ Database Configuration

Railway PostgreSQL auto-provides `DATABASE_URL`. Strapi config handles this:

**File:** `config/database.ts` - Already has:

```typescript
const client = env('DATABASE_CLIENT', 'sqlite');
```

**Verify this is set to 'postgres' in Railway env vars (see above)**

### 5. ✅ Admin Configuration

**File:** `config/admin.ts` - Already correct:

```typescript
secure: env('NODE_ENV') === 'production' || env.bool('FORCE_SECURE_COOKIE', false)
```

This means:
- ✅ Production (NODE_ENV=production) → secure: true → HTTPS cookies work
- ✅ Local (NODE_ENV=development) → secure: false → localhost works
- ✅ Override with `FORCE_SECURE_COOKIE=true` if needed

---

## 🛠️ Common Build Errors & Fixes

### Error: "Build command failed"

**Cause:** `build.sh` is not executable or not found

**Fix:**
```bash
cd cms/strapi-main
chmod +x build.sh
git add build.sh
git commit -m "make build.sh executable"
git push
```

### Error: "yarn: command not found"

**Cause:** Procfile or build script not using correct package manager

**Fix:** Ensure these files are correct:
- `Procfile`: `web: yarn start` (NOT `npm start`)
- `build.sh`: Uses `yarn install` (NOT `npm install`)
- `.nvmrc`: Set to `20.19.5`

### Error: "Cannot find module '@noble/hashes'"

**Cause:** Node version wrong (needs 20+, not 18)

**Fix:**
1. Verify `.nvmrc` contains: `20.19.5` (just version number)
2. Verify `package.json` has: `"node": ">=20.0.0 <=22.x.x"`
3. Verify `Procfile` is set (tells Railway to use custom start)

### Error: "Cannot send secure cookie over unencrypted connection"

**Cause:** `NODE_ENV` not set to `production` in Railway

**Fix:** Set in Railway dashboard:
```
NODE_ENV=production
```

### Error: "Failed to create admin session"

**Cause:** Missing or blank JWT/token secrets

**Fix:** Set in Railway dashboard:
```
ADMIN_JWT_SECRET=[random-string-at-least-16-chars]
API_TOKEN_SALT=[random-string-at-least-16-chars]
TRANSFER_TOKEN_SALT=[random-string-at-least-16-chars]
```

### Error: "yarn install failed" or "Cannot find yarn"

**Cause:** Railway using npm instead of yarn

**Fix:** All 4 Railway config files must exist:
1. `Procfile` - Tells Railway to use yarn start
2. `.nvmrc` - Specifies Node version
3. `.yarnrc.yml` - Yarn configuration
4. `yarn.lock` - Signals this is a yarn project

**Verify all exist:**
```bash
ls -la cms/strapi-main/{Procfile,.nvmrc,.yarnrc.yml,yarn.lock,build.sh}
```

---

## 📋 Pre-Deployment Checklist

Run this before pushing to production:

### Local Verification

```bash
# 1. Verify all Railway config files exist
cd cms/strapi-main
ls -la Procfile .nvmrc .yarnrc.yml yarn.lock build.sh railway.json

# 2. Verify package.json has correct engines
cat package.json | grep -A 3 "engines"
cat package.json | grep packageManager

# 3. Verify admin config is correct
grep -A 5 "secure:" config/admin.ts

# 4. Verify build.sh is executable (local development)
chmod +x build.sh
cat build.sh

# 5. Build locally to test
npm run dev  # Should start without errors
# OR
cd cms/strapi-main && npm run develop
```

### Railway Dashboard Verification

1. ✅ Go to railway.app → Your Project
2. ✅ Click on Strapi service
3. ✅ Go to "Settings" tab
4. ✅ Check "Environment" section
5. ✅ Verify these are set:
   - `NODE_ENV` = `production`
   - `DATABASE_URL` = [auto-provided or set manually]
   - `DATABASE_CLIENT` = `postgres`
   - `ADMIN_JWT_SECRET` = [set]
   - `API_TOKEN_SALT` = [set]
   - `TRANSFER_TOKEN_SALT` = [set]

**If any are blank or missing:**
1. Click "Edit"
2. Add the missing variables
3. Click "Save"
4. Railway will automatically redeploy

### Deployment Steps

```bash
# 1. Verify everything locally
npm run dev

# 2. Commit all changes
git add cms/strapi-main/
git commit -m "strapi: prepare for Railway deployment - verify config"

# 3. Push to dev first (optional staging test)
git push origin dev

# 4. Merge to main for production deploy
git checkout main
git merge dev
git push origin main

# 5. Monitor Railway logs
# Go to railway.app → Project → Strapi → Deployment
# Watch logs for build/start errors
```

---

## 🔬 Debug: Check Railway Logs

1. Go to railway.app → Your Project → Strapi Service
2. Click "Deployments" tab
3. Click the latest deployment
4. Check "Build Logs" for errors
5. Check "Deployment Logs" for runtime errors

**Look for these success indicators:**
```
✅ "Using yarn package manager"
✅ "yarn install" completed successfully
✅ "yarn run build" completed successfully
✅ "yarn start" running on port 1337
```

**Look for these failure indicators:**
```
❌ "npm: not found" - Wrong package manager
❌ "node-version:" error - Wrong .nvmrc format
❌ "yarn: not found" - Procfile not used
❌ "@noble/hashes" error - Node version wrong (need 20+)
❌ "Cannot create session" - NODE_ENV or ADMIN_JWT_SECRET missing
```

---

## 🆘 If Still Failing

Try these steps in order:

### Step 1: Regenerate yarn.lock

```bash
# Remove placeholder yarn.lock
rm cms/strapi-main/yarn.lock

# Create minimal yarn.lock (Railway will regenerate on first build)
cat > cms/strapi-main/yarn.lock << 'EOF'
# yarn lockfile v1
# This will be regenerated by Railway during: yarn install

EOF

git add cms/strapi-main/yarn.lock
git commit -m "regenerate yarn.lock placeholder"
git push origin main
```

### Step 2: Force Railway Rebuild

1. Go to railway.app → Project → Strapi Service
2. Click "Deployments"
3. Click the failing deployment
4. Click "Redeploy" button
5. Watch logs for the error

### Step 3: Check .nvmrc Format

**File:** `cms/strapi-main/.nvmrc`

```
20.19.5
```

Must be EXACTLY this format:
- ✅ Just the version number
- ❌ NOT `node-version: 20.19.5`
- ❌ NOT `node=20.19.5`
- ❌ NOT `v20.19.5`

**Fix if wrong:**

```bash
echo "20.19.5" > cms/strapi-main/.nvmrc
git add cms/strapi-main/.nvmrc
git commit -m "fix: correct .nvmrc format"
git push origin main
```

### Step 4: Manually Trigger Full Rebuild

1. Go to railway.app → Settings → General
2. Scroll to "Deployments"
3. Click "Delete" on the failing deployment
4. Push a new commit to trigger rebuild:

```bash
git commit --allow-empty -m "rebuild: trigger Railway rebuild"
git push origin main
```

---

## 📊 Expected Railway Build Timeline

| Step | Time | What's Happening |
|------|------|------------------|
| Detect | 10s | Railway detects push, reads railway.json |
| Build Start | 30s | Runs `bash build.sh` |
| yarn install | 1-2m | Downloads all dependencies with yarn |
| yarn build | 1-2m | Compiles TypeScript, builds Strapi |
| Deploy | 30s | Copies build to production, runs Procfile |
| Start | 20s | `yarn start` initializes, connects to DB |
| Ready | 5-10m total | Strapi running and accessible ✅ |

If build takes longer than 10 minutes, check logs for errors.

---

## ✅ Verification After Deploy

Once Railway shows "Deployment Successful":

```bash
# 1. Check if Strapi admin panel loads
curl https://your-railway-url.app/admin

# 2. Check if API endpoint works
curl https://your-railway-url.app/api/posts \
  -H "Authorization: Bearer YOUR_API_TOKEN"

# 3. Monitor logs in Railway dashboard for errors
# Look for: "[strapi] server has started successfully"
```

---

**Last Updated:** October 22, 2025  
**Strapi Version:** 5.18.1  
**Node:** 20.19.5  
**Package Manager:** yarn 1.22.22

See also:
- `docs/guides/troubleshooting/01-RAILWAY_YARN_FIX.md`
- `docs/03-DEPLOYMENT_AND_INFRASTRUCTURE.md`
