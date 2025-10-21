# 📦 Package Manager Strategy - Hybrid npm + yarn

**Date**: October 21, 2025  
**Status**: Implemented  
**Purpose**: Use npm locally, yarn for Strapi on Railway

---

## 🎯 Strategy Overview

The GLAD Labs monorepo uses a **hybrid package manager approach**:

| Component                 | Local Dev       | Railway Deploy | Reason                                    |
| ------------------------- | --------------- | -------------- | ----------------------------------------- |
| **Root + Web workspaces** | npm             | npm            | Consistent, modern, workspace support     |
| **Strapi CMS**            | npm (inherited) | yarn           | Strapi proven stable with yarn on Railway |

---

## 📋 Current Configuration

### Root `package.json`

```json
{
  "packageManager": "npm@9.0.0",
  "engines": {
    "node": ">=18.0.0",
    "npm": ">=9.0.0"
  },
  "workspaces": ["web/public-site", "web/oversight-hub", "cms/strapi-main"]
}
```

**Effect**: Root and web workspaces use **npm** everywhere

### Strapi `cms/strapi-main/package.json`

```json
{
  "packageManager": "yarn@1.22.22",
  "engines": {
    "node": ">=18.0.0 <=22.x.x",
    "yarn": ">=1.22.0"
  }
}
```

**Effect**: Strapi uses **yarn** on Railway, npm locally (inherited from root)

---

## 🚀 How It Works

### Local Development

```bash
# All use npm (inherited from root packageManager)
npm run dev                           # Runs all services with npm
npm install                           # Installs all dependencies with npm
npm run dev:strapi                    # Strapi uses npm locally
```

**Lockfile**: `package-lock.json` (root and all workspaces)

### Railway Deployment

#### For Web Applications (Next.js Public Site + Oversight Hub)

1. **Detection**: Railway sees `"packageManager": "npm@9.0.0"` in root
2. **Action**: Uses npm
3. **Lockfile**: Uses `package-lock.json`
4. **Result**: ✅ Deploy succeeds with npm

#### For Strapi CMS

1. **Detection**: Railway sees `"packageManager": "yarn@1.22.22"` in `cms/strapi-main/package.json`
2. **Action**: Uses yarn
3. **Lockfile**: Uses `cms/strapi-main/yarn.lock`
4. **Result**: ✅ Deploy succeeds with yarn

---

## 🔧 Lockfile Management

### What You Have

```
glad-labs-website/
├── package-lock.json ................. Root + workspaces (npm)
├── web/public-site/
│   └── (inherits npm)
├── web/oversight-hub/
│   └── (inherits npm)
└── cms/strapi-main/
    └── yarn.lock ..................... Strapi only (yarn)
```

### Why Two Lockfiles?

- **package-lock.json**: Root and web workspaces (npm)
- **yarn.lock**: Strapi workspace (yarn)
  - Ensures Strapi dependencies are locked for Railway
  - Prevents "lockfile needs to be updated" errors
  - Each package manager has its own lockfile format

---

## 📝 Workflow

### Adding Dependencies

**To root or web workspaces** (use npm):

```bash
npm install express
# Updates package-lock.json
```

**To Strapi** (npm will work locally, but yarn is primary):

```bash
# Option 1: npm (works locally, uses root's npm)
npm install lodash --workspace=cms/strapi-main

# Option 2: yarn (recommended, updates yarn.lock for Railway)
cd cms/strapi-main
yarn add lodash
```

### Updating Dependencies

```bash
# Root + web: npm
npm update

# Strapi: yarn
cd cms/strapi-main
yarn upgrade
```

---

## ✅ Verification

### Check Configuration

```bash
# Root should be npm
node -e "console.log(require('./package.json').packageManager)"
# Output: npm@9.0.0

# Strapi should be yarn
node -e "console.log(require('./cms/strapi-main/package.json').packageManager)"
# Output: yarn@1.22.22
```

### Check Lockfiles

```bash
# Both should exist
ls package-lock.json                    # ✅ npm lockfile
ls cms/strapi-main/yarn.lock           # ✅ yarn lockfile
```

---

## 🚀 Deployment Checklist

Before pushing to Railway:

- ✅ Run locally with npm: `npm run dev`
- ✅ Test Strapi specifically: `npm run dev:strapi`
- ✅ Verify `package-lock.json` exists (root)
- ✅ Verify `yarn.lock` exists (cms/strapi-main)
- ✅ No `yarn.lock` in root
- ✅ `cms/strapi-main/package.json` has `"packageManager": "yarn@1.22.22"`

---

## 🐛 Troubleshooting

### Issue: "Your lockfile needs to be updated, but yarn was run with --frozen-lockfile"

**Cause**: Railway tried to use yarn but lockfile format was wrong

**Fix**: Ensure `cms/strapi-main/yarn.lock` exists and is committed

```bash
cd cms/strapi-main
yarn install  # Regenerates yarn.lock if needed
git add yarn.lock
```

### Issue: Local npm install conflicts with Strapi

**Cause**: Running `npm install` in root tries to use npm for Strapi

**Fix**: This is expected. Locally you can use npm for everything, but Railway will respect the `packageManager` field for Strapi specifically.

### Issue: Dependencies different between local npm and Railway yarn

**Cause**: npm and yarn resolve dependencies differently sometimes

**Fix**: Keep `yarn.lock` synced with `package.json` by occasionally running:

```bash
cd cms/strapi-main
yarn install  # Updates yarn.lock
```

---

## 📚 References

- **Node.js Package Manager Field**: https://nodejs.org/en/docs/guides/package-manager-selection/
- **npm Workspaces**: https://docs.npmjs.com/cli/v9/using-npm/workspaces
- **Strapi Deployment**: https://docs.strapi.io/cloud/getting-started

---

## 🎯 Summary

| Task                  | Command                                | Notes                         |
| --------------------- | -------------------------------------- | ----------------------------- |
| **Local dev**         | `npm run dev`                          | All services use npm          |
| **Local Strapi only** | `npm run dev:strapi`                   | Uses npm locally (inherited)  |
| **Add to root**       | `npm install <pkg>`                    | Updates package-lock.json     |
| **Add to Strapi**     | `cd cms/strapi-main && yarn add <pkg>` | Updates yarn.lock for Railway |
| **Update root**       | `npm update`                           | Updates package-lock.json     |
| **Update Strapi**     | `cd cms/strapi-main && yarn upgrade`   | Updates yarn.lock             |

---

**Status**: ✅ Production Ready

This hybrid approach ensures:

- ✅ Consistent local development with npm
- ✅ Proven Strapi stability with yarn on Railway
- ✅ Proper lockfile management for reproducible builds
- ✅ Clear configuration for build systems like Railpack
