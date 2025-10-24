# GitHub Environments & Secrets - Quick Setup (5 Minutes)

**Last Updated:** October 24, 2025  
**For:** Setting up GitHub Secrets by component and environment

---

## 🎯 TL;DR - The Answer

**YES** - GitHub Environments let you organize secrets by component and environment automatically.

### How It Works

1. Create `staging` and `production` environments in GitHub
2. Add component-specific secrets to each environment
3. In your workflow, add: `environment: staging` or `environment: production`
4. GitHub automatically provides the correct secrets ✅

---

## ⚡ 5-Minute Setup

### Step 1: Create GitHub Environments (2 min)

Go to: **GitHub Repo → Settings → Environments**

**Create `staging` environment:**

- Name: `staging`
- Deployment branch: `dev`
- Leave "Required reviewers" empty

**Create `production` environment:**

- Name: `production`
- Deployment branch: `main`
- ✅ Check "Required reviewers" (recommended for safety)

### Step 2: Add Secrets to Staging (1.5 min)

In staging environment, click **Add secret** and add these:

```bash
# Strapi CMS
STRAPI_STAGING_DB_HOST = (your staging db host)
STRAPI_STAGING_DB_USER = (your db username)
STRAPI_STAGING_DB_PASSWORD = (your db password)
STRAPI_STAGING_ADMIN_PASSWORD = (generate one)
STRAPI_STAGING_JWT_SECRET = (generate one)
STRAPI_STAGING_API_TOKEN = (from Strapi admin)

# Co-Founder Agent
COFOUNDER_STAGING_OPENAI_API_KEY = (your OpenAI key)
COFOUNDER_STAGING_ANTHROPIC_API_KEY = (your Anthropic key)
COFOUNDER_STAGING_REDIS_HOST = (your staging redis)
COFOUNDER_STAGING_REDIS_PASSWORD = (your redis pass)

# Public Site
PUBLIC_SITE_STAGING_STRAPI_URL = https://staging-cms.railway.app
PUBLIC_SITE_STAGING_COFOUNDER_URL = https://staging-agent.railway.app:8000
PUBLIC_SITE_STAGING_GA_ID = (your staging GA4)

# Oversight Hub
OVERSIGHT_STAGING_STRAPI_URL = https://staging-cms.railway.app
OVERSIGHT_STAGING_COFOUNDER_URL = https://staging-agent.railway.app:8000
```

### Step 3: Add Secrets to Production (1.5 min)

Repeat Step 2 but with `PROD` versions and production values.

### Step 4: Update Your Workflows

In `.github/workflows/deploy-staging.yml`:

```yaml
jobs:
  deploy:
    environment: staging # 👈 Add this line
    runs-on: ubuntu-latest
```

In `.github/workflows/deploy-production.yml`:

```yaml
jobs:
  deploy:
    environment: production # 👈 Add this line
    runs-on: ubuntu-latest
```

### Step 5: Use Secrets in Workflow

```yaml
- name: Deploy Component
  env:
    DATABASE_PASSWORD: ${{ secrets.STRAPI_STAGING_DB_PASSWORD }}
    API_KEY: ${{ secrets.COFOUNDER_STAGING_OPENAI_API_KEY }}
  run: npm run deploy
```

**Done!** ✅

---

## 📊 What You Get

### Automatic Behavior

```text
When workflow runs on "dev" branch
  ↓
GitHub sees: environment: staging
  ↓
GitHub loads ALL staging secrets
  ↓
Workflow uses: ${{ secrets.SECRET_NAME }}
  ↓
Correct secret automatically provided ✅
```

### Same for Production

```text
When workflow runs on "main" branch
  ↓
GitHub sees: environment: production
  ↓
GitHub loads ALL production secrets
  ↓
Workflow uses: ${{ secrets.SECRET_NAME }}
  ↓
Correct secret automatically provided ✅
```

---

## 📋 Component Breakdown

### 4 Components × 2 Environments = 8 Secret Groups

| Component            | Staging Secrets         | Production Secrets   |
| -------------------- | ----------------------- | -------------------- |
| **Strapi CMS**       | `STRAPI_STAGING_*`      | `STRAPI_PROD_*`      |
| **Co-Founder Agent** | `COFOUNDER_STAGING_*`   | `COFOUNDER_PROD_*`   |
| **Public Site**      | `PUBLIC_SITE_STAGING_*` | `PUBLIC_SITE_PROD_*` |
| **Oversight Hub**    | `OVERSIGHT_STAGING_*`   | `OVERSIGHT_PROD_*`   |

### Repository-Level Secrets (Shared)

These go at **Settings → Secrets and variables → Actions** (not in environments):

```bash
RAILWAY_TOKEN          # Authentication for all Railway deployments
VERCEL_TOKEN          # Authentication for all Vercel deployments
GCP_PROJECT_ID        # GCP project identifier
GCP_SERVICE_ACCOUNT   # GCP authentication
```

---

## ✅ Verification

After setup, push to `dev` branch:

```bash
git push origin dev
# GitHub Actions runs with staging secrets ✅
```

Check logs at: **GitHub Repo → Actions → Latest Run**

Should show:

- ✅ Strapi deployed with staging config
- ✅ Agent deployed with staging API keys
- ✅ Public Site deployed with staging URLs
- ✅ Oversight Hub deployed with staging URLs
- ✅ No secrets visible in logs (masked automatically)

---

## 🔒 Security Notes

✅ **GitHub automatically masks secrets** in logs
✅ **Branch filtering works**: staging secrets only on `dev`, production secrets only on `main`
✅ **Each environment isolated**: staging secrets never accessible from production workflow
✅ **Manual approval available**: Set production to require reviewers

---

## 📚 Full Documentation

For complete details on all secrets, see: **[GITHUB_SECRETS_SETUP.md](./GITHUB_SECRETS_SETUP.md)**

For workflow examples, see:

- `.github/workflows/deploy-staging-with-environments.yml`
- `.github/workflows/deploy-production-with-environments.yml`

---

## 🆘 Troubleshooting

### Problem: "Secret not found" in logs

**Check:**

1. Environment name in workflow matches exactly: `environment: staging`
2. Secret name spelled correctly: `${{ secrets.STRAPI_STAGING_DB_HOST }}`
3. Secret actually added to that environment

### Problem: Wrong environment's secrets used

**Check:**

1. Workflow has `environment: staging` or `environment: production`
2. Branch name matches environment deployment branch

### Problem: Need to add/change a secret

**Steps:**

1. Go to Settings → Environments → `staging` (or `production`)
2. Click secret name to edit it
3. Or delete and re-add with new value

---

## 🚀 Next Steps

1. ✅ Create environments in GitHub Settings
2. ✅ Add all component secrets above
3. ✅ Update workflows to use environments
4. ✅ Push code and test deployment
5. ✅ Verify all components get correct secrets

**Questions?** See full guide: `GITHUB_SECRETS_SETUP.md`
