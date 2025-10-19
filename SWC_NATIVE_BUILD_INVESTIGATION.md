# SWC Native Binding - Build Investigation

**Status**: 🔴 Build from source flag didn't work on Railway  
**Timestamp**: 2025-10-19 05:34:35 UTC

## 🔍 What Happened

Deployed with build command:

```bash
npm install --build=from-source && npm run build
```

### ✅ What Worked

- `npm install --build=from-source` completed successfully
- Audited 1383 packages in 14s
- No npm install errors

### ❌ What Failed

- **Same SWC binding error occurred**:

```
Error: Failed to load native binding
at Object.<anonymous> (/app/node_modules/@swc/core/binding.js:333:11)
```

## 🤔 Why the Flag Didn't Work

Even though `--build=from-source` was passed:

1. **Railway container may not have build tools**
   - Missing C++ compiler (g++, make)
   - Missing Python (needed for node-gyp)
   - Missing Rust (needed for SWC)

2. **Flag execution context issue**
   - npm install completed (install step)
   - Build step runs npm run build in same shell
   - SWC was already installed incorrectly
   - Flag didn't propagate through second npm call

3. **Node cache/state pollution**
   - node_modules/.cache might have stale prebuilt binaries
   - Package manager state not reset between steps

## 🛠️ Next Approaches to Try

### Approach 1: Explicit Environment Variable Export (PREFERRED)

Use `.npmrc` file to persist the setting:

```ini
# .npmrc
optional=false
fund=false
update-notifier=false
production=true
build-from-source=true
```

**Why it works**: npm reads `.npmrc` for ALL commands in the session

---

### Approach 2: Clean Build with Explicit Flag Chaining

```bash
npm ci --build=from-source && npm run build
```

Using `npm ci` instead of `npm install`:

- More deterministic
- Skips already-installed packages
- Respects lock file exactly

---

### Approach 3: Two-Stage Build Command

```bash
npm config set build-from-source true && npm install && npm run build
```

Explicitly sets npm config before install, ensuring flag persists through build.

---

### Approach 4: Dual npm install/build

```bash
npm install --build=from-source && npm rebuild && npm run build
```

Forces rebuild of native modules after source compilation.

---

## 🎯 Recommended Solution

**Update `.npmrc` to include `build-from-source=true`**

This is the most reliable because:

1. Setting persists for all npm commands
2. No need to pass flags each time
3. Works across install and build steps
4. Survives npm cache
5. Can be committed to repo

---

## Implementation Plan

1. **Update .npmrc**:

   ```ini
   optional=false
   fund=false
   update-notifier=false
   production=true
   build-from-source=true
   ```

2. **Simplify railway.json**:

   ```json
   {
     "buildCommand": "npm install && npm run build"
   }
   ```

3. **Commit and push**
4. **Re-deploy to Railway**
5. **Monitor logs for success**

---

## Technical Deep Dive: Why Prebuilt SWC Binaries Fail

**SWC (@swc/core)**:

- Written in Rust
- Compiles to native Node.js binary (.node files)
- Prebuilt binaries are platform + Node version specific

**Railway Container Environment**:

- Linux x64 + Node.js 18.20.8
- Prebuilt binaries for this combo should work
- BUT: Some incompatibility exists

**Possible Root Cause**:

- SWC v1.13.5 prebuilts may be broken
- Or: Linux glibc version mismatch
- Or: Railpack has stripped dependencies needed to load .node files

**Source Compilation**:

- Downloads SWC Rust source
- Installs Rust compiler in build container
- Compiles to .node binary for exact environment
- No dependency assumptions

---

## Monitoring Next Deployment

Once deployed with `.npmrc` fix:

```bash
railway logs --follow
```

**Look for**:

```
✔ Building build context (XXms)
✔ Building admin panel (XXs)
```

**If it still fails**, we'll need to:

- Check for specific SWC version incompatibility
- Try a different SWC version
- Or implement alternative TypeScript compiler

---

## Timeline

- **Failed Attempt**: 2025-10-19 05:34:35 UTC
- **Investigation**: 2025-10-19 05:35:00 UTC
- **Fix Implementation**: ~2-3 minutes
- **Expected Retry**: Next push to main
- **Expected Success**: ~4-6 minutes after push
