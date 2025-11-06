## 🛠️ Railway Strapi Production Fix - Immediate Actions

### ⚠️ Current Problem

- Strapi crashes every ~66 minutes with `SIGTERM`
- Database name is EMPTY in logs
- 502 errors when trying to access API

### ✅ Quick Fix Checklist

#### Step 1: Update Strapi Database Configuration ✅

- [x] Updated `cms/strapi-main/config/database.js` with:
  - SSL support for production (Railway requirement)
  - Better connection pool settings
  - Proper timeout handling

#### Step 2: Set Railway Environment Variables (YOU DO THIS)

Go to: https://railway.app → Your Strapi Service → Variables

```
# REQUIRED (Get actual values from Railway)
DATABASE_URL=postgresql://[user]:[pass]@[host]:[port]/[dbname]
NODE_ENV=production
HOST=0.0.0.0
PORT=8080

# CRITICAL - Must have these
JWT_SECRET=[Generate a new one - see below]
API_TOKEN_SALT=[Generate a new one]
APP_KEYS=[Generate 4 - see below]

# URL Configuration
URL=https://[YOUR_STRAPI_RAILWAY_URL]
ADMIN_JWT_SECRET=[Generate a new one]
```

#### Step 3: Generate Required Secrets

Replace the placeholder values above with these generated secrets:

```powershell
# Run in PowerShell - each output is a secret value

# For JWT_SECRET (run once)
[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((New-Guid).ToString()))

# For API_TOKEN_SALT (run once)
[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((New-Guid).ToString()))

# For each APP_KEY (run 4 times, collect all outputs)
[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((New-Guid).ToString()))
```

Then for APP_KEYS, combine them with commas (no spaces):

```
APP_KEYS=key1_output,key2_output,key3_output,key4_output
```

#### Step 4: Deploy Updated Config

```bash
cd cms/strapi-main
git add config/database.js
git commit -m "fix: improve database pool config for Railway production"
git push
```

#### Step 5: Restart Strapi on Railway

1. Go to Railway.app → Your Strapi Service
2. Click "Redeploy"
3. Watch logs for:
   ```
   Database name      │ [your-db-name]  (Should NOT be empty!)
   Strapi started successfully
   ```

#### Step 6: Verify Stability

Check that:

- ✅ Logs show "Strapi started successfully"
- ✅ Database name is shown (not empty)
- ✅ Process stays running for >1 hour
- ✅ No SIGTERM signals in logs
- ✅ API responds to requests

### 🔍 If Still Failing After 5 Minutes

**The most common cause is incorrect DATABASE_URL.** Verify:

```bash
# Test the connection locally (if you have psql installed)
psql "[YOUR_DATABASE_URL_HERE]"

# If that works, the issue is in Strapi config
# If that fails, the DATABASE_URL is wrong
```

### 🚨 Debug Mode (If You Need More Info)

Add these variables to Railway to see what's happening:

```
DEBUG=strapi:*
LOG_LEVEL=debug
```

This will show detailed logs of database connection attempts.

### 📚 Reference: What Changed

**File: cms/strapi-main/config/database.js**

Changes made:

1. Added SSL support for Railway PostgreSQL: `ssl: { rejectUnauthorized: false }`
2. Improved connection pool: `max: 5` (was 7), `idleTimeoutMillis: 30000`
3. Added timeout handling: `connectionTimeoutMillis: 10000`
4. Added cleanup: `reapIntervalMillis: 1000`

### ✅ Expected Result

After these steps, Strapi should:

- Start successfully and stay running
- Properly connect to Railway PostgreSQL
- Return valid JSON from API endpoints
- No more 502 errors or SIGTERM crashes

---

**Status**: All code changes complete ✅  
**Next**: You need to configure Railway variables manually (1-2 minutes)  
**ETA**: Should be stable within 10 minutes of deploying
