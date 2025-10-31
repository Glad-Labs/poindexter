# PostgreSQL Duplicate Index Error - Fix Guide

**Error:** `DuplicateTableError: relation "idx_timestamp_desc" already exists`

**Status:** 🔴 **Staging environment startup blocked**

**Root Cause:** The `logs` table was defined with both old and new index names, causing a migration conflict when the database already has the old index.

---

## 🔍 Problem Analysis

**In `src/cofounder_agent/models.py` (Log class, line 499-500):**

```python
__table_args__ = (
    Index('idx_log_level_timestamp', 'level', 'timestamp'),
    Index('idx_log_timestamp_desc', 'timestamp'),  # ← Current code expects this
)
```

**In the staging database:** The old index `idx_timestamp_desc` already exists

**Conflict:** When SQLAlchemy tries to create the new index `idx_log_timestamp_desc`, PostgreSQL complains because:

1. The old `idx_timestamp_desc` index already exists on the `logs` table
2. They're both based on the same `timestamp` column
3. SQLAlchemy doesn't know how to handle the mismatch

---

## ✅ Solution (Choose One)

### Option A: Quick Fix (Drop Old Indexes)

**Use this if** you're in staging and can afford a brief database maintenance window.

```sql
-- Connect to your staging PostgreSQL database and run:
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;

-- Then restart the Co-Founder Agent
-- SQLAlchemy will automatically recreate the indexes with correct names
```

**When to use:** Staging environment, quick recovery

---

### Option B: Database Migration (Production Safe)

**Use this if** you want a permanent, version-controlled fix.

Creates a new Alembic migration file:

```bash
# In src/cofounder_agent directory
alembic revision -m "fix_duplicate_indexes_rename_indices"
```

Then edit the generated migration file:

```python
# alembic/versions/XXXX_fix_duplicate_indexes.py
def upgrade():
    # Drop old indexes (PostgreSQL will handle missing ones)
    op.execute("DROP INDEX IF EXISTS idx_timestamp_desc CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_service CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_timestamp_category CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_level_timestamp CASCADE")

def downgrade():
    pass
```

Then run the migration:

```bash
alembic upgrade head
```

**When to use:** Production, or when you want version-controlled migrations

---

### Option C: Restart with Fresh Migration (Development Only)

**Use this if** you're in local development and can afford to lose data.

```bash
# Delete the database file
rm .tmp/data.db

# Or for PostgreSQL, drop and recreate the database
psql -U postgres -c "DROP DATABASE glad_labs;"
psql -U postgres -c "CREATE DATABASE glad_labs;"

# Then restart the application
python -m uvicorn main:app --reload
```

**When to use:** Local development only, not safe for staging/production

---

## 🚀 Recommended Fix for Your Staging Environment

Since you're in **staging** and the error shows `startup_complete: true`, the app is running but reporting degraded status. Here's the safest approach:

### Step 1: Connect to Staging PostgreSQL

```bash
# Get your PostgreSQL connection string from Railway
# Format: postgresql://user:password@host:port/dbname
psql "your-connection-string-here"
```

### Step 2: Run the Fix Script

```sql
-- Drop duplicate indexes
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;

-- Verify indexes were dropped
SELECT indexname, tablename FROM pg_indexes
WHERE indexname LIKE 'idx_%' ORDER BY tablename, indexname;
```

### Step 3: Restart the Co-Founder Agent

The application will automatically:

1. Detect the missing indexes
2. Create the new ones with correct names
3. Continue normal startup

```bash
# In Railway dashboard:
1. Go to Co-Founder Agent service
2. Click "Redeploy" or restart
```

### Step 4: Verify Recovery

```bash
# Check health endpoint
curl https://staging-api.railway.app/api/health

# Expected response:
{
  "status": "healthy",
  "timestamp": "2025-10-30T...",
  "version": "1.0.0"
}
```

---

## 🔧 Permanent Prevention

To prevent this in the future, update `src/cofounder_agent/models.py`:

**Current (problematic):**

```python
class Log(Base):
    __tablename__ = "logs"
    __table_args__ = (
        Index('idx_log_level_timestamp', 'level', 'timestamp'),
        Index('idx_log_timestamp_desc', 'timestamp'),  # ← Redundant
    )
```

**Better (reduces redundancy):**

```python
class Log(Base):
    __tablename__ = "logs"
    __table_args__ = (
        Index('idx_log_level_timestamp', 'level', 'timestamp'),  # Composite index, most useful
    )

    # Also add index on timestamp column only (simple queries)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow,
                      nullable=False, server_default=func.now(), index=True)
```

**Why:**

- `index=True` on the column creates `idx_logs_timestamp` (SQLAlchemy naming)
- The composite `idx_log_level_timestamp` handles level+timestamp queries
- No redundant single-column indexes

---

## 📋 Troubleshooting Checklist

- [ ] Can I connect to the staging PostgreSQL database? → If no, check Railway PostgreSQL credentials
- [ ] Did I run the DROP INDEX commands? → If no, do that first
- [ ] Did I restart the Co-Founder Agent service? → If no, trigger redeploy in Railway
- [ ] Is `/api/health` now returning `"status": "healthy"`? → If no, check logs

### Check Logs

**In Railway dashboard:**

1. Click on Co-Founder Agent service
2. Go to "Deployments" tab
3. Click on latest deployment
4. Scroll to "Logs"
5. Look for "Application startup complete" message

---

## 📞 If This Doesn't Work

**Common issues and solutions:**

| Issue                                  | Solution                                                                                                     |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| "permission denied" in SQL             | Your PostgreSQL user doesn't have DROP INDEX permission. Contact admin.                                      |
| "connection refused"                   | Check DATABASE_URL in Railway environment variables. Should be `postgresql://user:password@host:port/dbname` |
| App still crashes after restart        | Check if there are OTHER migrations that haven't been applied. Run `alembic upgrade head`                    |
| Health endpoint still returns degraded | Check logs for other errors. May have multiple issues.                                                       |

---

## 📚 Files Involved

| File                                           | Purpose                     | Status                  |
| ---------------------------------------------- | --------------------------- | ----------------------- |
| `src/cofounder_agent/models.py` (line 499-500) | Log model index definitions | ✅ Correct              |
| `src/cofounder_agent/database.py`              | Database initialization     | ✅ OK                   |
| `src/cofounder_agent/main.py`                  | App startup                 | ✅ OK                   |
| `migrations/versions/001_initial_schema.py`    | Initial migration           | ⚠️ May have old indexes |
| `migrations/fix_duplicate_indexes.sql`         | Manual fix script           | ✅ Ready to use         |

---

## 🎯 Next Steps

1. **Immediate:** Run the DROP INDEX fix script above
2. **Verify:** Check `/api/health` endpoint
3. **Prevent:** Consider updating the Log model as suggested
4. **Document:** Add this fix to your deployment checklist

---

**Created:** October 30, 2025  
**Status:** Ready to implement  
**Estimated time:** 5-10 minutes  
**Downtime:** < 1 minute (during restart)
