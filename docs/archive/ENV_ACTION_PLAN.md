# 📋 ACTION PLAN: Clean Up Your .env Files for Production

**Date:** October 23, 2025  
**Goal:** Get your `.env` files production-ready  
**Time:** ~5 minutes to execute

---

## 🎯 Your Current Situation

```bash
✅ .env                    Keep (has YOUR secrets for local dev)
✅ .env.example            Keep (template for new developers)
✅ .env.staging            Keep (staging config - COMMIT THIS)
✅ .env.production         Keep (production config - COMMIT THIS)
❌ .env.tier1.production   DELETE (old naming convention)
❌ .env.old                DELETE (backup, no longer needed)
❌ .env.local              DELETE (Next.js override, not needed)
```

---

## ✅ Step-by-Step Actions

### Action 1: Delete Old Files

```bash
# Remove old/unused files
rm .env.tier1.production
rm .env.old
rm .env.local
```

**Why?**

- `.env.tier1.production` - Old naming, replaced by `.env.production`
- `.env.old` - Backup file, not part of current workflow
- `.env.local` - Next.js local override, not needed

---

### Action 2: Verify Your .env (Local Secrets)

**File:** `.env`  
**Contains:** ✅ YES, your real API keys  
**In Git:** ❌ NO (in `.gitignore`)  
**Needs:** Your actual secrets

**Checklist:**

```bash
# Check if .env exists
cat .env | head -20

# It should have:
✅ OPENAI_API_KEY=sk-...
✅ ANTHROPIC_API_KEY=sk-ant-...  (or GOOGLE_API_KEY)
✅ STRAPI_API_TOKEN=...
✅ DATABASE credentials (local sqlite or dev postgres)
```

**If missing anything:**

```bash
# Copy from example
cp .env.example .env

# Then add your real secrets
nano .env  # (or edit in VS Code)

# Add:
OPENAI_API_KEY=sk-your-real-key
ANTHROPIC_API_KEY=sk-ant-your-real-key
STRAPI_API_TOKEN=your-strapi-token
# etc.
```

---

### Action 3: Verify .env.staging (Staging Template)

**File:** `.env.staging`  
**Contains:** ❌ NO secrets, only references to GitHub Secrets  
**In Git:** ✅ YES (safe to commit)  
**Usage:** GitHub Actions uses this + GitHub Secrets when deploying `dev` branch

**Current content looks GOOD:**

```bash
✅ NEXT_PUBLIC_STRAPI_API_URL=https://staging-cms.railway.app
✅ STRAPI_API_TOKEN=${STAGING_STRAPI_TOKEN}    # References GitHub Secret
✅ DATABASE_HOST=${STAGING_DB_HOST}             # References GitHub Secret
✅ OPENAI_API_KEY=${STAGING_OPENAI_API_KEY}     # References GitHub Secret
```

**No changes needed!** ✅

---

### Action 4: Verify .env.production (Production Template)

**File:** `.env.production`  
**Contains:** ❌ NO secrets, only references to GitHub Secrets  
**In Git:** ✅ YES (safe to commit)  
**Usage:** GitHub Actions uses this + GitHub Secrets when deploying `main` branch

**Current content looks GOOD:**

```bash
✅ NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
✅ STRAPI_API_TOKEN=${PROD_STRAPI_TOKEN}        # References GitHub Secret
✅ DATABASE_HOST=${PROD_DB_HOST}                # References GitHub Secret
✅ OPENAI_API_KEY=${PROD_OPENAI_API_KEY}        # References GitHub Secret
```

**No changes needed!** ✅

---

### Action 5: Set GitHub Secrets

These are the **ACTUAL SECRETS** that GitHub Actions will inject at deploy time.

**Go to:** GitHub → Settings → Secrets and variables → Actions

**Add these for STAGING (when deploying `dev` branch):**

```
STAGING_STRAPI_TOKEN          = your-actual-staging-strapi-token
STAGING_DB_HOST               = staging-db.railway.app
STAGING_DB_USER               = postgres
STAGING_DB_PASSWORD           = your-staging-db-password
STAGING_ADMIN_PASSWORD        = your-staging-admin-password
STAGING_ANTHROPIC_API_KEY     = sk-ant-your-staging-key
STAGING_OPENAI_API_KEY        = sk-your-staging-key
```

**Add these for PRODUCTION (when deploying `main` branch):**

```
PROD_STRAPI_TOKEN             = your-actual-prod-strapi-token
PROD_DB_HOST                  = prod-db.railway.app
PROD_DB_USER                  = postgres
PROD_DB_PASSWORD              = your-prod-db-password
PROD_ADMIN_PASSWORD           = your-prod-admin-password
PROD_ANTHROPIC_API_KEY        = sk-ant-your-prod-key
PROD_OPENAI_API_KEY           = sk-your-prod-key
PROD_GA_ID                    = your-google-analytics-id
```

**Add these for BOTH Staging & Production:**

```
RAILWAY_TOKEN                 = your-railway-cli-token
VERCEL_TOKEN                  = your-vercel-cli-token
VERCEL_PROJECT_ID             = your-vercel-project-id
```

---

### Action 6: Update .gitignore (Verify It's Correct)

**File:** `.gitignore` in root

**Should HAVE (to never commit secrets):**

```gitignore
.env
.env.local
.env.secrets
.env.*.local
```

**Should ALLOW (safe to commit):**

```gitignore
!.env.example
!.env.staging
!.env.production
```

**Check it:**

```bash
cat .gitignore | grep -A5 "\.env"
```

---

## 🚀 Quick Commands to Execute

Run these in order:

```bash
# 1. Delete old files
rm .env.tier1.production .env.old .env.local

# 2. Verify git will track correct files
git status

# Should show:
# - .env (unstaged - not tracked, good!)
# - .env.staging (tracked, good!)
# - .env.production (tracked, good!)

# 3. Commit the cleanup
git add -A
git commit -m "chore: clean up old env files, keep only .env.staging and .env.production"

# 4. Push
git push origin feat/test-branch
```

---

## 📊 Final Result (After Cleanup)

```
✅ .env                    Your local secrets (NOT in git)
✅ .env.example            Template for new developers (in git)
✅ .env.staging            Staging config (in git) - uses GitHub Secrets
✅ .env.production         Production config (in git) - uses GitHub Secrets
```

**What Next:**

1. ✅ Delete old files (above)
2. ✅ Make sure `.env.staging` and `.env.production` are committed
3. ✅ Set GitHub Secrets (22 variables)
4. ✅ Test: Push `dev` branch → should auto-deploy to staging
5. ✅ Test: Push `main` branch → should auto-deploy to production

---

## ❓ FAQ

### Q: Do I commit my local `.env`?

**A:** NO! It has your real secrets. Keep it in `.gitignore`.

### Q: Do I commit `.env.staging` and `.env.production`?

**A:** YES! They don't have real secrets, just placeholder references to GitHub Secrets.

### Q: Where do my real API keys go?

**A:**

- **Local:** In your `.env` file (never committed)
- **Staging/Prod:** In GitHub Secrets (GitHub manages them securely)

### Q: How does GitHub Actions get my secrets?

**A:**

- GitHub Secrets are encrypted and stored in GitHub
- GitHub Actions reads them from the workflow
- Passes them to Railway/Vercel at deploy time
- Never stored in files or git history

### Q: What if I accidentally committed my `.env`?

```bash
# Remove from git history
git rm --cached .env
git commit -m "chore: remove .env from tracking"
git push

# File stays on your machine (safe)
# Add to .gitignore to prevent future commits
```

---

## 🎯 When You're Done

Your repo will be:

✅ **Clean** - Only necessary `.env` files  
✅ **Secure** - No secrets in git, all in GitHub Secrets  
✅ **Organized** - Clear separation: local vs staging vs prod  
✅ **Production-Ready** - GitHub Actions can deploy safely

---

**Need help with any step?** Let me know!
