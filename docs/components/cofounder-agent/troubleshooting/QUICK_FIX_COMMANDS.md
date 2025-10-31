# 🚀 Quick Reference: PostgreSQL Duplicate Index Fix

**Error:** `PostgreSQL connection failed: DuplicateTableError: relation "idx_timestamp_desc" already exists`

---

## ⚡ 30-Second Summary

**The Problem:**

- Old indexes like `idx_timestamp_desc` conflict with new ones like `idx_log_timestamp_desc`
- When Co-Founder Agent starts, SQLAlchemy migration fails

**The Solution:**

- Drop old indexes using SQL script
- Restart Co-Founder Agent service
- Verify health endpoint works

---

## 🎯 Fastest Method: Railway Web Console

### 1. Login to Railway

Go to [Railway Dashboard](https://railway.app/dashboard)

### 2. Select PostgreSQL (Staging or Production)

Click: PostgreSQL service → "Data" tab

### 3. Paste & Run This SQL

```sql
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;
```

### 4. Restart Co-Founder Agent

Railway Dashboard → Co-Founder Agent → Deployments → Redeploy (latest)

### 5. Verify

```bash
curl https://your-api.railway.app/api/health
```

Expected: `{"status": "healthy", ...}`

---

## 📋 Step-by-Step Guides

| Situation                       | Guide                                                                                         |
| ------------------------------- | --------------------------------------------------------------------------------------------- |
| I want to fix it NOW in Railway | [RAILWAY_WEB_CONSOLE_STEPS.md](troubleshooting/RAILWAY_WEB_CONSOLE_STEPS.md)                  |
| I have psql installed locally   | [RAILWAY_DATABASE_FIX.md](RAILWAY_DATABASE_FIX.md#-option-2-railway-cli)                      |
| I need to install psql first    | [RAILWAY_DATABASE_FIX.md](RAILWAY_DATABASE_FIX.md#-option-3-local-psql-installation-advanced) |
| General troubleshooting         | [RAILWAY_DATABASE_FIX.md](RAILWAY_DATABASE_FIX.md#-troubleshooting)                           |

---

## 🔧 Connection Strings (for local psql)

### Get from Railway

1. PostgreSQL service → Settings tab
2. Look for "Connect" section
3. Copy the Postgres URL

### Format

```bash
postgresql://user:password@host:port/dbname
```

### Example Local Connection

```bash
psql "postgresql://postgres:password@localhost:5432/railway" -f fix_staging_indexes.sql
```

---

## 📊 Files in This Fix

| File                                           | Purpose                                         |
| ---------------------------------------------- | ----------------------------------------------- |
| `fix_staging_indexes.sql`                      | SQL script to drop old indexes                  |
| `troubleshooting/RAILWAY_WEB_CONSOLE_STEPS.md` | Visual step-by-step guide (Railway web console) |
| `RAILWAY_DATABASE_FIX.md`                      | Comprehensive guide (3 methods)                 |
| `troubleshooting/QUICK_FIX_COMMANDS.md`        | This file - quick reference                     |

---

## ✅ Verification Checklist

After running the fix:

- [ ] SQL script ran without errors
- [ ] Old indexes dropped (saw "DROP INDEX" output)
- [ ] Co-Founder Agent restarted successfully
- [ ] `/api/health` returns success status
- [ ] No errors in Railway logs (Co-Founder Agent service)
- [ ] Both staging AND production databases fixed

---

## 🆘 Emergency Contacts

**In Railway Dashboard:**

1. Co-Founder Agent → Logs
2. PostgreSQL → Logs
3. Look for errors about "DuplicateTableError" or "idx_timestamp"

**Common errors:**

- "Permission Denied" → Try `SET search_path TO public;` first
- "Query Limit Exceeded" → Run script in 2 parts
- Still failing → Check Railway logs for real error message

---

## 📚 Related Documentation

- [POSTGRES_DUPLICATE_INDEX_ERROR.md](../POSTGRES_DUPLICATE_INDEX_ERROR.md) - Full technical details
- [RAILWAY_DATABASE_FIX.md](../RAILWAY_DATABASE_FIX.md) - Complete fix guide
- [QUICK_OPTIMIZATION_GUIDE.md](../QUICK_OPTIMIZATION_GUIDE.md) - Index optimization

---

**Status:** ✅ Production Ready  
**Time to Fix:** 5 minutes  
**Risk Level:** ⭐ Very Low (DROP INDEX IF EXISTS is safe)
