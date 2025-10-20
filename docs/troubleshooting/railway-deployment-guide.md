# Railway Deployment Guide

**Last Updated**: October 19, 2025  
**Status**: Production Ready  
**Related Docs**: [Deployment Infrastructure](../03-DEPLOYMENT_AND_INFRASTRUCTURE.md) | [SWC Binding Fix](./swc-native-binding-fix.md)

> Complete guide to deploying GLAD Labs Strapi backend to Railway.app with PostgreSQL.

---

## 📋 Table of Contents

1. [Quick Start (5 minutes)](#quick-start)
2. [Prerequisites](#prerequisites)
3. [Step-by-Step Setup](#step-by-step-setup)
4. [Environment Configuration](#environment-configuration)
5. [Build Configuration](#build-configuration)
6. [Troubleshooting](#troubleshooting)
7. [Monitoring & Logs](#monitoring)

---

## 🚀 Quick Start

If you already have a Railway project set up, deploy with one command:

```bash
railway deploy
```

For first-time setup, follow the **[Step-by-Step Setup](#step-by-step-setup)** section below.

---

## 📋 Prerequisites

### Required Tools

- **Railway CLI**: [Install from railway.app](https://docs.railway.app/guides/cli)
- **Git**: Version control setup
- **Node.js**: 18.x or higher
- **PostgreSQL**: (Railway manages this)

### Required Accounts

- ✅ Railway.app account (free tier available)
- ✅ GitHub account (for auto-deploy)
- ✅ Domain name (optional, Railway provides default)

### Project Status

Ensure your Strapi backend is:
- ✅ Running locally without errors
- ✅ All dependencies installed (`npm install`)
- ✅ Configuration files present:
  - `railway.json` (build/deploy config)
  - `.npmrc` (npm settings)
  - `.swcrc` (TypeScript compiler settings)
  - `config/database.js` (database config)

---

## 🔧 Step-by-Step Setup

### Step 1: Install Railway CLI

```bash
# macOS / Linux
brew install railway

# Windows PowerShell
iwr -Uri https://storage.googleapis.com/railway-io/installers/latest/railway-windows-x86_64.exe -OutFile railway.exe
# Move railway.exe to your PATH
```

Verify installation:
```bash
railway --version
```

### Step 2: Login to Railway

```bash
railway login
```

This opens a browser to authenticate with Railway.

### Step 3: Create New Project

```bash
railway init
```

Follow the prompts:
- **Project Name**: `glad-labs-strapi`
- **Use existing project**: No (select "Create a new project")

### Step 4: Add PostgreSQL Plugin

```bash
railway add
```

Select `PostgreSQL` from the list. Railway automatically:
- Creates a PostgreSQL 15 instance
- Sets environment variables (`DATABASE_URL`)
- Configures backups and monitoring

### Step 5: Configure Environment Variables

```bash
railway variables
```

Set these variables:

| Variable | Value | Notes |
|----------|-------|-------|
| `NODE_ENV` | `production` | Production mode |
| `ADMIN_PATH` | `/admin` | Strapi admin path |
| `STRAPI_ADMIN_BACKEND_URL` | `https://your-domain.railway.app` | Your Railway domain |
| `STRAPI_TELEMETRY_DISABLED` | `true` | Disable telemetry |
| `STRAPI_AI_URL` | `https://your-ai-backend` | (Optional) AI service URL |
| `STRAPI_ANALYTICS_URL` | `https://your-analytics` | (Optional) Analytics URL |

**Database Connection**:
- `DATABASE_URL` is set automatically by PostgreSQL plugin
- `DATABASE_PUBLIC_URL` (if needed for external connections)

### Step 6: Configure Build & Deployment

Create/update `railway.json` in your project root:

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "buildCommand": "npm install && npm run build"
  },
  "deploy": {
    "startCommand": "npm run start",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

**Key Settings**:
- `buildCommand`: Runs on Railway build container (includes source compilation for native modules)
- `startCommand`: Starts Strapi server
- `restartPolicyType`: Automatically restart on failures
- `restartPolicyMaxRetries`: Retry up to 10 times before giving up

### Step 7: Configure npm for Build

Update `.npmrc` in project root:

```ini
# Minimal npm configuration for Railway
optional=false
fund=false
update-notifier=false
production=true
build-from-source=true
```

**Why `build-from-source=true`**:
- SWC (Rust-based TypeScript compiler) needs compilation for each platform
- Prebuilt binaries don't work in Railway containers
- See [SWC Native Binding Fix](./swc-native-binding-fix.md) for details

### Step 8: Link GitHub for Auto-Deploy

```bash
railway link
```

Select your GitHub repo. Railway now:
- ✅ Automatically deploys on git push to main
- ✅ Sets up webhooks
- ✅ Enables preview deployments

### Step 9: Deploy

**First Deploy**:
```bash
railway deploy
```

**Subsequent Deploys**:
Just push to GitHub:
```bash
git add .
git commit -m "Deploy to Railway"
git push origin main
```

---

## 🌍 Environment Configuration

### Railway Environment Variables

Access via Railway dashboard or CLI:

```bash
railway variables
```

### Database Auto-Detection

Strapi configuration automatically:
1. Checks for `DATABASE_URL` environment variable
2. Parses connection string for PostgreSQL
3. Configures connection pooling
4. Sets SSL mode (required for Railway)

Your `config/database.js` should:

```javascript
module.exports = ({ env }) => {
  const connection = env('DATABASE_URL')
    ? {
        connectionString: env('DATABASE_URL'),
        ssl: { rejectUnauthorized: false },
      }
    : {
        host: env('DATABASE_HOST', 'localhost'),
        port: env('DATABASE_PORT', 5432),
        database: env('DATABASE_NAME', 'strapi'),
        username: env('DATABASE_USERNAME', 'postgres'),
        password: env('DATABASE_PASSWORD', ''),
      };

  return {
    defaultConnection: 'default',
    connections: {
      default: {
        connector: 'bookshelf',
        settings: {
          client: 'postgres',
          ...connection,
          acquireConnectionTimeout: 100000,
        },
        options: { useNullAsDefault: true },
      },
    },
  };
};
```

---

## 🏗️ Build Configuration

### Build Process

Railway uses **Railpack** 0.9.1 which:

1. **Detects Node.js**
   - Reads package.json engines
   - Installs Node.js 18.20.8

2. **Installs Dependencies**
   - Runs `npm install`
   - Uses .npmrc settings
   - Builds native modules with `build-from-source=true`

3. **Builds Application**
   - Runs `npm run build`
   - Compiles SWC from Rust source
   - Builds Strapi admin panel
   - ~2-3 minutes total build time

4. **Deploys**
   - Runs `npm run start`
   - Strapi listens on port 3000
   - Automatically exposed via Railway domain

### Build Time Expectations

**First Deploy**: ~5-6 minutes
- Node.js install: ~1.5 min
- npm install: ~1.5 min (with source compilation)
- Strapi build: ~30 seconds
- Startup: ~1 minute

**Subsequent Deploys**: ~4-5 minutes
- npm cache used: ~30 seconds
- Build cache used: ~1 minute
- Total: Faster than first

---

## 🛠️ Troubleshooting

### Build Failures

#### Error: "Failed to load native binding"

**Cause**: SWC prebuilt binaries are incompatible with Railway container

**Solution**: Ensure `.npmrc` has `build-from-source=true`:

```ini
build-from-source=true
```

See [SWC Native Binding Fix](./swc-native-binding-fix.md) for full details.

#### Error: "EBUSY: resource busy or locked"

**Cause**: npm cache conflicts during build

**Solution**: Simplify `.npmrc` and remove cache paths:

```ini
# Remove these conflicting lines:
# cache=~/.npm
# npm-cache
```

#### Error: "Command exited with code 1"

**Cause**: Build command failed (usually SWC or dependency issue)

**Solution**:
1. Test locally: `npm run build`
2. Fix any errors locally first
3. Then deploy

### Connection Issues

#### "Cannot connect to PostgreSQL"

**Check**:
1. Is PostgreSQL plugin added to Railway project?
2. Is `DATABASE_URL` environment variable set?
3. Test connection string: `psql <DATABASE_URL>`

**Fix**:
```bash
railway add  # Add PostgreSQL if missing
railway variables  # Check DATABASE_URL is set
```

#### "Admin panel blank/500 errors"

**Cause**: Likely SWC binding error or missing environment variables

**Fix**:
1. Check Railway logs: `railway logs --follow`
2. Look for "Error: Failed to load native binding"
3. If found, see [SWC Native Binding Fix](./swc-native-binding-fix.md)

### Performance Issues

#### Slow startup

**Possible causes**:
- Cold start (first request)
- SWC compilation on first build
- Database migration/seeding

**Monitor**:
```bash
railway logs --follow
```

#### High memory usage

**Check Strapi settings**:
- Reduce admin plugin bundle size
- Enable content caching
- Check database connection pooling

---

## 📊 Monitoring & Logs

### View Logs

**Real-time logs**:
```bash
railway logs --follow
```

**Last 50 lines**:
```bash
railway logs
```

**Filter logs**:
```bash
railway logs | grep "error"
railway logs | grep "SWC"
```

### Key Metrics

Railway dashboard shows:
- ✅ CPU usage
- ✅ Memory usage  
- ✅ Network I/O
- ✅ Deployment history
- ✅ Environment variables

### Health Checks

Strapi automatically runs health checks:
- ✅ Admin panel: `/admin`
- ✅ API: `/api`
- ✅ Health: `GET /` (returns 200)

### Alerts & Notifications

Set up in Railway dashboard:
- CPU threshold alerts
- Memory threshold alerts
- Deployment failure notifications

---

## 🚀 Advanced Configuration

### Custom Domain

1. Go to Railway dashboard
2. Select your service
3. Domain settings → Add custom domain
4. Add your domain (e.g., `api.yoursite.com`)
5. Configure DNS records (CNAME)

### Scaling

Railway free tier provides:
- ✅ Shared CPU/Memory
- ✅ 100 hours/month
- ✅ Suitable for small projects

For production scale:
- Upgrade to paid plan
- Get dedicated resources
- Auto-scaling available

### Backups

PostgreSQL backups automated:
- Daily backups (7-day retention)
- Configure in PostgreSQL plugin settings
- Restore via Railway dashboard

---

## 📝 Checklist

Before deploying, verify:

- [ ] Railway CLI installed and logged in
- [ ] `railway.json` configured
- [ ] `.npmrc` has `build-from-source=true`
- [ ] Environment variables set in Railway
- [ ] GitHub linked for auto-deploy
- [ ] Test build locally: `npm run build`
- [ ] Strapi starts locally: `npm start`
- [ ] Database connection works
- [ ] Admin panel accessible at `https://your-domain.railway.app/admin`

---

## 📚 References

- [Railway Documentation](https://docs.railway.app)
- [Strapi Deployment Guide](https://docs.strapi.io/user-docs/latest/getting-started/deployment.html)
- [SWC Native Binding Issues](./swc-native-binding-fix.md)
- [PostgreSQL Configuration](../reference/database-configuration.md)

