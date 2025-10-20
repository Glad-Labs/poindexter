# 03 - Deployment & Infrastructure

**Role**: DevOps, Backend Engineers  
**Reading Time**: 20-25 minutes  
**Last Updated**: October 18, 2025

---

## 🚀 Quick Navigation

- **[← Back to Docs](./00-README.md)** | **[↑ Setup](./01-SETUP_AND_OVERVIEW.md)** | **[↑ Architecture](./02-ARCHITECTURE_AND_DESIGN.md)** | **Next: [Development Workflow](./04-DEVELOPMENT_WORKFLOW.md) →**

---

## Overview

This document covers deployment strategies, infrastructure configuration, and production readiness for GLAD Labs. We support multiple deployment targets including Vercel (frontend), Railway (backend), and GCP (cloud functions).

---

## 📋 Table of Contents

1. [Deployment Architecture](#deployment-architecture)
2. [Vercel Frontend Deployment](#vercel-frontend-deployment)
3. [Railway Backend Deployment](#railway-backend-deployment)
4. [Environment Configuration](#environment-configuration)
5. [Database Management](#database-management)
6. [Production Checklist](#production-checklist)
7. [Troubleshooting](#troubleshooting)
8. [Advanced Topics](#advanced-topics)

---

## Deployment Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      GLAD LABS DEPLOYMENT                   │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Frontend Layer (Vercel)                                     │
│  ├─ Oversight Hub (React)                                   │
│  └─ Public Site (Next.js)                                   │
│                                                               │
│  API Layer (Railway)                                         │
│  ├─ Strapi CMS (Node.js + PostgreSQL)                       │
│  ├─ GraphQL API                                             │
│  └─ REST API                                                │
│                                                               │
│  Cloud Functions (GCP)                                      │
│  ├─ Intervene Trigger (Python)                             │
│  ├─ Background Jobs                                         │
│  └─ Event Processing                                        │
│                                                               │
│  Monitoring & Analytics                                     │
│  ├─ Application Insights                                    │
│  ├─ Error Tracking                                          │
│  └─ Performance Monitoring                                  │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Supported Platforms

| Platform    | Component                             | Status        | Cost       |
| ----------- | ------------------------------------- | ------------- | ---------- |
| **Vercel**  | Frontend (Oversight Hub, Public Site) | ✅ Production | $20-100/mo |
| **Railway** | Backend (Strapi, APIs)                | ✅ Production | $5-100/mo  |
| **GCP**     | Cloud Functions, Storage              | ✅ Production | Variable   |
| **GitHub**  | Source control, CI/CD                 | ✅ Production | Free       |

---

## Vercel Frontend Deployment

### Prerequisites

- Vercel account (free at vercel.com)
- GitHub repository connected
- Environment variables configured
- Build scripts verified

### Initial Setup

#### 1. Connect Repository

```bash
# Vercel will guide you through this in the dashboard
# Or use CLI:
npm i -g vercel
vercel login
vercel link
```

#### 2. Configure Build Settings

**For Next.js (Public Site)**:

```
Framework: Next.js
Root Directory: web/public-site
Build Command: npm run build
Output Directory: .next
Node Version: 18.x
```

**For React (Oversight Hub)**:

```
Framework: Create React App (or Vite)
Root Directory: web/oversight-hub
Build Command: npm run build
Output Directory: build
Node Version: 18.x
```

#### 3. Set Environment Variables

In Vercel Dashboard → Settings → Environment Variables:

```
NEXT_PUBLIC_API_URL=https://your-railway-app.up.railway.app
NEXT_PUBLIC_STRAPI_URL=https://your-railway-app.up.railway.app
REACT_APP_API_URL=https://your-railway-app.up.railway.app
```

### Deployment Workflow

#### Automatic Deployments

```
GitHub main branch → Push → Vercel builds → Tests → Deploy
```

#### Manual Deployment

```bash
cd web/public-site
vercel deploy --prod
```

#### Preview Deployments

Every pull request automatically creates a preview deployment:

```
PR opened → Vercel builds preview → Comment on PR with URL
```

### Environment Variables Required

```env
# Public (exposed to client)
NEXT_PUBLIC_API_URL=https://api.example.com
NEXT_PUBLIC_STRAPI_URL=https://strapi.example.com
REACT_APP_API_URL=https://api.example.com

# Private (server-side only)
DATABASE_URL=postgresql://...
API_SECRET=your-secret-key
```

### Troubleshooting Vercel Deployments

#### Build Fails: "Module not found"

**Cause**: Missing dependencies or wrong build path

**Solution**:

```bash
# Check package.json exists in root
# Check dependencies are listed
npm install
npm run build  # Test locally first

# In Vercel: Increase build timeout
# Settings → Function Timeout → Increase to 900s
```

#### Unauthorized Error on Build

**Cause**: Missing authentication for private npm packages

**Solution**:

```bash
# Create .npmrc with token
echo "//npm.pkg.github.com/:_authToken=YOUR_GITHUB_TOKEN" > .npmrc

# Add to Vercel environment variables
NPM_TOKEN=your_github_token
```

#### Blank Page After Deploy

**Cause**: API connection issues or env vars not loaded

**Solution**:

```bash
# Check env vars are loaded
console.log(process.env.NEXT_PUBLIC_API_URL)

# Verify API is accessible
curl https://your-api.up.railway.app/health

# Check browser console for errors
# F12 → Console → Look for network errors
```

---

## Railway Backend Deployment

### Prerequisites

- Railway account (free at railway.app)
- GitHub repository connected
- PostgreSQL database provisioned
- Strapi configured for production

### Initial Setup

#### 1. Create Railway Project

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Create new project
railway init
```

#### 2. Add Services

**Option A: GitHub Integration (Recommended)**

```
Dashboard → New → GitHub Repo → Select glad-labs-website
```

**Option B: CLI**

```bash
railway add
# Select:
# - Node.js for backend
# - PostgreSQL for database
```

#### 3. Configure Environment

In Railway Dashboard → Variables:

```env
# Node Config
NODE_ENV=production
PORT=3000

# Database
DATABASE_URL=postgresql://user:pass@host:port/db

# Strapi Config
ADMIN_JWT_SECRET=generate-random-secret
API_TOKEN_SALT=generate-random-salt
DATABASE_CLIENT=postgres
DATABASE_FILENAME=.env

# API Keys
JWT_SECRET=your-jwt-secret
API_WEBHOOK_TOKEN=your-token

# URLs
PUBLIC_URL=https://your-app.up.railway.app
ADMIN_URL=https://your-app.up.railway.app/admin
```

#### 4. Deploy from GitHub

```bash
# Push to main branch
git add .
git commit -m "Deploy to Railway"
git push origin main

# Railway watches GitHub and auto-deploys
# Check status at: railway.app/dashboard
```

### Managing PostgreSQL on Railway

#### Initial Setup

```sql
-- Railway provides a PostgreSQL instance
-- Connection string: postgresql://user:pass@host/dbname

-- Create initial database
CREATE DATABASE glad_labs_production;

-- Create users table
CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE,
  created_at TIMESTAMP DEFAULT NOW()
);
```

#### Backup Database

```bash
# Export backup
pg_dump postgresql://user:pass@host/db > backup.sql

# Restore from backup
psql postgresql://user:pass@host/db < backup.sql
```

#### Connect Locally

```bash
# Get connection string from Railway dashboard
psql "postgresql://user:pass@host:port/db"

# Or use in code
const connectionString = process.env.DATABASE_URL;
const client = new Pool({ connectionString });
```

### Strapi-Specific Configuration

#### Initial Strapi Setup on Railway

```bash
# 1. Build Strapi
cd cms/strapi-v5-backend
npm run build

# 2. Set production env vars
# In Railway dashboard add:
DATABASE_URL=postgresql://...
NODE_ENV=production
ADMIN_JWT_SECRET=random-secret
API_TOKEN_SALT=random-salt

# 3. Deploy
git push origin main

# 4. Access admin
https://your-app.up.railway.app/admin
```

#### First Admin User

On first deployment, Strapi shows admin creation screen:

```
1. Navigate to https://your-app.up.railway.app/admin
2. Create admin user (email, password)
3. Login
4. Configure content types
```

#### Seed Data on Production

```bash
# Run seed script
npm run seed:prod

# Or manually in Strapi Admin:
# 1. Go to Content Manager
# 2. Create entries
# 3. Publish them
```

---

## Environment Configuration

### Environment Variables by Environment

#### Development (Local)

```env
# .env.local
NODE_ENV=development
DATABASE_URL=postgresql://localhost/glad_labs_dev
STRAPI_URL=http://localhost:1337
API_URL=http://localhost:3000
```

#### Staging (Optional)

```env
# .env.staging
NODE_ENV=production
DATABASE_URL=postgresql://staging-host/db
STRAPI_URL=https://staging-strapi.railway.app
API_URL=https://staging-api.railway.app
```

#### Production

```env
# Set in Railway/Vercel dashboard (NEVER in git)
NODE_ENV=production
DATABASE_URL=postgresql://prod-host/db
STRAPI_URL=https://api.example.com
API_URL=https://api.example.com
```

### Secrets Management

**DO NOT commit secrets to git!**

#### Option 1: Environment Variables (Recommended)

```bash
# In Railway/Vercel dashboards, add variables
# They're encrypted and never exposed

# Reference in code:
const secret = process.env.API_SECRET;
```

#### Option 2: .env Files (Local Only)

```bash
# Create .env.local (add to .gitignore)
echo "API_SECRET=local-secret-only" > .env.local

# Never commit!
echo ".env.local" >> .gitignore
```

#### Option 3: Key Vault (Enterprise)

```bash
# Use Azure Key Vault or similar
const { SecretClient } = require("@azure/keyvault-secrets");
const client = new SecretClient(vaultUrl, credential);
const secret = await client.getSecret("api-secret");
```

---

## Database Management

### PostgreSQL on Railway

#### Connection Details

```
Host: container-xxx.railway.app
Port: 5432
Database: railway
User: postgres
Password: (from Railway dashboard)
```

#### Connection String Format

```
postgresql://postgres:PASSWORD@HOST:5432/DATABASE
```

#### Connecting from Node.js

```javascript
const { Pool } = require('pg');

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

// Query
const result = await pool.query('SELECT NOW()');
console.log(result.rows);
```

### Strapi Database Setup

#### Auto-Migration

Strapi automatically migrates schema:

```bash
# On startup, Strapi:
# 1. Reads content-type definitions
# 2. Creates/updates tables
# 3. Runs migrations

# No manual migration needed for new types
```

#### Manual Migrations

```bash
# If you modify database directly, tell Strapi:
npm run build
npm run start

# Strapi rebuilds schema
```

#### Database Monitoring

```bash
# Check table sizes
SELECT
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

# Check active connections
SELECT count(*) FROM pg_stat_activity;
```

---

## Production Checklist

### Before First Production Deploy

- [ ] **Environment Variables**
  - [ ] All secrets in dashboard (not in git)
  - [ ] API URLs configured
  - [ ] Database URL working
  - [ ] JWT secrets generated

- [ ] **Database**
  - [ ] PostgreSQL provisioned on Railway
  - [ ] Initial schema created
  - [ ] Backups enabled
  - [ ] Connection tested from app

- [ ] **Frontend**
  - [ ] Build completes without errors
  - [ ] API URLs point to production
  - [ ] Error tracking configured
  - [ ] Analytics integrated

- [ ] **Backend**
  - [ ] Strapi admin accessible
  - [ ] Content types created
  - [ ] Admin user created
  - [ ] API endpoints tested

- [ ] **Monitoring**
  - [ ] Error tracking enabled
  - [ ] Logs accessible
  - [ ] Uptime monitoring configured
  - [ ] Alerts configured

### During First Deploy

1. **Deploy to Staging First**

   ```bash
   # Test everything in staging
   # BEFORE going to production
   ```

2. **Monitor Initial Deploy**
   - Watch deployment logs
   - Check error tracking
   - Monitor database connections
   - Verify API responses

3. **Run Smoke Tests**
   ```bash
   # Quick validation
   curl https://your-api.up.railway.app/health
   curl https://your-app.vercel.app/
   ```

### Ongoing Production Monitoring

- [ ] Daily: Check uptime monitoring
- [ ] Daily: Review error tracking
- [ ] Weekly: Check database size
- [ ] Weekly: Review performance metrics
- [ ] Monthly: Database backup verification
- [ ] Monthly: Security patches

---

## Troubleshooting

### Common Issues

#### "Cannot connect to database"

**Diagnosis**:

```bash
# Test connection locally
psql $DATABASE_URL

# Check connection string format
echo $DATABASE_URL
# Should be: postgresql://user:pass@host:5432/dbname
```

**Solutions**:

- [ ] Verify DATABASE_URL in Railway dashboard
- [ ] Check IP whitelisting (Railway allows all)
- [ ] Restart Railway service
- [ ] Check PostgreSQL is running

#### "Strapi admin won't load"

**Diagnosis**:

```bash
# Check admin build
npm run build

# Check logs
npm run develop

# Try rebuilding cache
rm -rf .cache
npm run build
```

**Solutions**:

- [ ] Clear browser cache (Ctrl+Shift+Del)
- [ ] Check JavaScript console for errors
- [ ] Verify ADMIN_JWT_SECRET is set
- [ ] Check browser dev tools network tab

#### "API requests timing out"

**Diagnosis**:

```bash
# Check API health
curl -I https://your-api.up.railway.app/health

# Check response time
time curl https://your-api.up.railway.app/graphql
```

**Solutions**:

- [ ] Check Railway service memory (increase if needed)
- [ ] Check database query performance
- [ ] Review slow queries in logs
- [ ] Increase timeouts in Vercel/Railway config

#### "Build fails with 'out of memory'"

**Solution**:

```bash
# In Railway dashboard:
# 1. Click service
# 2. Settings → Memory
# 3. Increase from 512MB to 1GB+

# Or optimize build:
npm ci # Instead of npm install
npm run build # Remove unused code
```

---

## Advanced Topics

### Auto-Scaling

#### Railway Auto-Scaling

```
Settings → Deployment → Auto Scale
- Min instances: 1
- Max instances: 3
- CPU threshold: 80%
- Memory threshold: 80%
```

### Custom Domains

#### Vercel Custom Domain

```
1. Project Settings → Domains
2. Add your domain
3. Update DNS records (shown in dashboard)
4. Wait 24 hours for DNS propagation
```

#### Railway Custom Domain

```
1. Service Settings → Custom Domain
2. Add domain
3. Update DNS records
4. SSL certificate auto-generated
```

### CI/CD Pipeline

#### GitHub Actions Example

```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run tests
        run: npm test
      - name: Deploy to production
        run: |
          git push heroku main
```

---

## Next Steps

1. **[← Back to Documentation](./00-README.md)**
2. **Read**: [04 - Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)
3. **Try**: Deploy to staging first, then production
4. **Monitor**: Set up error tracking and uptime monitoring

---

**Last Updated**: October 18, 2025 | **Version**: 1.0
