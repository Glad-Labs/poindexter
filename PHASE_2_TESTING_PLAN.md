# Phase 2 Testing & Integration Plan

**Status:** Phase 2 Code Conversion ✅ COMPLETE  
**Next:** Phase 2 Testing (Unit Tests + Integration)  
**Timeline:** 4-6 hours estimated  
**Goal:** Validate memory_system.py works correctly with PostgreSQL backend

---

## 🎯 What's Done

✅ **memory_system.py (823 lines) fully converted from SQLite to PostgreSQL**

- All 12+ database functions now async/await with asyncpg
- All SQL parameters converted to PostgreSQL style ($1, $2)
- All row access converted to asyncpg.Record named fields
- All sqlite3 references removed (verified: 0 matches)
- All db_path references removed (verified: 0 matches)
- Code compiles without critical errors
- FastAPI server successfully reloading on changes ✅

**Services Running:**

- ✅ Strapi CMS on port 1337 (SQLite)
- ✅ FastAPI Co-founder Agent on port 8000 (PostgreSQL)
- ✅ Ollama AI on port 11434 (local models)
- ✅ Auto-reload working for source code changes

---

## 📋 Immediate Next Steps (Pick One)

### Option A: Write Unit Tests (Recommended - Most Thorough)

**File:** Create `src/cofounder_agent/tests/test_memory_system.py`

**Test Cases to Write:**

1. **Initialization Tests**

   ```python
   @pytest.mark.asyncio
   async def test_initialize():
       """Test async memory system initialization with db_pool"""

   @pytest.mark.asyncio
   async def test_verify_tables_exist():
       """Test PostgreSQL table verification"""
   ```

2. **Memory CRUD Tests**

   ```python
   @pytest.mark.asyncio
   async def test_store_and_retrieve_memory():
       """Store memory and verify retrieval"""

   @pytest.mark.asyncio
   async def test_memory_persistence():
       """Verify memories persisted to PostgreSQL"""

   @pytest.mark.asyncio
   async def test_update_memory_access():
       """Verify access count and timestamp updated"""
   ```

3. **Knowledge Cluster Tests**

   ```python
   @pytest.mark.asyncio
   async def test_knowledge_cluster_creation():
       """Create and retrieve knowledge clusters"""

   @pytest.mark.asyncio
   async def test_cluster_upsert():
       """Verify cluster ON CONFLICT upsert works"""
   ```

4. **Learning Pattern Tests**

   ```python
   @pytest.mark.asyncio
   async def test_store_learning_pattern():
       """Store and verify learning patterns"""

   @pytest.mark.asyncio
   async def test_identify_patterns():
       """Identify patterns from memories"""
   ```

5. **User Preference Tests**

   ```python
   @pytest.mark.asyncio
   async def test_learn_user_preference():
       """Learn and retrieve user preferences"""

   @pytest.mark.asyncio
   async def test_preference_upsert():
       """Verify preference ON CONFLICT upsert"""
   ```

6. **Advanced Tests**

   ```python
   @pytest.mark.asyncio
   async def test_forget_outdated_memories():
       """Test batch deletion of old memories"""

   @pytest.mark.asyncio
   async def test_get_memory_summary():
       """Test memory statistics and analytics"""

   @pytest.mark.asyncio
   async def test_concurrent_operations():
       """Verify thread-safe concurrent access"""

   @pytest.mark.asyncio
   async def test_error_handling():
       """Test database connection error handling"""
   ```

**Command to Run Tests:**

```bash
cd src/cofounder_agent
pytest tests/test_memory_system.py -v --cov=. --cov-report=html
```

**Expected Duration:** 4-6 hours  
**Coverage Target:** 95%+

---

### Option B: Quick Integration Test (Fastest)

**What to Do:**

1. Run the FastAPI server with PostgreSQL backend
2. Make a POST request to create a task
3. Verify memory system is working
4. Check logs for any errors

**Commands:**

```powershell
# Already running! Just check if working:
curl http://localhost:8000/api/health

# Create a test task (this uses memory system internally)
curl -X POST http://localhost:8000/api/tasks ^
  -H "Content-Type: application/json" ^
  -d '{"title": "Test Memory", "description": "Memory system test", "type": "content_generation"}'

# Check co-founder agent logs for errors
Get-Content -Path "logs/cofounder_agent.log" -Tail 50
```

**Expected Duration:** 15 minutes

---

### Option C: Performance Validation (Data-Driven)

**What to Test:**

- Semantic search performance: <500ms
- Memory storage: <100ms
- Memory recall: <200ms
- Batch operations: <1s

**Commands:**

```bash
cd src/cofounder_agent

# Run performance benchmarks
pytest tests/test_memory_performance.py -v --benchmark

# Or manually test response times
python -c "
import asyncio
import time

async def test():
    # Time memory operations
    start = time.time()
    # ... call memory_system methods ...
    elapsed = time.time() - start
    print(f'Operation took {elapsed*1000:.1f}ms')
"
```

**Expected Duration:** 1-2 hours

---

## 🚀 Recommended Sequence

**Phase 2A (Today - 1-2 hours):**

1. ✅ Code Conversion - DONE
2. ✅ Service Verification - DONE (services running)
3. ⏳ Quick Integration Test (Option B) - Take 15 min to verify basic functionality

**Phase 2B (Next - 4-6 hours):** 4. ⏳ Unit Tests (Option A) - Write comprehensive test suite

**Phase 2C (Final - 1-2 hours):** 5. ⏳ Performance Validation (Option C) - Ensure SLA compliance

**Phase 3 (After Phase 2 Complete - Nov 11+):** 6. ⏳ Integration with other agents - Build on proven memory system

---

## 📝 How to Proceed

### Method 1: Quick Check (15 minutes)

Run the quick integration test to verify basic functionality works:

```powershell
# In PowerShell from project root
curl http://localhost:8000/api/health | ConvertFrom-Json | Format-Table

# If output shows {"status": "healthy"}, we're good!
# Try creating a task to exercise memory system
```

**Expected Output:**

```
status     timestamp agents
------     --------- ------
healthy    2025-11-...
```

### Method 2: Full Unit Tests (4-6 hours)

Create comprehensive test suite that validates all memory operations:

```powershell
# From src/cofounder_agent/
pytest tests/test_memory_system.py -v --cov=.
```

**This Will:**

- ✅ Validate all 12+ database functions work correctly
- ✅ Catch any PostgreSQL syntax issues
- ✅ Measure actual performance
- ✅ Generate coverage report
- ✅ Prepare for Phase 3 integration

### Method 3: Hybrid Approach (Recommended)

1. **First (15 min):** Run quick integration test to verify it works
2. **Then (4-6 hrs):** Write and run unit tests
3. **Finally (1-2 hrs):** Performance validation

---

## 📊 Success Criteria for Phase 2

**Must Pass:**

- ✅ All unit tests pass (100% success rate)
- ✅ No SQLite references remaining (0 occurrences)
- ✅ Memory operations work with PostgreSQL
- ✅ All async/await patterns working correctly
- ✅ Connection pool lifecycle correct

**Should Pass:**

- ✅ Performance meets SLA (<500ms for semantic search)
- ✅ Error handling works for connection failures
- ✅ Concurrent access thread-safe
- ✅ Memory cleanup (forget_outdated_memories) works

**Code Quality:**

- ✅ >95% test coverage
- ✅ All type hints present
- ✅ Comprehensive error handling
- ✅ Clear, readable code

---

## 🎯 Decision: What Should We Do?

**My Recommendation:** Start with **Option B (Quick Integration Test)** to verify everything is working, then proceed with **Option A (Unit Tests)** for comprehensive validation.

### Quick Integration Test (Now - 15 min)

```powershell
# Check health
curl http://localhost:8000/api/health

# Check Ollama connectivity
curl http://localhost:11434/api/tags

# Verify PostgreSQL is running (from database.py)
# Then check if memory system is being used in tasks
```

### Full Unit Tests (Next - 4-6 hours)

```powershell
cd src/cofounder_agent
pytest tests/test_memory_system.py -v --cov=. --cov-report=html
```

---

## 📚 What to Verify During Testing

### Database Connectivity

```python
# Verify PostgreSQL connection works
async with db_pool.acquire() as conn:
    result = await conn.fetchval("SELECT 1")
    assert result == 1, "PostgreSQL not responding"
```

### Memory Operations

```python
# Test basic CRUD
memory = Memory(...)
await memory_system.store_memory(...)
retrieved = await memory_system.recall_memories(...)
assert retrieved is not None
```

### Error Handling

```python
# Test connection failures
try:
    await conn.execute("invalid SQL")
except Exception as e:
    assert error_handled_gracefully(e)
```

### Performance

```python
# Test response times
import time
start = time.time()
memories = await memory_system.recall_memories(query)
elapsed = time.time() - start
assert elapsed < 0.5, f"Semantic search took {elapsed}s, SLA: <500ms"
```

---

## 🔗 Related Files

- **memory_system.py** - The file we just converted (823 lines)
- **database.py** - PostgreSQL setup (imports, schemas, pool creation)
- **main.py** - FastAPI app startup (initializes memory_system)
- **tests/conftest.py** - pytest fixtures for db_pool

---

## 📞 Help & Troubleshooting

**If tests fail with connection errors:**

```bash
# Check PostgreSQL is running
# Check DATABASE_URL in .env is correct
# Check database.py MEMORY_TABLE_SCHEMAS match memory_system expectations
```

**If tests fail with asyncpg errors:**

```bash
# Verify asyncpg is installed
pip install asyncpg

# Check connection pool initialization
# Ensure pool is created before memory_system.initialize()
```

**If tests fail with SQL errors:**

```bash
# Check PostgreSQL parameter syntax: $1, $2 (not ?)
# Check ON CONFLICT clauses are correct
# Verify table schemas exist (run: python database.py)
```

---

## ✅ Next Action

**Choose One:**

1. **Quick Test (15 min):** `curl http://localhost:8000/api/health` → See if it works
2. **Full Tests (4-6 hrs):** `pytest tests/test_memory_system.py -v --cov=.` → Comprehensive validation
3. **Both (4.5-7 hrs):** Quick test first, then full test suite

**My Recommendation:** Do both - quick test now (verify), then full test suite (validate).

---

_Ready to proceed? Let me know which option you prefer, and I'll guide you through it!_
