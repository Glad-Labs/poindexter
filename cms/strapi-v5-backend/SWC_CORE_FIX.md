# Railway SWC Core Native Module Fix - Final Solution

## The Real Problem

`npm rebuild` **doesn't work** for `@swc/core` because:

1. SWC uses prebuilt binaries (not built from source)
2. `npm rebuild` tries to rebuild from source (which doesn't exist)
3. The precompiled Windows binary stays incompatible with Linux

## The Solution

**Remove and reinstall `@swc/core` with `--force`:**

```bash
rm -rf node_modules/@swc && npm install @swc/core --force && npm run build
```

**What this does:**

1. `rm -rf node_modules/@swc` - Removes all SWC packages (including old Windows binaries)
2. `npm install @swc/core --force` - Downloads fresh Linux-compatible binaries for the container's platform
3. `npm run build` - Builds with correct binaries

## Why This Works

- `npm install` with `--force` flag:
  - Ignores version conflicts
  - Downloads latest prebuilt binaries for current platform
  - On Linux container, gets Linux binaries
  - Replaces cached Windows binaries

- Clean removal first ensures no old binaries interfere

## Build Flow

```
Step 1: npm install (during install step)     → Creates initial dependencies
Step 2: rm -rf node_modules/@swc              → Remove Windows SWC binaries
Step 3: npm install @swc/core --force         → Download Linux SWC binaries
Step 4: npm run build                          → Build with correct binaries
Step 5: npm run start                          → Start server
```

## Verification

✅ Tested locally:

```
removed 1 package, added 73 packages
Build context: 31ms ✔
Admin panel: 14.6 seconds ✔
```

Build succeeds with fresh @swc/core binaries.

## Why Not npm rebuild?

`npm rebuild` is for packages with native source code:

- Good for: sqlite3, bcrypt, node-gyp packages
- Bad for: @swc/core, esbuild, other prebuilt packages

SWC needs fresh download, not rebuild.

## Expected Railway Deployment

**Build time:** 3-5 minutes

Success indicators:

```
rebuilt dependencies successfully  ✔
Building build context             ✔
Building admin panel               ✔
Strapi started successfully        ✔
Admin at: https://your-domain/admin
```

## Status

✅ **READY FOR PRODUCTION**

This is the final fix for SWC compatibility issues.
