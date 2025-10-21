# 504 Gateway Timeout - FIXED ✅

## Problem Summary

You were getting `504 GATEWAY_TIMEOUT` errors from Vercel when deploying. The build was taking too long to complete because your Next.js pages were trying to fetch data from Strapi during build time with no timeout protection.

## Root Cause

- `getStaticPaths()` and `getStaticProps()` were making API calls to Strapi
- If Strapi was slow or unreachable, the entire build would hang
- After 10+ minutes, Vercel would timeout the deployment
- Error: `FUNCTION_INVOCATION_TIMEOUT`

## What Was Fixed

### 1. API Request Timeout (10 seconds)

Added timeout protection to all Strapi API calls:

```javascript
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 10000);
await fetch(url, { signal: controller.signal });
```

### 2. Error Handling in Static Generation

All pages now gracefully handle API failures:

- **getStaticPaths()** returns fallback paths if API fails
- **getStaticProps()** returns 404 instead of crashing build
- Shorter revalidate time (10s) for error pages to retry faster

### 3. Files Modified

- `web/public-site/lib/api.js` - Added timeout to fetchAPI
- `web/public-site/pages/archive/[page].js` - Error handling
- `web/public-site/pages/category/[slug].js` - Error handling
- `web/public-site/pages/tag/[slug].js` - Error handling

## Impact

✅ Build won't timeout if Strapi is unreachable  
✅ Deployment completes even with API failures  
✅ Users see 404 instead of gateway errors  
✅ Revalidation retries faster on errors

## Next: Deploy to Vercel

The changes are committed. Now deploy:

```bash
git push origin dev
```

Vercel will automatically redeploy. Your 504 errors should be gone!

## Verify Fix

After deployment:

1. Go to https://vercel.com/dashboard
2. Check your project's build logs
3. Verify build completes without timeouts
4. Visit your site and test pages

## If Issues Continue

If you still get timeouts:

1. **Check Strapi is running**: `curl https://your-strapi.railway.app/api/health`
2. **Check Vercel logs**: Dashboard → Deployments → click failed build
3. **Check API response time**: Should be < 5 seconds
4. **Temporarily disable API calls**: Build locally with `npm run build`

## Long-term Prevention

1. Keep Strapi backend running 24/7
2. Monitor uptime: https://www.pingdom.com/
3. Set up Vercel alerts: https://vercel.com/dashboard
4. Use ISR (Incremental Static Regeneration) for dynamic content
5. Cache API responses when possible

See **TIMEOUT_FIX_GUIDE.md** for detailed information and troubleshooting.
