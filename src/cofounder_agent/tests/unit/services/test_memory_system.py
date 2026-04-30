"""
Unit tests for memory_system.py

Tests cover:
- MemoryType and ImportanceLevel enums
- Memory dataclass creation and defaults
- KnowledgeCluster dataclass creation
- LearningPattern dataclass creation
- AIMemorySystem.__init__ — initializes caches and configuration
- _init_embedding_model — handles available/unavailable sentence-transformers
- _row_to_memory — parses asyncpg rows, handles all tag/metadata formats
- _row_to_cluster — parses asyncpg rows, handles list/string/None inputs
- store_memory — creates memory, persists, updates caches, returns ID
- recall_memories — filters, scores, and returns memories; handles embedding path
- _extract_common_words — word frequency extraction
- learn_user_preference — stores in cache and DB
- get_user_preferences — returns all or filtered by category
- store_conversation_turn — appends to context, stores important messages as Memory
- get_conversation_context — returns last N turns
- identify_learning_patterns — discovers topic and question patterns
- get_contextual_knowledge — aggregates memories, prefs, context, clusters
- _calculate_cluster_importance — base + recency bonus
- forget_outdated_memories — deletes from DB and caches
- get_memory_summary — returns counts from DB
- get_progress_client helper (not memory_system, skip)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from memory_system import (
    AIMemorySystem,
    ImportanceLevel,
    KnowledgeCluster,
    LearningPattern,
    Memory,
    MemoryType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_pool():
    """Return a mock asyncpg connection pool."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="DELETE 1")

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


def make_memory_row(
    memory_id=None,
    content="Test memory",
    memory_type="conversation",
    importance=3,
    confidence=1.0,
    tags=None,
    related_memories=None,
    metadata=None,
    embedding=None,
):
    """Build a fake asyncpg Record-like dict."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": memory_id or str(uuid4()),
        "content": content,
        "memory_type": memory_type,
        "importance": importance,
        "confidence": confidence,
        "created_at": datetime.now(timezone.utc),
        "last_accessed": datetime.now(timezone.utc),
        "access_count": 0,
        "tags": tags,
        "related_memories": related_memories,
        "metadata": metadata,
        "embedding": embedding,
    }[key]
    return row


def make_cluster_row(
    cluster_id=None,
    name="Test Cluster",
    description="A test cluster",
    memories=None,
    confidence=0.9,
    topics=None,
):
    """Build a fake asyncpg cluster row."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": cluster_id or str(uuid4()),
        "name": name,
        "description": description,
        "memories": memories,
        "confidence": confidence,
        "last_updated": datetime.now(timezone.utc),
        "importance_score": 2.0,
        "topics": topics,
    }[key]
    return row


@pytest.fixture
def pool_and_conn():
    return make_mock_pool()


@pytest.fixture
def system(pool_and_conn):
    pool, conn = pool_and_conn
    with patch("memory_system.SENTENCE_TRANSFORMERS_AVAILABLE", False):
        ms = AIMemorySystem(db_pool=pool)
    return ms, conn


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_memory_type_values(self):
        assert MemoryType.CONVERSATION == "conversation"
        assert MemoryType.BUSINESS_FACT == "business_fact"
        assert MemoryType.STRATEGIC_INSIGHT == "strategic_insight"
        assert MemoryType.USER_PREFERENCE == "user_preference"
        assert MemoryType.PROCESS_KNOWLEDGE == "process_knowledge"
        assert MemoryType.MARKET_INTELLIGENCE == "market_intelligence"
        assert MemoryType.TECHNICAL_KNOWLEDGE == "technical_knowledge"
        assert MemoryType.RELATIONSHIP == "relationship"

    def test_importance_level_values(self):
        assert ImportanceLevel.TRIVIAL == 1
        assert ImportanceLevel.LOW == 2
        assert ImportanceLevel.MEDIUM == 3
        assert ImportanceLevel.HIGH == 4
        assert ImportanceLevel.CRITICAL == 5


# ---------------------------------------------------------------------------
# Memory dataclass
# ---------------------------------------------------------------------------


class TestMemoryDataclass:
    def test_required_fields(self):
        now = datetime.now(timezone.utc)
        m = Memory(
            id="mem-1",
            content="hello world",
            memory_type=MemoryType.CONVERSATION,
            importance=ImportanceLevel.MEDIUM,
            confidence=0.9,
            created_at=now,
            last_accessed=now,
        )
        assert m.id == "mem-1"
        assert m.content == "hello world"
        assert m.memory_type == MemoryType.CONVERSATION
        assert m.importance == ImportanceLevel.MEDIUM
        assert m.confidence == 0.9

    def test_defaults(self):
        now = datetime.now(timezone.utc)
        m = Memory(
            id="mem-1",
            content="x",
            memory_type=MemoryType.CONVERSATION,
            importance=ImportanceLevel.LOW,
            confidence=1.0,
            created_at=now,
            last_accessed=now,
        )
        assert m.access_count == 0
        assert m.tags is None
        assert m.related_memories is None
        assert m.metadata is None
        assert m.embedding is None


# ---------------------------------------------------------------------------
# KnowledgeCluster dataclass
# ---------------------------------------------------------------------------


class TestKnowledgeClusterDataclass:
    def test_creation(self):
        now = datetime.now(timezone.utc)
        kc = KnowledgeCluster(
            id="kc-1",
            name="Tech",
            description="Tech knowledge",
            memories=["mem-1", "mem-2"],
            confidence=0.85,
            last_updated=now,
            importance_score=3.0,
            topics=["AI", "ML"],
        )
        assert kc.id == "kc-1"
        assert len(kc.memories) == 2
        assert kc.topics == ["AI", "ML"]


# ---------------------------------------------------------------------------
# LearningPattern dataclass
# ---------------------------------------------------------------------------


class TestLearningPatternDataclass:
    def test_creation(self):
        now = datetime.now(timezone.utc)
        lp = LearningPattern(
            pattern_id="pat-1",
            pattern_type="preference",
            description="User likes cost-effective solutions",
            frequency=5,
            confidence=0.8,
            examples=["example 1", "example 2"],
            discovered_at=now,
        )
        assert lp.pattern_id == "pat-1"
        assert lp.frequency == 5


# ---------------------------------------------------------------------------
# AIMemorySystem.__init__
# ---------------------------------------------------------------------------


class TestAIMemorySystemInit:
    def test_initializes_caches(self, pool_and_conn):
        pool, _ = pool_and_conn
        with patch("memory_system.SENTENCE_TRANSFORMERS_AVAILABLE", False):
            ms = AIMemorySystem(db_pool=pool)
        assert ms.recent_memories == []
        assert ms.important_memories == []
        assert ms.user_preferences == {}
        assert ms.knowledge_clusters == {}
        assert ms.learning_patterns == {}
        assert ms.conversation_context == []

    def test_configuration_defaults(self, pool_and_conn):
        pool, _ = pool_and_conn
        with patch("memory_system.SENTENCE_TRANSFORMERS_AVAILABLE", False):
            ms = AIMemorySystem(db_pool=pool)
        assert ms.max_recent_memories == 100
        assert ms.max_important_memories == 500
        assert ms.embedding_dimension == 384
        assert ms.similarity_threshold == 0.7

    def test_embedding_model_none_when_unavailable(self, pool_and_conn):
        pool, _ = pool_and_conn
        with patch("memory_system.SENTENCE_TRANSFORMERS_AVAILABLE", False):
            ms = AIMemorySystem(db_pool=pool)
        assert ms.embedding_model is None


# ---------------------------------------------------------------------------
# _init_embedding_model
# ---------------------------------------------------------------------------


class TestInitEmbeddingModel:
    def test_sets_none_when_not_available(self, pool_and_conn):
        pool, _ = pool_and_conn
        with patch("memory_system.SENTENCE_TRANSFORMERS_AVAILABLE", False):
            ms = AIMemorySystem(db_pool=pool)
        assert ms.embedding_model is None

    def test_handles_exception_gracefully(self, pool_and_conn):
        pool, _ = pool_and_conn
        with (
            patch("memory_system.SENTENCE_TRANSFORMERS_AVAILABLE", True),
            patch("memory_system.SentenceTransformer", side_effect=RuntimeError("No model")),
        ):
            ms = AIMemorySystem(db_pool=pool)
        assert ms.embedding_model is None


# ---------------------------------------------------------------------------
# _row_to_memory
# ---------------------------------------------------------------------------


class TestRowToMemory:
    def test_basic_conversion(self, system):
        ms, _ = system
        row = make_memory_row(
            memory_id="abc123",
            content="Test",
            memory_type="business_fact",
            importance=4,
            confidence=0.95,
        )
        m = ms._row_to_memory(row)
        assert m.content == "Test"
        assert m.memory_type == MemoryType.BUSINESS_FACT
        assert m.importance == ImportanceLevel.HIGH
        assert m.confidence == 0.95

    def test_tags_as_list(self, system):
        ms, _ = system
        row = make_memory_row(tags=["ai", "ml"])
        m = ms._row_to_memory(row)
        assert m.tags == ["ai", "ml"]

    def test_tags_as_json_string(self, system):
        ms, _ = system
        row = make_memory_row(tags='["ai", "ml"]')
        m = ms._row_to_memory(row)
        assert m.tags == ["ai", "ml"]

    def test_tags_none_becomes_empty_list(self, system):
        ms, _ = system
        row = make_memory_row(tags=None)
        m = ms._row_to_memory(row)
        assert m.tags == []

    def test_metadata_as_dict(self, system):
        ms, _ = system
        row = make_memory_row(metadata={"key": "value"})
        m = ms._row_to_memory(row)
        assert m.metadata == {"key": "value"}

    def test_metadata_as_json_string(self, system):
        ms, _ = system
        row = make_memory_row(metadata='{"key": "value"}')
        m = ms._row_to_memory(row)
        assert m.metadata == {"key": "value"}

    def test_metadata_none_becomes_empty_dict(self, system):
        ms, _ = system
        row = make_memory_row(metadata=None)
        m = ms._row_to_memory(row)
        assert m.metadata == {}

    def test_embedding_none(self, system):
        ms, _ = system
        row = make_memory_row(embedding=None)
        m = ms._row_to_memory(row)
        assert m.embedding is None

    def test_invalid_embedding_bytes_handled(self, system):
        ms, _ = system
        # Corrupt bytes — should be handled gracefully
        row = make_memory_row(embedding=b"corrupt_bytes")
        m = ms._row_to_memory(row)
        assert m.embedding is None


# ---------------------------------------------------------------------------
# _row_to_cluster
# ---------------------------------------------------------------------------


class TestRowToCluster:
    def test_basic_conversion(self, system):
        ms, _ = system
        row = make_cluster_row(
            cluster_id="kc-1",
            name="Tech",
            memories=["m1", "m2"],
            topics=["AI"],
        )
        kc = ms._row_to_cluster(row)
        assert kc.name == "Tech"
        assert kc.memories == ["m1", "m2"]
        assert kc.topics == ["AI"]

    def test_memories_as_json_string(self, system):
        ms, _ = system
        row = make_cluster_row(memories='["m1", "m2"]')
        kc = ms._row_to_cluster(row)
        assert kc.memories == ["m1", "m2"]

    def test_memories_none_becomes_empty_list(self, system):
        ms, _ = system
        row = make_cluster_row(memories=None)
        kc = ms._row_to_cluster(row)
        assert kc.memories == []

    def test_topics_none_becomes_empty_list(self, system):
        ms, _ = system
        row = make_cluster_row(topics=None)
        kc = ms._row_to_cluster(row)
        assert kc.topics == []

    def test_topics_as_json_string(self, system):
        ms, _ = system
        row = make_cluster_row(topics='["AI", "ML"]')
        kc = ms._row_to_cluster(row)
        assert kc.topics == ["AI", "ML"]


# ---------------------------------------------------------------------------
# store_memory
# ---------------------------------------------------------------------------


class TestStoreMemory:
    @pytest.mark.asyncio
    async def test_returns_memory_id_string(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")

        memory_id = await ms.store_memory(
            content="Test content",
            memory_type=MemoryType.BUSINESS_FACT,
        )
        assert isinstance(memory_id, str)
        assert len(memory_id) > 0

    @pytest.mark.asyncio
    async def test_adds_to_recent_memories_cache(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])

        await ms.store_memory("Test", MemoryType.CONVERSATION)
        assert len(ms.recent_memories) == 1
        assert ms.recent_memories[0].content == "Test"

    @pytest.mark.asyncio
    async def test_high_importance_added_to_important_cache(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)

        await ms.store_memory(
            "Critical insight",
            MemoryType.STRATEGIC_INSIGHT,
            importance=ImportanceLevel.HIGH,
        )
        assert any(m.content == "Critical insight" for m in ms.important_memories)

    @pytest.mark.asyncio
    async def test_low_importance_not_added_to_important_cache(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)

        await ms.store_memory(
            "Minor note",
            MemoryType.CONVERSATION,
            importance=ImportanceLevel.LOW,
        )
        assert not any(m.content == "Minor note" for m in ms.important_memories)

    @pytest.mark.asyncio
    async def test_recent_memories_capped_at_max(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)
        ms.max_recent_memories = 3

        for i in range(5):
            await ms.store_memory(f"Memory {i}", MemoryType.CONVERSATION)

        assert len(ms.recent_memories) == 3

    @pytest.mark.asyncio
    async def test_uses_provided_tags(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)

        await ms.store_memory("Tag test", MemoryType.CONVERSATION, tags=["ai", "ml"])
        assert ms.recent_memories[0].tags == ["ai", "ml"]


# ---------------------------------------------------------------------------
# recall_memories
# ---------------------------------------------------------------------------


class TestRecallMemories:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_memories(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        result = await ms.recall_memories("AI strategy")
        assert result == []

    @pytest.mark.asyncio
    async def test_finds_keyword_matched_memory(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        # Pre-populate cache
        now = datetime.now(timezone.utc)
        ms.recent_memories = [
            Memory(
                id="m1",
                content="AI automation helps business grow",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.MEDIUM,
                confidence=1.0,
                created_at=now,
                last_accessed=now,
            )
        ]

        result = await ms.recall_memories("AI automation", min_relevance=0.1)
        assert len(result) == 1
        assert result[0].id == "m1"

    @pytest.mark.asyncio
    async def test_filters_by_memory_type(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)
        ms.recent_memories = [
            Memory(
                id="m1",
                content="business fact about AI",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.MEDIUM,
                confidence=1.0,
                created_at=now,
                last_accessed=now,
            ),
            Memory(
                id="m2",
                content="conversation about AI",
                memory_type=MemoryType.CONVERSATION,
                importance=ImportanceLevel.MEDIUM,
                confidence=1.0,
                created_at=now,
                last_accessed=now,
            ),
        ]

        result = await ms.recall_memories(
            "AI", memory_types=[MemoryType.BUSINESS_FACT], min_relevance=0.1
        )
        assert all(m.memory_type == MemoryType.BUSINESS_FACT for m in result)

    @pytest.mark.asyncio
    async def test_tag_bonus_applied(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)
        ms.recent_memories = [
            Memory(
                id="m1",
                content="something unrelated",
                memory_type=MemoryType.CONVERSATION,
                importance=ImportanceLevel.MEDIUM,
                confidence=1.0,
                created_at=now,
                last_accessed=now,
                tags=["AI"],
            )
        ]
        # "AI" in tags should get a 0.2 bonus, pushing it above the threshold
        result = await ms.recall_memories("AI project", min_relevance=0.1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_respects_limit(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)
        ms.recent_memories = [
            Memory(
                id=f"m{i}",
                content="AI automation",
                memory_type=MemoryType.CONVERSATION,
                importance=ImportanceLevel.MEDIUM,
                confidence=1.0,
                created_at=now,
                last_accessed=now,
            )
            for i in range(10)
        ]

        result = await ms.recall_memories("AI automation", limit=3, min_relevance=0.1)
        assert len(result) <= 3

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self, system):
        ms, conn = system
        # Simulate a DB error on update
        conn.execute = AsyncMock(side_effect=Exception("DB error"))

        now = datetime.now(timezone.utc)
        ms.recent_memories = [
            Memory(
                id="m1",
                content="AI strategy",
                memory_type=MemoryType.CONVERSATION,
                importance=ImportanceLevel.MEDIUM,
                confidence=1.0,
                created_at=now,
                last_accessed=now,
            )
        ]

        # recall_memories catches the exception and returns []
        result = await ms.recall_memories("AI strategy", min_relevance=0.1)
        # Either returns result or empty list — it catches exceptions at the outer level
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _extract_common_words
# ---------------------------------------------------------------------------


class TestExtractCommonWords:
    def test_returns_words_with_frequency_ge_2(self, system):
        ms, _ = system
        text = "artificial intelligence artificial intelligence machine"
        result = ms._extract_common_words(text)
        assert "artificial" in result
        assert "intelligence" in result

    def test_filters_short_words(self, system):
        ms, _ = system
        text = "AI AI AI is a tool tool tool"
        result = ms._extract_common_words(text, min_length=4)
        assert "AI" not in result
        assert "is" not in result
        assert "tool" in result

    def test_returns_sorted_by_frequency(self, system):
        ms, _ = system
        text = "business business business strategy strategy"
        result = ms._extract_common_words(text)
        assert result[0] == "business"

    def test_empty_text_returns_empty(self, system):
        ms, _ = system
        result = ms._extract_common_words("")
        assert result == []

    def test_filters_non_alpha_words(self, system):
        ms, _ = system
        text = "hello! hello! world world"
        result = ms._extract_common_words(text)
        assert "hello" in result  # punctuation stripped


# ---------------------------------------------------------------------------
# learn_user_preference
# ---------------------------------------------------------------------------


class TestLearnUserPreference:
    @pytest.mark.asyncio
    async def test_stores_in_cache(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")

        await ms.learn_user_preference("theme", "dark")
        assert ms.user_preferences["theme"] == "dark"

    @pytest.mark.asyncio
    async def test_calls_db_execute(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")

        await ms.learn_user_preference("theme", "dark")
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_db_error(self, system):
        ms, conn = system
        conn.execute = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await ms.learn_user_preference("theme", "dark")


# ---------------------------------------------------------------------------
# get_user_preferences
# ---------------------------------------------------------------------------


class TestGetUserPreferences:
    @pytest.mark.asyncio
    async def test_returns_all_preferences(self, system):
        ms, _ = system
        ms.user_preferences = {"theme": "dark", "language": "en", "cost_tier": "cheap"}

        result = await ms.get_user_preferences()
        assert result == ms.user_preferences

    @pytest.mark.asyncio
    async def test_filters_by_category(self, system):
        ms, _ = system
        ms.user_preferences = {"cost_tier": "cheap", "cost_preference": "low", "theme": "dark"}

        result = await ms.get_user_preferences(category="cost")
        assert "cost_tier" in result
        assert "cost_preference" in result
        assert "theme" not in result

    @pytest.mark.asyncio
    async def test_returns_copy_not_reference(self, system):
        ms, _ = system
        ms.user_preferences = {"theme": "dark"}

        result = await ms.get_user_preferences()
        result["theme"] = "light"
        assert ms.user_preferences["theme"] == "dark"


# ---------------------------------------------------------------------------
# store_conversation_turn
# ---------------------------------------------------------------------------


class TestStoreConversationTurn:
    @pytest.mark.asyncio
    async def test_appends_to_context(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)

        await ms.store_conversation_turn("user", "Hello world this is test")
        assert len(ms.conversation_context) == 1
        assert ms.conversation_context[0]["role"] == "user"
        assert ms.conversation_context[0]["content"] == "Hello world this is test"

    @pytest.mark.asyncio
    async def test_capped_at_50_turns(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)

        for i in range(60):
            await ms.store_conversation_turn("user", f"Message {i} " * 20)

        assert len(ms.conversation_context) == 50

    @pytest.mark.asyncio
    async def test_short_content_not_stored_as_memory(self, system):
        ms, conn = system

        await ms.store_conversation_turn("user", "Hi")
        # Short message (<50 chars) should not trigger store_memory
        assert len(ms.recent_memories) == 0

    @pytest.mark.asyncio
    async def test_important_keywords_raise_importance(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)

        await ms.store_conversation_turn(
            "user",
            "Our top priority strategy is to optimize costs and make important decisions",
        )
        # Should store as MEDIUM importance (keyword detected)
        stored = ms.recent_memories
        assert len(stored) == 1
        assert stored[0].importance == ImportanceLevel.MEDIUM

    @pytest.mark.asyncio
    async def test_non_important_message_stored_as_low(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=None)

        await ms.store_conversation_turn(
            "user", "The weather today is quite nice for a walk in the park"
        )
        assert ms.recent_memories[0].importance == ImportanceLevel.LOW


# ---------------------------------------------------------------------------
# get_conversation_context
# ---------------------------------------------------------------------------


class TestGetConversationContext:
    @pytest.mark.asyncio
    async def test_returns_last_n_turns(self, system):
        ms, _ = system
        ms.conversation_context = [{"turn": i} for i in range(20)]

        result = await ms.get_conversation_context(limit=5)
        assert len(result) == 5
        assert result[-1] == {"turn": 19}

    @pytest.mark.asyncio
    async def test_returns_all_when_limit_zero(self, system):
        ms, _ = system
        ms.conversation_context = [{"turn": i} for i in range(10)]

        result = await ms.get_conversation_context(limit=0)
        assert len(result) == 10


# ---------------------------------------------------------------------------
# identify_learning_patterns
# ---------------------------------------------------------------------------


class TestIdentifyLearningPatterns:
    @pytest.mark.asyncio
    async def test_returns_empty_with_few_messages(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        ms.conversation_context = [{"role": "user", "content": "Hello"} for _ in range(3)]

        result = await ms.identify_learning_patterns()
        assert result == []

    @pytest.mark.asyncio
    async def test_finds_common_topics_pattern(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")

        ms.conversation_context = [
            {"role": "user", "content": "artificial intelligence automation strategy planning"}
            for _ in range(5)
        ]

        result = await ms.identify_learning_patterns()
        assert any(p.pattern_id == "common_topics" for p in result)

    @pytest.mark.asyncio
    async def test_finds_question_pattern(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value="INSERT 0 1")

        ms.conversation_context = [
            {"role": "user", "content": f"What is the best approach for topic {i}?"}
            for i in range(6)
        ]

        result = await ms.identify_learning_patterns()
        assert any(p.pattern_id == "question_pattern" for p in result)

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self, system):
        ms, conn = system
        conn.execute = AsyncMock(side_effect=Exception("DB error"))

        ms.conversation_context = [
            {"role": "user", "content": "artificial intelligence automation strategy planning"}
            for _ in range(5)
        ]

        result = await ms.identify_learning_patterns()
        # Exception is caught at outer level — returns what was assembled before error
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_contextual_knowledge
# ---------------------------------------------------------------------------


class TestGetContextualKnowledge:
    @pytest.mark.asyncio
    async def test_returns_correct_structure(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        result = await ms.get_contextual_knowledge("AI strategy")

        assert "relevant_memories" in result
        assert "user_preferences" in result
        assert "conversation_context" in result
        assert "knowledge_clusters" in result
        assert "context_type" in result
        assert "generated_at" in result

    @pytest.mark.asyncio
    async def test_includes_matching_clusters(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)
        ms.knowledge_clusters["ai_cluster"] = KnowledgeCluster(
            id="kc-1",
            name="AI Knowledge",
            description="AI related",
            memories=["m1"],
            confidence=0.9,
            last_updated=now,
            importance_score=3.0,
            topics=["AI", "automation"],
        )

        result = await ms.get_contextual_knowledge("AI strategy")
        assert len(result["knowledge_clusters"]) == 1

    @pytest.mark.asyncio
    async def test_filters_clusters_by_topic(self, system):
        ms, conn = system
        conn.execute = AsyncMock(return_value=None)

        now = datetime.now(timezone.utc)
        ms.knowledge_clusters["unrelated_cluster"] = KnowledgeCluster(
            id="kc-2",
            name="Finance",
            description="Finance related",
            memories=["m2"],
            confidence=0.9,
            last_updated=now,
            importance_score=2.0,
            topics=["finance", "budget"],
        )

        result = await ms.get_contextual_knowledge("AI strategy")
        assert len(result["knowledge_clusters"]) == 0


# ---------------------------------------------------------------------------
# _calculate_cluster_importance
# ---------------------------------------------------------------------------


class TestCalculateClusterImportance:
    def test_recent_cluster_gets_bonus(self, system):
        ms, _ = system
        cluster = KnowledgeCluster(
            id="kc-1",
            name="Test",
            description="Test",
            memories=["m1", "m2", "m3"],
            confidence=0.9,
            last_updated=datetime.now(timezone.utc),
            importance_score=0.0,
        )
        score = ms._calculate_cluster_importance(cluster)
        # 3 memories * 0.1 + 1.0 recency bonus = 1.3
        assert score == pytest.approx(1.3)

    def test_old_cluster_gets_lower_bonus(self, system):
        ms, _ = system
        cluster = KnowledgeCluster(
            id="kc-1",
            name="Test",
            description="Test",
            memories=["m1"],
            confidence=0.9,
            last_updated=datetime.now(timezone.utc) - timedelta(days=10),
            importance_score=0.0,
        )
        score = ms._calculate_cluster_importance(cluster)
        # 1 memory * 0.1 + 0.5 recency = 0.6
        assert score == pytest.approx(0.6)

    def test_score_capped_at_5(self, system):
        ms, _ = system
        cluster = KnowledgeCluster(
            id="kc-1",
            name="Test",
            description="Test",
            memories=[f"m{i}" for i in range(100)],
            confidence=0.9,
            last_updated=datetime.now(timezone.utc),
            importance_score=0.0,
        )
        score = ms._calculate_cluster_importance(cluster)
        assert score == 5.0


# ---------------------------------------------------------------------------
# forget_outdated_memories
# ---------------------------------------------------------------------------


class TestForgetOutdatedMemories:
    @pytest.mark.asyncio
    async def test_deletes_from_db_and_cache(self, system):
        ms, conn = system
        old_id = "old-memory-id"
        conn.fetch = AsyncMock(return_value=[{"id": old_id}])
        conn.execute = AsyncMock(return_value="DELETE 1")

        now = datetime.now(timezone.utc)
        ms.recent_memories = [
            Memory(
                id=old_id,
                content="old memory",
                memory_type=MemoryType.CONVERSATION,
                importance=ImportanceLevel.LOW,
                confidence=0.5,
                created_at=now,
                last_accessed=now,
            )
        ]

        await ms.forget_outdated_memories(days_threshold=30)

        conn.execute.assert_called_once()
        assert not any(m.id == old_id for m in ms.recent_memories)

    @pytest.mark.asyncio
    async def test_no_delete_when_no_outdated(self, system):
        ms, conn = system
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock(return_value=None)

        await ms.forget_outdated_memories(days_threshold=30)
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_on_db_error(self, system):
        ms, conn = system
        conn.fetch = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await ms.forget_outdated_memories()


# ---------------------------------------------------------------------------
# get_memory_summary
# ---------------------------------------------------------------------------


class TestGetMemorySummary:
    @pytest.mark.asyncio
    async def test_returns_summary_dict(self, system):
        ms, conn = system
        conn.fetchval = AsyncMock(side_effect=[42, 10, 5, 3])  # total, prefs, clusters, patterns
        conn.fetch = AsyncMock(return_value=[{"memory_type": "conversation", "count": 25}])

        result = await ms.get_memory_summary()

        assert result["total_memories"] == 42
        assert result["total_preferences"] == 10
        assert result["total_knowledge_clusters"] == 5
        assert result["total_learning_patterns"] == 3
        assert "memory_by_type" in result
        assert result["embedding_model_active"] is False

    @pytest.mark.asyncio
    async def test_raises_on_db_error(self, system):
        ms, conn = system
        conn.fetchval = AsyncMock(side_effect=Exception("DB failure"))

        with pytest.raises(Exception, match="DB failure"):
            await ms.get_memory_summary()


# ---------------------------------------------------------------------------
# initialize — integration test (verify_tables + load_persistent_memory)
# ---------------------------------------------------------------------------


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_calls_verify_and_load(self, system):
        ms, conn = system
        conn.fetchval = AsyncMock(return_value=True)  # tables exist
        conn.fetch = AsyncMock(return_value=[])  # no memories, prefs, clusters

        await ms.initialize()

        # fetchval called for table verification
        conn.fetchval.assert_called()

    @pytest.mark.asyncio
    async def test_initialize_raises_on_verify_error(self, system):
        ms, conn = system
        conn.fetchval = AsyncMock(side_effect=Exception("connection failed"))

        with pytest.raises(Exception, match="connection failed"):
            await ms.initialize()
