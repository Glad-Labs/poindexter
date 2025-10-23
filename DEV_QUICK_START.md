# 🚀 Quick Start Guide - Fix npm run dev Now

**Problem:** `npm run dev` is failing because it tries to start the Python backend along with the frontend services, and Python is failing.

**Solution:** Updated `package.json` to run only the working services by default.

---

## ✅ What I Fixed

Changed `npm run dev` from:

```json
"dev": "npx npm-run-all --parallel dev:*"
```

To:

```json
"dev": "npx npm-run-all --parallel dev:strapi dev:public dev:oversight"
"dev:full": "npx npm-run-all --parallel dev:*"
```

---

## 🎯 Try This Right Now

### Option 1: Simple (Recommended - Start Here)

```powershell
npm run dev
```

This starts:

- ✅ Strapi CMS (port 1337)
- ✅ Public Site (port 3000)
- ✅ Oversight Hub (port 3001)
- ⏭️ Python backend skipped (can start manually if needed)

### Option 2: One Service Per Terminal (Most Reliable)

```powershell
# Terminal 1
cd cms\strapi-main
npm run develop

# Terminal 2 (wait for Strapi to start, then run)
cd web\public-site
npm run dev

# Terminal 3 (new terminal)
cd web\oversight-hub
npm start
```

### Option 3: Run Troubleshooting Script First

```powershell
. scripts/dev-troubleshoot.ps1
```

This will:

- Check your git branch (should be `feat/*`, not `main`)
- Verify `.env.local` exists
- Check Node.js version
- Verify workspace installations
- Check if ports are available

---

## 🔍 Verify It's Working

Once services start, you should see:

```text
✅ Strapi started at http://localhost:1337
✅ Public Site started at http://localhost:3000
✅ Oversight Hub started at http://localhost:3001
```

Visit these URLs:

- Strapi Admin: <http://localhost:1337/admin>
- Public Site: <http://localhost:3000>
- Oversight Hub: <http://localhost:3001>

---

## 📋 Checklist Before You Start

- [ ] You're on a `feat/*` branch (not `main` or `dev`)
- [ ] `.env.local` exists in the root folder
- [ ] `.env.local` has `NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337`
- [ ] `npm install --workspaces` has been run
- [ ] No other services are running on ports 1337, 3000, 3001

---

## 🚨 Still Having Issues?

### Issue: "npm-run-all: command not found"

```powershell
npm install -g npm-run-all
```

### Issue: Port already in use (e.g., port 1337)

```powershell
# Find what's using port 1337
netstat -ano | findstr :1337

# Kill it (replace PID with the number from above)
taskkill /PID 12345 /F
```

### Issue: Dependencies missing

```powershell
npm run install:all
```

### Issue: Python failing

Just use `npm run dev` (which doesn't include Python now). If you need Python later:

```powershell
cd src\cofounder_agent
python -m uvicorn main:app --reload
```

---

## 📝 What About the Git Workflow?

See `WORKFLOW_SETUP_GUIDE.md` in the root folder for complete workflow documentation!

Quick version:

- `feat/***` branch → `npm run dev` (local) → `.env.local`
- `dev` branch → GitHub Actions → Staging → `.env.staging`
- `main` branch → GitHub Actions → Production → `.env.tier1.production`

---

## ✨ Next Steps

1. ✅ Run `npm run dev` now
2. ✅ Visit <http://localhost:1337/admin> to verify Strapi
3. ✅ Visit <http://localhost:3000> to verify Public Site
4. ✅ Visit <http://localhost:3001> to verify Oversight Hub
5. ✅ Make code changes and see hot-reload working
6. 📖 Read `WORKFLOW_SETUP_GUIDE.md` for the full workflow

---

**Questions?** Check the troubleshooting script output or review `WORKFLOW_SETUP_GUIDE.md`!
