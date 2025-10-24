# 🔐 PRODUCTION ENV CHECKLIST

Use this checklist before deploying to production.

---

## ✅ Pre-Production Verification

- [ ] **Local Development Works**
  - `npm run dev` runs without errors
  - `.env` has your real API keys
  - All services accessible (localhost:3000, 3001, 8000, 1337)

- [ ] **.env.staging is Correct**
  - Uses `${STAGING_*}` placeholders
  - No real API keys present
  - Committed to git
- [ ] **.env.production is Correct**
  - Uses `${PROD_*}` placeholders
  - No real API keys present
  - Committed to git

- [ ] **GitHub Secrets are Set** (22 variables)
  - [ ] STAGING_STRAPI_TOKEN
  - [ ] STAGING_OPENAI_API_KEY
  - [ ] STAGING_ANTHROPIC_API_KEY
  - [ ] STAGING_DB_PASSWORD
  - [ ] STAGING_DB_HOST
  - [ ] STAGING_DB_USER
  - [ ] STAGING_ADMIN_PASSWORD
  - [ ] PROD_STRAPI_TOKEN
  - [ ] PROD_OPENAI_API_KEY
  - [ ] PROD_ANTHROPIC_API_KEY
  - [ ] PROD_DB_PASSWORD
  - [ ] PROD_DB_HOST
  - [ ] PROD_DB_USER
  - [ ] PROD_ADMIN_PASSWORD
  - [ ] PROD_GA_ID
  - [ ] RAILWAY_TOKEN
  - [ ] VERCEL_TOKEN
  - [ ] VERCEL_PROJECT_ID
  - [ ] Plus any additional secrets

- [ ] **Vercel Has Correct Variables**
  - [ ] NEXT_PUBLIC_STRAPI_API_URL (staging or prod)
  - [ ] STRAPI_API_TOKEN (from GitHub Secrets)
  - [ ] No backend-only variables (OPENAI_API_KEY, etc.)

- [ ] **Railway Has Correct Variables**
  - [ ] All database credentials
  - [ ] All AI provider keys
  - [ ] All GCP/Firebase credentials
  - [ ] No frontend-only variables

- [ ] **.gitignore is Correct**
  - [ ] `.env` is ignored (never committed)
  - [ ] `.env.staging` is NOT ignored (committed)
  - [ ] `.env.production` is NOT ignored (committed)

- [ ] **Old Files Deleted**
  - [ ] `.env.tier1.production` deleted
  - [ ] `.env.old` deleted
  - [ ] `.env.local` deleted

---

## 🚀 Deployment Checklist

Before pushing to `dev` (staging) branch:

- [ ] Run `npm run test`
- [ ] Run `npm run lint`
- [ ] Verify `.env` works locally
- [ ] Verify `.env.staging` is committed

Before pushing to `main` (production) branch:

- [ ] All tests pass
- [ ] All code reviewed
- [ ] Staging environment verified
- [ ] Rollback plan documented
- [ ] Backups configured
- [ ] Monitoring alerts enabled

---

## 🔍 GitHub Actions Verification

### Staging Deployment (dev branch)

- [ ] GitHub Actions workflow reads `.env.staging`
- [ ] GitHub Actions injects STAGING\_\* secrets
- [ ] Railway receives correct environment variables
- [ ] Staging deployment completes successfully
- [ ] Test at https://staging-cms.railway.app

### Production Deployment (main branch)

- [ ] GitHub Actions workflow reads `.env.production`
- [ ] GitHub Actions injects PROD\_\* secrets
- [ ] Vercel receives frontend variables
- [ ] Railway receives backend variables
- [ ] Frontend deployment completes successfully
- [ ] Backend deployment completes successfully
- [ ] Test at https://glad-labs.vercel.app

---

## 🎯 Secret Separation

### Local Machine (.env)

```
Your real secrets - NEVER commit
OPENAI_API_KEY=sk-xxxx
ANTHROPIC_API_KEY=sk-ant-xxxx
STRAPI_API_TOKEN=xxxx
DATABASE_PASSWORD=xxxx
```

### GitHub Secrets

```
STAGING_STRAPI_TOKEN=xxx
STAGING_OPENAI_API_KEY=sk-xxx
STAGING_DB_PASSWORD=xxx
...
PROD_STRAPI_TOKEN=xxx
PROD_OPENAI_API_KEY=sk-xxx
PROD_DB_PASSWORD=xxx
```

### .env.staging and .env.production

```
References only - SAFE TO COMMIT
STRAPI_API_TOKEN=${STAGING_STRAPI_TOKEN}
OPENAI_API_KEY=${STAGING_OPENAI_API_KEY}
DATABASE_PASSWORD=${STAGING_DB_PASSWORD}
```

---

## 🛑 DO NOT DO

❌ Put real API keys in `.env.staging` or `.env.production`
❌ Commit `.env` file
❌ Add backend secrets to Vercel
❌ Share GitHub Secrets with team (GitHub manages access)
❌ Hardcode secrets in code files
❌ Log sensitive information

---

## ✅ DO THIS

✅ Keep `.env` in `.gitignore`
✅ Use GitHub Secret references in committed `.env` files
✅ Store all real secrets in GitHub Secrets
✅ Use environment-specific variables (STAGING*\*, PROD*\*)
✅ Rotate secrets regularly
✅ Use strong, unique passwords
✅ Document the secret setup for team onboarding

---

## 📋 Final Verification

```bash
# 1. Check what will be committed
git status

# Should show:
# - .env.staging (modified/staged)
# - .env.production (modified/staged)
# - NOT .env (ignored, good!)

# 2. Verify secrets aren't in git
git grep "sk-" | grep -v ".env.example"
# Should return nothing (good!)

# 3. Double-check .gitignore
cat .gitignore | grep ".env"
# Should show .env is ignored

# 4. Commit
git add -A
git commit -m "chore: finalize env files for production"
git push origin feat/test-branch

# 5. Set GitHub Secrets (via GitHub web UI)
# Settings → Secrets and variables → Actions
# Add all variables above
```

---

## 🚨 Emergency Procedures

### If you accidentally commit .env

```bash
git rm --cached .env
git commit -m "chore: remove .env from tracking"
git push
# File stays on your machine (safe)
# Regenerate all secrets (they're now exposed)
```

### If a secret is exposed

```bash
1. Regenerate the secret immediately
2. Update in GitHub Secrets
3. Redeploy affected environments
4. Monitor for unauthorized access
5. Document incident
```

### If deployment fails

```bash
1. Check GitHub Actions logs for error
2. Verify all GitHub Secrets are set
3. Check .env.staging or .env.production for typos
4. Verify Railway/Vercel configurations
5. Check database connectivity
6. Rollback if necessary: git revert <commit>
```

---

**Status: Production Ready** ✅

You're all set for production deployment when you:

1. Delete old env files
2. Set GitHub Secrets
3. Push to feat/test-branch
