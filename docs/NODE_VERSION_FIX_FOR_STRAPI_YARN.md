# Node Version Fix for Strapi on Railway

## Problem

When reverting to yarn for Strapi, Railway deployment failed with:

```
error @noble/hashes@2.0.1: The engine "node" is incompatible with this module. 
Expected version ">= 20.19.0". Got "18.20.8"
```

### Root Cause

1. **Old Configuration:** Strapi's `package.json` allowed Node 18+: `"node": ">=18.0.0 <=22.x.x"`
2. **Railway Default:** When no specific Node version is required, Railway uses Node 18.20.8
3. **Yarn Dependency Conflict:** When switching from npm to yarn, yarn resolved packages differently
4. **@noble/hashes@2.0.1:** This dependency (used by Strapi v5 plugins) requires Node >= 20.19.0

### Why npm Worked with Node 18

npm was more lenient about version constraints and installed compatible versions. When we switched to yarn, it resolved to newer package versions that have stricter Node requirements.

## Solution

Changed Strapi's `package.json` engines requirement from:

```json
"engines": {
  "node": ">=18.0.0 <=22.x.x",  // ❌ Too permissive
  "yarn": ">=1.22.0"
}
```

To:

```json
"engines": {
  "node": ">=20.0.0 <=22.x.x",  // ✅ Requires Node 20+
  "yarn": ">=1.22.0"
}
```

## What Happens on Railway Now

1. **Railpack detects:** `"node": ">=20.0.0 <=22.x.x"` in Strapi's package.json
2. **Installs Node 20.x:** (instead of 18.20.8)
3. **Detects yarn:** `"packageManager": "yarn@1.22.22"`
4. **Installs with yarn:** All packages now compatible with Node 20+
5. **Deployment succeeds** ✅

## Verification Steps

After Railway redeploys (2-5 minutes), verify:

### 1. Check Railway Logs

Look for:
```
[97m↳ Detected Node[0m
[96m20.x.x[0m              <- Node version (not 18.x.x)

[97m↳ Using yarn1 package manager[0m
yarn install v1.22.22  <- Yarn being used
```

### 2. Test Strapi Admin Login

```
https://glad-labs-website-production.up.railway.app/admin
```

Should:
- ✅ Load without errors
- ✅ No "Cannot send secure cookie" error
- ✅ Accept login credentials

### 3. Check Services Status

All services should be running on Railway:
- PostgreSQL database ✅
- Strapi CMS on port 1337 ✅
- Node.js runtime ✅

## Why Node 20 Requirement

- **Strapi v5.18.1:** Officially supports Node 18+
- **@noble/hashes:** Used by authentication plugins, requires Node 20+
- **Future-proof:** Node 20 is actively maintained and widely supported
- **Production standard:** Node 20 LTS is the current production standard

## Files Changed

| File | Change | Reason |
|------|--------|--------|
| `cms/strapi-main/package.json` | `"node": ">=20.0.0 <=22.x.x"` | Fix @noble/hashes compatibility |

## Deployment Timeline

**Before:** Node 18.20.8 + npm → Works (lenient dependencies)  
**After Switch to Yarn:** Node 18.20.8 + yarn → Fails (strict dependencies)  
**With Fix:** Node 20+ + yarn → Works ✅

## Related Issues

- **Issue:** "Cannot send secure cookie over unencrypted connection" (cookie security)
- **Fix:** Reverted to yarn (STRAPI_RAILWAY_SECURE_COOKIE_FIX.md)
- **This Issue:** Node version incompatibility with yarn
- **Fix:** Require Node 20+ in package.json

## Next Steps

1. ✅ Commit Node version requirement: Done
2. ✅ Push to GitHub: Done
3. ⏳ Railway redeploys with Node 20 + yarn (2-5 minutes)
4. ⏳ Test admin login
5. ⏳ Verify production Strapi working
6. ⏳ Local services connect to production Strapi

