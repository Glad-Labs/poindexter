# 🚀 Quick Start Guide - October 24, 2025

**Status:** ✅ Frontend Services Ready | ⚠️ Strapi Temporarily Disabled | ✅ GitHub Secrets Guides Complete

---

## 🔐 NEW: Setting Up GitHub Secrets

If you're setting up **GitHub Secrets organized by component and environment**:

**→ Start here: [`GITHUB_SECRETS_FILE_INDEX.md`](./GITHUB_SECRETS_FILE_INDEX.md)**

Quick links:
- **Quick Setup (5 min):** [`GITHUB_SECRETS_QUICK_SETUP.md`](./GITHUB_SECRETS_QUICK_SETUP.md)
- **Complete Guide:** [`GITHUB_SECRETS_SETUP.md`](./GITHUB_SECRETS_SETUP.md)
- **Workflow Examples:** [`.github/workflows/deploy-*-with-environments.yml`](./.github/workflows/)

---

## Running Your Development Environment

### **Option 1: Frontend Only (QUICK START - RECOMMENDED)**

```bash
npm run dev
```

This starts:

- ✅ **Public Site:** [http://localhost:3000](http://localhost:3000)
- ✅ **Oversight Hub:** [http://localhost:3001](http://localhost:3001)

**Use this for:** Frontend development, testing UI components, API integration testing

---

### **Option 2: Frontend + Python Backend**

```bash
# Terminal 1
npm run dev:frontend

# Terminal 2
npm run dev:cofounder
```

Starts:

- ✅ **Public Site:** [http://localhost:3000](http://localhost:3000)
- ✅ **Oversight Hub:** [http://localhost:3001](http://localhost:3001)
- ✅ **Python Backend:** [http://localhost:8000/docs](http://localhost:8000/docs)

**Use this for:** Testing AI agents, backend API development, full system testing

---

### **Option 3: All Services (When Strapi is Fixed)**

```bash
npm run dev:full
```

**Current Status:** ❌ Strapi build fails (see `STRAPI_SETUP_WORKAROUND.md`)

---

## 📋 Available Commands

```bash
# Frontend only (recommended for now)
npm run dev                    # Oversight Hub + Public Site
npm run dev:frontend           # Same as above

# Individual services
npm run dev:public            # Just public site
npm run dev:oversight         # Just oversight hub
npm run dev:cofounder         # Just Python backend
npm run dev:strapi            # Just Strapi CMS (currently broken)

# Try all (will fail on Strapi, but others will work)
npm run dev:full              # All services (strapi fails, others succeed)

# Production builds
npm run build                 # Build all workspaces
npm run start:all             # Start production servers
```

---

## 🛠️ Troubleshooting

### "Port already in use"

```bash
# Find and kill process on port
# Windows:
netstat -ano | findstr :3000
taskkill /PID <PID> /F

# Or just change PORT
PORT=3001 npm run dev:public
```

### "Module not found"

```bash
# Reinstall dependencies
npm install --workspaces
```

### "Strapi won't start"

See `STRAPI_SETUP_WORKAROUND.md` for detailed solutions.

---

## 🎯 Next Steps

1. **Run:** `npm run dev`
2. **Visit:**
   - [http://localhost:3000](http://localhost:3000) (Public Site)
   - [http://localhost:3001](http://localhost:3001) (Oversight Hub)
3. **Test:** Try navigating the frontends
4. **Develop:** Make changes to files and watch hot reload

---

## 📚 Documentation

- **Setup Issues:** `docs/STRAPI_SETUP_WORKAROUND.md`
- **Full Docs:** `docs/00-README.md`
- **Environment:** `.env.local` (loaded automatically)

---

**Happy coding!** 🎉
