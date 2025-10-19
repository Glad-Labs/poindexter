# Critical Discovery: Why `--build=from-source` Flag Failed on Railway

## 🔴 The Problem

**The command we used**:

```bash
npm install --build=from-source && npm run build
```

**What happened**:

- ✅ npm install succeeded (14 seconds)
- ❌ npm run build FAILED with **same SWC native binding error**
- Same error after trying the "from-source" approach

**This means**: The flag didn't actually force compilation from source

---

## 🔍 Why the Flag Failed

### Root Cause: npm Config Context Loss

Railway runs builds in **separate shell steps**:

```
Step 1: install
  $ npm install
  ├─ No --build=from-source flag
  ├─ Downloads prebuilt SWC binaries
  ├─ Caches them
  └─ Duration: 14s

Step 2: build
  $ npm install --build=from-source && npm run build
  ├─ Flag passed to npm install
  ├─ BUT npm cache already has prebuilts
  ├─ Cache layer ignores flag (not a cache-invalidating change)
  ├─ Uses cached prebuilts instead
  └─ Duration: 14s (just reading cache)

RESULT: SWC prebuilts never rebuilt, same binding error
```

### The Cache Problem

**npm caching behavior**:

```
npm cache has: @swc/core@1.13.5 (prebuilt Linux binary)

Command: npm install --build=from-source
npm's logic:
  1. Check cache for @swc/core@1.13.5
  2. Found in cache ✓
  3. Skip download/rebuild (cache hit)
  4. Use cached prebuilt

RESULT: Flag ignored because cache had it
```

---

## ✅ The Fix: Configuration File

**Update `.npmrc`** with:

```ini
build-from-source=true
```

**Why this works**:

1. **npm reads `.npmrc` for EVERY command**

   ```
   npm install
   └─ Reads .npmrc
   └─ Sees: build-from-source=true
   └─ Compiles from source
   ```

2. **Survives npm cache**

   ```
   npm cache has: @swc/core prebuilt

   npm install with .npmrc
   └─ Reads .npmrc: build-from-source=true
   └─ Cache invalidated (config changed)
   └─ Downloads source code
   └─ Compiles on container
   └─ Creates working Linux binary
   ```

3. **Persists across all steps**

   ```
   Step 1: npm install
     Reads .npmrc → build-from-source=true
     Builds from source

   Step 2: npm run build
     Uses source-built SWC
     Build succeeds
   ```

---

## 📊 Comparison

| Aspect          | `--build=from-source` flag | `.npmrc` config     |
| --------------- | -------------------------- | ------------------- |
| **Applies to**  | Single npm command         | ALL npm commands    |
| **Persists**    | Current shell only         | All shell sessions  |
| **Cache**       | Cache may ignore it        | Cache respects it   |
| **Rails Steps** | Lost between steps         | Maintained          |
| **Reliability** | ❌ Failed                  | ✅ Expected to work |

---

## 🚀 Changes Deployed

### 1. Updated `.npmrc`

```ini
# Added this line:
build-from-source=true
```

### 2. Simplified `railway.json`

```json
{
  "buildCommand": "npm install && npm run build"
  // (was: npm install --build=from-source && npm run build)
}
```

### 3. Pushed to GitHub

```
Commit: bb584509b
Message: fix: add persistent build-from-source in npmrc for railway container builds
Status: Deployed to GitHub
```

**Railway will auto-rebuild** in ~2-3 minutes. Check:

```bash
railway logs --follow
```

---

## 🎯 Key Insight

```
Command-line flags: Temporary, context-based
Configuration files: Permanent, globally applied

For build systems with caching:
  ALWAYS use configuration files over flags
```

---

## ⏱️ Expected Timeline

- **Now**: Fix deployed
- **~2-3 min**: Railway detects GitHub push
- **~1-2 min**: Railpack spins up
- **~2 min**: npm install with source compilation
- **~30 sec**: npm run build
- **~1 min**: Strapi startup
- **Total**: ~4-6 minutes
- **Success**: Admin panel at https://your-railway-domain/admin
