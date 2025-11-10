# 🚀 PHASE 2 - Memory System Migration Plan

**Date Started:** November 8, 2025  
**Estimated Duration:** 2-3 days  
**Status:** IN PROGRESS  
**Primary File:** `src/cofounder_agent/memory_system.py` (845 lines)

---

## 📋 Executive Summary

The `memory_system.py` file currently uses SQLite for persistent memory storage. Phase 2 converts this to PostgreSQL to complete the SQLite removal initiative.

**Key Facts:**

- File Size: 845 lines
- Database Operations: ~10 major sqlite3.connect() blocks
- Tables to Migrate: 5 tables
- Complexity: High (complex data structures, async operations, embeddings)
- Impact: Critical - AI memory persistence depends on this

---

## 🏗️ Architecture Overview

### Current State (SQLite)

```python
# Current pattern in memory_system.py
def _init_database(self):
    """Initialize SQLite database for memory storage"""
    with sqlite3.connect(self.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS memories ...""")

async def _load_persistent_memory(self):
    """Load persistent memory from database"""
    with sqlite3.connect(self.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""SELECT * FROM memories ...""")
```

### Target State (PostgreSQL with asyncpg)

```python
# Target pattern
async def _init_database(self):
    """Initialize PostgreSQL database for memory storage"""
    async with self.db_pool.acquire() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS memories ...""")

async def _load_persistent_memory(self):
    """Load persistent memory from database"""
    async with self.db_pool.acquire() as conn:
        rows = await conn.fetch("""SELECT * FROM memories ...""")
```

---

## 📊 Database Schema Migration

### Table 1: memories

**Current SQLite:**

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    importance INTEGER NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    tags TEXT,
    related_memories TEXT,
    metadata TEXT,
    embedding BLOB
)
```

**Target PostgreSQL:**

```sql
CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    memory_type VARCHAR(50) NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    tags TEXT[],  -- PostgreSQL array type
    related_memories UUID[],  -- Array of UUIDs for relations
    metadata JSONB,  -- JSON for flexible metadata
    embedding VECTOR(384),  -- Vector type for embeddings
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_memory_type (memory_type),
    INDEX idx_importance (importance),
    INDEX idx_created_at (created_at)
)
```

### Table 2: knowledge_clusters

```sql
-- SQLite → PostgreSQL
CREATE TABLE knowledge_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    memories UUID[],  -- Array of memory IDs
    confidence REAL NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    importance_score REAL NOT NULL,
    topics TEXT[],  -- Array of topics
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### Table 3: learning_patterns

```sql
CREATE TABLE learning_patterns (
    pattern_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_type VARCHAR(50) NOT NULL,  -- "preference", "workflow", "decision_pattern"
    description TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    confidence REAL NOT NULL,
    examples TEXT[],  -- Array of example strings
    discovered_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### Table 4: user_preferences

```sql
CREATE TABLE user_preferences (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR(100)
)
```

### Table 5: conversation_sessions

```sql
CREATE TABLE conversation_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    topics TEXT[],
    summary TEXT,
    importance REAL DEFAULT 0.5,
    duration_seconds INTEGER GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (ended_at - started_at))
    ) STORED
)
```

---

## 🔄 Migration Steps

### Step 1: Update Database Service (database.py)

**Changes Needed:**

- Add memory table schema definitions to database.py
- Create async functions to initialize memory tables
- Add connection pool management for memory operations

**Code Pattern:**

```python
# In database.py - add memory schemas
MEMORY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS memories (...)
CREATE TABLE IF NOT EXISTS knowledge_clusters (...)
CREATE TABLE IF NOT EXISTS learning_patterns (...)
CREATE TABLE IF NOT EXISTS user_preferences (...)
CREATE TABLE IF NOT EXISTS conversation_sessions (...)
"""

async def init_memory_tables(engine):
    """Initialize memory tables in PostgreSQL"""
    async with engine.begin() as conn:
        for statement in MEMORY_TABLES_SQL.split(';'):
            if statement.strip():
                await conn.execute(text(statement))
```

### Step 2: Update memory_system.py - Database Initialization

**Replace Function:** `_init_database()`

**Current (SQLite):**

```python
def _init_database(self):
    """Initialize SQLite database for memory storage"""
    with sqlite3.connect(self.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS memories ...""")
        # ... multiple table creates
        conn.commit()
```

**Target (PostgreSQL):**

```python
async def _init_database(self):
    """Initialize PostgreSQL database for memory storage"""
    try:
        async with self.db_pool.acquire() as conn:
            # Tables created by database service at startup
            # Just verify tables exist
            result = await conn.fetch("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name IN ('memories', 'knowledge_clusters',
                                  'learning_patterns', 'user_preferences',
                                  'conversation_sessions')
            """)

            if len(result) < 5:
                self.logger.error("Memory tables not initialized in PostgreSQL")
                raise RuntimeError("PostgreSQL memory tables missing")

            self.logger.info("Memory tables verified in PostgreSQL")
    except Exception as e:
        self.logger.error(f"Failed to initialize PostgreSQL memory database: {e}")
        raise
```

### Step 3: Update memory_system.py - Load Persistent Memory

**Replace Function:** `_load_persistent_memory()`

**Current (SQLite):**

```python
async def _load_persistent_memory(self):
    """Load persistent memory from database"""
    try:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT * FROM memories ...""")
            rows = cursor.fetchall()
            self.recent_memories = [self._row_to_memory(row) for row in rows]
```

**Target (PostgreSQL):**

```python
async def _load_persistent_memory(self):
    """Load persistent memory from database"""
    try:
        async with self.db_pool.acquire() as conn:
            # Load recent memories
            rows = await conn.fetch("""
                SELECT id, content, memory_type, importance, confidence,
                       created_at, last_accessed, access_count, tags,
                       related_memories, metadata, embedding
                FROM memories
                ORDER BY last_accessed DESC
                LIMIT $1
            """, self.max_recent_memories)

            self.recent_memories = [self._row_to_memory(row) for row in rows]
            self.logger.info(f"Loaded {len(self.recent_memories)} recent memories")
```

### Step 4: Update memory_system.py - Store Memory

**Replace Function:** `store_memory()`

**Current Pattern (SQLite):**

```python
def store_memory(self, memory: Memory):
    """Store memory in database"""
    with sqlite3.connect(self.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""INSERT INTO memories VALUES ...""")
        conn.commit()
```

**Target Pattern (PostgreSQL):**

```python
async def store_memory(self, memory: Memory):
    """Store memory in PostgreSQL"""
    async with self.db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO memories
            (id, content, memory_type, importance, confidence,
             created_at, last_accessed, access_count, tags,
             related_memories, metadata, embedding)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        memory.id, memory.content, memory.memory_type.value,
        memory.importance.value, memory.confidence,
        memory.created_at, memory.last_accessed, memory.access_count,
        memory.tags, memory.related_memories,
        json.dumps(memory.metadata) if memory.metadata else None,
        memory.embedding
        )
```

### Step 5: Update All Database Operations

**Functions to Convert (~10 total):**

1. `_init_database()` - Create tables
2. `_load_persistent_memory()` - Load memories
3. `store_memory()` - Insert single memory
4. `retrieve_memory()` - Get memory by ID
5. `search_memories()` - Semantic search
6. `update_memory()` - Update memory
7. `delete_memory()` - Delete memory
8. `store_knowledge_cluster()` - Store clusters
9. `store_learning_pattern()` - Store patterns
10. `get_user_preferences()` - Load preferences

**Pattern for Each:**

- Remove: `sqlite3.connect()` with context manager
- Add: `async with self.db_pool.acquire() as conn:`
- Replace: `cursor.execute()` with `await conn.execute()` or `fetch()`
- Remove: `conn.commit()`

### Step 6: Update Constructor

**Current:**

```python
def __init__(self, memory_dir: str = "ai_memory_system"):
    self.memory_dir = Path(memory_dir)
    self.db_path = self.memory_dir / "memory.db"
    self._init_database()  # Synchronous
```

**Target:**

```python
async def __init__(self, db_pool):
    """Initialize memory system with PostgreSQL connection pool"""
    self.db_pool = db_pool  # AsyncPG connection pool from database service
    self.logger = logging.getLogger("ai_memory_system")

    # Initialize tables and load data
    await self._init_database()
    await self._load_persistent_memory()
```

---

## 🔌 Integration Points

### With database.py

The memory system needs access to the PostgreSQL connection pool:

```python
# In main.py lifespan context
from src.cofounder_agent.memory_system import AIMemorySystem
from src.cofounder_agent.database import db_service

# Initialize memory system with connection pool
memory_system = await AIMemorySystem(db_service.pool)
# Store in app state
app.state.memory_system = memory_system
```

### With Other Agents

Agents access memory like this:

```python
# In content_agent.py or other agents
async def generate_content(self, task):
    # Get memory system from app context
    memory = app.state.memory_system

    # Retrieve related memories
    related = await memory.search_memories(task.topic)

    # Store new knowledge
    new_memory = Memory(...)
    await memory.store_memory(new_memory)
```

---

## 🧪 Testing Strategy

### Unit Tests (test_memory_system.py)

```python
# Test cases needed
class TestMemorySystemPostgreSQL:

    @pytest.mark.asyncio
    async def test_store_and_retrieve_memory(self):
        """Test storing and retrieving a memory"""

    @pytest.mark.asyncio
    async def test_semantic_search(self):
        """Test semantic similarity search"""

    @pytest.mark.asyncio
    async def test_knowledge_cluster_operations(self):
        """Test knowledge cluster CRUD"""

    @pytest.mark.asyncio
    async def test_learning_pattern_tracking(self):
        """Test learning pattern storage"""

    @pytest.mark.asyncio
    async def test_concurrent_memory_operations(self):
        """Test thread-safe concurrent operations"""
```

### Integration Tests

```python
# Test with actual agents
@pytest.mark.asyncio
async def test_memory_system_with_content_agent(app, memory_system):
    """Test memory system integration with agents"""
    # Create task
    # Generate content with memory
    # Verify memories are stored
    # Verify related memories retrieved
```

---

## ✅ Verification Checklist

- [ ] All sqlite3 imports removed from memory_system.py
- [ ] All database operations converted to async PostgreSQL
- [ ] Connection pool properly initialized
- [ ] All 5 tables created in PostgreSQL
- [ ] All 10 major functions converted
- [ ] Unit tests passing (100% pass rate)
- [ ] Integration tests passing
- [ ] No sql errors in logs
- [ ] Memory operations perform within SLA (<500ms)
- [ ] Embeddings properly stored and retrieved

---

## 📈 Success Criteria

1. **Code Quality:**
   - ✅ All SQLite references removed
   - ✅ All functions async/await
   - ✅ Proper error handling
   - ✅ Type hints complete

2. **Performance:**
   - ✅ Memory retrieval <100ms
   - ✅ Semantic search <500ms
   - ✅ Batch operations optimized

3. **Testing:**
   - ✅ 95%+ unit test coverage
   - ✅ All integration tests pass
   - ✅ No performance regressions

4. **Documentation:**
   - ✅ All functions documented
   - ✅ Migration guide created
   - ✅ Examples provided

---

## ⏱️ Timeline

**Day 1 (4-5 hours):**

- Step 1-2: Update database.py and memory_system.py constructor
- Step 3-4: Convert \_init_database and \_load_persistent_memory
- Deliverable: First functions operational

**Day 2 (4-5 hours):**

- Step 5: Convert remaining 8 functions
- Start unit tests
- Deliverable: All functions converted

**Day 3 (2-3 hours):**

- Complete unit tests
- Integration tests
- Documentation and cleanup
- Deliverable: Phase 2 complete

---

## 🎯 Next Steps

1. ✅ Read this plan thoroughly
2. ⏳ Update database.py with memory table schemas
3. ⏳ Start converting memory_system.py functions
4. ⏳ Run tests incrementally
5. ⏳ Document all changes

---

**Status:** Ready to begin  
**Start Date:** November 8, 2025  
**Estimated Completion:** November 10-11, 2025

---

See INTEGRATION_IMPLEMENTATION_GUIDE.md for Phase 3 planning after this phase completes.
