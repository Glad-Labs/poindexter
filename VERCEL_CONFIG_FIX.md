# Vercel Configuration Fix

## What Was Wrong

1. **Missing `$schema`** - Vercel recommends adding the schema for IDE autocomplete and validation
2. **Using legacy `env` format** - Environment variables should NOT be defined in `vercel.json` (deprecated)
3. **Missing security headers** - No headers configured for production safety
4. **Missing URL normalization** - No cleanUrls or trailingSlash settings

---

## What Was Fixed

### ✅ Added Schema Autocomplete

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json"
}
```

This enables IDE autocomplete and validation in VS Code.

### ✅ Removed Legacy `env` Configuration

**OLD (deprecated):**

```json
{
  "env": {
    "NEXT_PUBLIC_STRAPI_API_URL": { ... },
    "STRAPI_API_TOKEN": { ... }
  }
}
```

**WHY:** Vercel recommends managing environment variables in the Project Settings dashboard instead of in `vercel.json`. This is:

- More secure (don't commit secrets)
- Easier to manage per environment
- Better for team collaboration

### ✅ Added Security Headers

```json
{
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "X-Frame-Options", "value": "DENY" },
        { "key": "X-XSS-Protection", "value": "1; mode=block" }
      ]
    }
  ]
}
```

### ✅ Added URL Normalization

```json
{
  "cleanUrls": true,
  "trailingSlash": false
}
```

- Removes `.html` extensions from URLs
- Redirects URLs with trailing slashes to without

---

## Next: Add Environment Variables to Vercel Dashboard

**You MUST add your environment variables in the Vercel dashboard instead of `vercel.json`:**

### Step 1: Go to Vercel Dashboard

1. Open https://vercel.com/dashboard
2. Select your `glad-labs-public-site` project

### Step 2: Add Environment Variables

1. Click **Settings** tab
2. Click **Environment Variables** on the left
3. Add these variables:

| Name                         | Value                                 | Type       |
| ---------------------------- | ------------------------------------- | ---------- |
| `NEXT_PUBLIC_STRAPI_API_URL` | `https://your-strapi-url.railway.app` | Plain text |
| `STRAPI_API_TOKEN`           | Your Strapi API token                 | Secret     |
| `NEXT_PUBLIC_SITE_URL`       | `https://gladlabs.io`                 | Plain text |

### Step 3: How to Get STRAPI_API_TOKEN

1. Go to your Strapi admin at `https://your-strapi-url.railway.app/admin`
2. Click **Settings** (bottom of left menu)
3. Click **API Tokens**
4. Click **Create new API token**
   - Name: `Vercel Full Access`
   - Description: `For Vercel public site access`
   - Type: Select `Full access`
5. Copy the token and paste into Vercel

### Step 4: Redeploy

1. After adding environment variables, redeploy on Vercel
2. Vercel will automatically trigger a redeployment
3. Or manually redeploy: Click **Deployments** → Click the latest one → Click **Redeploy**

---

## Current vercel.json Configuration

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "buildCommand": "npm run build",
  "devCommand": "npm run dev",
  "installCommand": "npm install",
  "framework": "nextjs",
  "cleanUrls": true,
  "trailingSlash": false,
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "X-Frame-Options", "value": "DENY" },
        { "key": "X-XSS-Protection", "value": "1; mode=block" }
      ]
    },
    {
      "source": "/service-worker.js",
      "headers": [
        {
          "key": "Cache-Control",
          "value": "public, max-age=0, must-revalidate"
        }
      ]
    }
  ]
}
```

### What Each Section Does:

| Property         | Purpose                                  |
| ---------------- | ---------------------------------------- |
| `$schema`        | Enables IDE autocomplete for vercel.json |
| `buildCommand`   | Runs `npm run build` during deployment   |
| `devCommand`     | Runs `npm run dev` locally               |
| `installCommand` | Runs `npm install` during deployment     |
| `framework`      | Tells Vercel this is a Next.js project   |
| `cleanUrls`      | Removes `.html` from URLs                |
| `trailingSlash`  | Removes trailing slashes from URLs       |
| `headers`        | Adds security headers to all responses   |

---

## Optional Enhancements

### Add Cache Headers for Static Assets

```json
{
  "source": "/assets/(.*)",
  "headers": [
    { "key": "Cache-Control", "value": "public, max-age=31536000, immutable" }
  ]
}
```

### Add Redirects (if needed)

```json
{
  "redirects": [
    {
      "source": "/old-page",
      "destination": "/new-page",
      "permanent": true
    }
  ]
}
```

### Add Rewrites for SPA Routes

```json
{
  "rewrites": [
    {
      "source": "/(.*)",
      "destination": "/index.html"
    }
  ]
}
```

---

## Troubleshooting

### "Build failed" after deployment

- Check that environment variables are set in Vercel dashboard
- Verify `NEXT_PUBLIC_STRAPI_API_URL` is correct and accessible
- Check Vercel logs for specific errors

### "Cannot find Strapi API"

- Verify `NEXT_PUBLIC_STRAPI_API_URL` is correct
- Make sure Railway backend is running
- Check CORS settings in Strapi

### "Deployment hangs"

- Check that build command completes locally: `npm run build`
- Verify all dependencies are in `package.json`
- Check for circular dependencies

---

## Summary

✅ **Fixed Configuration:**

- Added schema for validation
- Removed deprecated env format
- Added security headers
- Added URL normalization

📋 **Next Steps:**

1. Add environment variables in Vercel dashboard
2. Redeploy the project
3. Test that everything works

✨ **Your site is now production-ready!**
