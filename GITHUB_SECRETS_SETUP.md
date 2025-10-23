# ✅ GitHub Secrets & Deployment Configuration Checklist

**Status:** Pre-deployment setup  
**Last Updated:** October 23, 2025  
**Purpose:** Configure all required GitHub Secrets for staging and production deployments

---

## 📋 GitHub Secrets to Configure

### Step 1: Access GitHub Secrets Dashboard

1. Go to your GitHub repository
2. Click **Settings** (top menu)
3. Click **Secrets and variables** (left sidebar)
4. Click **Actions** (submenu)

### Step 2: Add Staging Secrets

Create these secrets by clicking **"New repository secret"** for each:

#### Database Secrets (Staging)

```
Name: STAGING_DB_HOST
Value: <your-railway-postgres-host>
Example: postgres-staging.railway.app

Name: STAGING_DB_USER
Value: <postgres_user>
Example: postgres

Name: STAGING_DB_PASSWORD
Value: <your-secure-password>

Name: STAGING_DB_PORT
Value: 5432

Name: STAGING_DB_NAME
Value: glad_labs_staging
```

#### API & Token Secrets (Staging)

```
Name: STAGING_STRAPI_TOKEN
Value: <your-strapi-api-token>
Description: Get from Strapi → Settings → API Tokens

Name: STAGING_STRAPI_URL
Value: https://staging-cms.railway.app

Name: STAGING_ADMIN_PASSWORD
Value: <strapi-admin-password>

Name: STAGING_ADMIN_EMAIL
Value: admin@staging.example.com
```

#### Railway Staging

```
Name: RAILWAY_STAGING_PROJECT_ID
Value: <your-railway-staging-project-id>
Description: Get from Railway dashboard

Name: RAILWAY_TOKEN
Value: <your-railway-api-token>
Description: Get from Railway → Account → API Tokens
Note: This is shared between staging and production
```

---

### Step 3: Add Production Secrets

Create these secrets for production deployment:

#### Database Secrets (Production)

```
Name: PROD_DB_HOST
Value: <your-railway-postgres-host-prod>
Example: postgres-prod.railway.app

Name: PROD_DB_USER
Value: <postgres_user_prod>

Name: PROD_DB_PASSWORD
Value: <your-VERY-SECURE-password>
⚠️ IMPORTANT: Use a different, stronger password than staging

Name: PROD_DB_PORT
Value: 5432

Name: PROD_DB_NAME
Value: glad_labs_production
```

#### API & Token Secrets (Production)

```
Name: PROD_STRAPI_TOKEN
Value: <your-strapi-production-token>

Name: PROD_STRAPI_URL
Value: https://cms.railway.app

Name: PROD_ADMIN_PASSWORD
Value: <strapi-admin-password-prod>
⚠️ IMPORTANT: Use a different password than staging

Name: PROD_ADMIN_EMAIL
Value: admin@glad-labs.com
```

#### Railway Production

```
Name: RAILWAY_PROD_PROJECT_ID
Value: <your-railway-production-project-id>
Description: Get from Railway dashboard
```

---

### Step 4: Add Vercel Secrets (For Frontend Deployment)

```
Name: VERCEL_TOKEN
Value: <your-vercel-api-token>
Description: Get from Vercel → Account → Settings → Tokens
Note: Shared between staging and production

Name: VERCEL_ORG_ID
Value: <your-vercel-organization-id>
Description: Get from Vercel → Settings → Team ID

Name: VERCEL_PROJECT_ID
Value: <your-vercel-project-id>
Description: Get from Vercel project → Settings → Project ID
```

---

## 🔐 How to Get Each Secret

### Railway Secrets

**Get Railway Token:**

1. Go to Railway → Account (top right)
2. Click **Settings**
3. Click **API Tokens**
4. Click **Create**
5. Copy the token

**Get Railway Project IDs:**

1. Go to Railway → Projects
2. Click your staging project
3. Click **Settings** → Copy **Project ID**
4. Repeat for production project

**Get Database Credentials:**

1. Go to project → **Resources** (left sidebar)
2. Click **PostgreSQL** plugin
3. Click **Plugin** tab
4. Copy connection details

### Strapi Secrets

**Get Strapi Token:**

1. Go to your Strapi admin (http://localhost:1337/admin in dev)
2. Click **Settings** (left sidebar)
3. Click **API Tokens**
4. Click **Create new API Token**
5. Name: `GitHub Staging` or `GitHub Production`
6. Type: `Full access` (for dev) or `Custom` (limit in prod)
7. Copy the token

**Get Strapi Admin Credentials:**

- Use the admin account you created during Strapi setup
- Username/Email: (your admin email)
- Password: (your secure password)

### Vercel Secrets

**Get Vercel Token:**

1. Go to Vercel → Account → Settings
2. Scroll to **API Tokens**
3. Click **Create Token**
4. Name it `GitHub CI/CD`
5. Expiration: 90 days or custom
6. Copy the token

**Get Vercel Organization ID:**

1. Go to Vercel → Team Settings
2. Look for **Team ID** or **Org ID**
3. Copy it

**Get Vercel Project ID:**

1. Go to Vercel project
2. Click **Settings** → **General**
3. Copy **Project ID**

---

## 📝 Environment Variables in Workflows

### How GitHub Actions Uses Secrets

When you push to `dev` or `main` branch, GitHub Actions:

1. **Reads the workflow file** (`.github/workflows/deploy-staging.yml` or `deploy-production.yml`)
2. **Accesses GitHub Secrets** using `${{ secrets.SECRET_NAME }}`
3. **Creates environment file** by replacing placeholders:
   ```
   DATABASE_HOST=${STAGING_DB_HOST} → DATABASE_HOST=postgres-staging.railway.app
   ADMIN_PASSWORD=${STAGING_ADMIN_PASSWORD} → ADMIN_PASSWORD=my-password
   ```
4. **Deploys with these variables** to Railway/Vercel

### Example Substitution

**In `.env.staging` (committed to git):**

```bash
DATABASE_HOST=${STAGING_DB_HOST}
DATABASE_USER=${STAGING_DB_USER}
DATABASE_PASSWORD=${STAGING_DB_PASSWORD}
STRAPI_TOKEN=${STAGING_STRAPI_TOKEN}
```

**GitHub Actions replaces to (in memory, not saved):**

```bash
DATABASE_HOST=postgres-staging.railway.app
DATABASE_USER=postgres
DATABASE_PASSWORD=my-secret-password
STRAPI_TOKEN=abc123...
```

**Result:** Secrets never committed to git, always secure! ✅

---

## ✅ Verification Checklist

After adding all secrets, verify they're configured:

```powershell
# Go to your GitHub repo
# Settings → Secrets and variables → Actions

Check boxes for each secret:
☐ STAGING_DB_HOST
☐ STAGING_DB_USER
☐ STAGING_DB_PASSWORD
☐ STAGING_DB_PORT
☐ STAGING_DB_NAME
☐ STAGING_STRAPI_TOKEN
☐ STAGING_STRAPI_URL
☐ STAGING_ADMIN_PASSWORD
☐ STAGING_ADMIN_EMAIL
☐ PROD_DB_HOST
☐ PROD_DB_USER
☐ PROD_DB_PASSWORD
☐ PROD_DB_PORT
☐ PROD_DB_NAME
☐ PROD_STRAPI_TOKEN
☐ PROD_STRAPI_URL
☐ PROD_ADMIN_PASSWORD
☐ PROD_ADMIN_EMAIL
☐ RAILWAY_TOKEN
☐ RAILWAY_STAGING_PROJECT_ID
☐ RAILWAY_PROD_PROJECT_ID
☐ VERCEL_TOKEN
☐ VERCEL_ORG_ID
☐ VERCEL_PROJECT_ID
```

---

## 🧪 Test Deployment Pipeline

### Test 1: Trigger Staging Deployment

```powershell
# Push to dev branch
git checkout dev
git pull origin dev

# Make a test commit
git commit -m "test: trigger staging deployment" --allow-empty

# Push
git push origin dev

# Watch GitHub Actions:
# 1. Go to Repository → Actions
# 2. Click "Deploy to Staging (dev branch)" workflow
# 3. Watch the run
# 4. Check logs for any secret-related errors
```

**Expected Output:**

```
✅ Build successful
✅ Deploy to Railway (Backend) successful
✅ Deploy to Vercel (Frontend) successful
📍 Staging available at:
   - https://staging-cms.railway.app
   - https://glad-labs-staging.vercel.app
```

**If it fails:**

```
Check these things:
1. GitHub Secrets are all configured (no typos)
2. Railway project IDs are correct
3. Vercel tokens haven't expired
4. .env.staging file exists and has ${PLACEHOLDER} syntax
```

### Test 2: Trigger Production Deployment

```powershell
# Push to main branch
git checkout main
git pull origin main
git merge dev

# Make a test commit
git commit -m "test: trigger production deployment" --allow-empty

# Push
git push origin main

# Watch GitHub Actions for successful deployment
```

**Expected Output:**

```
🎉 PRODUCTION DEPLOYMENT SUCCESSFUL!
🌍 Public Site: https://glad-labs.vercel.app
📊 Oversight Hub: https://admin.glad-labs.vercel.app
🚂 Backend API: https://api.railway.app
📝 CMS: https://cms.railway.app
```

---

## 🚨 Troubleshooting Secrets

### Issue: "Secret not found" in GitHub Actions logs

**Solution:**

1. Check spelling of secret name (case-sensitive)
2. Verify it's created in GitHub Settings
3. Check it uses `${{ secrets.NAME }}` syntax in workflow

### Issue: Deployment uses wrong environment variables

**Solution:**

1. Verify `.env.staging` or `.env.tier1.production` has `${PLACEHOLDER}` format
2. Check GitHub Secrets match the placeholder names
3. Look at workflow logs to see replaced values

### Issue: Railway/Vercel deployment fails with auth error

**Solution:**

1. Test tokens individually:
   ```bash
   railway login --token=${{ secrets.RAILWAY_TOKEN }}
   vercel --token=${{ secrets.VERCEL_TOKEN }} info
   ```
2. Regenerate tokens if expired
3. Verify tokens have correct permissions

### Issue: Local `.env.local` file conflicts with CI/CD

**Solution:**

- `.env.local` should be in `.gitignore` (it is)
- GitHub Actions uses `.env.staging` or `.env.tier1.production`
- Never affects CI/CD deployments

---

## 📚 Your Current Status

**Local Development:**
✅ Uses `.env.local` (SQLite, localhost)
✅ Not affected by GitHub Secrets

**Staging:**
🔄 Workflows exist (`.github/workflows/deploy-staging.yml`)
⏳ Needs GitHub Secrets configured
⏳ Ready to test after secrets added

**Production:**
🔄 Workflows exist (`.github/workflows/deploy-production.yml`)
⏳ Needs GitHub Secrets configured
⏳ Ready to test after secrets added

**Next Steps:**

1. Get all secrets/tokens from Railway, Strapi, Vercel
2. Add them to GitHub Settings → Secrets
3. Test staging deployment (push to dev)
4. Test production deployment (push to main)
5. Monitor in GitHub Actions tab

---

## 💡 Key Points Reminder

✅ **Secrets are secure** - Never visible in logs, only GitHub can see them  
✅ **Environment files safe** - `.env.local` gitignored, staging/prod committed without secrets  
✅ **Local dev unaffected** - Your `.env.local` stays local, CI/CD uses GitHub Secrets  
✅ **Deployments automatic** - Push to dev → stages, push to main → production  
✅ **package-lock.json safe** - Ensures consistent versions, not affected by secrets

---

**Ready to configure secrets? Start with Step 1 above!** 🚀
