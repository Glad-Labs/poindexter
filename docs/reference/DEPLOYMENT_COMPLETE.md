# Complete Deployment & Configuration Guide

**Consolidates:** DEPLOYMENT_GATES.md, STRAPI_ARCHITECTURE_CORRECTION.md, VERCEL_CONFIG_FIX.md, CODEBASE_UPDATE_SUMMARY_OCT20.md

**Date:** October 20, 2025  
**Status:** ✅ Production Ready

---

## 🎯 Overview

Complete guide for deploying GLAD Labs to production, including:

- Pre-deployment validation checklist
- Strapi-backed page architecture
- Vercel configuration
- Production readiness verification

---

## 🏗️ Strapi-Backed Page Architecture

Your pages are configured as **Strapi-backed with markdown fallbacks**, which means:

✅ Content is managed in Strapi (not in code)  
✅ Pages have markdown fallbacks for Strapi downtime  
✅ ISR (Incremental Static Regeneration) updates every 60 seconds  
✅ SEO-ready with metadata support

### Configured Pages

1. **`/about`** → `pages/about.js`
   - Fetches from Strapi `/api/about`
   - Content type: Page with markdown content
   - Fallback: Comprehensive about page in markdown
   - Next.js ISR: Revalidates every 60 seconds

2. **`/privacy-policy`** → `pages/privacy-policy.js`
   - Fetches from Strapi `/api/privacy-policy`
   - Content type: Legal page with compliance terms
   - Fallback: GDPR/CCPA compliant markdown
   - Next.js ISR: Revalidates every 60 seconds

3. **`/terms-of-service`** → `pages/terms-of-service.js`
   - Fetches from Strapi `/api/terms-of-service`
   - Content type: Legal page with usage terms
   - Fallback: Comprehensive legal terms markdown
   - Next.js ISR: Revalidates every 60 seconds

### Implementation Pattern

All page files follow this pattern:

```javascript
// pages/about.js
export async function getStaticProps() {
  try {
    const data = await fetchAPI('/api/about?populate=*');
    return {
      props: { data },
      revalidate: 60, // ISR: revalidate every 60 seconds
    };
  } catch (error) {
    // Fallback to markdown if Strapi is down
    return {
      props: { data: null },
      revalidate: 60,
    };
  }
}

export default function Page({ data }) {
  const content = data?.content || fallbackMarkdownContent;
  return <ReactMarkdown>{content}</ReactMarkdown>;
}
```

### Critical: 10-Second Timeout Protection

The `lib/api.js` fetchAPI function includes **essential 10-second timeout**:

```javascript
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 10000); // 10 seconds
```

**Why:** Without this timeout, Vercel builds hang if Strapi is slow or down.

---

## 🚀 Vercel Configuration

### Configuration File: `vercel.json`

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "projectId": "your-project-id",
  "orgId": "your-org-id",

  "buildCommand": "npm run build",
  "devCommand": "npm run dev",

  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        {
          "key": "X-Content-Type-Options",
          "value": "nosniff"
        },
        {
          "key": "X-Frame-Options",
          "value": "DENY"
        },
        {
          "key": "X-XSS-Protection",
          "value": "1; mode=block"
        }
      ]
    }
  ],

  "redirects": [
    {
      "source": "/api/:path*",
      "destination": "https://api.glad-labs.com/:path*"
    }
  ],

  "rewrites": [
    {
      "source": "/docs",
      "destination": "/docs/index.html"
    }
  ]
}
```

### Environment Variables in Vercel

**Set in Vercel Dashboard** (not in `vercel.json`):

```
NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
NEXT_PUBLIC_STRAPI_API_TOKEN=your-token-here
NEXT_PUBLIC_GA_ID=your-ga-id
```

**Why separate:** Environment variables shouldn't be in code/config for security.

### Build Settings

- **Framework:** Next.js
- **Build Command:** `npm run build`
- **Output Directory:** `.next`
- **Node Version:** 18.x
- **Package Manager:** npm

---

## 📋 Pre-Deployment Checklist

### Code Quality

- [ ] All unit tests passing: `npm run test`
- [ ] No linting errors: `npm run lint`
- [ ] No TypeScript errors: `npm run build`
- [ ] Code formatting correct: `npm run format:check`
- [ ] Coverage above 70%

### Performance

- [ ] Build size under 500KB
- [ ] Lighthouse scores:
  - [ ] Performance > 80
  - [ ] Accessibility > 80
  - [ ] Best Practices > 80
  - [ ] SEO > 80
- [ ] Page load time < 3 seconds
- [ ] Core Web Vitals:
  - [ ] LCP (Largest Contentful Paint) < 2.5s
  - [ ] FID (First Input Delay) < 100ms
  - [ ] CLS (Cumulative Layout Shift) < 0.1

### Functional Testing

- [ ] All pages render: `/`, `/about`, `/privacy-policy`, `/terms-of-service`
- [ ] Navigation links work
- [ ] API integration working:
  - [ ] Blog posts load
  - [ ] Categories filter
  - [ ] Tags display
- [ ] Contact form submits
- [ ] Error pages (404, 500) display properly

### Strapi/Backend

- [ ] Strapi running on production
- [ ] PostgreSQL database connected
- [ ] All content type endpoints responding
- [ ] CORS headers configured
- [ ] Rate limiting enabled
- [ ] API tokens valid and secure

### Deployment Configuration

- [ ] `vercel.json` configured correctly
- [ ] Environment variables set in Vercel dashboard
- [ ] `.env.production` configured locally
- [ ] GitHub Actions workflows working
- [ ] Railway deployment successful

### Security

- [ ] No hardcoded secrets in code
- [ ] Environment variables not in `vercel.json`
- [ ] Security headers configured
- [ ] CORS whitelist configured
- [ ] API tokens rotated if needed
- [ ] SSL/TLS enabled

### Monitoring

- [ ] Error tracking configured (Sentry, etc.)
- [ ] Analytics configured (Google Analytics, etc.)
- [ ] Log aggregation set up (if needed)
- [ ] Performance monitoring enabled
- [ ] Uptime monitoring configured

---

## 🔄 Deployment Process

### Local Testing

```bash
# 1. Load production environment
npm run env:select  # Should select .env.production

# 2. Run full test suite
npm run test

# 3. Build for production
npm run build

# 4. Verify build output
ls -la .next/
```

### Stage to Dev Branch

```bash
# 1. Create feature branch
git checkout -b feat/deployment-prep

# 2. Make changes (if needed)

# 3. Push to feature branch
git push origin feat/deployment-prep

# 4. Wait for test-on-feat.yml to pass

# 5. Create PR to dev
# 6. After review, merge to dev
```

### Deploy to Staging

```bash
# 1. Merge to dev
git checkout dev
git merge feat/deployment-prep
git push origin dev

# 2. GitHub Actions: deploy-staging.yml runs
# 3. Wait for deployment to Railway staging
# 4. Test on: https://staging-cms.railway.app

# 5. Verify all functionality works
# 6. Check error logs
```

### Deploy to Production

```bash
# 1. Create PR: dev → main
# 2. Final review

# 3. Merge to main
git checkout main
git merge --no-ff dev
git push origin main

# 4. GitHub Actions: deploy-production.yml runs
# 5. Frontend deploys to Vercel
# 6. Backend deploys to Railway production

# 7. Monitor deployment
# 8. Verify live site
```

---

## 🔍 Post-Deployment Verification

### Frontend (Vercel)

```bash
# 1. Check Vercel dashboard
# https://vercel.com/dashboard

# 2. Verify deployment succeeded
# Status should be "Ready"

# 3. Visit production URL
# https://glad-labs.vercel.app

# 4. Test all pages load:
curl https://glad-labs.vercel.app/
curl https://glad-labs.vercel.app/about
curl https://glad-labs.vercel.app/privacy-policy
curl https://glad-labs.vercel.app/terms-of-service

# 5. Check browser console for errors
# No red errors in DevTools
```

### Backend (Railway)

```bash
# 1. Check Railway dashboard
# https://railway.app/dashboard

# 2. Verify Strapi is running
# Status should be "Active"

# 3. Test Strapi API
curl https://cms.railway.app/api/about
curl https://cms.railway.app/api/privacy-policy
curl https://cms.railway.app/api/terms-of-service

# 4. Check database connection
# Verify PostgreSQL is connected

# 5. Monitor logs for errors
```

### Integration

```bash
# 1. Verify Strapi content loads on frontend
# Visit https://glad-labs.vercel.app/about
# Should show content from Strapi

# 2. Check API timeout is working
# Temporarily shut down Strapi
# Pages should still load with markdown fallback

# 3. Test ISR revalidation
# Update content in Strapi
# Wait 60 seconds
# Refresh page should show new content
```

---

## 🐛 Troubleshooting Deployments

### Vercel Build Hangs

**Cause:** Missing 10-second timeout in API calls

**Fix:**

```bash
# Check lib/api.js has timeout:
grep -A 5 "AbortController" web/public-site/lib/api.js

# Should show:
# const controller = new AbortController();
# const timeout = setTimeout(() => controller.abort(), 10000);
```

### Pages Show 404

**Cause:** Strapi endpoints not configured or down

**Fix:**

```bash
# 1. Verify Strapi is running
curl https://cms.railway.app/api/about

# 2. Check Vercel env vars
# Go to: Vercel Dashboard → Settings → Environment Variables
# Verify: NEXT_PUBLIC_STRAPI_API_URL is correct

# 3. Check Railway database
# Railway Dashboard → check PostgreSQL status
```

### Content Not Updating

**Cause:** ISR revalidation not working

**Fix:**

```bash
# 1. Verify revalidate is set in getStaticProps
grep -n "revalidate" web/public-site/pages/about.js

# Should show: revalidate: 60

# 2. Wait 60 seconds after updating Strapi content
# 3. Hard refresh page (Ctrl+Shift+R or Cmd+Shift+R)
# 4. Should show new content
```

### Build Fails on Vercel

**Cause:** Environment variables missing

**Fix:**

```bash
# 1. Check Vercel dashboard
# Settings → Environment Variables

# 2. Ensure these are set:
# NEXT_PUBLIC_STRAPI_API_URL
# NEXT_PUBLIC_STRAPI_API_TOKEN
# NEXT_PUBLIC_GA_ID (if using GA)

# 3. Re-run deployment
# Dashboard → Redeploy
```

---

## 📊 Environment Configuration

### Local (.env)

```
NODE_ENV=development
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
DATABASE_CLIENT=sqlite
ENABLE_DEBUG_LOGS=true
```

### Staging (.env.staging)

```
NODE_ENV=staging
NEXT_PUBLIC_STRAPI_API_URL=https://staging-cms.railway.app
DATABASE_CLIENT=postgres
DATABASE_NAME=glad_labs_staging
```

### Production (.env.production)

```
NODE_ENV=production
NEXT_PUBLIC_STRAPI_API_URL=https://cms.railway.app
DATABASE_CLIENT=postgres
DATABASE_NAME=glad_labs_production
```

---

## ✅ Deployment Success Criteria

✅ **Frontend**

- Vercel deployment successful
- All pages load
- No console errors
- Performance acceptable

✅ **Backend**

- Railway deployment successful
- PostgreSQL connected
- API responding
- Logs clean

✅ **Integration**

- Content loads from Strapi
- Fallback works if Strapi down
- ISR revalidation working
- No timeout errors

✅ **Monitoring**

- Error tracking operational
- Analytics working
- Logs accessible
- Uptime monitoring active

---

## 📚 Related Documentation

- **`docs/04-DEVELOPMENT_WORKFLOW.md`** - Development process
- **`docs/07-BRANCH_SPECIFIC_VARIABLES.md`** - Environment setup
- **`docs/guides/BRANCH_SETUP_COMPLETE.md`** - Branch workflows
- **`docs/reference/CI_CD_COMPLETE.md`** - CI/CD pipelines
- **`.github/workflows/`** - GitHub Actions automation

---

## 🚀 You're Ready for Production!

**Summary:**

- Strapi-backed pages with fallbacks ✅
- Vercel configuration optimized ✅
- Pre-deployment checklist complete ✅
- Deployment process documented ✅
- Verification procedures ready ✅

**Next Step:** Push to main and deploy! 🚀

---

**Status:** ✅ Production Ready  
**Last Updated:** October 20, 2025
