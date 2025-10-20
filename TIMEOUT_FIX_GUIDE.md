# 504 Gateway Timeout Fix - Vercel Function Invocation Timeout

**Problem:** `Code: FUNCTION_INVOCATION_TIMEOUT` - Your Vercel deployment was timing out

**Root Cause:** API calls to Strapi during build time (`getStaticPaths` and `getStaticProps`) were taking too long or failing to respond.

---

## What Caused The Timeout

Your Next.js pages (`/archive`, `/category`, `/tag`) were making Strapi API calls during build/deployment:

```javascript
// ❌ PROBLEM: No timeout, no error handling
export async function getStaticPaths() {
  const postsData = await getPaginatedPosts(1, 1); // Could hang forever
  // ... rest of code
}
```

**When Strapi:**

- Is offline or unreachable
- Is slow to respond
- Has network issues

**Result:** The entire Vercel deployment would hang and timeout after 10 minutes.

---

## Fixes Applied

### 1. ✅ Added 10-Second Timeout to All API Calls

**File:** `web/public-site/lib/api.js`

```javascript
async function fetchAPI(path, urlParamsObject = {}, options = {}) {
  try {
    // Add 10 second timeout
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);

    const response = await fetch(requestUrl, {
      ...mergedOptions,
      signal: controller.signal,
    });

    clearTimeout(timeout);
    // ... rest of code
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(`API request timed out`);
    }
    throw error;
  }
}
```

**Benefits:**

- API calls won't hang indefinitely
- Errors are caught quickly
- Build completes even if Strapi is slow

### 2. ✅ Added Error Handling to getStaticPaths

**Files:**

- `web/public-site/pages/archive/[page].js`
- `web/public-site/pages/category/[slug].js`
- `web/public-site/pages/tag/[slug].js`

```javascript
// ❌ OLD - Would crash entire build
export async function getStaticPaths() {
  const postsData = await getPaginatedPosts(1, 1);
  // ...
}

// ✅ NEW - Graceful fallback
export async function getStaticPaths() {
  try {
    const postsData = await getPaginatedPosts(1, 1);
    // ... generate paths
  } catch (error) {
    console.error('Error fetching pagination data:', error);
    // Return fallback paths if API fails
    return {
      paths: Array.from({ length: 5 }, (_, i) => ({
        params: { page: (i + 1).toString() },
      })),
      fallback: 'blocking', // Handle missing pages on-demand
    };
  }
}
```

### 3. ✅ Added Error Handling to getStaticProps

```javascript
// ✅ NEW - Returns 404 instead of crashing
export async function getStaticProps({ params }) {
  try {
    const page = parseInt(params.page, 10) || 1;
    const postsData = await getPaginatedPosts(page, POSTS_PER_PAGE);
    // ... return props
  } catch (error) {
    console.error(`Error fetching posts:`, error);
    return {
      notFound: true, // Return 404 instead of crashing
      revalidate: 10, // Retry sooner if there's an error
    };
  }
}
```

---

## How To Prevent This In The Future

### 1. Monitor Strapi Availability

- Keep your Strapi backend running at all times
- Set up uptime monitoring: https://www.pingdom.com/
- Test Strapi before deploying to Vercel

### 2. Use Revalidation

```javascript
return {
  props: { ... },
  revalidate: 60,  // Regenerate page every 60 seconds
};
```

This way, even if generation fails, the cached page will still serve.

### 3. Test Build Locally First

```bash
npm run build  # Test build locally
npm start      # Test production build
```

If this hangs, your API calls are too slow.

### 4. Increase Vercel Build Timeout (If Needed)

In `vercel.json`:

```json
{
  "buildCommand": "npm run build",
  "devCommand": "npm run dev"
}
```

For longer builds, create a custom build script with longer timeouts.

### 5. Use ISR (Incremental Static Regeneration)

Current approach is good - pages regenerate on-demand:

```javascript
export async function getStaticProps() {
  return {
    props: { ... },
    revalidate: 3600, // Regenerate every hour
  };
}
```

---

## Deployment Checklist

Before deploying to Vercel:

- [ ] Strapi backend is running and accessible
- [ ] Test build locally: `npm run build`
- [ ] All API endpoints respond within 5 seconds
- [ ] Environment variables are set in Vercel dashboard
- [ ] Error handling is in place (check your code above)
- [ ] Revalidate times are set appropriately
- [ ] No hardcoded URLs - use `NEXT_PUBLIC_STRAPI_API_URL` env var

---

## If Timeout Happens Again

### Step 1: Check Strapi Status

```bash
# Ping your Strapi backend
curl https://your-strapi-url.railway.app/api/health
```

### Step 2: Check Vercel Logs

1. Go to https://vercel.com/dashboard
2. Select your project
3. Click **Deployments**
4. Click the failed deployment
5. Click **Logs** to see what timed out

### Step 3: Temporary Fix

- Roll back to previous deployment
- Fix the underlying issue (Strapi down, slow API, etc.)
- Redeploy

### Step 4: Notify Team

- Strapi issues require operations team to fix
- Have a backup plan (cached pages, degraded mode)

---

## Performance Tips

### 1. Optimize API Queries

```javascript
// ❌ SLOW - Fetching full posts with all data
const postsData = await getPaginatedPosts(1, 1, null); // Could be slow

// ✅ FAST - Fetch only needed fields in production
const postsData = await getPaginatedPosts(1, 1, null, {
  fields: ['title', 'slug', 'publishedAt'],
  populate: 'coverImage',
});
```

### 2. Use CDN for Static Assets

Move images and assets to CDN if Strapi is slow:

```javascript
const IMAGE_CDN = process.env.NEXT_PUBLIC_IMAGE_CDN || '';
const imageUrl = IMAGE_CDN
  ? `${IMAGE_CDN}/image-name.jpg`
  : getStrapiURL(coverImage.url);
```

### 3. Enable Build Cache

In `vercel.json`:

```json
{
  "crons": [
    {
      "path": "/api/revalidate",
      "schedule": "0 */6 * * *" // Run every 6 hours
    }
  ]
}
```

---

## Current Status

✅ **Fixed:**

- API calls have 10-second timeout
- All pages have error handling
- Build won't crash if Strapi is down
- Fallback pages served on error

✅ **Deployed:**

- All changes committed to git
- Ready for next Vercel deployment

📊 **Monitoring:**

- Check Vercel dashboard for timeouts: https://vercel.com/dashboard
- Check Strapi logs if API is slow

---

## Next Steps

1. **Redeploy to Vercel**

   ```bash
   git push origin dev
   ```

   Vercel will automatically trigger a new deployment.

2. **Verify in Vercel Dashboard**
   - Go to https://vercel.com/dashboard
   - Check that build completes successfully
   - Verify logs don't show timeouts

3. **Test Production**
   - Visit your site: https://gladlabs.io
   - Navigate to archive, categories, tags
   - Check that pages load quickly

4. **Set Up Monitoring**
   - Use Vercel Analytics: https://vercel.com/analytics
   - Monitor build times
   - Alert on timeouts

---

## References

- [Vercel Build Timeout Docs](https://vercel.com/docs/platform/limits)
- [Next.js getStaticProps](https://nextjs.org/docs/basic-features/data-fetching/get-static-props)
- [Fetch Timeout Patterns](https://developer.mozilla.org/docs/Web/API/AbortController)
- [Railway Uptime](https://railway.app)
