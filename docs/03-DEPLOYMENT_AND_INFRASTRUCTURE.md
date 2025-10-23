# 03 - Deployment & Infrastructure

**Last Updated:** October 22, 2025  
**Version:** 1.0  
**Status:** ✅ Production Ready

---

## 🎯 Quick Links

- **[Before You Deploy](#-before-you-deploy)** - Deployment checklist
- **[Backend Deployment](#-backend-deployment)** - Railway setup
- **[Frontend Deployment](#-frontend-deployment)** - Vercel setup
- **[CMS Deployment](#-cms-deployment)** - Strapi setup
- **[Production Environment](#-production-environment)** - Env vars and config
- **[Monitoring & Support](#-monitoring--support)** - Health checks and logs

---

## 📋 Deployment Overview

GLAD Labs uses a three-tier deployment architecture:

```text
1. CMS Backend (Strapi v5)
   ↓ (REST API)
2. AI Co-Founder (FastAPI Backend)
   ↓ (REST API)
3. Web Frontends (Next.js)
   ├── Public Site (http://example.com)
   └── Oversight Hub (http://admin.example.com)
```

**Recommended Platforms:**
- **Backend:** Railway (PostgreSQL + Python/Node.js)
- **Frontends:** Vercel (optimized for Next.js)
- **Database:** PostgreSQL (production) / SQLite (dev)

---

## ✅ Before You Deploy

### Deployment Checklist

- [ ] All tests pass locally: `npm test && pytest src/`
- [ ] No uncommitted changes: `git status` is clean
- [ ] Environment variables configured in `.env.production`
- [ ] Database backups configured
- [ ] Monitoring/alerting configured
- [ ] SSL/HTTPS certificates ready
- [ ] Team notified of deployment window
- [ ] Rollback plan documented

### Required Secrets

Create `.env.production` with:

```bash
# API Keys (at least one required)
OPENAI_API_KEY=sk-your-key-here
ANTHROPIC_API_KEY=sk-ant-your-key-here
GOOGLE_API_KEY=your-key-here

# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Strapi
ADMIN_JWT_SECRET=your-secret-here
API_TOKEN_SALT=your-salt-here
APP_KEYS=key1,key2,key3,key4
JWT_SECRET=your-jwt-secret

# Backend
ENVIRONMENT=production
DEBUG=False

# Frontend URLs
NEXT_PUBLIC_STRAPI_API_URL=https://cms.example.com
NEXT_PUBLIC_BACKEND_URL=https://api.example.com
```

---

## 🚀 Backend Deployment (Railway)

### Option 1: Railway Template (Recommended - Fastest)

**For Strapi CMS:**

1. Visit: https://railway.com/template/strapi
2. Click "Deploy Now"
3. Connect GitHub account
4. Select repository branch
5. Configure environment variables
6. Deploy

**For FastAPI Co-Founder:**

1. Visit Railway dashboard: https://railway.app
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

### Strapi Production Configuration

```javascript
// cms/strapi-v5-backend/config/server.ts
export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  app: {
    keys: env.array('APP_KEYS'),
  },
  admin: {
    auth: {
      secret: env('ADMIN_JWT_SECRET'),
    },
  },
  api: {
    rest: {
      prefix: '/api',
      defaultLimit: 25,
      maxLimit: 100,
    },
  },
});
```

### FastAPI Production Configuration

```python
# src/cofounder_agent/main.py
app = FastAPI(
    title="GLAD Labs AI Co-Founder",
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

1. Visit: https://vercel.com
2. Click "New Project"
3. Import Git repository: `glad-labs-website`
4. Select root directory: `web/public-site/`
5. Add environment variables:
   ```
   NEXT_PUBLIC_STRAPI_API_URL=https://cms.example.com
   NEXT_PUBLIC_BACKEND_URL=https://api.example.com
   ```
6. Click "Deploy"

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

## 🛢️ CMS Deployment (Strapi)

### Database Setup (PostgreSQL)

```bash
# Create production database
createdb glad_labs_production

# Set environment variable
export DATABASE_URL="postgresql://user:password@localhost:5432/glad_labs_production"

# Run migrations
npm run strapi migrations:run
```

### Strapi Media Upload Configuration

```javascript
// cms/strapi-v5-backend/config/plugins.ts
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

```
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

# CMS health
curl https://cms.example.com/admin

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
- **Option 1:** Sentry (recommended): https://sentry.io
- **Option 2:** Railway dashboard
- **Option 3:** Application Insights

### Performance Monitoring

- **Core Web Vitals:** Vercel Analytics (automatic)
- **API Performance:** Application metrics
- **Database Performance:** PostgreSQL logs

---

## 🔄 Deployment Workflow

### Deploy to Staging

```bash
# 1. Merge to staging branch
git checkout staging
git merge dev
git push origin staging

# 2. Verify in staging
# Check: https://staging.example.com
# Check: https://staging-api.example.com

# 3. Run smoke tests
npm run test:smoke
```

### Deploy to Production

```bash
# 1. Create release tag
git tag v1.2.3
git push origin v1.2.3

# 2. Merge to main
git checkout main
git merge v1.2.3
git push origin main

# 3. Monitor deployment
railway logs  # or vercel logs

# 4. Verify production
curl https://example.com/api/health
```

### Rollback Procedure

```bash
# If deployment fails:
# 1. Identify last working version
git log --oneline

# 2. Revert to previous commit
git revert <commit-hash>
git push origin main

# 3. Redeploy
# Railway/Vercel will auto-deploy latest commit

# 4. Verify rollback
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

<div align="center">

**[← Back to Documentation Hub](./00-README.md)**

[Setup](./01-SETUP_AND_OVERVIEW.md) • [Architecture](./02-ARCHITECTURE_AND_DESIGN.md) • [Development](./04-DEVELOPMENT_WORKFLOW.md) • [Operations](./06-OPERATIONS_AND_MAINTENANCE.md)

</div>
