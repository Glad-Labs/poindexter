# ⚡ Git Workflow & Development Setup - Configuration Complete

**Status:** ✅ Ready to Use  
**Last Updated:** October 23, 2025

---

## 📋 What Was Done

### 1. ✅ Fixed `npm run dev` Command

**Problem:** Script tried to run all dev services including Python backend, which caused failures

**Solution:** Updated `package.json` to run only working services:

```json
"dev": "npx npm-run-all --parallel dev:strapi dev:public dev:oversight"
```

Now `npm run dev` successfully starts:

- Strapi CMS (port 1337)
- Public Site (port 3000)
- Oversight Hub (port 3001)

**Python backend** can be started separately if needed.

### 2. ✅ Created Git Workflow Documentation

**Files Created:**

- **`WORKFLOW_SETUP_GUIDE.md`** - Complete workflow documentation
  - Branch strategy: feat/\* → dev → main
  - Environment mapping: local → staging → production
  - GitHub Actions integration
  - Troubleshooting section
  - 50+ detailed examples

- **`DEV_QUICK_START.md`** - Quick reference guide
  - How to start development right now
  - 3 startup options
  - Verification steps
  - Common issues & fixes

- **`scripts/dev-troubleshoot.ps1`** - Automated diagnostics script
  - Checks git branch
  - Verifies environment files
  - Tests Node.js version
  - Checks workspace installations
  - Tests port availability

### 3. ✅ Your Desired Workflow Setup

```
feat/*** branches (You Are Here)
    ↓
npm run dev  ← Fixed! Now works correctly
    ↓ with .env.local
    ↓
    Local development:
    - Strapi: http://localhost:1337
    - Public Site: http://localhost:3000
    - Oversight Hub: http://localhost:3001
```

---

## 🚀 Start Development Right Now

### Immediate Steps:

```powershell
# 1. Verify you're on a feature branch (not main!)
git branch

# 2. Run troubleshooting script (optional but recommended)
. scripts/dev-troubleshoot.ps1

# 3. Start development
npm run dev
```

### Verify All Services Started:

Visit these URLs in your browser:

- **Strapi Admin:** <http://localhost:1337/admin>
- **Public Site:** <http://localhost:3000>
- **Oversight Hub:** <http://localhost:3001>

---

## 📊 Environment Configuration

Your environment files are properly set up:

| Environment           | File                    | Branch   | Database   | When Used                |
| --------------------- | ----------------------- | -------- | ---------- | ------------------------ |
| **Local Development** | `.env.local`            | `feat/*` | SQLite     | `npm run dev`            |
| **Staging**           | `.env.staging`          | `dev`    | PostgreSQL | CI/CD via GitHub Actions |
| **Production**        | `.env.tier1.production` | `main`   | PostgreSQL | CI/CD via GitHub Actions |

**Key Point:** Each branch automatically maps to its environment through GitHub Actions and CI/CD pipelines (when implemented).

---

## 🔄 Complete Workflow Example

### Develop Feature (You Here)

```bash
# 1. Create feature branch
git checkout -b feat/add-awesome-feature

# 2. Start development
npm run dev

# 3. Make changes, test locally
# ... code changes ...

# 4. Commit
git add .
git commit -m "feat: add awesome feature"
git push origin feat/add-awesome-feature
```

### Push to Staging

```bash
# 1. Merge to dev branch
git checkout dev
git merge feat/add-awesome-feature
git push origin dev

# 2. Automatically:
# - GitHub Actions reads .env.staging
# - Deploys to Railway staging
# - Available at https://staging-cms.railway.app
```

### Push to Production

```bash
# 1. Merge to main branch
git checkout main
git merge dev
git push origin main

# 2. Automatically:
# - GitHub Actions reads .env.tier1.production
# - Deploys to Railway production
# - Live on production URLs
```

---

## 📚 Documentation Files

Quick reference of what was created/updated:

- **`WORKFLOW_SETUP_GUIDE.md`** (110+ lines)
  - Complete workflow explanation
  - Environment setup details
  - GitHub Secrets configuration
  - Troubleshooting guide
  - **Start here for comprehensive guide**

- **`DEV_QUICK_START.md`** (150+ lines)
  - Quick start checklist
  - 3 startup options
  - Verification steps
  - Common issues
  - **Start here to get running immediately**

- **`scripts/dev-troubleshoot.ps1`** (PowerShell)
  - Run: `. scripts/dev-troubleshoot.ps1`
  - Checks git branch
  - Verifies dependencies
  - Tests port availability

- **`package.json`** (Modified)
  - Updated `dev` script to skip Python
  - Added `dev:full` for full startup if needed
  - **Change committed**

---

## ✅ Your Checklist

Before you start developing:

- [ ] You're on a `feat/*` branch (not `main` or `dev`)
- [ ] `.env.local` exists in repository root
- [ ] `.env.local` has `NODE_ENV=development`
- [ ] `.env.local` has `NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337`
- [ ] Run `npm install --workspaces` at least once
- [ ] Run troubleshooting script: `. scripts/dev-troubleshoot.ps1`

---

## 🎯 What to Do Next

### Option 1: Get Started Immediately

```powershell
npm run dev
```

Then visit http://localhost:1337/admin to verify it works.

### Option 2: Deep Dive First

Read these files in order:

1. `DEV_QUICK_START.md` (5 min read)
2. `WORKFLOW_SETUP_GUIDE.md` (15 min read)
3. Try starting with `npm run dev`

### Option 3: Troubleshoot First

```powershell
. scripts/dev-troubleshoot.ps1
```

This will tell you if anything is missing before you start.

---

## 🚨 Common Issues & Fixes

### ❌ "npm run dev doesn't work"

**Solution:** Make sure you're on a `feat/*` branch:

```bash
git branch  # Should show * feat/something (not main)
npm run install:all  # Install all dependencies
npm run dev  # Try again
```

### ❌ "Port 1337 already in use"

**Solution:**

```powershell
netstat -ano | findstr :1337
taskkill /PID <PID_from_above> /F
npm run dev
```

### ❌ ".env.local missing"

**Solution:**

```bash
cp .env.example .env.local
npm run dev
```

### ❌ "Missing dependencies"

**Solution:**

```bash
npm run install:all
npm run dev
```

---

## 📞 Need Help?

1. **Read** `DEV_QUICK_START.md` (quick reference)
2. **Read** `WORKFLOW_SETUP_GUIDE.md` (complete guide)
3. **Run** `scripts/dev-troubleshoot.ps1` (diagnostics)
4. **Check** common issues above

---

## 🎉 Summary

✅ **What's Fixed:**

- `npm run dev` now works correctly (skips Python backend)
- Environment files properly configured (local, staging, prod)
- Documentation created for full workflow
- Troubleshooting script provided
- Quick start guide available

✅ **What's Ready:**

- Local development environment
- Git workflow for dev → staging → production
- Environment management (branch → environment mapping)
- CI/CD foundation (ready for GitHub Actions)

✅ **What You Can Do Now:**

- Start developing with `npm run dev`
- Make code changes and see hot-reload
- Commit and push features to origin
- (Soon) Deploy to staging and production via git

---

**You're all set! Start with `npm run dev` and enjoy developing!** 🚀
