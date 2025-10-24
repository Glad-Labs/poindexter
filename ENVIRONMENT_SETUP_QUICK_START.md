# 🚀 Quick Start: Environment Files & GitHub Secrets

**Time Required:** 30-45 minutes  
**Difficulty:** 🟢 Easy - just copy/paste and fill in values

---

## 📋 What You Need to Do (Step by Step)

### STEP 1: Create Local `.env` File (5 minutes)

**File Location:** `c:\Users\mattm\glad-labs-website\.env`

**Action:** Create this file and add your local development settings:

```bash
# ==================================
# LOCAL DEVELOPMENT ENVIRONMENT
# ==================================

NODE_ENV=development
LOG_LEVEL=DEBUG

# Ports
STRAPI_PORT=1337
PUBLIC_SITE_PORT=3000
OVERSIGHT_HUB_PORT=3001
COFOUNDER_AGENT_PORT=8000

# Database (SQLite for local dev)
DATABASE_CLIENT=sqlite
DATABASE_FILENAME=.tmp/data.db

# Strapi Local
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=dev-token-12345

# Frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:3000
NEXT_PUBLIC_COFOUNDER_AGENT_URL=http://localhost:8000

# AI Provider (choose ONE)
OPENAI_API_KEY=sk-YOUR-OPENAI-KEY-HERE
# OR
# ANTHROPIC_API_KEY=sk-ant-YOUR-KEY-HERE
# OR use free Ollama
# USE_OLLAMA=true

# Feature Flags
ENABLE_ANALYTICS=false
ENABLE_ERROR_REPORTING=false
ENABLE_DEBUG_LOGS=true

# Timeouts
API_TIMEOUT=30000
API_RETRY_ATTEMPTS=1
RATE_LIMIT_REQUESTS_PER_MINUTE=10000
```

**✅ Action Taken:**

- [ ] Created `.env` file with your local settings
- [ ] Added your actual API key (OpenAI, Anthropic, or using Ollama)
- [ ] Saved the file

**Important:** This file will NOT be committed (it's in `.gitignore`). Keep it safe!

---

### STEP 2: Create Workspace `.env.local` Files (5 minutes)

Create these three files to override settings per workspace:

**File 1:** `web/public-site/.env.local`
```bash
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
```

**File 2:** `web/oversight-hub/.env.local`
```bash
REACT_APP_API_URL=http://localhost:8000
REACT_APP_STRAPI_URL=http://localhost:1337
```

**File 3:** `src/cofounder_agent/.env.local`
```bash
# Use same API key from root .env
OPENAI_API_KEY=sk-YOUR-OPENAI-KEY-HERE
```

**✅ Action Taken:**

- [ ] Created `.env.local` in `web/public-site/`
- [ ] Created `.env.local` in `web/oversight-hub/`
- [ ] Created `.env.local` in `src/cofounder_agent/`

---

### STEP 3: Add GitHub Secrets (15-20 minutes)

**Where:** GitHub → Your Repository → Settings → Secrets and variables → Actions

**What to Add:**

Copy these secret names and values. Get values from their respective platforms:

| Secret Name | Where to Get Value |
|-------------|-------------------|
| `NEXT_PUBLIC_STRAPI_API_URL` | Your Railway Strapi URL (e.g., `https://cms.railway.app`) |
| `NEXT_PUBLIC_STRAPI_API_TOKEN` | Strapi Admin → Settings → API Tokens → Create token |
| `VERCEL_TOKEN` | Vercel Dashboard → Settings → Tokens → Create token |
| `VERCEL_PROJECT_ID` | Vercel Project → Settings → Project ID |
| `VERCEL_ORG_ID` | Vercel Account → Settings → Team ID |
| `RAILWAY_TOKEN` | Railway Dashboard → Account → API Tokens |
| `RAILWAY_STAGING_PROJECT_ID` | Railway → Select staging project → Copy ID from URL |
| `RAILWAY_PROD_PROJECT_ID` | Railway → Select prod project → Copy ID from URL |
| `STAGING_DB_HOST` | Railway → PostgreSQL → Database settings |
| `STAGING_DB_USER` | Railway → PostgreSQL → Username |
| `STAGING_DB_PASSWORD` | Railway → PostgreSQL → Password |
| `PROD_DB_HOST` | Railway → PostgreSQL → Database settings |
| `PROD_DB_USER` | Railway → PostgreSQL → Username |
| `PROD_DB_PASSWORD` | Railway → PostgreSQL → Password |
| `STAGING_STRAPI_TOKEN` | Strapi staging → Settings → API Tokens |
| `PROD_STRAPI_TOKEN` | Strapi production → Settings → API Tokens |
| `STAGING_OPENAI_API_KEY` | OpenAI Dashboard → API Keys |
| `PROD_OPENAI_API_KEY` | OpenAI Dashboard → API Keys |
| `STAGING_ANTHROPIC_API_KEY` | Anthropic Console → API Keys |
| `PROD_ANTHROPIC_API_KEY` | Anthropic Console → API Keys |

**How to Add Each Secret:**

1. Go to GitHub → Your repo → Settings → Secrets and variables → Actions
2. Click **New repository secret**
3. Copy name from table above
4. Paste value from respective platform
5. Click **Add secret**
6. Repeat for all ~20 secrets

**✅ Action Taken:**

- [ ] Added `NEXT_PUBLIC_STRAPI_API_URL`
- [ ] Added `NEXT_PUBLIC_STRAPI_API_TOKEN`
- [ ] Added `VERCEL_TOKEN`
- [ ] Added `VERCEL_PROJECT_ID`
- [ ] Added `VERCEL_ORG_ID`
- [ ] Added `RAILWAY_TOKEN`
- [ ] Added all staging secrets (DB, Strapi, AI)
- [ ] Added all production secrets (DB, Strapi, AI)

---

### STEP 4: Verify Everything Works Locally (5 minutes)

Run these commands to test:

```powershell
# Start all services
npm run dev

# You should see:
# - Strapi: "Server is running at http://localhost:1337"
# - Public Site: "Local: http://localhost:3000"
# - Oversight Hub: "Compiled successfully"
# - Backend: "Application startup complete"
```

**✅ Action Taken:**

- [ ] All services start without "environment variable not found" errors
- [ ] Strapi loads at `http://localhost:1337`
- [ ] Public site loads at `http://localhost:3000`
- [ ] No red errors in terminals

---

### STEP 5: Commit Your Changes (5 minutes)

```powershell
# Check what files to commit
git status

# You should see:
# - Modified: .gitignore (if updated)
# - Untracked: docs/ENVIRONMENT_FILES_GUIDE.md (this guide)
# - NOT .env (should be ignored)

# Stage and commit
git add .gitignore docs/ENVIRONMENT_FILES_GUIDE.md

git commit -m "chore: add environment files guide and organize secrets setup

- Create comprehensive environment files guide
- Document GitHub Secrets required for deployments
- Document .env.local setup for local development
- Ready for team to add secrets to GitHub"

git push origin feat/test-branch
```

**✅ Action Taken:**

- [ ] Committed environment guide
- [ ] Pushed to GitHub
- [ ] Ready for team review

---

## 📁 File Organization Summary

After completing all steps, you'll have:

```
glad-labs-website/
├── .env                   ✅ Created (LOCAL - never commit)
├── .env.example           ✅ Exists (template)
├── .env.staging           ✅ Exists (template)
├── .env.production        ✅ Exists (template)
├── .gitignore             ✅ Updated (ignore .env, allow templates)
├── docs/
│   └── ENVIRONMENT_FILES_GUIDE.md  ✅ Created (detailed guide)
│
├── web/
│   ├── public-site/
│   │   └── .env.local     ✅ Created (LOCAL - never commit)
│   │
│   └── oversight-hub/
│       └── .env.local     ✅ Created (LOCAL - never commit)
│
└── src/
    └── cofounder_agent/
        └── .env.local     ✅ Created (LOCAL - never commit)
```

---

## 🔐 Security Checklist

Before deploying, verify:

- [ ] No `.env` file committed to git
- [ ] No `.env.local` files committed
- [ ] All secrets added to GitHub (20+ secrets)
- [ ] No API keys in any committed files
- [ ] `.env.example`, `.env.staging`, `.env.production` only have placeholders
- [ ] `.gitignore` has proper patterns

**Check with this command:**

```powershell
git log --all --full-history -- ".env"

# Should show: "No commits found"
# (Meaning .env was never committed)
```

---

## ✅ Success Indicators

When everything is set up correctly, you should see:

**Local Development:**

- ✅ Services start without env var errors
- ✅ Strapi connects to SQLite database
- ✅ Frontend loads Strapi content
- ✅ API keys work (OpenAI/Anthropic/Ollama)

**GitHub Actions (on next deployment):**

- ✅ Build succeeds without "undefined variable" errors
- ✅ Secrets are available to workflows
- ✅ Frontend deploys to Vercel
- ✅ Backend deploys to Railway

**Repository:**

- ✅ `.env` never appears in git history
- ✅ Templates (`.env.example`, `.env.staging`, `.env.production`) are committed
- ✅ All GitHub Secrets are configured

---

## 🆘 Troubleshooting

### Problem: "Cannot find module 'dotenv'"

**Solution:** Stop dev server, run `npm install`, restart

### Problem: "API key undefined" locally

**Solution:** Make sure `.env` file exists in root with your actual API key

### Problem: "GitHub Actions build fails with undefined vars"

**Solution:** Go to GitHub → Settings → Secrets → verify all ~20 secrets are added

### Problem: "Different values in staging vs production"

**Solution:** Check GitHub Secrets vs `.env.staging` vs `.env.production` - they should match

### Problem: ".env file committed by accident"

**Solution:** Run:

```powershell
git rm --cached .env
git commit -m "chore: remove accidentally committed .env"
```

---

## 📞 Next Steps After This

1. ✅ **Complete all 5 steps above**
2. ⏳ **Wait for CI/CD to test with GitHub Secrets**
3. ⏳ **Deploy to staging (dev branch)**
4. ⏳ **Test staging environment**
5. ⏳ **Deploy to production (main branch)**

---

**Estimated Total Time:** 45 minutes  
**Difficulty:** 🟢 Easy (mostly copy/paste)  
**Questions?** See `ENVIRONMENT_FILES_GUIDE.md` for detailed information

