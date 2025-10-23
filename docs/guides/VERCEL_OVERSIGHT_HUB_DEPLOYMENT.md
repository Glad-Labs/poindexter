# 🎯 Vercel Deployment Guide - Oversight Hub (React Admin)

**Date**: October 22, 2025  
**Project**: GLAD Labs Oversight Hub (React 18 CRA)  
**Deployment**: `web/oversight-hub/` to Vercel (Separate Project)

---

## 📋 Overview

Deploy your React 18 admin dashboard (Oversight Hub) to Vercel for:

- ✅ Production-grade React hosting
- ✅ Automatic HTTPS with custom domains
- ✅ Edge caching for global performance
- ✅ Automatic deployments from Git
- ✅ Environment-specific deployments
- ✅ Real-time monitoring & analytics

**Important**: Deploy this as a SEPARATE Vercel project from your public-site for:

- Independent scaling
- Isolated environment variables
- Separate deployment schedules
- Better security isolation

---

## 🎯 Prerequisites

Before starting, ensure you have:

### 1. Vercel Account

- [ ] Sign up at https://vercel.com
- [ ] Verify email
- [ ] Add GitHub connection (for automatic deployments)

### 2. GitHub Setup

```bash
# Ensure your code is pushed to GitHub
git status
# Expected: On branch feat/cost-optimization, nothing to commit

# Verify remote
git remote -v
# Expected: origin → github.com/mattg-stack/glad-labs-website

# Push latest changes
git push origin feat/cost-optimization
```

### 3. Required Tools (Optional)

```bash
# Install Vercel CLI (optional, for local testing)
npm i -g vercel

# Verify installation
vercel --version
```

### 4. Environment Variables

Collect all required environment variables (see checklist below)

---

## 🔐 Environment Variables Checklist

### Required for React (Oversight Hub)

```bash
# ========== Co-Founder Agent Backend ==========
REACT_APP_COFOUNDER_API_URL="https://your-app.railway.app"
# or "http://localhost:8000" for local dev

# ========== Strapi CMS ==========
REACT_APP_STRAPI_URL="https://your-strapi.railway.app"
# or "http://localhost:1337" for local dev

REACT_APP_STRAPI_API_TOKEN="your_strapi_api_token"

# ========== Firebase Configuration ==========
REACT_APP_FIREBASE_API_KEY="AIzaSyDxxx..."
REACT_APP_FIREBASE_AUTH_DOMAIN="your-project.firebaseapp.com"
REACT_APP_FIREBASE_PROJECT_ID="your-project-id"
REACT_APP_FIREBASE_STORAGE_BUCKET="your-project.appspot.com"
REACT_APP_FIREBASE_MESSAGING_SENDER_ID="123456789"
REACT_APP_FIREBASE_APP_ID="1:123456789:web:abc123def456"
REACT_APP_FIREBASE_DATABASE_URL="https://your-project.firebaseio.com"

# ========== Analytics & Monitoring ==========
REACT_APP_ENVIRONMENT="production"
REACT_APP_LOG_LEVEL="info"
REACT_APP_ENABLE_ANALYTICS="true"

# ========== Feature Flags ==========
REACT_APP_ENABLE_ADMIN_PANEL="true"
REACT_APP_ENABLE_CONTENT_MANAGEMENT="true"
REACT_APP_ENABLE_REPORTING="true"
```

---

## 🚀 Step-by-Step Deployment

### Step 1: Create New Vercel Project

```bash
# Option A: Via Vercel Dashboard (Recommended)
# 1. Go to https://vercel.com/new
# 2. Select "Import Git Repository"
# 3. Choose: glad-labs-website
# 4. Configure project settings (see below)

# Option B: Via Vercel CLI
vercel --prod
# Follow prompts for setup
```

### Step 2: Configure Project Settings

In Vercel Dashboard, during project import:

```
Project Name:        oversight-hub
Framework:          React
Root Directory:     web/oversight-hub
Build Command:      npm run build
Start Command:      npm start
Output Directory:   build
```

### Step 3: Important - Don't Deploy Yet!

**Stop before clicking "Deploy"**. We need to set environment variables first!

### Step 4: Add Environment Variables

```bash
# In Vercel Dashboard:
# 1. Go to Project → Settings → Environment Variables
# 2. Add each variable from checklist above:

# Add preview/development variables
REACT_APP_COFOUNDER_API_URL = "http://localhost:8000" (Preview)
REACT_APP_COFOUNDER_API_URL = "https://cofounder.railway.app" (Production)

REACT_APP_STRAPI_URL = "http://localhost:1337" (Preview)
REACT_APP_STRAPI_URL = "https://strapi.railway.app" (Production)

# Firebase variables (same for all environments)
REACT_APP_FIREBASE_API_KEY = "AIzaSyDxxx..."
REACT_APP_FIREBASE_AUTH_DOMAIN = "your-project.firebaseapp.com"
# ... continue with all Firebase vars
```

**Pro Tip**: Click "Edit" on each variable and select which environments it applies to (Production/Preview/Development).

### Step 5: Configure Build Settings

In Vercel Dashboard → Settings → Build & Development Settings:

```
Framework Preset:   React
Build Command:      npm run build
Dev Command:        npm start
Output Directory:   build
Install Command:    npm ci

# Enable:
✅ Use npm ci
✅ Include source maps in production
✅ Enable Web Analytics
```

### Step 6: Set Up GitHub Connection

```bash
# In Vercel Dashboard:
# 1. Go to Settings → Git
# 2. Connect to GitHub repo (if not already)
# 3. Production branch: main
# 4. Preview branch: * (all branches)
# 5. Automatic deployments: Enabled
```

### Step 7: Deploy!

```bash
# Option A: Via Dashboard
# Click "Deploy Now" button

# Option B: Automatic (Recommended)
# Push to GitHub and Vercel auto-deploys
git add .
git commit -m "Deploy oversight hub to Vercel"
git push origin feat/cost-optimization

# Then merge to main for production
git checkout main
git merge feat/cost-optimization
git push origin main
# Vercel auto-deploys to production!

# Option C: Via CLI
vercel --prod
```

### Step 8: Monitor Deployment

```bash
# In Vercel Dashboard:
# 1. Go to Deployments
# 2. Watch status in real-time
# 3. Expected: "Ready"

# Check logs:
# Click on deployment → Logs tab
# Should see build logs and no errors
```

---

## ✅ Verification Checklist

After deployment, verify everything works:

### Health Check

```bash
# Visit your app
https://oversight-hub.vercel.app
# or your custom domain

# Expected:
# - Page loads without errors
# - Console has no critical errors
# - Firebase connects successfully
```

### Test Key Features

```javascript
// Open browser console and test:

// 1. Check environment variables are set
console.log(process.env.REACT_APP_COFOUNDER_API_URL);
// Should output: https://your-app.railway.app

// 2. Check Firebase initialization
firebase.auth().currentUser;
// Should show current user or null

// 3. Test API connectivity
fetch(process.env.REACT_APP_COFOUNDER_API_URL + '/health')
  .then((r) => r.json())
  .then(console.log);
// Should return: {"status": "healthy"}
```

### Monitor Performance

```bash
# In Vercel Dashboard:
# 1. Go to Analytics
# 2. Check:
#    - Core Web Vitals (green = good)
#    - Response times (< 200ms ideal)
#    - Error rate (should be 0%)
```

### Check Build Logs

```bash
# In Vercel Dashboard:
# Click Deployments → Select deployment → Logs
# Look for:
# ✅ "✓ Build completed"
# ✅ No ERROR messages
# ✅ "Uploaded N files"
```

---

## 🔗 Integration with Other Services

### Connect to Railway Backend (Co-Founder Agent)

After Railway deployment, update your Oversight Hub:

```bash
# In Vercel Dashboard → Environment Variables
# Add production variable:

REACT_APP_COFOUNDER_API_URL = "https://your-app.railway.app"

# Then redeploy
git push origin main
```

### Connect to Strapi Backend

```bash
# In Vercel Dashboard → Environment Variables
REACT_APP_STRAPI_URL = "https://strapi.railway.app"
REACT_APP_STRAPI_API_TOKEN = "your_token"

# Redeploy
git push origin main
```

### Connect Firebase (Already Configured)

Firebase credentials are already in environment variables. Just verify:

```bash
# In Vercel Dashboard → Environment Variables
# Should see all REACT_APP_FIREBASE_* variables set

# Test connection:
# Open app → Check browser console
# Should see: "Firebase initialized successfully"
```

---

## 🐛 Troubleshooting

### Build Fails - Missing Dependencies

**Error**: `npm ERR! 404 Not Found - GET`

**Solution**:

```bash
# Update package.json to ensure all dependencies are listed
cd web/oversight-hub
npm install  # Installs any missing packages

# Commit and push
git add package-lock.json
git commit -m "Update dependencies"
git push origin feat/cost-optimization

# Redeploy from Vercel dashboard
```

### App Shows Blank Page

**Error**: Loads but shows nothing

**Solution**:

1. Check browser console for errors (F12)
2. Common causes:
   - Firebase not initialized
   - Environment variables not set
   - CORS issues from API

```javascript
// Debug in console:
console.log('Environment:', {
  apiUrl: process.env.REACT_APP_COFOUNDER_API_URL,
  strapiUrl: process.env.REACT_APP_STRAPI_URL,
  firebaseKey: process.env.REACT_APP_FIREBASE_API_KEY ? 'SET' : 'NOT SET',
});
```

### 404 Errors on Page Refresh

**Error**: Works on first load, 404 on refresh

**Solution**: Configure Vercel routing for SPA

```bash
# Create/Update: vercel.json in root
{
  "buildCommand": "npm run build --prefix web/oversight-hub",
  "outputDirectory": "web/oversight-hub/build",
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}

# Or in Vercel Dashboard:
# Settings → Git → Production Branch → Build settings
# Add env variable for proper SPA routing
```

### CORS Errors from API

**Error**: `Access to XMLHttpRequest blocked by CORS policy`

**Solution**:

```bash
# Your Railway backend needs CORS enabled
# Add to src/cofounder_agent/main.py:

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://oversight-hub.vercel.app",
        "http://localhost:3000",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redeploy to Railway:
git push origin feat/cost-optimization
railway up
```

### Environment Variables Show as Undefined

**Error**: `process.env.REACT_APP_* is undefined`

**Solution**:

1. Verify variables are set in Vercel Dashboard
2. Check they start with `REACT_APP_` (required for React apps)
3. Redeploy after adding variables

```bash
# In Vercel Dashboard:
# 1. Settings → Environment Variables
# 2. Check each variable
# 3. Make sure "Production" is selected
# 4. Trigger redeploy:

git push origin main  # or current branch
```

---

## 📊 Monitoring & Logging

### View Deployment Logs

```bash
# In Vercel Dashboard:
# 1. Deployments tab
# 2. Click specific deployment
# 3. View "Build Logs" tab
# 4. View "Runtime Logs" tab

# Or via CLI:
vercel logs <project-id>
```

### Monitor Performance

```bash
# In Vercel Dashboard:
# 1. Analytics tab
# 2. Track:
#    - Page views
#    - Bounce rate
#    - Core Web Vitals
#    - Error rate
```

### Set Up Error Alerts

```bash
# In Vercel Dashboard:
# 1. Settings → Integrations
# 2. Add Slack/Email integration
# 3. Enable alerts for:
#    - Build failure
#    - High error rate
#    - Performance issues
```

---

## 🔄 Updates & Redeployment

### Deploy Code Changes

```bash
# Make code changes locally
cd web/oversight-hub
# ... edit files ...

# Test locally (optional)
npm start
# Visit http://localhost:3000

# Commit
git add .
git commit -m "feat: Update admin dashboard"

# Push to deploy
git push origin feat/cost-optimization

# For production deploy:
git checkout main
git merge feat/cost-optimization
git push origin main
```

### Update Environment Variables

```bash
# In Vercel Dashboard:
# 1. Settings → Environment Variables
# 2. Edit variable and save
# 3. Auto-redeploy happens (or manually trigger)

# Or via CLI:
vercel env set MY_VAR "new_value"
```

### Rollback to Previous Deployment

```bash
# In Vercel Dashboard:
# 1. Deployments tab
# 2. Find previous good deployment
# 3. Click three-dots menu
# 4. Select "Promote to Production"

# Or via CLI:
vercel promote <deployment-id>
```

---

## 💡 Pro Tips

### 1. Use Preview Deployments for Testing

```bash
# Create feature branch and push
git checkout -b feature/my-feature
# ... make changes ...
git push origin feature/my-feature

# Vercel auto-creates preview deployment
# Check email for preview URL
# Test before merging to main

# When satisfied:
git checkout main
git merge feature/my-feature
git push origin main
# Vercel deploys to production
```

### 2. Use Environment-Specific Variables

In Vercel Dashboard → Environment Variables:

```
Preview environments (branches):
- REACT_APP_COFOUNDER_API_URL = http://localhost:8000

Production environment (main):
- REACT_APP_COFOUNDER_API_URL = https://cofounder.railway.app
```

### 3. Monitor Build Times

```bash
# In Vercel Dashboard:
# Go to Deployments → Select deployment
# Check "Build took X seconds"
# Target: < 60 seconds

# If slow:
# 1. Check for large dependencies
# 2. Use npm ci instead of npm install
# 3. Enable Image Optimization
```

### 4. Use Custom Domain

```bash
# In Vercel Dashboard:
# 1. Settings → Domains
# 2. Add domain: admin.gladlabs.ai
# 3. Point DNS to Vercel nameservers
# 4. Auto HTTPS enabled

# Access via: https://admin.gladlabs.ai
```

### 5. Enable Analytics

```bash
# In Vercel Dashboard:
# Settings → Analytics
# Enable Web Analytics to see:
# - Core Web Vitals
# - Performance metrics
# - Error tracking
```

---

## ✨ Success Indicators

After deployment, you should see:

✅ App accessible at `https://oversight-hub.vercel.app`  
✅ All pages load without errors  
✅ Firebase authentication works  
✅ Can connect to Railway backend successfully  
✅ Dashboard shows "Ready" status  
✅ No errors in deployment logs  
✅ Core Web Vitals are green

---

## 📚 Additional Resources

- **Vercel Docs**: https://vercel.com/docs
- **React CRA Docs**: https://create-react-app.dev
- **Firebase Docs**: https://firebase.google.com/docs
- **Vercel Analytics**: https://vercel.com/analytics

---

## 🎯 Next Steps

1. ✅ Set up Vercel account
2. ✅ Connect GitHub repository
3. ✅ Configure environment variables
4. ✅ Deploy Oversight Hub as separate project
5. ✅ Verify deployment works
6. ✅ Connect to Railway backend
7. ✅ Set up monitoring & alerts
8. ✅ Configure custom domain (optional)

**Status**: Ready to deploy! 🚀
