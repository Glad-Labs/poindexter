# 🎯 GLAD Labs - Git Workflow Quick Reference Card

**Print this or bookmark it!**

---

## 🚀 START HERE - 30 Second Quickstart

```powershell
# 1. Make sure you're on a feature branch
git branch  # Should show: * feat/something

# 2. Run development
npm run dev

# 3. Open in browser
# - Strapi: http://localhost:1337/admin
# - Public Site: http://localhost:3000
# - Oversight Hub: http://localhost:3001

# Done! You're developing now.
```

---

## 📋 Your Workflow

### 🔧 LOCAL DEVELOPMENT (You Are Here)

**Branch:** `feat/your-feature`  
**Command:** `npm run dev`  
**Environment:** `.env.local` (localhost)  
**Services:** Strapi (1337) + Public Site (3000) + Oversight Hub (3001)

```powershell
# Create feature branch
git checkout -b feat/my-awesome-feature

# Start development
npm run dev

# Make changes and code
# (Services auto-reload on file changes)

# Commit your work
git add .
git commit -m "feat: add my awesome feature"

# Push to origin
git push origin feat/my-awesome-feature
```

---

### 📊 STAGING DEPLOYMENT (Next Step)

**Branch:** `dev`  
**Trigger:** Push to dev branch  
**Environment:** `.env.staging` (PostgreSQL, Railway staging)  
**Services:** Staging URLs from Railway

```powershell
# Merge your feature to dev
git checkout dev
git pull origin dev
git merge feat/my-awesome-feature
git push origin dev

# GitHub Actions automatically:
# 1. Reads .env.staging
# 2. Gets secrets from GitHub (STAGING_DB_HOST, etc.)
# 3. Deploys to Railway staging
# 4. Available at: https://staging-cms.railway.app

# Test on staging
# Then merge dev → main for production
```

---

### 🚀 PRODUCTION DEPLOYMENT (Final Step)

**Branch:** `main`  
**Trigger:** Push to main branch  
**Environment:** `.env.tier1.production` (PostgreSQL, Railway production)  
**Services:** Production URLs from Railway

```powershell
# Merge dev to main
git checkout main
git pull origin main
git merge dev
git push origin main

# GitHub Actions automatically:
# 1. Reads .env.tier1.production
# 2. Gets secrets from GitHub (PROD_DB_HOST, etc.)
# 3. Deploys to Railway production
# 4. Available at: https://cms.railway.app (live!)

# Verify production is working
```

---

## 🔑 Key Files

| File | Purpose | Read Time |
|------|---------|-----------|
| `DEV_QUICK_START.md` | Get started NOW | 5 min |
| `WORKFLOW_SETUP_GUIDE.md` | Complete guide | 15 min |
| `SETUP_COMPLETE_SUMMARY.md` | What changed | 5 min |
| `SESSION_SUMMARY.md` | Session overview | 5 min |
| `scripts/dev-troubleshoot.ps1` | Auto-diagnose issues | Run it |

---

## 🚨 Common Issues & Fixes

### ❌ "Port 1337 already in use"

```powershell
netstat -ano | findstr :1337
taskkill /PID <number> /F
npm run dev
```

### ❌ "npm run dev fails"

```powershell
npm run install:all
npm run dev
```

### ❌ "Can't find .env.local"

```powershell
cp .env.example .env.local
npm run dev
```

### ❌ "I'm on main branch"

```powershell
git checkout -b feat/my-feature
npm run dev
```

### ❌ "Need Python backend too"

```powershell
npm run dev:full
# Or start separately:
cd src/cofounder_agent
python -m uvicorn main:app --reload
```

---

## ✅ Environment Variables by Branch

| Branch | File | Database | URLs | Used For |
|--------|------|----------|------|----------|
| `feat/*` | `.env.local` | SQLite | `localhost:*` | Local dev |
| `dev` | `.env.staging` | PostgreSQL | `staging-*` | Staging |
| `main` | `.env.tier1.production` | PostgreSQL | `*.railway.app` | Production |

---

## 📱 Service URLs

### Local Development (npm run dev)

- Strapi Admin: <http://localhost:1337/admin>
- Public Site: <http://localhost:3000>
- Oversight Hub: <http://localhost:3001>
- Co-Founder Agent: <http://localhost:8000/docs>

### Staging (After git push origin dev)

- Strapi: <https://staging-cms.railway.app>
- API: <https://staging-api.glad-labs.com>
- Agent: <https://staging-agent.glad-labs.com:8000>

### Production (After git push origin main)

- Strapi: <https://cms.railway.app>
- API: <https://api.glad-labs.com>
- Agent: <https://agent.glad-labs.com:8000>

---

## 🔄 Complete Example Workflow

```powershell
# DAY 1: Start feature development
git checkout -b feat/add-user-auth
npm run dev  # Develop locally
# Make changes...
git add .
git commit -m "feat: add user authentication"
git push origin feat/add-user-auth

# DAY 2: Code review approved, merge to dev
git checkout dev
git pull origin dev
git merge feat/add-user-auth
git push origin dev
# → GitHub Actions deploys to staging
# → Test at https://staging-cms.railway.app

# DAY 3: Staging tests pass, merge to main
git checkout main
git pull origin main
git merge dev
git push origin main
# → GitHub Actions deploys to production
# → Feature now live!
```

---

## 💡 Pro Tips

1. **Always pull before pushing:**
   ```bash
   git pull origin <branch>
   git push origin <branch>
   ```

2. **Use descriptive commit messages:**
   ```bash
   git commit -m "feat: add user authentication"
   git commit -m "fix: resolve Strapi connection issue"
   git commit -m "docs: update workflow guide"
   ```

3. **Check your branch before doing anything:**
   ```bash
   git branch  # See current branch
   git status  # See uncommitted changes
   ```

4. **Test locally before pushing:**
   ```bash
   npm run dev  # Test locally
   npm run build  # Test production build
   npm run test  # Run tests (when available)
   ```

5. **Never commit to main directly:**
   - Always work on `feat/*` branches
   - Push to `dev` when ready for staging
   - Merge `dev` to `main` for production

---

## 🆘 Need Help?

**Quick Diagnostics:**
```powershell
. scripts/dev-troubleshoot.ps1
```

**Detailed Guides:**
- Setup issues: Read `DEV_QUICK_START.md`
- Workflow details: Read `WORKFLOW_SETUP_GUIDE.md`
- What changed: Read `SESSION_SUMMARY.md`

**Git Issues:**
```bash
git log --oneline  # See commit history
git diff  # See uncommitted changes
git status  # See branch and file status
```

---

**Last Updated:** October 23, 2025  
**Status:** ✅ Ready to Use  
**Your Next Command:** `npm run dev` 🚀
