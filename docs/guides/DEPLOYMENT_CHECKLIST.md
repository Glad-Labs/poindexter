# 📋 Complete Deployment Checklist

**Deploying**: Co-Founder Agent (Railway) + Oversight Hub (Vercel)  
**Target Environments**: Production  
**Last Updated**: October 22, 2025

---

## 🎯 Pre-Deployment: Local Verification

### Code Quality Checks

- [ ] All code changes committed to git
- [ ] No uncommitted changes: `git status` shows clean
- [ ] Branch correct: currently on `feat/cost-optimization`
- [ ] All recent commits pushed: `git push origin feat/cost-optimization`
- [ ] No Python syntax errors in main.py
- [ ] No React build errors: `npm run build` (from web/oversight-hub/)
- [ ] No console warnings in React app
- [ ] All imports resolve correctly

### Dependency Verification

**Python Backend**

- [ ] `src/cofounder_agent/requirements.txt` exists and is up-to-date
- [ ] All required packages listed:
  - [ ] fastapi
  - [ ] uvicorn
  - [ ] pydantic
  - [ ] aiohttp
  - [ ] pexels-api (for new Pexels integration)
  - [ ] (others as needed)
- [ ] No deprecated packages
- [ ] All dependencies have compatible versions

**React Frontend**

- [ ] `web/oversight-hub/package.json` up-to-date
- [ ] All required packages installed: `npm ci`
- [ ] Build succeeds: `npm run build`
- [ ] No critical vulnerability warnings: `npm audit`
- [ ] Build output size reasonable (< 500KB gzipped)

### Feature Verification

**New APIs Integration (Cost Optimization)**

- [ ] Pexels client initialized correctly
  - [ ] API key available: `PEXELS_API_KEY` set locally
  - [ ] Test image search: `pexels_client.search_images("test")`
  - [ ] Photographer attribution working
- [ ] Serper client initialized correctly
  - [ ] API key available: `SERPER_API_KEY` set locally
  - [ ] Test search: `serper_client.search("test")`
  - [ ] No rate limiting issues

- [ ] Image caching working
  - [ ] Cache class instantiated
  - [ ] Test cache/retrieve cycle
  - [ ] TTL logic correct (30 days)

- [ ] Ollama retry logic working
  - [ ] Retry method callable
  - [ ] Exponential backoff implemented
  - [ ] Error handling correct

### Environment Variables - Local

**Python Backend** (`src/cofounder_agent/`)

- [ ] `LLM_PROVIDER` set correctly
- [ ] `GEMINI_API_KEY` available if using Gemini fallback
- [ ] `GCP_PROJECT_ID` set
- [ ] `GCP_SERVICE_ACCOUNT_EMAIL` set
- [ ] `PEXELS_API_KEY` set (NEW)
- [ ] `SERPER_API_KEY` set (NEW)
- [ ] `STRAPI_API_URL` set (for CMS integration)
- [ ] `STRAPI_API_TOKEN` set
- [ ] All other required vars present in `.env` or env file

**React Frontend** (`web/oversight-hub/`)

- [ ] `REACT_APP_COFOUNDER_API_URL` set (localhost for dev)
- [ ] `REACT_APP_STRAPI_URL` set (localhost for dev)
- [ ] `REACT_APP_STRAPI_API_TOKEN` set
- [ ] All `REACT_APP_FIREBASE_*` variables set
- [ ] `REACT_APP_ENVIRONMENT` set to "development"
- [ ] All other required vars present in `.env.local`

---

## 🚀 Railway Deployment Checklist

### Pre-Deployment Setup

- [ ] Railway account created: https://railway.app
- [ ] Payment method added (for free tier, optional)
- [ ] Railway CLI installed: `railway --version`
- [ ] Logged into Railway CLI: `railway login`

### Project Creation & Configuration

- [ ] New Railway project created: `glad-labs-cofounder-agent`
- [ ] Project linked locally: `railway init` or `railway link`
- [ ] Environment set to "production": `railway environment list`
- [ ] Correct project selected: `railway project select`

### Procfile & Startup Configuration

- [ ] Procfile exists at project root OR `src/cofounder_agent/`
- [ ] Procfile content correct:
  ```
  web: cd src/cofounder_agent && python -m uvicorn main:app --host 0.0.0.0 --port $PORT
  ```
- [ ] **NOT** hard-coded port (must use `$PORT`)
- [ ] Python version correct (3.9+)

### Database (Optional)

- [ ] PostgreSQL added if needed: `railway add`
- [ ] `DATABASE_URL` auto-generated
- [ ] Connection string verified: `railway variables`
- [ ] Database migrations run (if applicable)

### Environment Variables Setup

**Critical for Railway**

- [ ] `LLM_PROVIDER="local"` (use local Ollama)
- [ ] `LOCAL_LLM_API_URL="http://localhost:11434"`
- [ ] `GEMINI_API_KEY` set (fallback)
- [ ] `GCP_PROJECT_ID` set
- [ ] `GCP_SERVICE_ACCOUNT_EMAIL` set
- [ ] `PEXELS_API_KEY` set
- [ ] `SERPER_API_KEY` set
- [ ] `STRAPI_API_URL` set (external URL if Strapi on Railway)
- [ ] `STRAPI_API_TOKEN` set

**Set via CLI**

```bash
railway variables set LLM_PROVIDER="local"
railway variables set GEMINI_API_KEY="your_key"
railway variables set PEXELS_API_KEY="wdq7jNG49KWxBipK90hu32V5RLpXD0I5J81n61WeQzh31sdGJ9sua1qT"
railway variables set SERPER_API_KEY="fcb6eb4e893705dc89c345576950270d75c874b3"
# ... continue for all vars
```

- [ ] All variables confirmed: `railway variables`
- [ ] Production environment selected: `railway --environment production`

### Deployment

**Option A: Git-based (Recommended)**

- [ ] Code pushed to GitHub: `git push origin feat/cost-optimization`
- [ ] Go to https://railway.app → New → GitHub repo
- [ ] Select: `glad-labs-website`
- [ ] Set root directory: `src/cofounder_agent`
- [ ] Environment variables added via dashboard
- [ ] Deployment started

**Option B: Railway CLI**

- [ ] Procfile in place
- [ ] Environment variables set: `railway variables`
- [ ] Deploy: `railway up`
- [ ] Watch logs: `railway logs --follow`

### Post-Deployment Verification

- [ ] Deployment shows "Ready" status
- [ ] No errors in deployment logs: `railway logs`
- [ ] App accessible: `railway open` or visit dashboard URL
- [ ] Health endpoint responds: `curl https://app.railway.app/health`
- [ ] API endpoints respond correctly
- [ ] No 502/503 errors
- [ ] Logs show "Application startup complete"

### Integration Testing

- [ ] Test Pexels image search working
- [ ] Test Serper web search working
- [ ] Test content generation endpoint
- [ ] Test Strapi CMS connection (if deployed)
- [ ] Test database connection (if using PostgreSQL)

### Monitoring Setup

- [ ] Logs accessible: `railway logs --follow`
- [ ] Cost monitoring enabled (go to Billing)
- [ ] Alerts configured (if available)
- [ ] Auto-scaling configured (if needed)

---

## 🎯 Vercel Oversight Hub Deployment Checklist

### Pre-Deployment Setup

- [ ] Vercel account created: https://vercel.com
- [ ] Payment method added (optional for free tier)
- [ ] GitHub repository connected
- [ ] Vercel CLI installed (optional): `vercel --version`

### Project Creation

- [ ] **NEW** Vercel project created (NOT merged with public-site)
- [ ] Project name: `oversight-hub`
- [ ] GitHub repo: `glad-labs-website`
- [ ] Root directory: `web/oversight-hub`
- [ ] Framework preset: React
- [ ] Production branch: `main`
- [ ] Preview branches: `*` (all)

### Build Configuration

- [ ] Build command: `npm run build`
- [ ] Dev command: `npm start`
- [ ] Output directory: `build`
- [ ] Install command: `npm ci`
- [ ] Root directory: `web/oversight-hub`

### Environment Variables

**Production Environment** (Do NOT deploy yet!)

- [ ] `REACT_APP_COFOUNDER_API_URL = "https://your-app.railway.app"` (Production)
- [ ] `REACT_APP_COFOUNDER_API_URL = "http://localhost:8000"` (Preview)
- [ ] `REACT_APP_STRAPI_URL = "https://strapi.railway.app"` (Production)
- [ ] `REACT_APP_STRAPI_URL = "http://localhost:1337"` (Preview)
- [ ] `REACT_APP_STRAPI_API_TOKEN = "token"` (All environments)
- [ ] All `REACT_APP_FIREBASE_API_KEY` set (All environments)
- [ ] All `REACT_APP_FIREBASE_AUTH_DOMAIN` set (All environments)
- [ ] All `REACT_APP_FIREBASE_PROJECT_ID` set (All environments)
- [ ] All `REACT_APP_FIREBASE_STORAGE_BUCKET` set (All environments)
- [ ] All `REACT_APP_FIREBASE_MESSAGING_SENDER_ID` set (All environments)
- [ ] All `REACT_APP_FIREBASE_APP_ID` set (All environments)

**Set via Dashboard**

1. Go to Settings → Environment Variables
2. For each variable:
   - Add variable name
   - Add value
   - Select which environments (Production/Preview/Development)
   - Save

- [ ] All 10+ Firebase variables set
- [ ] Both API URL variables set (with different values per environment)
- [ ] Strapi token set for all environments
- [ ] Preview deploys will use localhost URLs
- [ ] Production deploys will use Railway URLs

### GitHub Integration

- [ ] Production branch set to `main`
- [ ] Deploy previews enabled for PRs
- [ ] Auto-deploy on push: enabled
- [ ] Automatic deployments: On

### Ready to Deploy

- [ ] All environment variables confirmed in dashboard
- [ ] Build command tested locally (optional)
- [ ] React app builds successfully: `npm run build`
- [ ] No build errors in preview

### Deployment

**Option A: Dashboard Deploy**

- [ ] Click "Deploy Now" button
- [ ] Watch deployment progress
- [ ] Expected: "Ready" status

**Option B: Auto-deploy from Git** (Recommended)

- [ ] Merge feat/cost-optimization → main
  ```bash
  git checkout main
  git merge feat/cost-optimization
  git push origin main
  ```
- [ ] Vercel auto-detects push and starts deployment
- [ ] Watch dashboard for build logs

### Post-Deployment Verification

- [ ] Deployment shows "Ready" status
- [ ] Build logs show no errors
- [ ] App accessible at vercel.app URL
- [ ] App loads without blank page
- [ ] Console has no critical errors (F12)
- [ ] Firebase initializes successfully
- [ ] Page routing works (refresh doesn't 404)
- [ ] Links to backend APIs work
- [ ] No CORS errors

### Integration Testing

- [ ] Can log in with Firebase
- [ ] Dashboard loads correctly
- [ ] Can connect to Railway backend
- [ ] Can fetch Strapi data
- [ ] Admin features accessible
- [ ] Real-time updates working

### Custom Domain (Optional)

- [ ] Domain configured in Vercel dashboard
- [ ] DNS records updated (if using external domain)
- [ ] HTTPS working (auto-enabled)
- [ ] Custom domain accessible

### Monitoring Setup

- [ ] Analytics enabled: Settings → Analytics
- [ ] Web Vitals tracking enabled
- [ ] Error alerts configured
- [ ] Deployment notifications enabled

---

## 🔗 Integration Verification

### Railway Backend ↔ React Frontend

**Before Merging to Production**

- [ ] Oversight Hub can reach Railway backend

  ```javascript
  // In browser console on deployed app
  fetch('https://your-app.railway.app/health')
    .then((r) => r.json())
    .then(console.log);
  // Should return: {status: "healthy"}
  ```

- [ ] No CORS errors between apps
- [ ] API responses include correct data
- [ ] Authentication flows work
- [ ] Real-time updates working

### Firebase Integration

- [ ] Firebase authentication working
- [ ] Can create/read/write documents
- [ ] No permission errors
- [ ] Storage uploads working (if used)

### Strapi CMS Integration

- [ ] Content loads from Strapi
- [ ] Can create/edit/delete content (if admin)
- [ ] API tokens working
- [ ] No authentication errors

---

## ⚠️ Common Issues & Verification

### If Railway Deployment Fails

- [ ] Check Procfile exists and is correct
- [ ] Check port uses `$PORT` variable (not hardcoded)
- [ ] Check requirements.txt is complete
- [ ] Check environment variables are set
- [ ] Check startup logs: `railway logs`
- [ ] Check Python version compatibility

### If Vercel Deployment Fails

- [ ] Check build command: `npm run build`
- [ ] Check output directory: `build`
- [ ] Check root directory set correctly
- [ ] Check all env vars set (REACT*APP*\* prefix)
- [ ] Check Firebase credentials valid
- [ ] Check package.json has all dependencies

### If Apps Don't Communicate

- [ ] Check CORS enabled on Railway
- [ ] Check API URLs are correct
- [ ] Check environment variables set correctly
- [ ] Check network requests in browser (F12 → Network)
- [ ] Check for 403/401 permission errors
- [ ] Check API tokens not expired

---

## 📊 Deployment Tracking

### Timeline

| Step                       | Status | Date | Notes |
| -------------------------- | ------ | ---- | ----- |
| Local testing complete     | ⏳     | —    | —     |
| Code pushed to GitHub      | ⏳     | —    | —     |
| Railway project created    | ⏳     | —    | —     |
| Railway env vars set       | ⏳     | —    | —     |
| Railway deployment started | ⏳     | —    | —     |
| Railway deployment ready   | ⏳     | —    | —     |
| Vercel project created     | ⏳     | —    | —     |
| Vercel env vars set        | ⏳     | —    | —     |
| Vercel deployment started  | ⏳     | —    | —     |
| Vercel deployment ready    | ⏳     | —    | —     |
| Integration tests passed   | ⏳     | —    | —     |
| Production verified        | ⏳     | —    | —     |

### Deployment URLs

**After deployment, fill in actual URLs:**

```
Railway Backend:     https://_______________
Oversight Hub:       https://_______________
Strapi CMS:          https://_______________
Public Site:         https://_______________

Railway Logs:        railway logs --follow
Vercel Dashboard:    https://vercel.com/projects
```

---

## ✅ Final Verification

Before marking as "Production Ready":

- [ ] All checklist items completed
- [ ] Both apps deployed successfully
- [ ] Apps communicate correctly
- [ ] No critical errors in logs
- [ ] Performance acceptable
- [ ] Monitoring/alerts configured
- [ ] Team notified of new URLs
- [ ] Documentation updated
- [ ] Backup of current production (if any)

---

## 📞 Support Contacts

**If Deployment Fails:**

1. Check logs first: `railway logs` or Vercel Dashboard
2. Review troubleshooting sections in deployment guides
3. Check environment variables are correct
4. Verify Git branch is correct
5. Try redeploying from scratch

**Documentation References:**

- Railway Guide: `docs/guides/RAILWAY_DEPLOYMENT_GUIDE.md`
- Vercel Guide: `docs/guides/VERCEL_OVERSIGHT_HUB_DEPLOYMENT.md`
- General Deployment: `docs/guides/VERCEL_DEPLOYMENT_STRATEGY.md`
- Cost Optimization: `docs/guides/COST_OPTIMIZATION_COMPLETE.md`

---

**Status**: Ready to deploy! ✨

Use this checklist to track progress and ensure nothing is missed.
