# 🎯 Production Fix Summary - 3 Actions Required

## Your Current Problem

✅ **Root Cause Identified:** Oversight Hub trying to connect to `localhost:8000` in production

```
localhost:8000/command:1 - net::ERR_CONNECTION_REFUSED  ← ERROR!
manifest.json:1 - 404 error                             ← ERROR!
```

This happens because:

- Environment variables not configured in Vercel
- App uses `REACT_APP_API_URL` env var
- Vercel doesn't have it set, so defaults to `http://localhost:8000`
- Production Vercel can't reach localhost = connection refused

## 3 Required Actions

### Action 1: Get Your Backend URLs ⚠️ YOU MUST DO THIS

Get these from your infrastructure:

```
BACKEND_API_URL = https://xxx.railway.app  (Co-Founder Agent API)
STRAPI_URL = https://xxx.railway.app       (Strapi CMS)
```

**Where to find them:**

- Railway dashboard → Services → Copy the URL for each service
- Format should be: `https://service-name.railway.app`

**Questions?**

- If backend is on Railway: Look for "Co-Founder Agent" service URL
- If CMS is on Railway: Look for "Strapi" service URL
- Don't include `/api` - just the domain

### Action 2: Set Environment Variables in Vercel ⚠️ DO THIS IMMEDIATELY

**Steps:**

1. Go to: https://vercel.com/dashboard
2. Click your project: `glad-labs-website-oversight-hub`
3. Click "Settings" (top right)
4. Go to "Environment Variables"
5. Add these 3 variables:

| Name                     | Value                              | Source                  |
| ------------------------ | ---------------------------------- | ----------------------- |
| `REACT_APP_API_URL`      | `https://your-backend.railway.app` | Railway Dashboard       |
| `REACT_APP_STRAPI_URL`   | `https://your-strapi.railway.app`  | Railway Dashboard       |
| `REACT_APP_STRAPI_TOKEN` | Your Strapi API Token              | Strapi Admin → Settings |

### Action 3: Redeploy Vercel ⚠️ DO THIS AFTER SETTING VARS

Option A (Easiest): Through Vercel UI

```
Deployments → Click latest → Button "Redeploy"
```

Option B: Through git push

```bash
git add .
git commit -m "fix: configure production API endpoints"
git push origin main
```

Option C: Through Vercel CLI

```bash
vercel --prod
```

---

## Verification (After Redeploy)

1. Open https://glad-labs-website-oversight-hub.vercel.app
2. Open DevTools (F12 → Console)
3. Look for **NO errors** about:
   - `localhost:8000`
   - `manifest.json 404`
   - Connection refused

4. Try creating a task - should work!

---

## Files Already Fixed ✅

I've already updated these:

- ✅ `web/oversight-hub/public/manifest.json` - Created (fixes 404 error)
- ✅ `web/oversight-hub/public/index.html` - Updated title and metadata
- ✅ `web/oversight-hub/src/services/cofounderAgentClient.js` - Added warnings
- ✅ `web/oversight-hub/.env.example` - Clarified variable names

---

## If It Still Doesn't Work

**Check 1: Environment variables set?**

```javascript
// Paste in browser console
console.log(process.env.REACT_APP_API_URL);
// Should show your backend URL, NOT http://localhost:8000
```

**Check 2: Backend URL correct?**

```bash
# From your terminal, test backend URL
curl https://your-backend-url/api/health
# Should return 200 OK
```

**Check 3: CORS enabled on backend?**
Backend must allow requests from Vercel domain:

```python
# Check src/cofounder_agent/main.py has:
allow_origins=[
    "https://glad-labs-website-oversight-hub.vercel.app",
    "http://localhost:3001"
]
```

---

## Quick Reference

| Component   | Local                   | Production                                           |
| ----------- | ----------------------- | ---------------------------------------------------- |
| Frontend    | `http://localhost:3001` | `https://glad-labs-website-oversight-hub.vercel.app` |
| Backend API | `http://localhost:8000` | `https://xxx.railway.app`                            |
| Strapi CMS  | `http://localhost:1337` | `https://xxx.railway.app`                            |

---

**Next Step:** Follow the 3 Actions above in order. Estimated time: 5 minutes.
