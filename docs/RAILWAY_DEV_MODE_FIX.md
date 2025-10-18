# Railway Strapi Development Mode - Fix Guide

## Problem

When running `npm run develop` on Railway, Strapi fails healthcheck because:

- ❌ `.env` is configured for localhost PostgreSQL
- ❌ Railway service can't connect to `127.0.0.1:5432`
- ❌ Strapi admin won't start

## Solution

You have **two options**:

## Option 1: Use Railway's DATABASE_URL (Easiest)

Railway automatically provides a `DATABASE_URL` environment variable. Strapi can use it directly.

### Step 1: Update `.env` to use DATABASE_URL

Modify `cms/strapi-v5-backend/.env`:

```bash
# Remove individual database variables and use DATABASE_URL instead
# DATABASE_CLIENT=postgres
# DATABASE_HOST=...
# DATABASE_PORT=...
# DATABASE_NAME=...
# DATABASE_USERNAME=...
# DATABASE_PASSWORD=...

# Instead, Strapi will auto-detect DATABASE_URL from Railway
```

### Step 2: Create Railway-specific config

Create `cms/strapi-v5-backend/config/database.ts`:

```typescript
import path from 'path';

export default ({ env }) => {
  // If DATABASE_URL exists (Railway), parse and use it
  if (env('DATABASE_URL')) {
    return {
      connection: {
        client: 'postgres',
        connection: {
          connectionString: env('DATABASE_URL'),
          ssl: {
            rejectUnauthorized: false,
          },
        },
        useNullAsDefault: true,
      },
    };
  }

  // Fallback to individual vars for local development
  return {
    connection: {
      client: 'postgres',
      connection: {
        host: env('DATABASE_HOST', '127.0.0.1'),
        port: env.int('DATABASE_PORT', 5432),
        database: env('DATABASE_NAME', 'strapi'),
        user: env('DATABASE_USERNAME', 'strapi'),
        password: env('DATABASE_PASSWORD', 'strapi'),
        ssl: env.bool('DATABASE_SSL', false),
        schema: env('DATABASE_SCHEMA', 'public'),
      },
      useNullAsDefault: true,
    },
  };
};
```

### Step 3: Redeploy on Railway

```bash
railway up
```

Railway will:

1. Build with `npm run build`
2. Start with `npm run develop`
3. Use the `DATABASE_URL` automatically
4. Strapi admin should now be accessible

---

## Option 2: Manually Configure in Railway

If Option 1 doesn't work:

1. **Railway Dashboard** → Strapi service
2. **Variables** tab
3. **Add these individual variables** (copy from Railway PostgreSQL service):
   - `DATABASE_CLIENT=postgres`
   - `DATABASE_HOST=` (from Railway Postgres service)
   - `DATABASE_PORT=` (usually 5432)
   - `DATABASE_NAME=` (from Railway Postgres)
   - `DATABASE_USERNAME=` (from Railway Postgres)
   - `DATABASE_PASSWORD=` (from Railway Postgres)
   - `DATABASE_SSL=true`

Then redeploy.

---

## Quick Verification

After deployment, check if Strapi is running:

```bash
railway logs --service strapi
```

Look for messages like:

- ✅ `Strapi is running at http://...`
- ✅ `Content-Type Builder enabled`

If you see errors about database connection, that's the issue above.

---

## After Strapi Starts in Dev Mode

1. **Access admin**: https://strapi-production-b234.up.railway.app/admin
2. **Create content types** via Content-Type Builder
3. **Save to database** (no code deployment needed)
4. **Exit dev mode** when done:
   - Change Railway start command back to `npm run start`
   - Or seed data and deploy with `npm run build`

---

## Recommended: Use Strapi's Official Deployment Config

Better yet, follow Strapi's official Railway guide:

- https://docs.strapi.io/dev-docs/deployment/railway

They provide pre-configured templates that handle this automatically.
