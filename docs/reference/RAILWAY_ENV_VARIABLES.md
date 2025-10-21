# 🚀 Railway Environment Variables Guide

Complete reference for Strapi + PostgreSQL on Railway.app

---

## 📋 Quick Overview

Railway provides **pre-configured environment variables** that connect Strapi to PostgreSQL automatically. You don't need to manually create most of these - they're injected by Railway.

| Variable                                      | Purpose                                         | Source                      |
| --------------------------------------------- | ----------------------------------------------- | --------------------------- |
| `DATABASE_URL`                                | Internal PostgreSQL connection (no egress fees) | Railway Postgres plugin     |
| `RAILWAY_PUBLIC_DOMAIN`                       | Your production URL                             | Railway                     |
| Secrets: `APP_KEYS`, `ADMIN_JWT_SECRET`, etc. | Encryption/authentication                       | You generate via Railway UI |

---

## 🗄️ PostgreSQL Environment Variables

These are **automatically provided** by Railway when you add the PostgreSQL plugin. You don't need to set these manually.

### Connection Variables

| Variable            | Value                             | Notes                             |
| ------------------- | --------------------------------- | --------------------------------- |
| `PGDATA`            | `/var/lib/postgresql/data/pgdata` | Database storage location         |
| `PGHOST`            | `${{RAILWAY_PRIVATE_DOMAIN}}`     | Internal domain (no egress costs) |
| `PGPORT`            | `5432`                            | Standard PostgreSQL port          |
| `PGUSER`            | `postgres`                        | Database user (Railway default)   |
| `PGPASSWORD`        | `${{ secret(32, ...) }}`          | Auto-generated random password    |
| `PGDATABASE`        | `railway`                         | Default database name             |
| `POSTGRES_DB`       | `railway`                         | Same as PGDATABASE                |
| `POSTGRES_USER`     | `postgres`                        | Same as PGUSER                    |
| `POSTGRES_PASSWORD` | `${{ secret(...) }}`              | Same as PGPASSWORD                |

### Connection Strings

**For Strapi (use this one):**

```
DATABASE_URL=postgresql://postgres:PASSWORD@RAILWAY_PRIVATE_DOMAIN:5432/railway
```

**For Railway Data Panel (public connection):**

```
DATABASE_PUBLIC_URL=postgresql://postgres:PASSWORD@RAILWAY_TCP_PROXY_DOMAIN:RAILWAY_TCP_PROXY_PORT/railway
```

### Other PostgreSQL Variables

| Variable                              | Value | Notes                     |
| ------------------------------------- | ----- | ------------------------- |
| `SSL_CERT_DAYS`                       | `820` | SSL cert validity period  |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `60`  | Graceful shutdown timeout |

---

## 🎯 Strapi Environment Variables

Add these to your **Railway service settings** (NOT git):

### Required Variables

| Variable          | Example                              | Notes                             |
| ----------------- | ------------------------------------ | --------------------------------- |
| `DATABASE_CLIENT` | `postgres`                           | Must be `postgres` for Railway    |
| `DATABASE_URL`    | `postgresql://...`                   | Auto-provided by Railway Postgres |
| `URL`             | `https://your-domain.up.railway.app` | Public production URL             |
| `HOST`            | `::`                                 | Listen on all interfaces          |
| `BROWSER`         | `false`                              | Don't open browser on startup     |

### Secret Variables (Generate via Railway UI)

Railway automatically generates these when you deploy. They're cryptographic secrets for security.

| Variable              | What It Does       | How to Generate            |
| --------------------- | ------------------ | -------------------------- |
| `APP_KEYS`            | Session encryption | Railway generates (4 keys) |
| `API_TOKEN_SALT`      | API token hashing  | Railway generates          |
| `ADMIN_JWT_SECRET`    | Admin JWT tokens   | Railway generates          |
| `TRANSFER_TOKEN_SALT` | Transfer tokens    | Railway generates          |
| `JWT_SECRET`          | General JWT tokens | Railway generates          |
| `ENCRYPTION_KEY`      | Data encryption    | Railway generates          |

### Optional Variables

| Variable                             | Default | Purpose               |
| ------------------------------------ | ------- | --------------------- |
| `STRAPI_TELEMETRY_DISABLED`          | `true`  | Don't send usage data |
| `STRAPI_DISABLE_UPDATE_NOTIFICATION` | `true`  | Hide update messages  |

---

## ✅ Setup Checklist

### Step 1: Create Railway Project

- [ ] Login to [Railway.app](https://railway.app)
- [ ] Create new project from GitHub repo
- [ ] Select `glad-labs-website` repository

### Step 2: Add PostgreSQL Plugin

- [ ] Go to project settings
- [ ] Click "Add plugins"
- [ ] Select PostgreSQL
- [ ] Railway auto-injects: `DATABASE_URL`, `PGHOST`, `PGPORT`, etc.

### Step 3: Configure Strapi Service

- [ ] Go to your Strapi service settings
- [ ] Set environment variables:

```bash
# Basic Config
DATABASE_CLIENT=postgres
URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
HOST=::
BROWSER=false

# Security - let Railway generate these
APP_KEYS=${{secret()}}
API_TOKEN_SALT=${{secret()}}
ADMIN_JWT_SECRET=${{secret()}}
TRANSFER_TOKEN_SALT=${{secret()}}
JWT_SECRET=${{secret()}}

# Telemetry
STRAPI_TELEMETRY_DISABLED=true
STRAPI_DISABLE_UPDATE_NOTIFICATION=true

# Database - AUTO-PROVIDED BY RAILWAY
# DATABASE_URL=${{Postgres.DATABASE_URL}}
# DATABASE_PUBLIC_URL=${{Postgres.DATABASE_PUBLIC_URL}}
```

### Step 4: Deploy

- [ ] Commit and push changes
- [ ] Railway auto-deploys
- [ ] Check logs: `railway logs -f`
- [ ] Test login at `https://YOUR_DOMAIN/admin`

---

## 🔐 Security Best Practices

### ✅ DO

- Store all secrets in Railway UI, not `.env.railway`
- Use `${{secret()}}` for cryptographic values
- Reference other services: `${{Postgres.DATABASE_URL}}`
- Use internal domain: `RAILWAY_PRIVATE_DOMAIN` (no egress costs)
- Set `BROWSER=false` in production

### ❌ DON'T

- Commit `.env.railway` to Git (it contains secrets!)
- Hardcode secret values
- Use `DATABASE_PUBLIC_URL` for Strapi (costs money)
- Enable telemetry in production
- Set `BROWSER=true` in production

---

## 🛠️ Variable Reference Syntax

Railway supports dynamic variable references:

```bash
# Reference PostgreSQL values
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Reference Railway system values
URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
DOMAIN=${{RAILWAY_PRIVATE_DOMAIN}}

# Generate random secrets
APP_KEYS=${{secret()}}
PASSWORD=${{secret(32)}}

# Reference environment names
ENV_NAME=${{RAILWAY_ENVIRONMENT_NAME}}

# Reference region
REGION=${{RAILWAY_REGION}}
```

---

## 📊 Variable Flow Diagram

```
┌─────────────────────────────────┐
│   PostgreSQL Plugin (Railway)    │
│                                 │
│ Generates:                      │
│ - DATABASE_URL                  │
│ - PGHOST, PGPORT, PGUSER        │
│ - POSTGRES_PASSWORD (random)    │
└────────────┬────────────────────┘
             │
             ↓
┌─────────────────────────────────┐
│   Strapi Service (Railway)      │
│                                 │
│ Uses:                           │
│ - DATABASE_URL → config/db.ts   │
│ - URL → config/server.ts        │
│ - Secrets → authentication      │
└────────────┬────────────────────┘
             │
             ↓
┌─────────────────────────────────┐
│   Running in Production          │
│                                 │
│ Admin accessible at:            │
│ https://YOUR_DOMAIN/admin       │
└─────────────────────────────────┘
```

---

## 🐛 Troubleshooting

### "DATABASE_URL not found"

```bash
# Fix: Check PostgreSQL plugin is added
railway status
railway logs -f | grep DATABASE_URL
```

### "Cannot connect to database"

```bash
# Verify internal domain is correct
echo ${{RAILWAY_PRIVATE_DOMAIN}}

# Check credentials
echo $PGUSER: $POSTGRES_USER
echo $PGPASSWORD: $POSTGRES_PASSWORD
echo $PGHOST: $RAILWAY_PRIVATE_DOMAIN
```

### "Secure cookie error" (HTTPS issue)

```bash
# Verify URL is set
echo $URL

# Verify proxy is enabled in server.ts
# Should be: proxy: true
```

### "Admin can't login"

```bash
# Check secrets are generated
railway logs -f | grep -i "secret\|jwt\|auth"

# Verify ADMIN_JWT_SECRET is set
railway secret list | grep ADMIN_JWT_SECRET
```

---

## 📚 Reference Files

- **Your config**: `cms/strapi-v5-backend/config/`
  - `database.ts` - Uses `DATABASE_URL`
  - `server.ts` - Uses `URL`, `HOST`, `proxy: true`
  - `admin.ts` - Uses `ADMIN_JWT_SECRET`

- **Example env**: `.env.railway` (for reference only, don't commit)

- **Railway docs**: https://docs.railway.app
  - PostgreSQL: https://docs.railway.app/databases/postgresql
  - Environment Variables: https://docs.railway.app/develop/variables

---

## 🎯 Summary

| Step | Variable                     | Source           | Used By                |
| ---- | ---------------------------- | ---------------- | ---------------------- |
| 1    | PostgreSQL plugin added      | Railway          | Auto                   |
| 2    | `DATABASE_URL` injected      | Railway Postgres | `config/database.ts`   |
| 3    | `APP_KEYS` generated         | Railway UI       | Strapi session         |
| 4    | `URL` set                    | You → Railway    | `config/server.ts`     |
| 5    | `ADMIN_JWT_SECRET` generated | Railway UI       | Admin authentication   |
| 6    | App runs                     | Strapi           | Accessible at `/admin` |

✅ **You're all set!** Your current setup matches this exactly.
