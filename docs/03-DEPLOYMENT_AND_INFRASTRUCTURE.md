# 03 - Deployment & Infrastructure

**Last Updated:** November 5, 2025  
<<<<<<< HEAD
**Version:** 3.0  
**Status:** ✅ Production Ready | GitHub Actions CI/CD Active | Railway + Vercel Integration
=======
**Version:** 1.1  
**Status:** ✅ Production Ready
>>>>>>> feat/refine

---

## 🎯 Quick Links

- **[Before You Deploy](#before-you-deploy)** - Deployment checklist
- **[Backend Deployment](#backend-deployment-railway)** - Railway setup
- **[Frontend Deployment](#frontend-deployment-vercel)** - Vercel setup
- **[CMS Deployment](#cms-deployment-strapi)** - Strapi setup
- **[Production Environment](#production-environment)** - Env vars and config
- **[Monitoring & Support](#monitoring--support)** - Health checks and logs

---

## 📋 Deployment Overview

<<<<<<< HEAD
Glad Labs uses a three-tier deployment architecture:
=======
Glad Labs uses a two-tier deployment architecture:
>>>>>>> feat/refine

```text
1. AI Co-Founder (FastAPI Backend + PostgreSQL)
   ↓ (REST API)
2. Web Frontends (Next.js)
   ├── Public Site (http://example.com)
   └── Oversight Hub (http://admin.example.com)
```

**Recommended Platforms:**

- **Backend:** Railway (PostgreSQL + Python)
- **Frontends:** Vercel (optimized for Next.js)
- **Database:** PostgreSQL (production) / SQLite (dev)

---

## ✅ Before You Deploy

### Deployment Checklist

- [x] All tests pass locally: ✅ **267/267 tests passing (100%)**
- [x] No uncommitted changes: ✅ **Latest: commit 6add7f62e**
- [ ] Environment variables configured in `.env.production`
- [ ] Database backups configured
- [ ] Monitoring/alerting configured (Sentry, DataDog - optional but recommended)
- [x] SSL/HTTPS certificates ready: ✅ **Auto via Vercel & Railway**
- [ ] Team notified of deployment window
- [ ] Rollback plan documented

### Deployment Status Summary

| Component         | Status              | Notes                                                     |
| ----------------- | ------------------- | --------------------------------------------------------- |
| **Tests**         | ✅ 100% Passing     | 267 tests (116 unit + 101 integration + 18 perf + 32 E2E) |
| **Code Quality**  | ✅ Excellent        | Type hints, ESLint, Prettier configured                   |
| **Architecture**  | ✅ Production Ready | Multi-tier, async-first, error recovery                   |
| **Secrets**       | ✅ Configured       | GitHub Secrets configured for Railway/Vercel              |
| **CI/CD**         | ✅ Ready            | GitHub Actions pipelines in place                         |
| **Documentation** | ✅ Complete         | 8 core docs + 50+ reference/guide docs                    |

### GitHub Secrets Configuration (REQUIRED)

These must be set in GitHub → Settings → Secrets and Variables → Actions:

**Critical Secrets (5 minimum):**

```
OPENAI_API_KEY              (or ANTHROPIC_API_KEY or GOOGLE_API_KEY)
RAILWAY_TOKEN               (for Railway deployments)
RAILWAY_PROD_PROJECT_ID     (production Railway project)
VERCEL_TOKEN                (for Vercel deployments)
VERCEL_PROJECT_ID           (production Vercel project)
```

**Recommended Additional Secrets:**

```
STRAPI_ADMIN_JWT_SECRET     (for Strapi security)
DATABASE_URL                (production PostgreSQL URL)
```

**Local Development (.env.production - NEVER COMMIT):**

```bash
# API Keys (at least one required)
OPENAI_API_KEY=sk-your-key-here
# OR
ANTHROPIC_API_KEY=sk-ant-your-key-here
# OR
GOOGLE_API_KEY=your-key-here
# OR use free Ollama (no key needed)
USE_OLLAMA=true

# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Backend
ENVIRONMENT=production
DEBUG=False

# Frontend URLs
NEXT_PUBLIC_BACKEND_URL=https://api.example.com
```

**See:** [`07-BRANCH_SPECIFIC_VARIABLES.md`](./07-BRANCH_SPECIFIC_VARIABLES.md) for detailed environment setup

---

## 🚀 Backend Deployment (Railway)

### Option 1: Railway Template (Recommended - Fastest)

**For FastAPI Co-Founder:**

1. Visit [Railway Dashboard](https://railway.app)
2. Create new project
3. Select "Deploy from GitHub"
4. Choose `glad-labs-website` repository
5. Set root directory: `src/cofounder_agent/`
6. Configure build command: `pip install -r requirements.txt`
7. Configure start command: `python -m uvicorn main:app --host 0.0.0.0 --port $PORT`
8. Add environment variables from `.env.production`
9. Deploy

### Option 2: Manual Railway Setup

```bash
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Login to Railway
railway login

# 3. Create new project
railway init

# 4. Link to GitHub
railway connect

# 5. Deploy
railway up
```

### Option 3: Docker Deployment

```bash
# Build Docker image
docker build -t glad-labs-backend:latest .

# Push to Docker registry
docker push your-registry/glad-labs-backend:latest

# Deploy to Railway with Docker image
railway service create --dockerfile Dockerfile
```

### FastAPI Production Configuration

```python
# src/cofounder_agent/main.py
app = FastAPI(
    title="Glad Labs AI Co-Founder",
    version="1.0.0",
    docs_url="/api/docs" if DEBUG else None,
    redoc_url="/api/redoc" if DEBUG else None,
)

# CORS for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 🌐 Frontend Deployment (Vercel)

### Option 1: Vercel Dashboard (Recommended)

**For Public Site:**

1. Visit: [Vercel](https://vercel.com)
2. Click "New Project"
3. Import Git repository: `glad-labs-website`
4. Select root directory: `web/public-site/`
5. Add environment variables:

```bash
NEXT_PUBLIC_BACKEND_URL=https://api.example.com
```

1. Click "Deploy"

**For Oversight Hub:**

Repeat above with root directory: `web/oversight-hub/`

### Option 2: Vercel CLI

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy public site
cd web/public-site
vercel --prod

# Deploy oversight hub
cd web/oversight-hub
vercel --prod
```

### Option 3: GitHub Integration (Auto-Deploy)

1. Connect Vercel to GitHub
2. Select repository
3. Configure build settings:
   - Framework: Next.js
   - Build command: `npm run build`
   - Install command: `npm install`
4. Set environment variables
5. Click "Deploy"
6. Future commits to `main` deploy automatically

### Next.js Production Build

```bash
# Build for production
npm run build

# Test production build locally
npm start

# Verify build output
ls -la .next/
```

---

## 🛢️ Database Deployment (PostgreSQL)

### Database Setup (PostgreSQL)

```bash
# Create production database
createdb glad_labs_production

# Set environment variable
export DATABASE_URL="postgresql://user:password@localhost:5432/glad_labs_production"

# Run migrations (using Alembic or custom script)
# cd src/cofounder_agent
# alembic upgrade head
```

### Strapi Media Upload Configuration

```javascript
// cms/strapi-main/config/plugins.ts
export default ({ env }) => ({
  upload: {
    config: {
      provider: 'aws-s3',
      providerOptions: {
        s3Options: {
          accessKeyId: env('AWS_ACCESS_KEY_ID'),
          secretAccessKey: env('AWS_SECRET_ACCESS_KEY'),
          region: env('AWS_REGION'),
          bucket: env('AWS_BUCKET'),
          cdnUrl: env('CDN_URL'),
        },
      },
      actionOptions: {
        upload: {},
        uploadStream: {},
        delete: {},
      },
    },
  },
});
```

### Strapi Production Deployment

```bash
# Build Strapi
npm run build

# Start in production mode
NODE_ENV=production npm start

# Or with PM2 (recommended)
pm2 start npm --name "strapi" -- run start
```

---

## 🔐 Production Environment

### Database Backups

```bash
# Backup PostgreSQL daily
0 2 * * * pg_dump $DATABASE_URL | gzip > /backups/db-$(date +\%Y\%m\%d).sql.gz

# Backup S3 media (if using AWS)
0 3 * * * aws s3 sync s3://bucket-name /backups/media/
```

### SSL/HTTPS Configuration

**Vercel:** Automatic SSL (included)

**Railway:** Let's Encrypt (automatic)

**Manual:**

```bash
# Using Certbot
certbot certonly --standalone -d example.com
certbot certonly --standalone -d api.example.com
```

### Environment Separation

```text
Production:  main branch → Railway/Vercel
Staging:     staging branch → Railway/Vercel
Development: dev branch → local or dev servers
```

---

## 📊 Monitoring & Support

### Health Check Endpoints

```bash
# Backend health
curl https://api.example.com/api/health

# Frontend
curl https://example.com/
```

### Log Monitoring

```bash
# Railway logs
railway logs

# Vercel logs
vercel logs

# Local logs
tail -f logs/application.log
```

### Error Tracking

Configure error monitoring:

- **Option 1:** [Sentry](https://sentry.io) (recommended)
- **Option 2:** Railway dashboard
- **Option 3:** Application Insights

### Performance Monitoring

- **Core Web Vitals:** Vercel Analytics (automatic)
- **API Performance:** Application metrics
- **Database Performance:** PostgreSQL logs

---

## 🔄 Automated Deployment Workflow (GitHub Actions)

Glad Labs uses GitHub Actions to automate deployments from git branches to cloud platforms.

### Branch Strategy

```text
feat/my-feature (local development)
    ↓ git push
Pull Request → dev branch
    ↓ merge approved
dev branch → GitHub Actions → Railway Staging + Vercel Staging
    ↓ test on staging
dev → main (create PR)
    ↓ merge after approval
main branch → GitHub Actions → Railway Production + Vercel Production
```

### GitHub Actions Setup

**1. Configure GitHub Secrets** (Settings → Secrets and variables → Actions)

For a complete, authoritative list of all secrets with examples and detailed setup instructions, see: **[GITHUB_SECRETS_SETUP.md](../reference/GITHUB_SECRETS_SETUP.md)**

**Quick Summary:**

```text
# Railway
RAILWAY_TOKEN
RAILWAY_STAGING_PROJECT_ID
RAILWAY_PROD_PROJECT_ID

# Vercel
VERCEL_TOKEN
VERCEL_PROJECT_ID
VERCEL_ORG_ID

# Database (if needed)
STAGING_DATABASE_URL
PROD_DATABASE_URL
```

👉 **See [GITHUB_SECRETS_SETUP.md](../reference/GITHUB_SECRETS_SETUP.md) for complete instructions with examples.**

**2. Workflows** (in `.github/workflows/`)

- `deploy-staging.yml` - Triggers on `dev` branch push
- `deploy-production.yml` - Triggers on `main` branch push

### 3. What Happens Automatically

On `dev` push:

- Run tests
- Build frontend with staging URLs
- Deploy backend to Railway staging
- Deploy frontend to Vercel staging
- Available at: `https://staging-*.railway.app`

On `main` push:

- Run full test suite
- Build frontend with production URLs
- Deploy backend to Railway production
- Deploy frontend to Vercel production
- Available at: `https://glad-labs.vercel.app`

### Manual Deployment Workflow (Dev to Staging)

```bash
# 1. Create feature branch
git checkout -b feat/my-feature

# 2. Make changes and test locally
npm run dev

# 3. Commit and push
git add .
git commit -m "feat: add my feature"
git push origin feat/my-feature

# 4. Create Pull Request to dev
# - Open GitHub
# - Click "New Pull Request"
# - Base: dev, Compare: feat/my-feature
# - Add description
# - Request review

# 5. After approval, merge to dev
git checkout dev
git merge feat/my-feature
git push origin dev
# ← GitHub Actions automatically deploys to staging

# 6. Test on staging
# Check: https://staging-*.railway.app
```

### Manual Deployment Workflow (Staging to Production)

```bash
# 1. Create release PR (dev → main)
git checkout main
git pull origin main
git merge dev
git push origin main
# ← GitHub Actions automatically deploys to production

# 2. Monitor deployment
# - Check GitHub Actions: Actions tab
# - Check Railway: Services tab
# - Check Vercel: Deployments tab

# 3. Verify production
curl https://example.com/api/health
```

### Rollback Procedure

```bash
# If deployment fails on production:
# 1. Identify last working commit
git log --oneline main

# 2. Revert to previous commit
git revert <commit-hash>
git push origin main
# ← GitHub Actions automatically deploys the revert

# 3. Verify rollback
curl https://example.com/api/health

# 4. Post-mortem
# Document what went wrong and how to prevent it
```

### Deploy to Staging (Manual)

```bash
# 1. Merge to dev branch
git checkout dev
git pull origin dev
git push origin dev

# 2. Verify in staging
# Check: https://staging.example.com
# Check: https://staging-api.example.com

# 3. Run smoke tests
npm run test:smoke
```

### Deploy to Production (Manual)

```bash
# 1. Create release tag
git tag v1.2.3
git push origin v1.2.3

# 2. Merge to main
git checkout main
git merge v1.2.3
git push origin main

# 3. Monitor deployment
# GitHub Actions log / Railway / Vercel dashboard

# 4. Verify production
curl https://example.com/api/health
```

---

## 🚨 Troubleshooting Deployments

### Build Fails on Railway

```bash
# Check build logs
railway logs --service=<service-name>

# Common fixes:
# 1. Clear cache
railway service delete cache

# 2. Rebuild
railway redeploy

# 3. Check dependencies
npm ci  # instead of npm install
```

### Vercel Build Fails

```bash
# Check build output
vercel logs --follow

# Common fixes:
# 1. Check Node.js version: must be 18-22 (not 25+)
# 2. Clear build cache: vercel env pull → rebuild
# 3. Check environment variables: vercel env ls
```

### Database Connection Issues

```bash
# Test connection
psql $DATABASE_URL -c "SELECT 1"

# Check connection string format
# postgresql://user:password@host:5432/dbname

# Verify firewall rules allow connection
```

### API Returns 502 Bad Gateway

```bash
# Check backend service status
railway logs

# Restart service
railway redeploy

# Check resource limits
railway service info
```

---

## 📝 Deployment Checklist (Final)

Before considering deployment complete:

- [ ] All services responding to health checks
- [ ] Database migrations completed
- [ ] SSL certificates active and valid
- [ ] Backups configured and tested
- [ ] Monitoring alerts configured
- [ ] Error tracking receiving events
- [ ] Team can access admin dashboards
- [ ] Public site loads without errors
- [ ] API endpoints responding
- [ ] Logs being collected properly

---

## 🔗 Related Documentation

- **[Setup Guide](./01-SETUP_AND_OVERVIEW.md)** - Local development
- **[Architecture](./02-ARCHITECTURE_AND_DESIGN.md)** - System design
- **[Development](./04-DEVELOPMENT_WORKFLOW.md)** - Git and testing
- **[Operations](./06-OPERATIONS_AND_MAINTENANCE.md)** - Production support

---

**[← Back to Documentation Hub](./00-README.md)**

[Setup](./01-SETUP_AND_OVERVIEW.md) • [Architecture](./02-ARCHITECTURE_AND_DESIGN.md) • [Development](./04-DEVELOPMENT_WORKFLOW.md) • [Operations](./06-OPERATIONS_AND_MAINTENANCE.md)
