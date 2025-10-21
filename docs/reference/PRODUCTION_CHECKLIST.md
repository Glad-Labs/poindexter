# 🚀 Production Deployment Checklist - Quick Reference

**Status**: Ready to Deploy  
**Updated**: October 20, 2025

---

## ✅ Pre-Deployment Setup (Do Once)

### GitHub Secrets Configuration

- [ ] **Vercel Secrets**
  - [ ] `VERCEL_TOKEN` - Personal access token from Vercel
  - [ ] `VERCEL_ORG_ID` - Your Vercel organization ID
  - [ ] `VERCEL_PROJECT_ID_PUBLIC` - Public site project ID
  - [ ] `VERCEL_PROJECT_ID_OVERSIGHT` - Oversight Hub project ID

- [ ] **Railway Secrets**
  - [ ] `RAILWAY_TOKEN` - API token from Railway
  - [ ] `RAILWAY_PROJECT_ID` - Project ID for Strapi
  - [ ] `DATABASE_URL_PRODUCTION` - Production PostgreSQL URL
  - [ ] `DATABASE_URL_STAGING` - Staging PostgreSQL URL

- [ ] **Strapi Secrets**
  - [ ] `STRAPI_API_TOKEN` - Full access API token from Strapi admin

- [ ] **AI Model Secrets**
  - [ ] `OPENAI_API_KEY` - OpenAI API key
  - [ ] `ANTHROPIC_API_KEY` - Anthropic API key
  - [ ] `GOOGLE_AI_API_KEY` - Google Gemini API key
  - [ ] `XAI_API_KEY` - X AI API key
  - [ ] `META_API_KEY` - Meta Llama API key (if needed)

- [ ] **GCP Secrets** (if using Cloud Functions)
  - [ ] `GCP_SERVICE_ACCOUNT_KEY` - Service account JSON key
  - [ ] `GCP_PROJECT_ID` - GCP project ID

### Environment Files Configuration

- [ ] `.env` created with local development variables
- [ ] `.env.staging` created with staging variables
- [ ] `.env.production` created with production variables
- [ ] All API keys and database URLs filled in
- [ ] `.env*` files added to `.gitignore`

---

## 🔄 Branch Workflow (For Each Release)

### Step 1: Feature Development

```bash
# On: feat/test-branch

npm run test          # ← Must pass
npm run lint:fix      # ← Must pass
npm run build         # ← Must succeed

git add .
git commit -m "feat: [description]"
git push origin feat/test-branch
```

**Checklist:**

- [ ] Local tests pass
- [ ] No linting errors
- [ ] Build completes successfully
- [ ] No console errors in browser
- [ ] GitHub Actions test-on-feat.yml passes

### Step 2: Staging Deployment

```bash
# Merge: feat/test-branch → dev

git checkout dev
git pull origin dev
git merge feat/test-branch
git push origin dev
```

**Checklist:**

- [ ] GitHub Actions deploy-staging.yml triggers
- [ ] Wait 2-3 minutes for deployment
- [ ] Visit staging URL and test all features
- [ ] Check logs for errors (Railway dashboard)
- [ ] Verify Strapi content loads
- [ ] All functionality working

### Step 3: Production Deployment

```bash
# Merge: dev → main

git checkout main
git pull origin main
git merge --no-ff dev
git push origin main
```

**Checklist:**

- [ ] GitHub Actions deploy-production.yml triggers
- [ ] Wait 5-10 minutes for full deployment
- [ ] Vercel deployment completes
- [ ] Railway deployment completes
- [ ] GCP functions deployed successfully

---

## ✅ Pre-Release Verification

**Local Testing (Before Push to Main):**

```bash
# 1. Full test suite
npm run test
# Expected: ✅ All tests pass

# 2. Linting
npm run lint:fix
# Expected: ✅ No errors

# 3. Build
npm run build
# Expected: ✅ Build succeeds

# 4. Dev server
npm run dev
# Expected: ✅ All 4 services start
#           ✅ No console errors
#           ✅ Pages load correctly
```

- [ ] All npm scripts pass
- [ ] Dev server runs without errors
- [ ] Frontend pages load correctly
- [ ] Strapi admin accessible
- [ ] API responds without errors
- [ ] Python agent responds

---

## 🚀 Production Deployment Verification

**Immediately After Deploy:**

### Frontend Verification

- [ ] https://gladlabs.io loads
- [ ] All pages accessible (/, /about, /privacy-policy)
- [ ] No console errors in DevTools
- [ ] Network requests all 2xx/3xx
- [ ] Mobile responsive view works
- [ ] Images load correctly

### Backend Verification

- [ ] Strapi admin loads at https://glad-labs-website-production.up.railway.app/admin
- [ ] API documentation accessible at /api/docs
- [ ] Strapi content endpoints respond
- [ ] No 404 or 500 errors in logs
- [ ] Database connections working

### Agent Verification

- [ ] FastAPI endpoint responds (GCP function)
- [ ] Health check passes
- [ ] No errors in GCP Cloud Logging
- [ ] Model inference working

### Monitoring

- [ ] Vercel Analytics dashboard shows traffic
- [ ] Railway logs show clean deployments
- [ ] No error alerts triggered
- [ ] All health checks passing

---

## 🦙 Ollama Setup (Local Development)

**Install Ollama:**

```bash
# Download from https://ollama.ai
# Or: brew install ollama (macOS)
# Or: apt install ollama (Linux)
```

- [ ] Ollama installed (`ollama --version`)
- [ ] Ollama service running (`ollama serve`)

**Configure FastAPI:**

**.env (Local Development):**

```
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama2
```

- [ ] `.env` configured for Ollama
- [ ] `npm run dev` starts successfully
- [ ] Ollama responds to requests

**Test Ollama:**

```bash
# Pull a model
ollama pull llama2

# Test it
curl http://localhost:11434/api/generate -d '{
  "model": "llama2",
  "prompt": "Hello",
  "stream": false
}'
```

- [ ] Model pulled successfully
- [ ] API responds to requests
- [ ] FastAPI agent can use Ollama

---

## 🔍 Monitoring & Logging (Post-Deployment)

### Dashboards to Monitor

- [ ] **Vercel**: https://vercel.com/dashboard
  - Check deployment status
  - Monitor traffic patterns
  - Review error rates

- [ ] **Railway**: https://railway.app
  - Check build and deployment status
  - Review application logs
  - Monitor resource usage

- [ ] **GitHub Actions**: https://github.com/mattg-stack/glad-labs-website/actions
  - Verify latest workflow passed
  - Check deploy-production.yml logs

### Key Metrics to Track

- [ ] Page load times < 2 seconds
- [ ] API response times < 500ms
- [ ] Error rate < 0.1%
- [ ] Uptime > 99.5%
- [ ] Database connections healthy

---

## 🚨 Rollback Plan (If Issues Occur)

**Quick Rollback:**

```bash
git checkout main
git revert HEAD
git push origin main
# GitHub Actions automatically redeploys previous version
```

- [ ] Previous version deployed
- [ ] Production restored to stable state
- [ ] Verify previous version working

**Or Specific Rollback:**

```bash
git log --oneline
git revert [COMMIT_HASH]
git push origin main
```

---

## 📋 Post-Deployment Checklist

**24 Hours After Deploy:**

- [ ] No error spikes in logs
- [ ] No user complaints or issues
- [ ] Performance metrics stable
- [ ] All integrations working

**1 Week After Deploy:**

- [ ] Traffic patterns normal
- [ ] No recurring errors
- [ ] Database performing well
- [ ] Users happy with new features

---

## 🔗 Related Documentation

- **Full Guide**: [Production Deployment Ready](./PRODUCTION_DEPLOYMENT_READY.md)
- **Branch Setup**: [Branch Setup Complete](./guides/BRANCH_SETUP_COMPLETE.md)
- **CI/CD Reference**: [CI/CD Complete](./reference/CI_CD_COMPLETE.md)
- **Deployment Guide**: [Deployment & Infrastructure](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)

---

## 🎯 Next Steps

1. **Configure GitHub Secrets** - Follow "GitHub Secrets Configuration" above
2. **Update `.env*` files** - Fill in all API keys and URLs
3. **Run local tests** - Verify everything passes
4. **Deploy to staging** - Push to `dev` branch
5. **Test staging** - Verify all features work
6. **Deploy to production** - Push to `main` branch
7. **Monitor deployment** - Watch GitHub Actions
8. **Verify production** - Test live site
9. **Setup Ollama** - For local LLM development

---

**Ready to deploy? Start with Step 1 above! 🚀**
