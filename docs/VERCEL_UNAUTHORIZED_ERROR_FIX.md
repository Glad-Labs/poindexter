# Vercel Build Failure: "Unauthorized" Error - Solution

## Problem Summary

The Vercel build is now failing with **"Unauthorized"** errors when fetching data from Strapi:

```
20:52:57.583 Unauthorized
20:52:57.586 Unauthorized
20:52:57.587 Error: An error occurred please try again
```

This happens during the `getStaticPaths()` and `getStaticProps()` phase when building `/archive/[page]`.

## Root Cause

The `web/public-site/lib/posts.js` requires **two** environment variables to be set:

1. **`STRAPI_API_TOKEN`** (private token for authentication) - ❌ MISSING in Vercel
2. **`NEXT_PUBLIC_STRAPI_API_URL`** (public API URL) - ⚠️ Wrong URL (pointing to old Strapi instance)

Your local `.env.local` file has these values, but Vercel cannot see local files. You need to set these as **Vercel Environment Variables**.

### Code Reference

`web/public-site/lib/posts.js` (lines 32-50):

```javascript
async function fetchAPI(query, { variables } = {}) {
  const apiUrl =
    process.env.NEXT_PUBLIC_STRAPI_API_URL || 'http://localhost:1337';
  const apiToken = process.env.STRAPI_API_TOKEN;

  // Critical check to ensure the API token is configured.
  if (!apiToken) {
    console.error(
      'FATAL: STRAPI_API_TOKEN is not set in your environment variables.'
    );
    throw new Error(
      'The Strapi API token is missing. Please check your .env.local file.'
    );
  }
  // ... continues with fetch request using Authorization header
}
```

## Solution: Set Vercel Environment Variables

### Step 1: Get Your Strapi Token

Your token is already in `.env.local`:

```
STRAPI_API_TOKEN="f96a8db7330483b6395666c96369a7a5b97214c734cda9ea958ce1edc97b43ea59cd46bef60a1fc82dbb38acfeb43a900b1b72010e9521978a76a6adaa302f70a2b0b67838b354785eaa8dab3c81111f21d2d2fda7c6c24d82707096e9f47aefe3b6e321b175d6a0cce19de9418eb71b0687a152c8f614b72781101ad1867c4b"
```

**IMPORTANT:** Keep this token secure! Don't commit it to Git.

### Step 2: Get Your Production Strapi URL

Based on your deployment, use:

```
https://glad-labs-strapi-v5-backend-production.up.railway.app
```

(NOT the old URL: `https://healing-appliance-9fd84df4a1.strapiapp.com/`)

### Step 3: Configure Vercel Environment Variables

1. Go to **Vercel Dashboard**: https://vercel.com/dashboard
2. Select your project: **glad-labs-website**
3. Navigate to: **Settings** → **Environment Variables**
4. Add these two variables:

   | Variable Name                | Value                                                                                                                                                                                                                                                              | Visibility          |
   | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------- |
   | `STRAPI_API_TOKEN`           | `f96a8db7330483b6395666c96369a7a5b97214c734cda9ea958ce1edc97b43ea59cd46bef60a1fc82dbb38acfeb43a900b1b72010e9521978a76a6adaa302f70a2b0b67838b354785eaa8dab3c81111f21d2d2fda7c6c24d82707096e9f47aefe3b6e321b175d6a0cce19de9418eb71b0687a152c8f614b72781101ad1867c4b` | **Production** only |
   | `NEXT_PUBLIC_STRAPI_API_URL` | `https://glad-labs-strapi-v5-backend-production.up.railway.app`                                                                                                                                                                                                    | All Environments    |

5. Click **Save**

### Step 4: Redeploy

Option A: **From Vercel Dashboard**

- Go to **Deployments**
- Find the failed deployment
- Click the three dots (**...**)
- Select **Redeploy**

Option B: **From Git**

- Push any commit to the `main` branch
- Vercel will automatically redeploy with the new env vars

## Verification

After redeploy, check:

1. ✅ **Build succeeds** - No "Unauthorized" errors
2. ✅ **Posts display** - Archive page shows content
3. ✅ **Pagination works** - Navigation between pages functions
4. ✅ **No API errors** - Check browser console for fetch errors

## Why STRAPI*API_TOKEN vs NEXT_PUBLIC*\*

- **`STRAPI_API_TOKEN`**: Private token (no `NEXT_PUBLIC` prefix)
  - Only available at build time
  - Used server-side in `getStaticProps`
  - Safe for backend authentication
  - NOT exposed to browser

- **`NEXT_PUBLIC_STRAPI_API_URL`**: Public URL (with `NEXT_PUBLIC` prefix)
  - Available at build time AND in browser
  - Clients need to know the API endpoint
  - Safe to expose (just a URL)

## Environment Variable Checklist

- [ ] `STRAPI_API_TOKEN` set in Vercel (Production only)
- [ ] `NEXT_PUBLIC_STRAPI_API_URL` set in Vercel (Production URL, not localhost)
- [ ] `.env.local` remains local (not committed to Git)
- [ ] Vercel deployment triggered (redeploy or new push)
- [ ] Build log shows "✓ Compiled successfully"
- [ ] No "Unauthorized" errors in build output

## Troubleshooting

### Still Getting "Unauthorized"?

1. Verify token is correct (check against `.env.local`)
2. Ensure token has "Full access" permission in Strapi
3. Check Strapi instance is running and accessible
4. Wait 2-3 minutes for Vercel env vars to propagate

### Strapi Token Expired?

If the token is old, generate a new one:

1. Log into Strapi: https://glad-labs-strapi-v5-backend-production.up.railway.app/admin
2. Navigate: **Settings** → **API Tokens**
3. Create new "Full access" token
4. Copy the full token string
5. Update in Vercel: **Settings** → **Environment Variables** → `STRAPI_API_TOKEN`
6. Redeploy

## Additional Notes

- The `NEXT_PUBLIC_` prefix in `NEXT_PUBLIC_STRAPI_API_URL` makes it available to frontend code
- Build-time data fetching (`getStaticProps`) requires full API authentication
- Cache revalidation is set to 60 seconds (line 56 in posts.js)
- Consider rotating tokens periodically for security
