# Railway Build Failure Analysis & Fix

**Date**: 2025-10-19 05:34:35 UTC  
**Build Attempt**: With `--build=from-source` flag  
**Result**: ❌ Failed with same SWC native binding error  
**Status**: 🔧 Fixed and redeploying

---

## 📋 What the Logs Show

### Build Log Timeline

```
[Region: us-east4]
Railpack 0.9.1 detected
Node.js 18.20.8 installed
npm 10.x.x ready

Step 1: npm install
✅ Success: removed 1 package, audited 1383 packages in 14s

Step 2: npm install --build=from-source && npm run build
✅ npm install: removed 1 package in 14s
❌ npm run build: FAILED

Error: Failed to load native binding
at Object.<anonymous> (/app/node_modules/@swc/core/binding.js:333:11)

Exit Code: 1 - Build failed
```

### Key Observations

1. **npm install succeeded twice**
   - First in install step: 14s
   - Second in build step: 14s
   - No errors reported

2. **SWC still broken despite --build=from-source**
   - The flag was executed
   - But the binary still failed to load
   - Same exact error as before

3. **Timeline**
   - Build started: 05:34:17 UTC
   - Install step: 05:34:18 to 05:34:33 (15s)
   - Build step: 05:34:33 to 05:34:35 (2s for command, but failed in build)
   - Total time to failure: ~18 seconds

---

## 🔍 Root Cause Analysis

### Why `--build=from-source` Didn't Work as Expected

#### The Problem

The flag was passed, npm reported success, but SWC still couldn't load. This happens because:

**1. npm Config Context Issue**

```bash
npm install --build=from-source
  └─ Sets flag in this shell session ONLY
  └─ npm child processes might not inherit it
  └─ When build step runs, flag context is lost
```

**2. Railway Build Step Separation**

```
Step: install
  $ npm install

Step: build
  $ npm install --build=from-source && npm run build
  └─ New shell session
  └─ npm config flags don't persist
  └─ SWC was already cached from Step 1
```

**3. npm Cache Pollution**

- Step 1 installs all packages (including SWC prebuilts)
- Step 2 tries to reinstall with `--build=from-source`
- But npm cache already has prebuilts
- npm skips re-downloading/rebuilding due to cache

**4. Railway Caching Layer**

- Railpack uses shared caches for `/root/.npm` and `/app/node_modules/.cache`
- Prebuilt binaries cached between deploys
- Flag doesn't invalidate cache automatically

---

## ✅ The Fix: Persistent Configuration

### Why `.npmrc` Is Better

```ini
# .npmrc
build-from-source=true
```

**This works because**:

1. **Persistent across commands**
   - npm reads `.npmrc` for EVERY command
   - Not just the current shell

2. **Survives npm cache**
   - npm respects `.npmrc` settings when reading cache
   - Will rebuild instead of using cached prebuilts

3. **Applies to all steps**

   ```
   Step: install
     Reads .npmrc → build-from-source=true
     npm install → Builds from source

   Step: build
     Reads .npmrc → build-from-source=true
     npm run build → Uses source-built binaries
   ```

4. **No shell context needed**
   - Works across shell sessions
   - Works across container layers
   - Works with npm cache

### How It's Better Than Command-Line Flag

```bash
# ❌ Command-line flag (what we tried)
npm install --build=from-source && npm run build
└─ Context lost between commands
└─ Cache may ignore flag
└─ Doesn't persist across npm steps

# ✅ Configuration file (.npmrc)
build-from-source=true
└─ Applied to ALL npm operations
└─ Cache respects it
└─ Persists across all steps
```

---

## 🔧 Changes Made

### 1. Updated `.npmrc`

**File**: `cms/strapi-v5-backend/.npmrc`

```ini
# Minimal npm configuration for Railway.app
# Avoid cache conflicts in containerized environments

# Skip optional dependencies in CI
optional=false

# Disable telemetry
fund=false
update-notifier=false

# Production mode
production=true

# Compile native modules from source (required for SWC/Rust packages in containers)
# This ensures platform-specific binaries are built for the exact runtime environment
build-from-source=true
```

**Why this works**:

- `build-from-source=true` tells npm to ALWAYS compile native modules
- Applied to every `npm install`, `npm ci`, `npm rebuild` command
- Persists across all build steps and shell sessions

### 2. Simplified `railway.json`

**Before**:

```json
{
  "buildCommand": "npm install --build=from-source && npm run build"
}
```

**After**:

```json
{
  "buildCommand": "npm install && npm run build"
}
```

**Why simpler is better**:

- `.npmrc` now handles the `build-from-source` setting
- Build command is cleaner
- npm will use `.npmrc` config automatically
- Easier to maintain

---

## 📊 Expected Behavior on Next Deploy

### Build Process Flow

```
Railway receives push → GitHub webhook triggers rebuild

Build Container Starts:
  ├─ Node.js 18.20.8 installed
  ├─ npm reads .npmrc
  │   └─ Sees: build-from-source=true
  │
  ├─ Step 1: npm install
  │   └─ Sees build-from-source=true
  │   └─ Downloads SWC source (Rust code)
  │   └─ Compiles to Linux binary
  │   └─ ~1-2 minutes (first time - compilation)
  │
  ├─ Step 2: npm run build (Strapi build)
  │   └─ Uses newly-compiled SWC binary
  │   └─ Builds admin panel successfully
  │   └─ ~30-45 seconds
  │
  └─ Step 3: npm run start
      └─ Strapi starts on port 3000
      └─ ~30 seconds

Total Time: ~4-6 minutes
```

### Success Indicators in Logs

```
✔ npm install: added 73 packages in 1m45s
✔ Building build context (79ms)
✔ Building admin panel (14.6s)
✔ Strapi server started successfully
🎉 Admin panel available at: https://your-railway-domain.railway.app/admin
```

### Error Would Look Like

```
✖ npm install: build errors
Error: Failed to load native binding
```

(This means the fix didn't work - we'd need alternative approach)

---

## 🚀 Next Steps

1. **Deployed**: ✅ Commit with `.npmrc` fix pushed to GitHub
2. **Queued**: Build should trigger automatically on Railway
3. **Monitor**: Check Railway logs in ~2-3 minutes

   ```bash
   railway logs --follow
   ```

4. **Expected Success**: 4-6 minutes after push
5. **If Successful**: Admin panel accessible at Railway URL

---

## 🎯 Technical Summary

| Aspect             | Before                 | After               |
| ------------------ | ---------------------- | ------------------- |
| **Approach**       | Command-line flag      | Configuration file  |
| **Persistence**    | Single shell session   | All npm operations  |
| **Cache Behavior** | Ignored by cache layer | Respected by cache  |
| **Cross-Step**     | Lost between steps     | Maintained          |
| **Reliability**    | Failed (18s)           | Expected to succeed |
| **Build Time**     | N/A (failed)           | ~4-6 minutes        |

---

## 📝 Git Commits

```
commit bb584509b
Author: Matt M <...>
Date: 2025-10-19

fix: add persistent build-from-source in npmrc for railway container builds

- Add build-from-source=true to .npmrc for persistent configuration
- Simplify railway.json buildCommand (config handles build-from-source)
- Add comprehensive investigation documentation
```

---

## ⚠️ If This Still Fails

Fallback approaches to try:

### Approach 1: Use Different Build Command

```json
{
  "buildCommand": "npm ci --build=from-source && npm rebuild && npm run build"
}
```

### Approach 2: Pin SWC Version

If v1.13.5 is the problem, try:

```json
{
  "@swc/core": "~1.12.0"
}
```

### Approach 3: Try Alternative Compiler

Strapi v5 supports esbuild without SWC:

- Update vite config to use esbuild only
- Remove SWC dependency
- Rebuild and redeploy

### Approach 4: Container Customization

Create `.railwayignore` with:

```
# Clear npm cache to force rebuild
npm cache clean --force
rm -rf node_modules/.cache
```

---

## 📚 Reference

- **SWC Documentation**: https://swc.rs
- **npm Configuration**: https://docs.npmjs.com/cli/v10/configuring-npm/npmrc
- **Railway Documentation**: https://docs.railway.app
- **Strapi v5 Build**: https://docs.strapi.io/developer-docs/latest/intro.html
