# PostgreSQL Index Error - Complete Troubleshooting & Prevention

**Issue Date:** October 30, 2025  
**Environment:** Staging  
**Service:** Co-Founder Agent  
**Status:** 🟡 Degraded (startup error: duplicate index)  
**Resolution Time:** 5-10 minutes

---

## 🔍 Root Cause Analysis

### What Happened?

When the Co-Founder Agent started in staging, SQLAlchemy tried to initialize the database tables. During this process:

1. **SQLAlchemy expected to create:** `idx_log_timestamp_desc` (on `logs.timestamp`)
2. **PostgreSQL already had:** `idx_timestamp_desc` (an old/residual index)
3. **Result:** `DuplicateTableError` - Can't create index, already exists

### Why Did This Happen?

This typically occurs when:

- Previous migrations had slightly different index naming conventions
- Database schema wasn't cleaned up between deployments
- Multiple code changes with conflicting index definitions
- Migration scripts ran multiple times (idempotency issue)

### Evidence

```text
Startup Error:
  sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError
  <class 'asyncpg.exceptions.DuplicateTableError'>:
    relation "idx_timestamp_desc" already exists
  [SQL: CREATE INDEX idx_timestamp_desc ON logs (timestamp)]
```

**Note:** The error says `CREATE INDEX idx_timestamp_desc` (old name), but the code expects `idx_log_timestamp_desc` (new name).

---

## ✅ Immediate Fix (For You Now)

### Option 1: Quick SQL Fix (Recommended)

#### Step 1: Get PostgreSQL Connection String

```text
Railway Dashboard → PostgreSQL Service → Connect Tab
Copy the connection string (looks like):
postgresql://user:password@host:port/dbname
```

#### Step 2: Connect and Run SQL

```bash
# Option A: Command line
psql "postgresql://user:password@host:port/dbname" << 'EOF'
DROP INDEX IF EXISTS idx_timestamp_desc CASCADE;
DROP INDEX IF EXISTS idx_service CASCADE;
DROP INDEX IF EXISTS idx_timestamp_category CASCADE;
DROP INDEX IF EXISTS idx_level_timestamp CASCADE;
EOF

# Option B: Using Railway's web UI
1. Go to Railway → PostgreSQL service
2. Click "Query" tab
3. Paste the 4 DROP INDEX lines above
4. Click Execute
```

#### Step 3: Restart Service

```text
Railway Dashboard → Co-Founder Agent → Deployments → Latest → Redeploy
```

#### Step 4: Verify

```bash
# Wait 2-3 minutes, then test:
curl https://your-staging-api.railway.app/api/health

# Should respond:
{
  "status": "healthy",
  "timestamp": "2025-10-30T12:34:56.789Z",
  "version": "1.0.0"
}
```

---

### Option 2: Complete Database Reset (Nuclear Option)

**Use only if Option 1 fails or you want a clean slate**

```text
Railway Dashboard → PostgreSQL Service → Delete
Then → Add new PostgreSQL Service

(This will delete all data - use for staging/dev only!)
```

---

## 🛡️ Prevention (For Future)

### Permanent Code Fix

The issue is resolved by ensuring consistent index naming. Update `models.py`:

**Current (has redundant indexes):**

```python
class Log(Base):
    __tablename__ = "logs"
    __table_args__ = (
        Index('idx_log_level_timestamp', 'level', 'timestamp'),
        Index('idx_log_timestamp_desc', 'timestamp'),  # ← Redundant!
    )

    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
```

**Better (removes redundancy):**

```python
class Log(Base):
    __tablename__ = "logs"
    __table_args__ = (
        Index('idx_log_level_timestamp', 'level', 'timestamp'),
    )

    # Single-column index created via index=True
    timestamp = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True  # ← Creates idx_logs_timestamp automatically
    )
```

**Why this works:**

- Composite index `idx_log_level_timestamp` handles queries filtering by both level AND timestamp
- Single-column index on timestamp handles time-only queries
- No redundant indexes on the same column
- SQLAlchemy naming is consistent (`idx_logs_timestamp` vs custom names)

### Review Other Tables

Check for similar issues in other models:

```bash
# Search for redundant single-column indexes
grep -n "Index(" src/cofounder_agent/models.py | grep -i "timestamp\|level\|service"

# Look for duplicate index patterns:
# - Multiple indexes on same column
# - Column with both index=True AND __table_args__ Index()
```

**Example redundancy patterns to fix:**

```python
# ❌ BAD: Duplicate indexes
Column(String(100), index=True)  # Creates: idx_<table>_<column>
# PLUS:
Index('custom_index_name', '<column>')  # Creates: custom_index_name

# ✅ GOOD: One way or the other, not both
Column(String(100), index=True)  # Use for simple indexes
# OR
__table_args__ = (
    Index('custom_name', 'col1', 'col2'),  # Use for composite indexes
)
```

---

## 🔍 Diagnostic Queries

### Check Existing Indexes

```sql
-- List all indexes in the logs table
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'logs'
ORDER BY indexname;

-- Expected output (after fix):
-- idx_log_level_timestamp  | CREATE INDEX idx_log_level_timestamp ON public.logs USING btree (level, "timestamp")
-- idx_logs_timestamp       | CREATE INDEX idx_logs_timestamp ON public.logs USING btree ("timestamp")
```

### Check for Index Conflicts

````sql
-- Find all duplicate column indexes (indexes on same column)
SELECT
    a.tablename,
    a.indexname as index1,
    b.indexname as index2,
    a.indexdef
FROM pg_indexes a
JOIN pg_indexes b ON a.tablename = b.tablename
WHERE a.indexname < b.indexname  -- Avoid duplicates in result
  AND a.indexdef LIKE '%' || a.indexname || '%'
  AND b.indexdef LIKE '%' || b.indexname || '%'
  AND a.tablename NOT LIKE 'pg_%'
ORDER BY a.tablename;
```---

## 📋 Step-by-Step Recovery Checklist

- [ ] **Identified the problem:** Duplicate index `idx_timestamp_desc` on logs table
- [ ] **Accessed PostgreSQL:** Connected to staging database (got connection string from Railway)
- [ ] **Ran DROP INDEX SQL:** Executed the 4 drop statements successfully
- [ ] **Verified drops:** Confirmed no errors in execution
- [ ] **Restarted service:** Clicked Redeploy in Railway for Co-Founder Agent
- [ ] **Waited for restart:** Service deployed and restarted (2-3 minutes)
- [ ] **Tested health endpoint:** `curl /api/health` returns `status: healthy`
- [ ] **Checked logs:** No index errors in Co-Founder Agent logs
- [ ] **Monitored for 5 min:** No other errors appeared
- [ ] **Updated documentation:** Added this fix to deployment runbook
- [ ] **Planned prevention:** Will implement model changes to prevent recurrence

---

## 📞 Support Escalation

**If the quick fix doesn't work:**

1. **Check Error Details**

   ```text
   Railway Dashboard → Co-Founder Agent → Latest Deployment → Logs
   Search for: "idx_" or "DuplicateTableError"
````

2. **Verify Database Connection**

   ```sql
   -- In PostgreSQL, run:
   SELECT 1;  -- Should return 1 if connection works
   ```

3. **Check for Other Migrations**

   ```sql
   -- Check migration history (if using Alembic)
   SELECT * FROM alembic_version;
   ```

4. **Force Clean Migration** (Last resort)

   ```text
   1. Export current database (backup)
   2. Drop all tables: DROP SCHEMA public CASCADE; CREATE SCHEMA public;
   3. Run migrations from scratch: alembic upgrade head
   ```

---

## 📚 Related Files

| File                                 | Purpose                 | Action                     |
| ------------------------------------ | ----------------------- | -------------------------- |
| `src/cofounder_agent/models.py`      | Log model definition    | Review + update indexes    |
| `src/cofounder_agent/database.py`    | Database initialization | No changes needed          |
| `migrations/001_initial_schema.py`   | Initial migration       | May need review            |
| `migrations/fix_staging_indexes.sql` | Quick fix script        | Use for immediate recovery |
| `DUPLICATE_INDEX_FIX.md`             | User-friendly fix guide | Reference guide            |

---

## 🎯 Action Items

### Immediate (Next 10 minutes)

- [ ] Run the DROP INDEX SQL
- [ ] Restart Co-Founder Agent service
- [ ] Verify health endpoint

### Short Term (Next 24 hours)

- [ ] Review other table indexes for similar issues
- [ ] Test full application flow
- [ ] Monitor logs for stability

### Medium Term (Next sprint)

- [ ] Implement model.py preventive fix
- [ ] Add index review to code review checklist
- [ ] Document index naming conventions
- [ ] Create automated index validation in tests

### Long Term (Next quarter)

- [ ] Migrate to Alembic for version-controlled migrations
- [ ] Set up database schema tests (validate indexes exist)
- [ ] Automate index optimization (identify unused indexes)
- [ ] Create deployment checklist with pre-flight database checks

---

## 📊 Quick Reference Card

```
PROBLEM:      Duplicate index error on logs table
CAUSE:        Old index name conflicts with new index name
SOLUTION:     DROP old indexes, restart service
TIME:         5-10 minutes to fix
DOWNTIME:     < 1 minute
RISK:         Low (no data loss, reversible)
PREVENTION:   Update model.py, review indexes, test migrations
```

---

**Created:** October 30, 2025  
**Last Updated:** October 30, 2025  
**Status:** ✅ Complete and ready to implement  
**Confidence:** High (tested pattern, well-documented)
