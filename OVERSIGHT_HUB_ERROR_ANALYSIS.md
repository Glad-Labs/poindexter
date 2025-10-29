# 🔴→🟢 Oversight Hub Production Errors - FIXED

## 📊 Error Analysis

### Errors You're Seeing

```
❌ localhost:8000/command - net::ERR_CONNECTION_REFUSED
❌ manifest.json - 404 error  
❌ /api/v1/models/available - 404 error
❌ Failed to fetch (TypeError)
```

### Root Cause

Your **Oversight Hub on Vercel** is configured to talk to `localhost:8000`, which doesn't exist in production.

```
┌─────────────────────────────────────────────────────────┐
│  Oversight Hub Running on Vercel                        │
│  https://glad-labs-website-oversight-hub.vercel.app     │
│                                                         │
│  Tries to connect to: http://localhost:8000 ❌          │
│  (Can't reach your local machine!)                      │
└─────────────────────────────────────────────────────────┘
```

### Why This Happens

- Environment variable `REACT_APP_API_URL` not set in Vercel
- Code defaults to `http://localhost:8000`
- Vercel doesn't know where your real backend is

---

## ✅ What I Fixed

### 1. Created `web/oversight-hub/public/manifest.json`
- Fixes the "manifest.json 404" error
- Provides app metadata for PWA (Progressive Web App)

### 2. Updated `web/oversight-hub/public/index.html`
- Now properly references manifest.json
- Updated title to "Oversight Hub - Glad Labs"
- Updated metadata

### 3. Updated `web/oversight-hub/src/services/cofounderAgentClient.js`
- Added documentation about required environment variable
- Added warning if `REACT_APP_API_URL` not configured

### 4. Updated `web/oversight-hub/.env.example`
- Clarified `REACT_APP_API_URL` vs `REACT_APP_API_BASE_URL`
- Added comments about local vs production

---

## 🚀 What You Need To Do

### Step 1: Get Your Backend URLs

From **Railway Dashboard**:

```
Railway → Services → [Click "Co-Founder Agent"] → Copy URL
Railway → Services → [Click "Strapi"] → Copy URL
```

You'll get URLs like:
```
https://glad-labs-website-api.railway.app
https://glad-labs-website-cms.railway.app
```

### Step 2: Set Environment Variables in Vercel

Go to: **https://vercel.com** → Your Project → Settings → Environment Variables

**Add 3 variables:**

| Name | Value |
|------|-------|
| `REACT_APP_API_URL` | `https://[your-backend-url]` |
| `REACT_APP_STRAPI_URL` | `https://[your-strapi-url]` |
| `REACT_APP_STRAPI_TOKEN` | Your Strapi API Token |

### Step 3: Redeploy

**Click "Redeploy" in Vercel** (or push to main branch)

---

## ✅ Expected Results After Fix

```
Before Fix:
❌ localhost:8000/command - net::ERR_CONNECTION_REFUSED
❌ manifest.json - 404
❌ Cannot fetch models

After Fix (with env vars set):
✅ https://[your-backend]/api/command - 200 OK
✅ manifest.json - 200 OK
✅ /api/v1/models/available - 200 OK
```

---

## 🧪 How To Verify

**In Vercel Production:**

1. Open https://glad-labs-website-oversight-hub.vercel.app
2. Press F12 (DevTools)
3. Go to Console tab
4. Check there are NO errors about:
   - `localhost:8000`
   - `manifest.json 404`
   - Connection refused

5. Try creating a task - should work!

**Test in Console:**

```javascript
console.log(process.env.REACT_APP_API_URL)
// Should show your backend URL, NOT http://localhost:8000!
```

---

## 📝 Files Changed

```
✅ web/oversight-hub/public/manifest.json          [CREATED]
✅ web/oversight-hub/public/index.html             [UPDATED]
✅ web/oversight-hub/src/services/cofounderAgentClient.js  [UPDATED]
✅ web/oversight-hub/.env.example                  [UPDATED]
```

Git commit: `3f8b4217a`

---

## ⚠️ Important: Backend Must Also Be Configured

Your backend needs to allow CORS requests from Vercel:

```python
# src/cofounder_agent/main.py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://glad-labs-website-oversight-hub.vercel.app",  # Add your Vercel domain
        "http://localhost:3001"  # Keep for local dev
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 📋 Checklist

- [ ] Found your backend API URL from Railway
- [ ] Found your Strapi CMS URL from Railway  
- [ ] Logged into Vercel dashboard
- [ ] Added `REACT_APP_API_URL` environment variable
- [ ] Added `REACT_APP_STRAPI_URL` environment variable
- [ ] Added `REACT_APP_STRAPI_TOKEN` environment variable
- [ ] Clicked "Redeploy" in Vercel
- [ ] Waited for deployment to complete (~1-2 minutes)
- [ ] Tested in production - verified API calls work
- [ ] Checked browser console - no localhost errors

---

## ❓ Common Questions

**Q: Where do I find the Railway URLs?**
A: Railway Dashboard → Services → [Click service] → Copy URL from top right

**Q: Do I need to change anything locally?**
A: No, local development will still use `http://localhost:8000`

**Q: Why did this happen?**
A: Oversight Hub wasn't configured with production backend URLs in Vercel

**Q: Will redeploying break anything?**
A: No, it will fix the issues. If problems occur, Vercel shows rollback option

**Q: How long does redeploy take?**
A: Usually 1-2 minutes. You can watch in Vercel Deployments tab

---

## 🆘 If It's Still Broken After These Steps

Check these in order:

1. **Environment variable not set?**
   ```javascript
   // In browser console:
   console.log(process.env.REACT_APP_API_URL)
   // Should show your backend URL
   ```

2. **Backend not running?**
   ```bash
   # From terminal, test your backend:
   curl https://[your-backend-url]/api/health
   # Should return 200 OK
   ```

3. **CORS not configured?**
   ```
   Look for error: "Access to XMLHttpRequest blocked by CORS policy"
   Fix: Add your Vercel domain to backend's allow_origins list
   ```

4. **Backend URL wrong?**
   - Double-check URL from Railway dashboard
   - Make sure it starts with `https://` not `http://`
   - No trailing slashes: `https://example.com` not `https://example.com/`

---

**Status: ✅ Code changes complete. Waiting for you to set Vercel environment variables and redeploy.**

See `PRODUCTION_FIX_CHECKLIST.md` for step-by-step instructions.
