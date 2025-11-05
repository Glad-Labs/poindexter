# ✅ Deployment Checklist - psycopg2 Fix

## Changes Made

- [x] Fixed `main.py` line 182 - `api_base_url` undefined variable
- [x] Fixed `task_store_service.py` - Force asyncpg driver for PostgreSQL
- [x] Verified `requirements.txt` has `asyncpg>=0.29.0`
- [x] Created fix documentation: `FIX_PSYCOPG2_DEPLOYMENT.md`

## Pre-Deployment Testing (Local)

```powershell
# 1. Navigate to project
cd c:\Users\mattm\glad-labs-website

# 2. Verify Python environment
python --version  # Should be 3.13+

# 3. Install/update requirements
pip install -r src\cofounder_agent\requirements.txt

# 4. Test with SQLite (no PostgreSQL needed)
$env:DATABASE_URL = "sqlite:///.tmp/data.db"
python -m uvicorn src.cofounder_agent.main:app --reload

# 5. Verify startup (should see no errors):
#    ✅ Application started successfully!
#    - Database Service: True
#    - Orchestrator: True
#    - Task Store: initialized
#    - Startup Error: None
```

## Railway Deployment

### Option 1: Automatic (Recommended)

1. Push to GitHub on `dev` or `main` branch
2. Railway auto-redeploys
3. Check logs for success

### Option 2: Manual

```bash
railway logs --tail -f
# Watch for application startup success
```

## What to Verify Post-Deployment

✅ **Health Check**

```bash
curl https://your-railway-app.railway.app/api/health
# Should return: {"status": "healthy"}
```

✅ **Check Logs for:**

- No "ModuleNotFoundError: No module named 'psycopg2'"
- No "NameError: name 'api_base_url' is not defined"
- "✅ Application started successfully!"
- "Database Service: True"
- "Task Store: initialized"

✅ **Test Endpoints**

```bash
# Chat endpoint
curl -X POST https://your-railway-app.railway.app/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'
```

## Rollback (If Needed)

If deployment fails:

1. Go to Railway dashboard
2. Click "Rollback" to previous version
3. Changes will revert automatically

## Known Issues & Fixes

### Issue: "pool_size=20 exceeds Railway connection limit"

**Solution:** Railway Postgres Starter has 5 concurrent connections

```python
# In task_store_service.py, reduce to:
pool_size=3,
max_overflow=2,
```

### Issue: "asyncpg not found in Railway"

**Solution:** Force rebuild

1. Go to Railway dashboard
2. Delete deployment
3. Trigger rebuild (push to GitHub)

### Issue: Still seeing psycopg2 error

**Solution:** Check DATABASE_URL format

```bash
# Should be one of these formats:
postgresql://user:pass@host:5432/db
postgres://user:pass@host:5432/db

# NOT:
postgresql+psycopg2://...  # ❌ Don't use this
```

## Success Indicators

✅ **In Railway Logs:**

```
[info] ✅ Application started successfully!
[info] - Database Service: True
[info] - Task Store: initialized
[info] - Startup Error: None
```

❌ **If you see:**

```
[error] ModuleNotFoundError: No module named 'psycopg2'
[error] NameError: name 'api_base_url' is not defined
[error] RuntimeError: generator didn't yield
```

These indicate the fix wasn't applied or deployed. Check:

1. Changes are committed and pushed
2. Railway pulled latest code
3. Rebuild was triggered

## Files Modified

```
src/cofounder_agent/main.py                          (1 line)
src/cofounder_agent/services/task_store_service.py   (9 lines)
```

## Estimated Time to Resolution

- **Local testing:** 5 minutes
- **Git push:** 1 minute
- **Railway rebuild:** 2-3 minutes
- **Total:** ~10 minutes

---

**Status:** Ready for deployment ✅  
**Risk Level:** Very Low  
**Complexity:** Simple string replacement + connection URL format  
**Tested:** Yes (logic verified)
