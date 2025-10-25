# 📦 Monorepo Setup & Module Resolution Guide

**Last Updated:** October 25, 2025  
**Status:** ✅ Production Ready  
**Problem Solved:** Strapi v5 startup failures due to module resolution  
**Solution:** Automated setup-dev.ps1 script + configuration defaults

---

## 🎯 Quick Summary

**The Problem:**
GLAD Labs is an **npm workspace monorepo** with interdependent packages. When root `node_modules` becomes corrupted or incomplete, downstream packages (like Strapi) fail because npm's "hoisting" mechanism depends on shared dependencies at the root level.

**The Solution:**

1. Run `.\scripts\setup-dev.ps1` to automate everything
2. Or manually follow the "Manual Setup" section below

**Why This Matters:**
npm workspaces are powerful but complex. Understanding the module resolution chain prevents hours of debugging.

---

## 📚 Table of Contents

- **[What is a Monorepo?](#what-is-a-monorepo)** - Foundational concept
- **[How npm Workspaces Work](#how-npm-workspaces-work)** - The mechanism
- **[The Root Cause of Today's Issue](#the-root-cause-of-todays-issue)** - Why it failed
- **[The Solution](#the-solution)** - How we fixed it
- **[Automated Setup (Recommended)](#automated-setup-recommended)** - Use setup-dev.ps1
- **[Manual Setup](#manual-setup)** - If automation fails
- **[Troubleshooting](#troubleshooting)** - Common issues
- **[Best Practices](#best-practices)** - Prevention

---

## 📦 What is a Monorepo?

A **monorepo** (monolithic repository) is a single Git repository containing multiple related projects/packages that share common dependencies and build configuration.

### GLAD Labs Monorepo Structure

```
c:\Users\mattm\glad-labs-website\                     ← ROOT
├── package.json                                     ← ROOT manifest (workspaces defined here)
├── node_modules/                                    ← ROOT node_modules (shared deps)
│   ├── @strapi/
│   ├── @next/
│   ├── react/
│   └── ... (shared by all workspaces)
│
├── cms/strapi-main/                                 ← WORKSPACE 1
│   ├── package.json                                 ← Workspace manifest
│   ├── node_modules/                                ← Workspace-specific deps
│   └── ... (Strapi code)
│
├── src/cofounder_agent/                             ← WORKSPACE 2 (Python)
│   ├── pyproject.toml
│   └── ... (Python FastAPI code)
│
├── web/oversight-hub/                               ← WORKSPACE 3
│   ├── package.json                                 ← Workspace manifest
│   ├── node_modules/                                ← Workspace-specific deps
│   └── ... (React code)
│
├── web/public-site/                                 ← WORKSPACE 4
│   ├── package.json                                 ← Workspace manifest
│   ├── node_modules/                                ← Workspace-specific deps
│   └── ... (Next.js code)
│
└── scripts/                                         ← Shared scripts
```

### Why Use a Monorepo?

| Benefit                   | Example                                             |
| ------------------------- | --------------------------------------------------- |
| **Shared code**           | Common utilities used by Strapi + FastAPI + React   |
| **Synchronized versions** | All packages update @strapi/core together           |
| **Single CI/CD**          | One GitHub Actions workflow tests everything        |
| **Atomic commits**        | Related changes in frontend + backend in one commit |
| **Shared config**         | ESLint, Prettier, TypeScript configs apply to all   |

---

## 🔄 How npm Workspaces Work

### The Hoisting Mechanism

npm uses "hoisting" to reduce disk space by installing shared dependencies at the ROOT level instead of duplicating them in each workspace.

**Example:**

```javascript
// Without hoisting (OLD - lots of duplication):
root/
  node_modules/react/              ← Duplicate #1 (300MB)
cms/strapi-main/
  node_modules/react/              ← Duplicate #2 (300MB)
web/oversight-hub/
  node_modules/react/              ← Duplicate #3 (300MB)
// Total: 900MB just for React!

// With hoisting (NEW - shared):
root/
  node_modules/react/              ← Shared (300MB)
  node_modules/@strapi/strapi/     ← Shared
  node_modules/next/               ← Shared
cms/strapi-main/
  node_modules/                    ← Only workspace-specific packages
web/oversight-hub/
  node_modules/                    ← Only workspace-specific packages
// Total: 300MB + smaller workspace node_modules = much less disk space
```

### Module Resolution Chain

When code tries to load a module, Node.js searches in this order:

```
1. Local directory:           ./node_modules/module-name
2. Parent directories:        ../node_modules/module-name
                              ../../node_modules/module-name
                              ../../../node_modules/module-name  (← ROOT)
3. Global:                    /usr/local/lib/node_modules/module-name
4. Builtin:                   node:module-name
```

**Example Resolution:**

```javascript
// Code in: cms/strapi-main/src/index.js
require('@strapi/strapi')

// Node.js searches:
1. cms/strapi-main/node_modules/@strapi/strapi/     ← Not here (workspace deps)
2. cms/strapi-main/../node_modules/@strapi/strapi/  ← Not here
3. cms/../node_modules/@strapi/strapi/              ← Not here
4. ../../node_modules/@strapi/strapi/               ← ✅ FOUND HERE (root)
```

### The Critical Dependency

When Strapi's internal code tries to load `@strapi/strapi`, it relies on this hoisting mechanism:

```javascript
// Inside @strapi/core (at root):
const strapi = require('@strapi/strapi');

// This ONLY works if @strapi/strapi is at ROOT node_modules
// If root node_modules is corrupted/incomplete, this fails!
```

---

## 🔴 The Root Cause of Today's Issue

### What Happened

1. **Root `node_modules` became corrupted** (from previous session)
   - `@strapi/strapi` was missing
   - Other @strapi packages partially installed
   - Module hoisting broken

2. **Strapi tried to start**
   - Internal code required `@strapi/strapi`
   - Searched in workspace node_modules → Not found
   - Searched in root node_modules → NOT FOUND ❌
   - Module resolution chain failed

3. **Error cascade**
   ```
   Error: Cannot find module '@strapi/strapi/package.json'
   at Function.Module._resolveFilename
   ```

### Why Standard npm install Didn't Fix It

```bash
# You might try:
npm install  # At workspace level

# But this only installs workspace-specific dependencies!
# It doesn't re-add @strapi/strapi to ROOT node_modules
# because @strapi/strapi is listed in root package.json

# So the module chain stayed broken.
```

### Why This Is a Monorepo Problem

In a simple project with one package.json:

```bash
npm install  # Installs everything, done!
```

In a monorepo with multiple package.jsons:

```bash
npm install                    # At root - installs shared deps
npm install --workspaces      # In all workspaces - installs workspace-specific

# If root install is incomplete/corrupt, entire system breaks!
```

---

## ✅ The Solution

### Step-by-Step Fix (What We Applied)

**Step 1: Clean root node_modules**

```bash
rm -r node_modules package-lock.json  # Remove corrupted data
```

**Step 2: Fresh root install** (CRITICAL)

```bash
npm install  # Reinstall root node_modules with @strapi/strapi
```

**Step 3: Verify module chain**

```bash
ls -la node_modules/@strapi/strapi/  # Verify @strapi/strapi exists
```

**Step 4: Install workspace deps**

```bash
npm install --workspaces  # Install workspace-specific packages
```

**Step 5: Add SQLite drivers**

```bash
# At cms/strapi-main/:
npm install sqlite3 better-sqlite3  # SQLite support
```

**Step 6: Verify everything**

```bash
npm run dev:strapi  # Should start without module errors
```

### Why This Worked

- ✅ Root node_modules restored with `@strapi/strapi`
- ✅ Module resolution chain fixed
- ✅ All dependencies hoisted properly
- ✅ Strapi can now find itself

---

## 🚀 Automated Setup (Recommended)

### One-Command Setup

```powershell
.\scripts\setup-dev.ps1
```

This script automates everything:

1. ✅ Validates prerequisites (Node.js, npm, git)
2. ✅ Optionally cleans node_modules (use `-Clean` flag)
3. ✅ Creates .env from .env.example
4. ✅ **Installs root dependencies** (the breakthrough fix)
5. ✅ **Installs @strapi/strapi** at root level (explicit fix)
6. ✅ Installs workspace dependencies
7. ✅ Installs SQLite drivers
8. ✅ Verifies module resolution chain
9. ✅ Reports success or errors

### Usage Examples

```powershell
# Standard setup (recommended first time)
.\scripts\setup-dev.ps1

# Clean setup (remove old node_modules and reinstall)
.\scripts\setup-dev.ps1 -Clean

# Skip .env creation (use existing .env)
.\scripts\setup-dev.ps1 -SkipEnv

# Verbose output for debugging
.\scripts\setup-dev.ps1 -Verbose

# Full reset
.\scripts\setup-dev.ps1 -Clean -Verbose
```

### What the Script Does

**Phase 1: Validation**

```
✅ Checking prerequisites
  - Node.js v20.11.1
  - npm v10+
  - Git (optional)
```

**Phase 2: Cleanup (if -Clean)**

```
✅ Removing node_modules
✅ Removing package-lock.json
✅ Fresh install coming
```

**Phase 3: Environment Setup**

```
✅ Creating .env from .env.example
  (If .env already exists, skips)
```

**Phase 4: Root Install** (CRITICAL)

```
✅ npm install at project root
  This installs shared dependencies
```

**Phase 5: @strapi/strapi Explicit Fix**

```
✅ npm install @strapi/strapi@^5.18.1 --save-dev
  This ensures @strapi/strapi is in root node_modules
```

**Phase 6: Workspace Installation**

```
✅ npm install --workspaces
  Install all workspace-specific dependencies
```

**Phase 7: Strapi-Specific Setup**

```
✅ Installing sqlite3, better-sqlite3
  SQLite support for local development
```

**Phase 8: Verification**

```
✅ @strapi/strapi in root node_modules
✅ Strapi workspace node_modules exists
✅ Database config found
✅ .env configuration exists

📋 All checks passed! Ready to start.
```

---

## 🔧 Manual Setup

If the automated script doesn't work, follow these steps manually:

### 1. Navigate to Project Root

```powershell
cd c:\Users\mattm\glad-labs-website
# Verify you're in the right place:
ls package.json  # Should show the file exists
```

### 2. Clean (Optional but Recommended)

```powershell
# Remove corrupted node_modules
rm -r node_modules -Force
rm package-lock.json -Force

# Verify they're gone
ls node_modules  # Should error "does not exist"
```

### 3. Install Root Dependencies

```powershell
# Critical: Install at PROJECT ROOT, not in subdirectories
npm install

# Verify @strapi/strapi is now in root node_modules
ls node_modules/@strapi/strapi/package.json
# Should show:
#   Mode                 LastWriteTime         Length Name
#   ----                 -------------         ------ ----
#   -a---         10/25/2025  2:20 AM           8434 package.json
```

### 4. Explicitly Install @strapi/strapi

```powershell
# Even though root npm install should have done this,
# explicitly ensure it's there:
npm install @strapi/strapi@^5.18.1 --save-dev

# Verify it worked
ls node_modules/@strapi/strapi/
```

### 5. Install Workspace Dependencies

```powershell
# Install specific workspaces:
npm install --workspace=cms/strapi-main
npm install --workspace=web/oversight-hub
npm install --workspace=web/public-site

# Or all at once:
npm install --workspaces
```

### 6. Install SQLite Support

```powershell
cd cms/strapi-main
npm install sqlite3 better-sqlite3
cd ../..  # Back to root
```

### 7. Create .env File

```powershell
# Copy example to .env
cp .env.example .env

# Edit .env with your values (or use defaults for development)
# Important defaults are already in .env.example now!
```

### 8. Verify Module Chain

```powershell
# Check that critical modules can be resolved:
node -e "require('@strapi/strapi')" 2>&1 | Select-Object -First 10
# Should NOT error

# Should output nothing or just load (if no error, you're good!)
```

### 9. Test Strapi Startup

```powershell
npm run dev:strapi

# Should see:
#   ✔ Strapi started successfully
#   Admin at: http://127.0.0.1:1337/admin
```

---

## 🐛 Troubleshooting

### Issue 1: "Cannot find module '@strapi/strapi'"

**Cause:** @strapi/strapi not in root node_modules

**Solution:**

```powershell
# At PROJECT ROOT:
npm install @strapi/strapi@^5.18.1 --save-dev

# Verify:
ls node_modules/@strapi/strapi/package.json
```

### Issue 2: "Error: Cannot find module 'better-sqlite3'"

**Cause:** SQLite drivers not installed

**Solution:**

```powershell
cd cms/strapi-main
npm install sqlite3 better-sqlite3
cd ../..
```

### Issue 3: "npm install hangs or times out"

**Cause:** Network issue or corrupted cache

**Solution:**

```powershell
# Clear npm cache
npm cache clean --force

# Retry install
npm install
```

### Issue 4: "Port 1337 already in use"

**Cause:** Strapi already running

**Solution:**

```powershell
# Find process using port 1337
netstat -ano | findstr :1337
# Output: TCP    127.0.0.1:1337   LISTENING   12345

# Kill the process
taskkill /PID 12345 /F

# Try again
npm run dev:strapi
```

### Issue 5: "node_modules massive (5GB+)"

**Cause:** npm not hoisting properly, duplication

**Solution:**

```powershell
# Clean everything
rm -r node_modules -Force
rm package-lock.json -Force

# Reinstall with --force
npm install --force

# Check size
(gci node_modules -Recurse | Measure-Object -Sum -Property Length).Sum / 1GB
# Should be 1-2GB, not 5GB+
```

---

## 🎯 Best Practices

### DO ✅

- ✅ **Run setup-dev.ps1 first** if anything breaks
- ✅ **Keep .env.example updated** with new variables
- ✅ **Document new packages** in their workspace README
- ✅ **Test module imports** before committing
- ✅ **Use npm workspaces** instead of manually managing dependencies
- ✅ **Keep .env out of git** - add to .gitignore
- ✅ **Review package.json changes** in PRs carefully

### DON'T ❌

- ❌ **Don't manually edit node_modules** - it gets overwritten
- ❌ **Don't commit node_modules** - huge file size, always regenerate
- ❌ **Don't mix package managers** - use npm everywhere (not yarn + npm)
- ❌ **Don't ignore package-lock.json** - commit it for reproducibility
- ❌ **Don't delete .env without backup** - keep your local config
- ❌ **Don't assume hoisting works** - test your imports
- ❌ **Don't skip npm install --workspaces** - each workspace has deps

### Prevention

**1. Use setup-dev.ps1 Every Time You Clone**

```powershell
git clone <repo>
cd glad-labs-website
.\scripts\setup-dev.ps1  # Do this first!
```

**2. Keep Root package.json in Sync**

```json
{
  "workspaces": ["cms/strapi-main", "web/oversight-hub", "web/public-site"]
}
```

**3. Document Module Sources**

```javascript
// BAD - unclear where this comes from
const strapi = require('strapi');

// GOOD - clearly from root node_modules (hoisted)
const strapi = require('@strapi/strapi');

// GOOD - workspace-specific
const config = require('../config/database');
```

**4. Test After Adding Dependencies**

```bash
# After: npm install new-package
# Always verify imports work:
node -e "require('new-package')"
```

---

## 📋 Checklist: New Team Member Setup

Use this when onboarding a new developer:

- [ ] Clone repository: `git clone <repo>`
- [ ] Navigate to root: `cd glad-labs-website`
- [ ] Run setup: `.\scripts\setup-dev.ps1`
- [ ] Verify startup: `npm run dev:strapi`
- [ ] Access admin: `http://localhost:1337/admin`
- [ ] Create admin account
- [ ] Generate API token
- [ ] Read: `docs/MONOREPO_SETUP.md` (this file)
- [ ] Read: `docs/01-SETUP_AND_OVERVIEW.md`
- [ ] Ask questions in team Slack/Discord

---

## 🔗 Related Documentation

- **[Setup Guide](./01-SETUP_AND_OVERVIEW.md)** - Complete setup instructions
- **[Architecture](./02-ARCHITECTURE_AND_DESIGN.md)** - System overview
- **[Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)** - Git workflow

---

## ✅ Summary

**The Monorepo Problem:**
npm workspaces use "hoisting" to share dependencies. If root `node_modules` is corrupted, everything breaks.

**The Solution:**

```powershell
.\scripts\setup-dev.ps1  # Automated
# OR manually:
npm install              # At root
npm install @strapi/strapi@^5.18.1 --save-dev
npm install --workspaces
```

**Prevention:**
Always run setup-dev.ps1 after cloning, and keep root package.json in sync with actual workspaces.

**Key Insight:**
When debugging npm workspace issues, always check root `node_modules` first—that's where the module resolution chain starts.

---

**Questions?** See the troubleshooting section above, or refer to npm workspace documentation: https://docs.npmjs.com/cli/v10/using-npm/workspaces

**Created:** October 25, 2025  
**Updated:** October 25, 2025
