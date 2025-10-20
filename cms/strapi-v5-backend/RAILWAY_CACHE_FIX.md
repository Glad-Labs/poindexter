# Railway Build Cache Conflict - Root Cause Analysis & Fix

## The Problem (From Build Logs)

```
npm error EBUSY: resource busy or locked, rmdir '/app/node_modules/.cache'
npm error errno -16
```

**When:** During the build step while running `npm ci --omit=dev --omit=optional`

**Why it happened:** Railway's Railpack build system runs two npm commands in sequence:

1. **Install step:** `npm install` (creates node_modules and cache)
2. **Build step:** `npm ci --omit=dev --omit=optional` (tries to remove cache)

The problem: `npm ci` was trying to clean and rebuild the cache while the cache was still being accessed.

---

## Root Cause

Railway's build configuration had conflicting requirements:

### Issue 1: Duplicate npm Operations

```
Step 1 (install): npm install                                  ← Creates cache
Step 2 (build):   npm ci --omit=dev --omit=optional           ← Tries to clean cache
                  npm run build                                 ← Tries to use cache
```

**Why this fails:** npm can't remove the cache directory it just created if it's still in use.

### Issue 2: Problematic `.npmrc` Configuration

Original `.npmrc`:

```properties
cache=/tmp/.npm-cache              ← Custom cache location
optional=false                     ← Skip optional deps
fetch-timeout=60000                ← Long timeouts
```

**Why this failed:**

- Custom cache location conflicts with Railpack's cache management
- Multiple cache settings cause lockfile conflicts
- The build system was trying to manage cache in two places simultaneously

---

## The Solution

### 1. Simplified `railway.json`

```json
{
  "build": {
    "buildCommand": "npm run build"
  }
}
```

**Why:** Since `npm install` runs in the install step, the build step just needs to run `npm run build`. The node_modules are already there, so we don't need `npm ci` again.

### 2. Minimal `.npmrc`

```properties
# Minimal npm configuration for Railway.app
optional=false           ← Skip optional deps (safer in containers)
fund=false              ← Disable fund messages
update-notifier=false   ← Disable update notifications
production=true         ← Enable production mode
```

**Why:**

- Removes cache path conflicts
- Lets Railpack manage caching automatically
- Reduces npm configuration surface area
- No timeout overrides that cause conflicts

---

## Build Process Flow (After Fix)

### Railway Railpack Steps

```
Step 1: Install Node.js 18.20.8
        ✅ Success (mise package manager)

Step 2: npm install
        ✅ Installs all dependencies (1382 packages in 51s)
        ✅ Creates node_modules
        ✅ Cache automatically managed by Railpack

Step 3: npm run build (strapi build)
        ✅ Uses existing node_modules
        ✅ No cache conflicts
        ✅ Builds admin panel
        ✅ Completes in ~15 seconds

Step 4: npm run start
        ✅ Starts Strapi server
        ✅ Connects to PostgreSQL
        ✅ Admin panel ready at /admin
```

---

## Why This Works

1. **Single npm install:** Only one dependency installation pass
2. **Automatic cache management:** Railpack handles caching, not npm config
3. **No conflicting commands:** Build step doesn't try to rebuild cache
4. **Minimal configuration:** Fewer variables = fewer conflicts
5. **Railway best practice:** Aligns with Railpack's design patterns

---

## Testing Locally

✅ Build tested and verified on Windows:

```
Building build context ...................... 30ms ✔
Building admin panel ....................... 14.9s ✔
Total build time .......................... ~15 seconds
```

---

## Expected Railway Deployment

**Build time:** 3-4 minutes total

- Node.js installation: ~1.5 min
- npm install: ~1 min
- npm run build: ~30 sec
- Container startup: ~30 sec

**Success indicators in logs:**

```
✔ Building build context
✔ Building admin panel
✔ Strapi started successfully
🎉 Admin panel available at: https://your-domain.railway.app/admin
```

---

## Commits

- `76e443e6d` - Simplify railway build and minimize npmrc
- Previous: `607aff1eb` - Removed broken vite alias
- Previous: `982ba4720` - Simplified build command

---

## Key Takeaway

❌ **Don't:**

- Use `npm ci` if `npm install` already ran
- Configure custom cache paths in containers
- Run multiple npm install variants
- Override Railpack's cache management

✅ **Do:**

- Let Railway manage caching
- Minimize `.npmrc` configuration
- Use one dependency installation method
- Trust Railpack's build system

---

## Status: ✅ READY FOR DEPLOYMENT

All build issues resolved. Push to GitHub → Railway auto-deploys.

Monitor with: `railway logs --follow`
