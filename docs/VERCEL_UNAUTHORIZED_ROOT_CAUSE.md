# Vercel Build "Unauthorized" - Root Cause Analysis & Fix

## The Issue

Build is failing with "Unauthorized" errors from Strapi during page data collection:

```
21:29:43.636 Unauthorized
21:29:43.640 Error: An error occurred please try again
```

This occurs on pages with `getStaticProps()`:

- ❌ `/archive/[page]`
- ❌ `/category/[slug]`
- ❌ `/tag/[slug]`
- ❌ `/posts/[slug]`
- ❌ `/` (homepage)
- ❌ `/about`

## Root Cause

**Token + API URL Mismatch**

Your `.env.local` was pointing to the **OLD Strapi instance**:

```
# ❌ WRONG - Old Strapi deployment
NEXT_PUBLIC_STRAPI_API_URL="https://healing-appliance-9fd84df4a1.strapiapp.com/"
```

But your token is for the **NEW Railway Strapi instance**:

```
# ✅ CORRECT - Current Railway deployment
NEXT_PUBLIC_STRAPI_API_URL="https://glad-labs-strapi-v5-backend-production.up.railway.app"
```

**Why this caused "Unauthorized":**

1. Token is issued for Railway Strapi instance
2. Code tries to connect to old Strapi URL
3. Old instance doesn't recognize the token → "Unauthorized"

## Solution Applied

### Local Fix

✅ Updated `.env.local`:

```bash
# Before
NEXT_PUBLIC_STRAPI_API_URL="https://healing-appliance-9fd84df4a1.strapiapp.com/"

# After
NEXT_PUBLIC_STRAPI_API_URL="https://glad-labs-strapi-v5-backend-production.up.railway.app"
```

### Vercel Fix (Still Required)

You must set **both** environment variables in Vercel:

1. Go to: https://vercel.com/dashboard → glad-labs-website → Settings → Environment Variables

2. Add/Update these two variables:

   **Variable 1: API Token (Private)**
   - Name: `STRAPI_API_TOKEN`
   - Value: `f96a8db7330483b6395666c96369a7a5b97214c734cda9ea958ce1edc97b43ea59cd46bef60a1fc82dbb38acfeb43a900b1b72010e9521978a76a6adaa302f70a2b0b67838b354785eaa8dab3c81111f21d2d2fda7c6c24d82707096e9f47aefe3b6e321b175d6a0cce19de9418eb71b0687a152c8f614b72781101ad1867c4b`
   - Environment: **Production** (only)
   - Click **Save**

   **Variable 2: API URL (Public)**
   - Name: `NEXT_PUBLIC_STRAPI_API_URL`
   - Value: `https://glad-labs-strapi-v5-backend-production.up.railway.app`
   - Environment: **All** (Production, Preview, Development)
   - Click **Save**

3. **Redeploy**: Go to Deployments → Click the failed deployment → Redeploy

## Why This Happened

1. You originally had Strapi on a managed cloud (healing-appliance-9fd84df4a1.strapiapp.com)
2. You migrated to Railway for better control
3. `.env.local` wasn't updated to reflect the new URL
4. Vercel environment variables were set (or being set) but without the URL update, the token couldn't authenticate

## How to Test

After Vercel redeploys with both env vars:

1. ✅ Check Vercel build log - should see "✓ Compiled successfully"
2. ✅ No "Unauthorized" errors in build output
3. ✅ Visit the preview URL
4. ✅ Homepage loads with posts
5. ✅ Archive page shows paginated posts
6. ✅ Category pages display filtered posts
7. ✅ Check browser console - no API errors

## Environment Variables Summary

| Variable                     | Purpose              | Type    | Location                           |
| ---------------------------- | -------------------- | ------- | ---------------------------------- |
| `NEXT_PUBLIC_STRAPI_API_URL` | GraphQL endpoint URL | Public  | `.env.local` + Vercel (All)        |
| `STRAPI_API_TOKEN`           | Authentication token | Private | `.env.local` + Vercel (Production) |

**Key Differences:**

- `NEXT_PUBLIC_` prefix = sent to browser, visible in client code
- No prefix = server-only, kept private during build

## Pages Affected by This Issue

All these pages call `getStaticProps()` which requires API authentication:

```
pages/
├── index.js              (homepage)
├── about.js              (about page)
├── archive/[page].js     (paginated archive)
├── category/[slug].js    (category listing)
├── tag/[slug].js         (tag listing)
└── posts/[slug].js       (individual post)
```

Each calls GraphQL queries via `lib/posts.js` → `fetchAPI()` → requires token + URL

## Preventing This in the Future

1. **Keep `.env.local` in `.gitignore`** (don't commit secrets)
2. **Document env vars clearly** in `.env.example`
3. **Use consistent URLs** across environments
4. **Test locally before deploying** to Vercel
5. **Verify Vercel env vars** before each deployment

## Additional Notes

- The token has "Full access" permission in Strapi
- Railway URL is the production source of truth
- Old Strapi instance should be decommissioned to avoid confusion
- Consider documenting this migration in your wiki/docs
