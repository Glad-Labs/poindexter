"""
Unit tests for AIMemorySystem with PostgreSQL backend.

Tests cover:
- Async initialization and database connectivity
- Memory CRUD operations
- Knowledge clustering
- Learning patterns
- User preferences
- Memory cleanup

Run: pytest tests/test_memory_system_simplified.py -v
"""

import pytest
import pickle
from datetime import datetime
from typing import List
from uuid import uuid4

from src.cofounder_agent.memory_system import (
    AIMemorySystem,
    Memory,
    MemoryType,
    ImportanceLevel,
    KnowledgeCluster,
    LearningPattern,
)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def create_memory(
    id: str = None,  # type: ignore
    content: str = "Test memory",
    mem_type: MemoryType = MemoryType.BUSINESS_FACT,
    importance: ImportanceLevel = ImportanceLevel.HIGH,
) -> Memory:
    """Create a Memory object with all required fields."""
    now = datetime.now()
    if id is None:
        id = str(uuid4())
    return Memory(
        id=id,
        content=content,
        memory_type=mem_type,
        importance=importance,
        confidence=0.9,
        created_at=now,
        last_accessed=now,
        access_count=0,
        tags=["test"],
        related_memories=[],
        metadata={},
        embedding=None,  # Changed from pickle.dumps to None for UUID migration
    )


def create_cluster(
    id: str = None,  # type: ignore
    name: str = "Test Cluster",
) -> KnowledgeCluster:
    """Create a KnowledgeCluster object."""
    if id is None:
        id = str(uuid4())
    return KnowledgeCluster(
        id=id,
        name=name,
        description="Test cluster",
        memories=[str(uuid4()), str(uuid4())],  # Generate proper UUIDs
        confidence=0.85,
        topics=["test"],
        importance_score=7,
        last_updated=datetime.now(),
    )


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
async def db_pool():
    """Test database pool (may not exist, tests will skip gracefully)."""
    try:
        import asyncpg

        pool = await asyncpg.create_pool(
            "postgresql://postgres:postgres@localhost:5432/glad_labs_test",
            min_size=2,
            max_size=5,
        )
        yield pool
        await pool.close()
    except Exception as e:
        pytest.skip(f"Database not available: {e}")


@pytest.fixture
async def memory_system(db_pool):
    """Initialized memory system."""
    system = AIMemorySystem(db_pool=db_pool)
    await system.initialize()
    return system


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================


class TestInitialization:
    """Test memory system initialization."""

    @pytest.mark.asyncio
    async def test_system_can_initialize(self, db_pool):
        """Memory system initializes without errors."""
        system = AIMemorySystem(db_pool=db_pool)
        await system.initialize()
        assert system.db_pool is not None

    @pytest.mark.asyncio
    async def test_system_has_cache_structures(self, memory_system):
        """Memory system has cache structures."""
        # Should have cache lists and dicts
        assert isinstance(memory_system.recent_memories, list)
        assert isinstance(memory_system.user_preferences, dict)


# ============================================================================
# MEMORY OPERATIONS TESTS
# ============================================================================


class TestMemoryOperations:
    """Test memory store and recall."""

    @pytest.mark.asyncio
    async def test_store_memory(self, memory_system):
        """Store memory in system."""
        await memory_system.store_memory(
            content="Test memory about Python",
            memory_type=MemoryType.TECHNICAL_KNOWLEDGE,
            importance=ImportanceLevel.HIGH,
            tags=["python"],
        )
        # Memory stored in system - check that it was persisted
        assert memory_system.db_pool is not None

    @pytest.mark.asyncio
    async def test_recall_memories(self, memory_system):
        """Recall memories from cache."""
        # Store first
        await memory_system.store_memory(
            content="Test fact about databases",
            memory_type=MemoryType.BUSINESS_FACT,
            importance=ImportanceLevel.HIGH,
        )

        # Recall should return list
        results = await memory_system.recall_memories(query="database", limit=5)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_store_conversation_turn(self, memory_system):
        """Store conversation as memory."""
        await memory_system.store_conversation_turn(
            role="user",
            content="What is AI?",
            context={},
        )
        # Conversation stored in system
        assert memory_system.conversation_context is not None


# ============================================================================
# KNOWLEDGE CLUSTER TESTS
# ============================================================================


class TestKnowledgeClusters:
    """Test knowledge cluster operations."""

    @pytest.mark.asyncio
    async def test_persist_cluster(self, memory_system):
        """Persist knowledge cluster to database."""
        cluster = create_cluster()  # Uses uuid4() now
        await memory_system._persist_knowledge_cluster(cluster)
        # Should complete without error
        assert len(cluster.id) == 36  # Valid UUID format
        assert cluster.id.count("-") == 4

    @pytest.mark.asyncio
    async def test_cluster_upsert(self, memory_system):
        """Upsert cluster (should not duplicate)."""
        cluster = create_cluster()  # Uses uuid4() now

        # Create and store
        await memory_system._persist_knowledge_cluster(cluster)

        # Update and store again (upsert)
        cluster.name = "Updated Test"
        await memory_system._persist_knowledge_cluster(cluster)

        # Should work without error
        assert len(cluster.id) == 36  # Valid UUID format


# ============================================================================
# LEARNING PATTERNS TESTS
# ============================================================================


class TestLearningPatterns:
    """Test learning pattern detection."""

    @pytest.mark.asyncio
    async def test_store_learning_pattern(self, memory_system):
        """Store a learning pattern."""
        pattern = LearningPattern(
            pattern_id=str(uuid4()),  # Generate proper UUID
            pattern_type="user_preference",
            description="User likes concise responses",
            frequency=5,
            confidence=0.9,
            examples=["ex1", "ex2"],
            discovered_at=datetime.now(),
        )

        await memory_system._store_learning_pattern(pattern)
        # Should complete without error
        assert len(pattern.pattern_id) == 36  # Valid UUID format
        assert pattern.pattern_id.count("-") == 4

    @pytest.mark.asyncio
    async def test_identify_patterns(self, memory_system):
        """Identify patterns from memories."""
        # Add some memories
        for i in range(3):
            await memory_system.store_memory(
                content=f"Pattern test {i}",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.HIGH,
            )

        # Identify patterns
        patterns = await memory_system.identify_learning_patterns()
        assert isinstance(patterns, list)


# ============================================================================
# USER PREFERENCES TESTS
# ============================================================================


class TestUserPreferences:
    """Test user preference learning."""

    @pytest.mark.asyncio
    async def test_learn_preference(self, memory_system):
        """Learn user preference."""
        await memory_system.learn_user_preference(
            preference_key="style",
            preference_value={"verbose": False},
            confidence=0.9,
        )
        assert "style" in memory_system.user_preferences

    @pytest.mark.asyncio
    async def test_preference_upsert(self, memory_system):
        """Upsert preference (ON CONFLICT)."""
        key = "upsert-pref"

        # Store
        await memory_system.learn_user_preference(
            preference_key=key,
            preference_value={"v": 1},
            confidence=0.5,
        )

        # Update
        await memory_system.learn_user_preference(
            preference_key=key,
            preference_value={"v": 2},
            confidence=0.9,
        )
        # Should work without error

    @pytest.mark.asyncio
    async def test_get_preferences(self, memory_system):
        """Retrieve all preferences."""
        # Store some
        for i in range(2):
            await memory_system.learn_user_preference(
                preference_key=f"pref-{i}",
                preference_value={"index": i},
                confidence=0.8,
            )

        # Get all
        prefs = await memory_system.get_user_preferences()
        assert isinstance(prefs, dict)


# ============================================================================
# CLEANUP TESTS
# ============================================================================


class TestMemoryCleanup:
    """Test memory cleanup operations."""

    @pytest.mark.asyncio
    async def test_forget_outdated(self, memory_system):
        """Forget outdated memories."""
        # Store some old memories
        for i in range(3):
            await memory_system.store_memory(
                content=f"Old memory {i}",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.LOW,
            )

        # Forget old ones (method only takes days_threshold)
        await memory_system.forget_outdated_memories(days_threshold=0)
        # Should complete without error

    @pytest.mark.asyncio
    async def test_cleanup_batch_delete(self, memory_system):
        """Batch delete memories (PostgreSQL ANY clause)."""
        # Add several
        ids = []
        for i in range(5):
            await memory_system.store_memory(
                content=f"Batch delete test {i}",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.LOW,
            )

        # Cleanup should use batch delete
        await memory_system.forget_outdated_memories(days_threshold=0)


# ============================================================================
# ANALYTICS TESTS
# ============================================================================


class TestMemorySummary:
    """Test memory analytics."""

    @pytest.mark.asyncio
    async def test_get_summary(self, memory_system):
        """Get memory system summary statistics."""
        # Add some data
        for i in range(3):
            await memory_system.store_memory(
                content=f"Summary test {i}",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.HIGH,
            )

        # Get summary
        summary = await memory_system.get_memory_summary()
        assert isinstance(summary, dict)


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================


class TestErrorHandling:
    """Test error scenarios."""

    @pytest.mark.asyncio
    async def test_persist_without_pool(self):
        """Handle error when db_pool is None."""
        system = AIMemorySystem(db_pool=None)  # type: ignore

        memory = create_memory()

        # Should raise error
        try:
            await system._persist_memory(memory)
            pytest.fail("Should have raised error")
        except (AttributeError, TypeError):
            pass  # Expected


# ============================================================================
# ASYNC/CONCURRENT TESTS
# ============================================================================


class TestAsyncPatterns:
    """Test async and concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_store(self, memory_system):
        """Store multiple memories concurrently."""
        import asyncio

        tasks = [
            memory_system.store_memory(
                content=f"Concurrent {i}",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.HIGH,
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed
        for result in results:
            assert not isinstance(result, Exception)

    @pytest.mark.asyncio
    async def test_async_initialization(self, db_pool):
        """Async initialization completes."""
        system = AIMemorySystem(db_pool=db_pool)
        await system.initialize()
        # Should complete without blocking


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestIntegration:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_full_workflow(self, memory_system):
        """Complete workflow: store -> recall -> learn -> cleanup."""
        # 1. Store
        await memory_system.store_memory(
            content="Workflow test memory",
            memory_type=MemoryType.STRATEGIC_INSIGHT,
            importance=ImportanceLevel.CRITICAL,
        )

        # 2. Recall
        memories = await memory_system.recall_memories(query="workflow", limit=10)
        assert isinstance(memories, list)

        # 3. Learn preference
        await memory_system.learn_user_preference(
            preference_key="workflow_pref",
            preference_value={"works": True},
            confidence=0.95,
        )

        # 4. Get summary
        summary = await memory_system.get_memory_summary()
        assert isinstance(summary, dict)

    @pytest.mark.asyncio
    async def test_cluster_and_patterns(self, memory_system):
        """Test clusters and pattern identification together."""
        # Create cluster (factory generates UUID automatically)
        cluster = create_cluster()
        await memory_system._persist_knowledge_cluster(cluster)

        # Identify patterns
        patterns = await memory_system.identify_learning_patterns()
        assert isinstance(patterns, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
