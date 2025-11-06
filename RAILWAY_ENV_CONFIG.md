# Railway Environment Variables for Strapi v5 Production

## Critical Fix for SIGTERM Crashes

Your Strapi is crashing every ~66 minutes because Railway doesn't have the correct configuration.

### 🔧 Required Environment Variables for Railway

Add these to your Railway Strapi service environment:

```
# DATABASE CONNECTION (Replace with actual Railway Postgres values)
DATABASE_CLIENT=postgres
DATABASE_URL=postgresql://[USER]:[PASSWORD]@[HOST]:[PORT]/[DATABASE]

# Strapi Configuration
HOST=0.0.0.0
PORT=8080
NODE_ENV=production

# Security
JWT_SECRET=[GENERATE_NEW_SECRET]
API_TOKEN_SALT=[GENERATE_NEW_SECRET]
APP_KEYS=[KEY1],[KEY2],[KEY3],[KEY4]

# URL Configuration (CRITICAL)
URL=https://strapi-staging.railway.app
ADMIN_JWT_SECRET=[GENERATE_NEW_SECRET]

# Connection Pool (Fix memory issues)
DATABASE_POOL_MIN=0
DATABASE_POOL_MAX=5

# Logging
LOG_LEVEL=debug
STRAPI_WEBHOOKS_POPULATE_RELATIONS=false
```

### 📋 Step-by-Step Fix

1. **Go to Railway Dashboard**: https://railway.app
2. **Select your Strapi project** (Staging or Prod)
3. **Click "Variables" tab**
4. **Add the environment variables above**
5. **Get your DATABASE_URL from the PostgreSQL service:**
   - Click the PostgreSQL service in the same project
   - Copy the connection string from the `DATABASE_URL` variable
   - Paste it into your Strapi service

### 🔑 Generate Required Secrets

Run this in your terminal to generate new secrets:

```powershell
# Generate JWT_SECRET
[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((New-Guid).ToString())) | Select-Object -First 1

# Generate multiple APP_KEYS (run 4 times)
[System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes([System.Guid]::NewGuid().ToString()))
```

### 🔍 Verify Configuration

After setting variables, restart the service:

1. Click the Strapi service
2. Click "Redeploy" button
3. Check logs for:
   ```
   Database name      │ glad_labs_prod  (NOT EMPTY!)
   Strapi started successfully
   ```

### 📊 Monitor for Stability

Check that Strapi:

- ✅ Stays running for more than 1 hour
- ✅ Shows correct database name in logs
- ✅ Responds to health checks
- ✅ **Never gets SIGTERM again**

### 🚨 If Still Failing

The SIGTERM is likely being sent because:

1. **Railway detected the process as unhealthy**
   - Fix: Ensure `DATABASE_URL` is correct
2. **Memory leak during startup**
   - Fix: Check database queries for N+1 problems
   - Add: `DEBUG=strapi:*` to see what's happening

3. **PostgreSQL connection timeout**
   - Fix: Increase connection pool timeout in database.js:
   ```javascript
   pool: {
     min: 0,
     max: 5,
     idleTimeoutMillis: 30000,
     connectionTimeoutMillis: 10000
   }
   ```

### 📝 Update database.js with Better Pool Config

```javascript
module.exports = ({ env }) => {
  const databaseUrl = env('DATABASE_URL');

  if (databaseUrl && databaseUrl.includes('postgresql')) {
    return {
      connection: {
        client: 'postgres',
        connection: {
          connectionString: databaseUrl,
          ssl: { rejectUnauthorized: false }, // Railway requires SSL
        },
        debug: false,
        pool: {
          min: 0,
          max: 5,
          idleTimeoutMillis: 30000,
          connectionTimeoutMillis: 10000,
          reapIntervalMillis: 1000,
        },
      },
    };
  }

  // Fallback for local
  return {
    connection: {
      client: 'sqlite',
      connection: {
        filename: env('DATABASE_FILENAME', './.tmp/data.db'),
      },
      useNullAsDefault: true,
    },
  };
};
```

### ✅ Expected Result

After fixing:

- Strapi stays running indefinitely
- Database name shows correctly
- No more SIGTERM crashes
- 502 errors should resolve
- API calls work properly
