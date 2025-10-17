# Railway Deployment Guide - Strapi v5 CMS

**Last Updated:** October 17, 2025  
**Status:** ✅ Production Deployment Successful  
**URL:** https://glad-labs-strapi-v5-backend-production.up.railway.app

---

## Table of Contents

1. [Why Railway?](#why-railway)
2. [Prerequisites](#prerequisites)
3. [Quick Start](#quick-start)
4. [Detailed Setup](#detailed-setup)
5. [Environment Variables](#environment-variables)
6. [Troubleshooting](#troubleshooting)
7. [Cost Estimation](#cost-estimation)

---

## Why Railway?

After **6+ hours** and **8 failed builds** on Strapi Cloud, we switched to Railway.app for the following reasons:

### ✅ Railway Advantages
- **Full control** over build process
- **Monorepo support** with configurable root directory
- **SSH access** to containers for debugging
- **Better error messages** and build logs
- **PostgreSQL included** (managed database)
- **Railpack builder** (newer than Nixpacks)
- **Cost-effective**: $10-20/month vs. fighting free tier limitations

### ❌ Strapi Cloud Limitations
- Poor monorepo support
- Can't customize npm install process
- Ignores .npmrc configuration
- Limited debugging capabilities
- Build timeouts on complex projects
- No SSH access to troubleshoot

---

## Prerequisites

### Required Tools
```powershell
# Railway CLI
npm install -g @railway/cli

# Git (for deployment)
git --version

# Node.js v18+
node --version
```

### Required Accounts
1. **Railway Account**: [railway.app](https://railway.app)
2. **GitHub Account**: (for auto-deployment)
3. **GitLab Account**: (our primary repo host)

---

## Quick Start

```powershell
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Login to Railway
railway login

# 3. Create GitHub mirror (Railway doesn't support GitLab directly)
git remote add github https://github.com/YOUR-USERNAME/glad-labs-website.git
git push github main

# 4. Create Railway project via dashboard
# https://railway.app/new

# 5. Link CLI to project
cd cms/strapi-v5-backend
railway link

# 6. Add PostgreSQL database
railway add --database postgres

# 7. Set environment variables (see below)
railway variables --set "DATABASE_CLIENT=postgres"
railway variables --set "NODE_ENV=production"
# ... (see full list below)

# 8. Deploy
git push github main
```

---

## Detailed Setup

### Step 1: GitHub Mirror Setup

Railway requires GitHub. Create a mirror of your GitLab repo:

```powershell
# Add GitHub as a remote
git remote add github https://github.com/YOUR-USERNAME/glad-labs-website.git

# Push to both remotes
git push origin main  # GitLab (primary)
git push github main  # GitHub (Railway deployment)
```

**Keep both in sync:**
```powershell
# Add to your workflow
git push origin main; git push github main
```

### Step 2: Railway Project Configuration

1. **Create New Project** in Railway dashboard
2. **Deploy from GitHub** repo
3. **Configure Settings:**
   - **Root Directory**: `cms/strapi-v5-backend`
   - **Builder**: Railpack (default, no config needed)
   - **Start Command**: `npm run start`

4. **Create `railway.json`** in `cms/strapi-v5-backend/`:
```json
{
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

### Step 3: Add PostgreSQL Database

```powershell
# Via CLI
railway add --database postgres

# Or via Dashboard:
# Project > "New" > "Database" > "Add PostgreSQL"
```

Railway will automatically create these variables:
- `DATABASE_URL` (internal connection)
- `DATABASE_PUBLIC_URL` (external connection - costs egress fees!)
- `DATABASE_PRIVATE_URL` (internal connection - use this!)

### Step 4: Configure Environment Variables

**Critical:** Use `DATABASE_PRIVATE_URL` to avoid egress fees!

```powershell
# Database Configuration
railway variables --set "DATABASE_CLIENT=postgres"
railway variables --set "DATABASE_URL=\${{DATABASE_PRIVATE_URL}}"

# Core Strapi Settings
railway variables --set "NODE_ENV=production"
railway variables --set "HOST=0.0.0.0"
railway variables --set "PORT=5000"

# Proxy Settings (REQUIRED for Railway)
railway variables --set "TRUST_PROXY=true"
railway variables --set "COOKIE_SECURE=false"

# Generate Secrets (run these commands to generate values)
railway variables --set "ADMIN_JWT_SECRET=$(node -e "console.log(require('crypto').randomBytes(16).toString('base64'))")"
railway variables --set "API_TOKEN_SALT=$(node -e "console.log(require('crypto').randomBytes(16).toString('base64'))")"
railway variables --set "TRANSFER_TOKEN_SALT=$(node -e "console.log(require('crypto').randomBytes(16).toString('base64'))")"
railway variables --set "JWT_SECRET=$(node -e "console.log(require('crypto').randomBytes(16).toString('base64'))")"
railway variables --set "APP_ENCRYPTION_KEY=$(node -e "console.log(require('crypto').randomBytes(32).toString('base64'))")"

# Generate 4 APP_KEYS
$key1 = node -e "console.log(require('crypto').randomBytes(16).toString('base64'))"
$key2 = node -e "console.log(require('crypto').randomBytes(16).toString('base64'))"
$key3 = node -e "console.log(require('crypto').randomBytes(16).toString('base64'))"
$key4 = node -e "console.log(require('crypto').randomBytes(16).toString('base64'))"
railway variables --set "APP_KEYS=$key1,$key2,$key3,$key4"
```

---

## Environment Variables

### Complete List

| Variable | Purpose | How to Generate |
|----------|---------|-----------------|
| `DATABASE_CLIENT` | Database type | `postgres` |
| `DATABASE_URL` | Database connection | `${{DATABASE_PRIVATE_URL}}` |
| `NODE_ENV` | Environment | `production` |
| `HOST` | Bind address | `0.0.0.0` |
| `PORT` | Server port | `5000` |
| `TRUST_PROXY` | Trust Railway proxy | `true` |
| `COOKIE_SECURE` | Disable secure cookies | `false` |
| `APP_KEYS` | App encryption keys | 4 comma-separated base64 strings |
| `ADMIN_JWT_SECRET` | Admin JWT secret | `openssl rand -base64 16` |
| `API_TOKEN_SALT` | API token salt | `openssl rand -base64 16` |
| `TRANSFER_TOKEN_SALT` | Transfer token salt | `openssl rand -base64 16` |
| `JWT_SECRET` | JWT secret | `openssl rand -base64 16` |
| `APP_ENCRYPTION_KEY` | Encryption key | `openssl rand -base64 32` |

### Why `COOKIE_SECURE=false`?

Railway's proxy terminates SSL (HTTPS → HTTP internally). The connection between Railway's proxy and Strapi is plain HTTP. Setting `secure: true` on cookies causes "Cannot send secure cookie over unencrypted connection" errors.

**Security Note:** This is safe because Railway's proxy handles SSL. External users always connect via HTTPS.

---

## Troubleshooting

### Issue: "Cannot find module 'pg'"

**Cause:** PostgreSQL driver not installed.

**Solution:**
```json
// cms/strapi-v5-backend/package.json
{
  "dependencies": {
    "pg": "^8.13.1"
  }
}
```

### Issue: "Cannot send secure cookie over unencrypted connection"

**Cause:** Strapi trying to set secure cookies but connection is HTTP.

**Solutions:**

1. **Environment Variable:**
```powershell
railway variables --set "COOKIE_SECURE=false"
```

2. **Admin Config** (`config/admin.ts`):
```typescript
export default ({ env }) => ({
  auth: {
    secret: env('ADMIN_JWT_SECRET'),
    options: {
      cookieSecure: false, // Disable for Railway proxy
    },
  },
  // ... rest of config
});
```

3. **Middleware Config** (`config/middlewares.ts`):
```typescript
export default [
  // ... other middlewares
  {
    name: 'strapi::session',
    config: {
      cookie: {
        secure: false, // Railway proxy handles SSL
        httpOnly: true,
        sameSite: 'lax',
      },
    },
  },
  // ... other middlewares
];
```

4. **Server Config** (`config/server.ts`):
```typescript
export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  app: {
    keys: env.array('APP_KEYS'),
  },
  proxy: true, // Trust Railway proxy
  url: env('PUBLIC_URL', 'https://your-app.up.railway.app'),
});
```

### Issue: Database shows "sqlite" instead of "postgres"

**Cause:** `DATABASE_CLIENT` not set.

**Solution:**
```powershell
railway variables --set "DATABASE_CLIENT=postgres"
```

### Issue: "Encryption key is missing from config"

**Cause:** Wrong variable name in config.

**Solution:**
```typescript
// config/admin.ts
export default ({ env }) => ({
  // ... other config
  appEncryptionKey: env('APP_ENCRYPTION_KEY'), // Not 'ENCRYPTION_KEY'
});
```

### Issue: Railway detects Dockerfile when you don't want it to

**Cause:** Railway prioritizes Docker over Railpack.

**Solution:**
```powershell
# Delete or rename Dockerfile
rm cms/strapi-v5-backend/Dockerfile

# Ensure railway.json exists
# Railway will use Railpack automatically
```

### Issue: Build fails with monorepo errors

**Cause:** Railway building from wrong directory.

**Solution:**
1. Go to Railway dashboard
2. Service Settings > "Root Directory"
3. Set to: `cms/strapi-v5-backend`

---

## Cost Estimation

### Railway Pricing (as of October 2025)

| Resource | Usage | Cost |
|----------|-------|------|
| Compute | ~500 hours/month | $5-10 |
| PostgreSQL | Small instance | $5 |
| Data Transfer | Moderate | $0-5 |
| **Total** | | **$10-20/month** |

### Cost Optimization Tips

1. **Use Private URLs:**
   ```powershell
   # Use DATABASE_PRIVATE_URL instead of DATABASE_PUBLIC_URL
   # Avoids egress fees
   railway variables --set "DATABASE_URL=\${{DATABASE_PRIVATE_URL}}"
   ```

2. **Enable Sleep on Idle** (if traffic is low):
   - Railway dashboard > Service Settings > Sleep on Idle

3. **Monitor Usage:**
   ```powershell
   railway status
   ```

---

## Deployment Workflow

### After Setup (Normal Deployment)

```powershell
# 1. Make changes locally
# 2. Commit and push to both remotes
git add -A
git commit -m "Your changes"
git push origin main  # GitLab (primary)
git push github main  # GitHub (triggers Railway deploy)

# 3. Monitor deployment
railway logs

# 4. Open app
railway open
```

### Manual Deployment (if auto-deploy fails)

```powershell
# Link to project
railway link

# Deploy from CLI
railway up

# Or trigger redeploy in dashboard
railway open
# Click "Deploy" button
```

---

## Next Steps

1. ✅ **Strapi Deployed** - Complete
2. ⏳ **Create Admin Account** - In Progress
3. 📝 **Configure Content Types** - Next
4. 🚀 **Deploy Public Site** to Vercel
5. 💰 **Apply for Google AdSense**

---

## Support & Resources

- **Railway Docs:** https://docs.railway.app
- **Railway CLI:** https://docs.railway.app/develop/cli
- **Strapi Docs:** https://docs.strapi.io
- **Our GitLab:** https://gitlab.com/glad-labs-org/glad-labs-website
- **Our GitHub Mirror:** https://github.com/mattg-stack/glad-labs-website

---

**Created:** October 17, 2025  
**Last Deploy:** October 17, 2025 03:33 UTC  
**Status:** ✅ Production Ready (pending admin access)
