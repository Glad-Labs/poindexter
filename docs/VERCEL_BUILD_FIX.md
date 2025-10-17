# Vercel Deployment Fix: Strapi API Connection Issues

## Problem

The Vercel build is failing with:
```
TypeError: fetch failed
  Error: connect ECONNREFUSED 127.0.0.1:1337
```

This happens because:
- Next.js tries to fetch content from Strapi during the build process (`getStaticProps`)
- The build environment doesn't have `localhost:1337` available
- The production Strapi URL is not configured in Vercel environment variables

## Solution

### Step 1: Get Your Production Strapi URL

Your production Strapi is deployed at Railway:
```
https://glad-labs-strapi-v5-backend-production.up.railway.app
```

### Step 2: Set Vercel Environment Variables

1. Go to your Vercel project dashboard
2. Navigate to Settings → Environment Variables
3. Add the following variables:

**For all environments (Production, Preview, Development):**

```
NEXT_PUBLIC_STRAPI_API_URL=https://glad-labs-strapi-v5-backend-production.up.railway.app
```

**For Production only (if you have different URLs):**

```
NEXT_PUBLIC_STRAPI_API_URL=https://glad-labs-strapi-v5-backend-production.up.railway.app
STRAPI_API_TOKEN=your-api-token-here
```

### Step 3: Trigger a New Build

After setting the environment variables:

1. Go to Vercel dashboard
2. Find your project
3. Click "Deployments"
4. Click the three dots on the latest failed deployment
5. Select "Redeploy"

Or manually redeploy:
```bash
vercel --prod --force
```

### Step 4: Verify the Build

The build should now:
- Successfully fetch posts from your production Strapi
- Generate static pages for the archive
- Complete without errors

## Environment Variables Reference

| Variable | Value | Required | Purpose |
|----------|-------|----------|---------|
| `NEXT_PUBLIC_STRAPI_API_URL` | `https://glad-labs-strapi-v5-backend-production.up.railway.app` | ✅ Yes | Base URL for Strapi API during build and runtime |
| `STRAPI_API_TOKEN` | Your API token | ⚠️ Optional | Needed if Strapi API requires authentication |

## Testing Locally Before Deploy

To test your build locally with the production URL:

```bash
# Set the environment variable
export NEXT_PUBLIC_STRAPI_API_URL="https://glad-labs-strapi-v5-backend-production.up.railway.app"

# Build the project
npm run build

# Check if build succeeds
echo $?  # Should output 0 if successful
```

## Troubleshooting

### If build still fails:

1. **Check Strapi is running:**
   ```bash
   curl https://glad-labs-strapi-v5-backend-production.up.railway.app/api
   ```
   Should return JSON response

2. **Check API token (if required):**
   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" \
     https://glad-labs-strapi-v5-backend-production.up.railway.app/api/blog-posts
   ```

3. **Check Vercel logs:**
   - Go to Vercel dashboard
   - Select your project
   - Go to "Deployments"
   - Click on failed deployment
   - Scroll to build logs
   - Look for "Error" or "fetch" messages

### If API returns 403/401:

If you get authentication errors, you need to:

1. Generate an API token in Strapi admin panel:
   - Go to `https://glad-labs-strapi-v5-backend-production.up.railway.app/admin`
   - Settings → API Tokens → Create new API token
   - Set permissions to allow reading blog posts

2. Add token to Vercel:
   ```
   STRAPI_API_TOKEN=your-token-from-step-1
   ```

3. Update build code to use the token if needed (check `lib/api.js`)

## Key Files Involved

- `/web/public-site/.env.example` - Example environment variables
- `/web/public-site/pages/archive/[page].js` - Archive page that fetches during build
- `/web/public-site/lib/api.js` - API utility functions
- `/web/public-site/lib/posts.js` - Post fetching functions

## Next Steps After Fixing

1. ✅ Redeploy to Vercel
2. ✅ Test that posts appear on the public site
3. ✅ Verify archive page loads with pagination
4. ✅ Check sitemap generation completes
5. ✅ Validate SEO meta tags are correct

---

**Note:** Whenever you update Strapi content, Vercel will need to rebuild to pick up the changes. For real-time content updates, consider adding ISR (Incremental Static Regeneration) or switching to dynamic rendering on specific pages.
