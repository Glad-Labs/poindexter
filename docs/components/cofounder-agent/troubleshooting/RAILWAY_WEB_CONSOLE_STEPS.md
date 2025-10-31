# 🚂 Railway Web Console: Step-by-Step Fix

## Quick Fix for PostgreSQL Duplicate Index Error

---

## 🎯 5-Minute Fix Process

### Step 1: Log into Railway Dashboard

```text
URL: https://railway.app/dashboard
```

- Click your **Glad Labs** project
- You should see multiple services listed

---

### Step 2: Select Your PostgreSQL Database

**You have TWO databases (do both!):**

1. **Staging PostgreSQL** (or "PostgreSQL Staging")
2. **Production PostgreSQL** (or "PostgreSQL Production")

**Start with STAGING first** ← Do this first to test!

- Click the **PostgreSQL** card/service
- Look for the database name (e.g., "railway" or your custom name)

---

### Step 3: Open the Data/SQL Editor

Inside the PostgreSQL service:

- Look for tabs at the top: **Overview** | **Deploy** | **Settings** | **Data**
- Click the **"Data"** tab

You should see a SQL editor appear (white box for entering SQL queries).

---

### Step 4: Copy & Paste the Fix Script

Copy this entire script:

```sql
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;

SELECT
    schemaname,
    tablename,
    indexname
FROM pg_indexes
WHERE schemaname = 'public' AND tablename IN ('logs', 'tasks', 'audit_logs')
ORDER BY tablename, indexname;
```

Paste into the SQL editor box in Railway

---

### Step 5: Execute the Query

- Look for **"Execute"** or **"Run"** button (usually bottom right)
- Click it

**Expected output:**

```text
DROP INDEX
DROP INDEX
DROP INDEX
DROP INDEX

 schemaname | tablename | indexname
 -----------+-----------+-------------------
 public     | logs      | idx_log_service
 public     | logs      | idx_log_timestamp_category
 public     | logs      | idx_log_timestamp_desc
 public     | tasks     | idx_task_created_at
 ...
```

✅ **No errors?** Continue to Step 6!

---

### Step 6: Restart the Co-Founder Agent Service

1. Go back to your **project dashboard**
2. Find the **"Co-Founder Agent"** service
3. Click on it
4. Click the **"Deployments"** tab
5. Find the **latest deployment** at the top
6. Click **"Redeploy"** (or the 🔄 reload icon)
7. Wait 2-3 minutes for it to restart

**You should see:**

- Status: "Building" → "Deploying" → "Success"

---

### Step 7: Test the Health Endpoint

Once redeployed, test if it works:

#### Option A - Browser

```text
https://your-railway-url.railway.app/api/health
```

#### Option B - Terminal

```powershell
curl https://your-railway-url.railway.app/api/health
```

**Expected response:**

```json
{
  "status": "healthy",
  "timestamp": "2025-10-30T15:45:22.123456",
  "version": "1.0.0"
}
```

✅ **If you see this, the fix worked!**

---

### Step 8: Repeat for PRODUCTION

Once staging is working:

1. Go to Railway Dashboard
2. Select **Production PostgreSQL**
3. Repeat Steps 3-7 for production
4. Verify production `/api/health` endpoint works

---

## 🚨 If You Get Stuck

### "Data tab doesn't appear"

**Solution:** Railway may have updated their UI. Alternative:

1. Go to PostgreSQL service
2. Click **"Settings"** tab
3. Scroll down to find **"Connect"**
4. Copy the **connection string**
5. Use local `psql` to connect (see other guide)

### "Query Editor has limit"

**Solution:** Run in two parts:

Part 1:

```sql
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
```

(Wait for this to complete)

Part 2:

```sql
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;
```

### "Permission Denied" error

**Solution:** Try this first:

```sql
SET search_path TO public;
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
```

---

## ✅ Verification Checklist

- [ ] Logged into Railway Dashboard
- [ ] Found PostgreSQL service
- [ ] Opened "Data" tab
- [ ] Pasted fix script
- [ ] Executed without errors
- [ ] Redeployed Co-Founder Agent
- [ ] `/api/health` returns success
- [ ] Did production database too

---

## 📞 Still Not Working?

Check the **Co-Founder Agent logs** in Railway:

1. Railway Dashboard
2. Co-Founder Agent service
3. Click **"Logs"** tab
4. Look for connection errors
5. Search for "idx_timestamp" or "DuplicateTableError"

If still seeing the error, the migration might be running automatically. Contact support with:

- Service name: "Co-Founder Agent"
- Error message (from logs)
- Database: "Staging" or "Production"

---

**Time to complete: 5 minutes**  
**Difficulty: Easy**  
**Risk: None (DROP INDEX IF EXISTS is safe)**
