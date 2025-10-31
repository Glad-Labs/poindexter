# 🚂 Running Database Fix on Railway PostgreSQL

**Status:** Production-Ready Guide  
**Date:** October 30, 2025  
**Error:** PostgreSQL duplicate index (`idx_timestamp_desc` already exists)  
**Solution:** Drop old indexes, restart service

---

## 📋 Option 1: Railway Web Console (Recommended ✅)

**This is the easiest and most secure method - no `psql` installation needed!**

### Step 1: Access Railway Dashboard

1. Go to [railway.app](https://railway.app)
2. Log in with your account
3. Select your **Glad Labs** project

### Step 2: Connect to Your Database

**For Staging Environment:**

1. Click on your **PostgreSQL** plugin (staging)
2. Click the **"Data"** tab at the top
3. You should see a web-based SQL editor
4. _(If no web editor, continue to Step 3 alternative)_

**For Production Environment:**

1. Repeat the same steps for your **Production PostgreSQL** plugin

### Step 3: Run the Fix Script

**Copy the entire script below and paste it into the Railway SQL editor:**

```sql
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;

SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename IN ('logs', 'tasks', 'audit_logs')
ORDER BY tablename, indexname;
```

### Step 4: Execute & Verify

1. Click **"Execute"** or **"Run Query"** button
2. You should see:
   - ✅ `DROP INDEX` messages (or "no output" if indexes didn't exist)
   - ✅ Table showing remaining indexes (these are correct)
3. No errors = Success! ✅

### Step 5: Restart Co-Founder Agent Service

1. In Railway Dashboard, click on **"Co-Founder Agent"** service
2. Click **"Deployments"** tab
3. Click on the latest deployment
4. Click **"Redeploy"** button
5. Wait 2-3 minutes for restart

### Step 6: Test Health Endpoint

```bash
# Replace YOUR_STAGING_URL with your actual Railway URL
curl https://your-staging-api.railway.app/api/health

# Expected response:
# {"status": "healthy", "timestamp": "2025-10-30T...", "version": "1.0.0"}
```

---

## 📋 Option 2: Railway CLI (If you have it installed)

### Prerequisites

- Railway CLI installed: `railway login`
- You're authenticated to your project

### Run the Script

```bash
# For Staging
railway connect postgres-staging < fix_staging_indexes.sql

# For Production
railway connect postgres-production < fix_staging_indexes.sql
```

**Note:** This requires the `psql` command to be installed locally.

---

## 📋 Option 3: Local psql Installation (Advanced)

### Install PostgreSQL Client Tools on Windows

#### Option A - Using Scoop (easiest if you have it)

```powershell
scoop bucket add extras
scoop install postgresql
psql --version  # Verify installation
```

#### Option B - Using Windows Package Manager

```powershell
winget install PostgreSQL.PostgreSQL
```

#### Option C - Direct Download

1. Go to [postgresql.org/download/windows](https://www.postgresql.org/download/windows/)
2. Download **PostgreSQL** (includes `psql`)
3. Run installer, select "Command Line Tools"
4. Verify: Open PowerShell and run `psql --version`

### Get Connection String from Railway

1. Railway Dashboard → PostgreSQL → **Connect** tab
2. Copy the **"Postgres URL"** (looks like: `postgresql://user:pass@host:port/dbname`)

### Run the Fix Script

```powershell
# Copy the connection string and replace USER_PASS_HOST_PORT_DBNAME
psql "postgresql://user:password@host.railway.internal:5432/railway" -f fix_staging_indexes.sql

# Or use environment variable
$env:DATABASE_URL = "postgresql://..."
psql $env:DATABASE_URL -f fix_staging_indexes.sql
```

---

## ✅ Verification Checklist

After running the fix script:

- [ ] No error messages in the SQL editor
- [ ] DROP INDEX commands executed successfully
- [ ] SELECT query showed correct indexes (idx*log*_, idx*service*_, etc.)
- [ ] Co-Founder Agent service restarted successfully
- [ ] `/api/health` endpoint returns `{"status": "healthy", ...}`
- [ ] Application logs show no connection errors
- [ ] Both staging and production databases fixed (if needed)

---

## 🚨 Troubleshooting

### Issue: "Query Limit Exceeded" in Railway

**Solution:** The web editor has a query size limit. Break the script into parts:

```sql
-- Part 1: Drop indexes
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;

-- Wait for this to complete, then run Part 2

-- Part 2: Verify
SELECT * FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'logs';
```

### Issue: "Permission Denied" Error

**Solution:** Your Railway user might not have admin rights. Try using:

```sql
-- Use public schema (default for Railway PostgreSQL)
SET search_path TO public;
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
```

### Issue: Still Getting "idx_timestamp_desc" Error After Restart

**Solution:** The migration runner might have cached the schema. Try:

1. Kill the Co-Founder Agent pod in Railway
2. Wait 30 seconds
3. Let it auto-restart (Railway will start a new pod)
4. Check `/api/health` again

---

## 📚 Resources

- **Railway PostgreSQL Docs:** [railway.app/resources/postgres](https://docs.railway.app/resources/postgres)
- **Railway CLI Guide:** [docs.railway.app/cli/quick-start](https://docs.railway.app/cli/quick-start)
- **PostgreSQL Index Troubleshooting:** [postgresql.org indexes](https://www.postgresql.org/docs/current/indexes.html)

---

## 🎯 What Happens After Fix

1. **Old indexes dropped:** `idx_timestamp_desc`, `idx_service`, etc.
2. **SQLAlchemy creates correct indexes:** On next migration run
3. **Correct index names used:**
   - `idx_log_timestamp_desc` (instead of `idx_timestamp_desc`)
   - `idx_log_service` (instead of `idx_service`)
   - `idx_log_timestamp_category` (instead of `idx_timestamp_category`)
4. **Service restarts cleanly:** No more duplicate index errors
5. **Health endpoint works:** `/api/health` returns success

---

## 🔗 Quick Links

- **Railway Dashboard:** [railway.app/dashboard](https://railway.app/dashboard)
- **Project Settings:** Railway.app → Project → Settings
- **Co-Founder Agent Logs:** Rails Dashboard → Co-Founder Agent → Logs
- **PostgreSQL Logs:** Rails Dashboard → PostgreSQL → Logs

---

**Document Status:** ✅ Production Ready  
**Last Updated:** October 30, 2025  
**Next Review:** Upon completion of database fix
