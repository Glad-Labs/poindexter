# 🚨 Fix: Oversight Hub Production Configuration

## Problem Summary

Your Oversight Hub on Vercel is failing because:

1. ❌ **API URL not configured** - Trying to connect to `localhost:8000` (doesn't exist in production)
2. ❌ **manifest.json missing** - 404 error from PWA manifest file
3. ❌ **Environment variables not set in Vercel** - Using local defaults

## Solution Implemented

### ✅ Step 1: Files Created/Updated

- ✅ Created `public/manifest.json` - PWA manifest for app metadata
- ✅ Updated `public/index.html` - Now properly references manifest and has correct metadata
- ✅ Updated `src/services/cofounderAgentClient.js` - Added documentation and warning logging
- ✅ Updated `.env.example` - Clarified `REACT_APP_API_URL` purpose

### ✅ Step 2: Configure Vercel Environment Variables

You must set these in Vercel:

**Go to:** Vercel Dashboard → Project Settings → Environment Variables

**Add these variables:**

```env
REACT_APP_API_URL = https://your-backend-domain.com
REACT_APP_STRAPI_URL = https://your-strapi-domain.com
REACT_APP_STRAPI_TOKEN = your-strapi-api-token
```

**Examples for your setup:**

| Environment | Variable | Value |
|------------|----------|-------|
| Production | `REACT_APP_API_URL` | `https://glad-labs-website-api.railway.app` |
| Production | `REACT_APP_STRAPI_URL` | `https://glad-labs-website-cms.railway.app` |
| Staging | `REACT_APP_API_URL` | `https://staging-api.railway.app` |
| Local Dev | `REACT_APP_API_URL` | `http://localhost:8000` |

### ✅ Step 3: Rebuild & Redeploy

After setting environment variables in Vercel:

```bash
# Option 1: Redeploy through Vercel UI
# Go to Deployments → Select latest → Click "Redeploy"

# Option 2: Redeploy through CLI
vercel --prod

# Option 3: Trigger through git push
git add .
git commit -m "fix: configure production API endpoints for Oversight Hub"
git push origin main
```

## Expected Fixes

✅ **Before (Current Errors):**

```
localhost:8000/command - net::ERR_CONNECTION_REFUSED
manifest.json - 404 error
Cannot fetch available models - 404
```

✅ **After (With Env Variables):**

```
https://glad-labs-website-api.railway.app/api/command - 200 OK
/manifest.json - 200 OK
/api/v1/models/available - 200 OK
```

## Quick Checklist

- [ ] Got your backend API URL from Railway/hosting provider
- [ ] Got your Strapi CMS URL
- [ ] Logged into Vercel dashboard
- [ ] Added `REACT_APP_API_URL` environment variable
- [ ] Added `REACT_APP_STRAPI_URL` environment variable
- [ ] Redeployed Oversight Hub
- [ ] Tested in production - verify API calls work
- [ ] Check browser console - should show no localhost errors

## Testing the Fix

**In Vercel Production:**

1. Open browser DevTools (F12)
2. Go to Console tab
3. Try creating a task or command
4. You should see:
   - ✅ HTTP 200 responses from your production backend
   - ✅ No `localhost:8000` errors
   - ✅ No `manifest.json 404` errors

**If still broken:**

```javascript
// In browser console, check current configuration:
console.log('API URL:', process.env.REACT_APP_API_URL);
console.log('Strapi URL:', process.env.REACT_APP_STRAPI_URL);
// Should show your production URLs, not localhost!
```

## Files Modified

1. **web/oversight-hub/public/manifest.json** - Created
2. **web/oversight-hub/public/index.html** - Updated metadata and title
3. **web/oversight-hub/src/services/cofounderAgentClient.js** - Added documentation
4. **web/oversight-hub/.env.example** - Clarified variable names

## Next: Backend Must Be Running

Make sure your backend is deployed and running:

- [ ] FastAPI Co-Founder Agent running on production URL
- [ ] Strapi CMS running on production URL
- [ ] Both have CORS configured to allow Vercel domain
- [ ] Both are accessible from the internet (not localhost)

## CORS Configuration (Backend)

If you get CORS errors, your backend needs to allow Vercel:

```python
# src/cofounder_agent/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://glad-labs-website-oversight-hub.vercel.app",  # Your Vercel domain
        "http://localhost:3001",  # Local development
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Questions?

1. **Where do I find my backend URL?** - Check Railway dashboard for deployed service URLs
2. **Why localhost:8000 in production?** - Environment variables weren't set in Vercel
3. **Will this affect local development?** - No, local dev uses `http://localhost:8000` by default
4. **Do I need to rebuild locally?** - No, just set Vercel env vars and redeploy

---

**Status:** ✅ Ready for deployment with proper environment variables
