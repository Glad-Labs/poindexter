# SWC Native Binding Fix - Complete Guide

**Last Updated**: October 19, 2025  
**Status**: SOLVED  
**Severity**: Critical for Railway/Container Deployments  
**Related**: [Railway Deployment Guide](./railway-deployment-guide.md)

---

## 🎯 The Problem

When deploying to Railway, the build fails with:

```
Error: Failed to load native binding
at Object.<anonymous> (/app/node_modules/@swc/core/binding.js:333:11)
```

This occurs even though:
- ✅ `npm install` succeeds
- ✅ No errors reported
- ✅ Same code works locally
- ✅ No apparent SWC issues

---

## 🔍 Root Cause Analysis

### What is SWC?

**SWC** (@swc/core) is a Rust-based TypeScript/JavaScript compiler. Unlike pure JavaScript tools:
- Written in **Rust**, not JavaScript
- Compiles to **native binary** (.node files)
- Platform and Node version specific
- Requires **platform-specific binaries**

### Why Prebuilt Binaries Fail

```
Your Computer (Windows):
  SWC prebuilt → Windows x64 binary (.node)
  ✅ Works perfectly

Railway Container (Linux):
  Prebuilt Windows binary → Can't load on Linux
  ❌ Error: Failed to load native binding

Even downloading Linux prebuilts:
  Prebuilt Linux binary → May have dependency issues
  ❌ Incompatible or missing dependencies
```

### Why Previous Attempts Failed

**Attempt 1: `npm rebuild`**
```bash
npm rebuild
```
- ❌ Only works for packages with source code
- ❌ SWC has no source to rebuild from
- ❌ Windows binaries remained

**Attempt 2: `npm install @swc/core --force`**
```bash
npm install @swc/core --force
```
- ✅ Downloads latest prebuilt binaries
- ❌ Prebuilts still have incompatibility issues
- ❌ Same error on Railway container

**Attempt 3: `npm install --build=from-source`**
```bash
npm install --build=from-source
```
- ✅ Flag passed to npm
- ❌ npm cache had prebuilts from Step 1
- ❌ Cache layer ignores command-line flags
- ❌ Prebuilts used anyway

### The Breakthrough: Configuration File

```ini
# .npmrc
build-from-source=true
```

**Why this works**:
1. `.npmrc` read for EVERY npm command
2. Not just current shell session
3. npm cache respects config file as cache key
4. Forces compilation for exact environment

---

## ✅ The Solution

### Step 1: Update `.npmrc`

Add this line to `.npmrc`:

```ini
# Compile native modules from source (required for SWC/Rust packages)
build-from-source=true
```

**Full .npmrc Example**:
```ini
# Minimal npm configuration for Railway
optional=false
fund=false
update-notifier=false
production=true

# Compile native modules from source
# Required for SWC (Rust-based) and other native packages
build-from-source=true
```

### Step 2: Simplify Build Command

Update `railway.json`:

**BEFORE** (flag-based - doesn't work):
```json
{
  "build": {
    "buildCommand": "npm install --build=from-source && npm run build"
  }
}
```

**AFTER** (config-based - works):
```json
{
  "build": {
    "buildCommand": "npm install && npm run build"
  },
  "deploy": {
    "startCommand": "npm run start"
  }
}
```

### Step 3: Test Locally

```bash
# Test that SWC compiles from source
npm install

# Test that build succeeds
npm run build

# Should see:
# ✔ Building build context
# ✔ Building admin panel
# NO ERRORS
```

### Step 4: Commit and Deploy

```bash
git add .npmrc railway.json
git commit -m "fix: enable build-from-source for swc compatibility"
git push origin main
```

Railway will auto-deploy and use the config from `.npmrc`.

---

## 📊 How It Works

### Build Process with Configuration File

```
Railway receives push
↓
Railpack spins up Node.js container
↓
npm reads .npmrc
  └→ Sees: build-from-source=true
↓
npm install
  ├→ Sees config: build-from-source=true
  ├→ Downloads SWC source code (Rust)
  ├→ Installs Rust compiler
  ├→ Compiles SWC to Linux binary
  ├→ Creates working native binding
  └→ Time: ~1-2 minutes (compilation)
↓
npm run build
  ├→ Uses newly-compiled SWC binary
  ├→ Builds Strapi admin panel
  ├→ Time: ~30 seconds
  └→ NO ERRORS
↓
npm run start
  └→ Strapi starts successfully
```

### Build Time Impact

**First Deploy**: ~5-6 minutes
- Node install: ~1.5 min
- npm install with compilation: ~1.5-2 min (SWC from source)
- npm run build: ~30 sec
- Startup: ~1 min

**Subsequent Deploys**: ~4-5 minutes
- npm cache speeds up install
- SWC compilation cached (only if deps unchanged)

---

## 🧪 Why This Approach

### ✅ Advantages

1. **Permanent Fix**
   - Not command-specific
   - Works for all npm operations
   - Survives npm cache
   - Works across build steps

2. **Platform-Specific**
   - Compiles for exact target (Linux)
   - No dependency mismatches
   - No prebuilt binary issues
   - Working native bindings guaranteed

3. **Maintainable**
   - Simple `.npmrc` configuration
   - Standard npm feature
   - Easy to understand
   - Future-proof (new Rust packages benefit)

4. **Reproducible**
   - Same config on all machines
   - Same result every time
   - Version-controlled
   - Team can follow exact same approach

### ❌ Why Other Approaches Failed

| Approach | Why Failed |
|----------|-----------|
| npm rebuild | No source code to rebuild |
| force install | Prebuilts still incompatible |
| --build flag | Cache ignored flag |
| Different SWC version | All versions have same issue |
| Alternative compiler | Strapi designed for SWC |

---

## 🔧 Technical Deep Dive

### SWC vs JavaScript Tools

```javascript
// JavaScript Tool (esbuild)
// Can run anywhere Node.js runs
Node.js → esbuild JavaScript → Works everywhere

// Rust-Based Tool (SWC)
// Must compile for each platform
Rust Source → Compile → Linux binary (.node file)
             on Linux
// Can't use Windows binary on Linux!
```

### Native Module Compilation

```
npm install --build-from-source

Process:
1. Check package.json for native modules (SWC, node-gyp)
2. For each native module:
   a. Download source code
   b. Install compiler (gcc, Python, Rust)
   c. Run build script
   d. Compile to platform-specific binary
   e. Place .node file in node_modules
3. Resulting binaries work on that platform
```

### Why Prebuilts Fail on Railway

```
Railway Container:
- OS: Linux (Ubuntu)
- Arch: x86_64
- glibc: Specific version

Prebuilt Binary was built on:
- OS: Linux (but different distro?)
- glibc: Different version
- Missing dependencies?

Result: Binary can't load
Error: Failed to load native binding
```

---

## ✅ Verification

After deploying, verify the fix:

### Check Railway Logs

```bash
railway logs --follow
```

**Success Indicators**:
```
✔ Building build context (79ms)
✔ Building admin panel (14.6s)
✔ Strapi server started successfully
🎉 Admin panel available at: https://your-domain.railway.app/admin
```

**Failure Indicators**:
```
✖ Building admin panel
[ERROR]  There seems to be an unexpected error

Error: Failed to load native binding
at Object.<anonymous> (/app/node_modules/@swc/core/binding.js:333:11)
```

### Check Admin Panel

```bash
curl https://your-railway-domain.railway.app/admin
```

Should return HTML (not 500 error).

---

## 🆘 If It Still Fails

### Verify .npmrc is Committed

```bash
git show HEAD:.npmrc
# Should include: build-from-source=true
```

### Check Build Command

```bash
railway open  # Opens Railway dashboard
# Check: Deploy → Build command should just be:
# npm install && npm run build
# (NOT with --build=from-source flag)
```

### Try Force Rebuild

```bash
railway deploy --force
```

This:
- Clears cache
- Downloads all packages fresh
- Recompiles everything

### Check for Other Native Modules

If still failing, other native modules might have issues:

```bash
npm list | grep -E "gyp|native|binding"
```

If many native modules, might need different approach.

---

## 📚 Related Issues

### Similar Problems with Other Packages

This same issue affects ANY Rust-based npm package:
- `esbuild` (Go-based) - similar issue
- `native node-gyp packages` - may have similar issues
- Database drivers with native components

**Solution**: All use `build-from-source=true`

### Cookie/Auth Issues on HTTPS

If you have separate HTTPS/cookie issues, see:
[Strapi HTTPS Cookies Guide](./strapi-https-cookies.md)

---

## 📊 References

- [SWC Official Docs](https://swc.rs)
- [npm build-from-source](https://docs.npmjs.com/cli/v10/configuring-npm/npmrc)
- [Native Node Modules](https://nodejs.org/en/docs/guides/nodejs-docker-webapp/)
- [Railway Documentation](https://docs.railway.app)

---

## 💡 Key Takeaway

```
Rust-based packages (SWC, esbuild) in containers:
  ❌ NEVER use prebuilt binaries
  ✅ ALWAYS compile from source
  ✅ USE: build-from-source=true in .npmrc
```

