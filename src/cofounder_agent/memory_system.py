"""AI Memory & Knowledge Management System for Glad Labs Co-Founder

This module provides persistent memory, knowledge base management, and learning
capabilities for the AI co-founder system. It enables the AI to remember conversations,
learn from interactions, build domain expertise, and maintain context across sessions.

Uses PostgreSQL for persistent storage (no SQLite).
"""

import asyncio
import logging
import json
import os
import pickle
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
import hashlib
from uuid import uuid4

import numpy as np
import asyncpg
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SentenceTransformer = None
    SENTENCE_TRANSFORMERS_AVAILABLE = False


class MemoryType(str, Enum):
    """Types of memories the AI can store"""
    CONVERSATION = "conversation"
    BUSINESS_FACT = "business_fact"
    STRATEGIC_INSIGHT = "strategic_insight"
    USER_PREFERENCE = "user_preference"
    PROCESS_KNOWLEDGE = "process_knowledge"
    MARKET_INTELLIGENCE = "market_intelligence"
    TECHNICAL_KNOWLEDGE = "technical_knowledge"
    RELATIONSHIP = "relationship"


class ImportanceLevel(int, Enum):
    """Importance levels for memories and knowledge"""
    TRIVIAL = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5


@dataclass
class Memory:
    """A single memory or piece of knowledge"""
    id: str
    content: str
    memory_type: MemoryType
    importance: ImportanceLevel
    confidence: float
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    tags: Optional[List[str]] = None
    related_memories: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    embedding: Optional[List[float]] = None


@dataclass
class KnowledgeCluster:
    """A cluster of related knowledge/memories"""
    id: str
    name: str
    description: str
    memories: List[str]
    confidence: float
    last_updated: datetime
    importance_score: float
    topics: Optional[List[str]] = None


@dataclass
class LearningPattern:
    """Pattern learned from user interactions"""
    pattern_id: str
    pattern_type: str  # "preference", "workflow", "decision_pattern"
    description: str
    frequency: int
    confidence: float
    examples: List[str]
    discovered_at: datetime


class AIMemorySystem:
    """
    Comprehensive memory and knowledge management system for AI co-founder.
    
    Features:
    1. Persistent conversation memory (PostgreSQL)
    2. Business knowledge base
    3. User preference learning
    4. Strategic insight retention
    5. Contextual information retrieval
    6. Knowledge graph relationships
    7. Adaptive forgetting and importance weighting
    
    Uses PostgreSQL via asyncpg for all persistence operations.
    """
    
    def __init__(self, db_pool: asyncpg.Pool):
        """
        Initialize AI Memory System.
        
        Args:
            db_pool: asyncpg connection pool for PostgreSQL database
        """
        self.db_pool = db_pool
        self.logger = logging.getLogger("ai_memory_system")
        
        # Embedding model for semantic similarity
        self.embedding_model = None
        self._init_embedding_model()
        
        # Memory caches
        self.recent_memories: List[Memory] = []
        self.important_memories: List[Memory] = []
        self.user_preferences: Dict[str, Any] = {}
        self.knowledge_clusters: Dict[str, KnowledgeCluster] = {}
        
        # Learning systems
        self.learning_patterns: Dict[str, LearningPattern] = {}
        self.conversation_context: List[Dict[str, Any]] = []
        
        # Configuration
        self.max_recent_memories = 100
        self.max_important_memories = 500
        self.embedding_dimension = 384
        self.similarity_threshold = 0.7
    
    async def initialize(self) -> None:
        """
        Async initialization - loads memories from PostgreSQL.
        Must be called after instantiation before using the system.
        """
        await self._verify_tables_exist()
        await self._load_persistent_memory()
    
    async def _verify_tables_exist(self) -> None:
        """
        Verify that memory tables exist in PostgreSQL.
        Tables are created by init_memory_tables() during app startup.
        This just checks they're present.
        """
        try:
            async with self.db_pool.acquire() as conn:
                # Check if memories table exists
                result = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_name = 'memories'
                    );
                """)
                
                if result:
                    self.logger.info("Memory system tables verified in PostgreSQL")
                else:
                    self.logger.warning("Memory tables not found - they should have been created by init_memory_tables()")
                    
        except Exception as e:
            self.logger.error(f"Error verifying memory tables: {e}")
            raise
    
    def _init_embedding_model(self):
        """Initialize sentence embedding model for semantic similarity"""
        try:
            # Use a lightweight but effective sentence transformer model
            if SENTENCE_TRANSFORMERS_AVAILABLE and SentenceTransformer:
                self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
                self.logger.info("Embedding model initialized successfully")
            else:
                self.embedding_model = None
                self.logger.info("Sentence transformers not available, using fallback similarity")
        except Exception as e:
            self.logger.error(f"Failed to initialize embedding model: {e}")
            self.embedding_model = None
    
    async def _load_persistent_memory(self) -> None:
        """Load persistent memory from PostgreSQL"""
        try:
            async with self.db_pool.acquire() as conn:
                # Load recent and important memories
                rows = await conn.fetch("""
                    SELECT id, content, memory_type, importance, confidence, 
                           created_at, last_accessed, access_count, tags, 
                           related_memories, metadata, embedding
                    FROM memories 
                    ORDER BY last_accessed DESC 
                    LIMIT $1
                """, self.max_recent_memories)
                
                self.recent_memories = [self._row_to_memory(row) for row in rows]
                
                # Load user preferences
                pref_rows = await conn.fetch("""
                    SELECT key, value, confidence FROM user_preferences
                """)
                self.user_preferences = {
                    row['key']: json.loads(row['value']) for row in pref_rows
                }
                
                # Load knowledge clusters
                cluster_rows = await conn.fetch("""
                    SELECT id, name, description, memories, confidence, 
                           last_updated, importance_score, topics
                    FROM knowledge_clusters
                """)
                self.knowledge_clusters = {
                    row['id']: self._row_to_cluster(row) for row in cluster_rows
                }
                
                self.logger.info(f"Loaded {len(self.recent_memories)} memories, {len(self.user_preferences)} preferences")
                
        except Exception as e:
            self.logger.error(f"Error loading persistent memory: {e}")
    
    def _row_to_memory(self, row: asyncpg.Record) -> Memory:
        """Convert PostgreSQL row to Memory object"""
        embedding = None
        if row['embedding']:  # bytea type
            try:
                embedding = pickle.loads(row['embedding'])
            except Exception:
                pass
        
        # Handle tags: PostgreSQL text[] returns as list, not JSON string
        tags = row['tags']
        if isinstance(tags, str):
            tags = json.loads(tags) if tags else []
        elif tags is None:
            tags = []
        
        # Handle related_memories: PostgreSQL uuid[] returns as list
        related_memories = row['related_memories']
        if isinstance(related_memories, str):
            related_memories = json.loads(related_memories) if related_memories else []
        elif related_memories is None:
            related_memories = []
        
        # Handle metadata: PostgreSQL JSONB returns as dict already
        metadata = row['metadata']
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        elif metadata is None:
            metadata = {}
        
        return Memory(
            id=row['id'],
            content=row['content'],
            memory_type=MemoryType(row['memory_type']),
            importance=ImportanceLevel(row['importance']),
            confidence=row['confidence'],
            created_at=row['created_at'],
            last_accessed=row['last_accessed'],
            access_count=row['access_count'],
            tags=tags,
            related_memories=related_memories,
            metadata=metadata,
            embedding=embedding
        )
    
    def _row_to_cluster(self, row: asyncpg.Record) -> KnowledgeCluster:
        """Convert PostgreSQL row to KnowledgeCluster object"""
        # Handle memories: PostgreSQL uuid[] returns as list
        memories = row['memories']
        if isinstance(memories, str):
            memories = json.loads(memories) if memories else []
        elif memories is None:
            memories = []
        
        # Handle topics: PostgreSQL text[] returns as list
        topics = row['topics']
        if isinstance(topics, str):
            topics = json.loads(topics) if topics else []
        elif topics is None:
            topics = []
        
        return KnowledgeCluster(
            id=row['id'],
            name=row['name'],
            description=row['description'],
            memories=memories,
            confidence=row['confidence'],
            last_updated=row['last_updated'],
            importance_score=row['importance_score'],
            topics=topics
        )
    
    async def store_memory(self, content: str, memory_type: MemoryType, 
                          importance: ImportanceLevel = ImportanceLevel.MEDIUM,
                              confidence: float = 1.0, tags: Optional[List[str]] = None,
                              metadata: Optional[Dict[str, Any]] = None) -> str:
        """Store a new memory"""
        
        # Generate unique ID
        memory_id = hashlib.md5(f"{content}{datetime.now().isoformat()}".encode()).hexdigest()
        
        # Generate embedding if model is available
        embedding = None
        if self.embedding_model:
            try:
                embedding = self.embedding_model.encode([content])[0].tolist()
            except Exception as e:
                self.logger.error(f"Error generating embedding: {e}")
        
        # Create memory object
        memory = Memory(
            id=memory_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            confidence=confidence,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            tags=tags or [],
            metadata=metadata or {},
            embedding=embedding
        )
        
        # Store in database
        await self._persist_memory(memory)
        
        # Add to caches
        self.recent_memories.insert(0, memory)
        if len(self.recent_memories) > self.max_recent_memories:
            self.recent_memories.pop()
        
        if importance.value >= ImportanceLevel.HIGH.value:
            self.important_memories.append(memory)
            if len(self.important_memories) > self.max_important_memories:
                # Remove least important
                self.important_memories.sort(key=lambda m: m.importance.value)
                self.important_memories.pop(0)
        
        # Update knowledge clusters
        await self._update_knowledge_clusters(memory)
        
        self.logger.info(f"Stored memory: {memory_type.value} - {content[:50]}...")
        
        return memory_id
    
    async def _persist_memory(self, memory: Memory) -> None:
        """Persist memory to PostgreSQL database"""
        try:
            embedding_bytes = None
            if memory.embedding:
                embedding_bytes = pickle.dumps(memory.embedding)
            
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO memories 
                    (id, content, memory_type, importance, confidence, created_at, 
                     last_accessed, access_count, tags, related_memories, metadata, embedding)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (id) DO UPDATE SET
                        content = $2,
                        confidence = $5,
                        last_accessed = $6,
                        access_count = $8,
                        metadata = $11
                """, 
                memory.id,
                memory.content,
                memory.memory_type.value,
                memory.importance.value,
                memory.confidence,
                memory.created_at,
                memory.last_accessed,
                memory.access_count,
                memory.tags if memory.tags else None,  # Pass list directly, not JSON
                memory.related_memories if memory.related_memories else None,  # Pass list directly
                json.dumps(memory.metadata) if memory.metadata else None,
                embedding_bytes
            )
            
        except Exception as e:
            self.logger.error(f"Error persisting memory: {e}")
            raise
    
    async def recall_memories(self, query: str, memory_types: Optional[List[MemoryType]] = None,
                             limit: int = 10, min_relevance: float = 0.5) -> List[Memory]:
        """Recall memories relevant to a query"""
        
        relevant_memories = []
        
        try:
            # Generate query embedding
            query_embedding = None
            if self.embedding_model:
                query_embedding = self.embedding_model.encode([query])[0]
            
            # Search through memories
            search_memories = self.recent_memories + self.important_memories
            
            # Filter by memory type if specified
            if memory_types:
                search_memories = [m for m in search_memories if m.memory_type in memory_types]
            
            for memory in search_memories:
                relevance_score = 0.0
                
                # Text-based relevance
                query_lower = query.lower()
                content_lower = memory.content.lower()
                
                # Simple keyword matching
                query_words = set(query_lower.split())
                content_words = set(content_lower.split())
                keyword_overlap = len(query_words.intersection(content_words)) / len(query_words) if query_words else 0
                
                # Embedding-based similarity
                if query_embedding is not None and memory.embedding:
                    try:
                        cosine_similarity = np.dot(query_embedding, memory.embedding) / (
                            np.linalg.norm(query_embedding) * np.linalg.norm(memory.embedding))
                        relevance_score = max(relevance_score, cosine_similarity)
                    except Exception:
                        pass
                
                # Combine scores
                relevance_score = max(relevance_score, keyword_overlap)
                
                # Tag matching bonus
                if memory.tags and any(tag.lower() in query_lower for tag in memory.tags):
                    relevance_score += 0.2
                
                if relevance_score >= min_relevance:
                    # Update access information
                    memory.last_accessed = datetime.now()
                    memory.access_count += 1
                    
                    relevant_memories.append((memory, relevance_score))
            
            # Sort by relevance and return top results
            relevant_memories.sort(key=lambda x: x[1], reverse=True)
            result = [memory for memory, _ in relevant_memories[:limit]]
            
            # Update access counts in database
            for memory in result:
                await self._update_memory_access(memory)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error recalling memories: {e}")
            return []
    
    async def _update_memory_access(self, memory: Memory) -> None:
        """Update memory access information in PostgreSQL"""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    UPDATE memories 
                    SET last_accessed = $1, access_count = $2
                    WHERE id = $3::uuid
                """, memory.last_accessed, memory.access_count, memory.id)
        except Exception as e:
            self.logger.error(f"Error updating memory access: {e}")
    
    async def learn_user_preference(self, preference_key: str, preference_value: Any,
                                  confidence: float = 1.0, source: str = "conversation") -> None:
        """Learn and store user preferences in PostgreSQL"""
        try:
            # Store in cache
            self.user_preferences[preference_key] = preference_value
            
            # Store in database
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO user_preferences
                    (key, value, confidence, updated_at, source)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (key) DO UPDATE SET
                        value = $2,
                        confidence = $3,
                        updated_at = $4
                """,
                preference_key,
                json.dumps(preference_value),
                confidence,
                datetime.now(),
                source
            )
            
            self.logger.info(f"Learned user preference: {preference_key} = {preference_value}")
        except Exception as e:
            self.logger.error(f"Error learning user preference: {e}")
            raise
    
    async def get_user_preferences(self, category: Optional[str] = None) -> Dict[str, Any]:
        """Get user preferences, optionally filtered by category"""
        if category:
            return {k: v for k, v in self.user_preferences.items() if category in k.lower()}
        return self.user_preferences.copy()
    
    async def store_conversation_turn(self, role: str, content: str, context: Optional[Dict[str, Any]] = None):
        """Store a conversation turn with context"""
        
        conversation_memory = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "context": context or {}
        }
        
        self.conversation_context.append(conversation_memory)
        
        # Keep only recent conversation history
        if len(self.conversation_context) > 50:
            self.conversation_context = self.conversation_context[-50:]
        
        # Store important conversations as memories
        if len(content) > 50:  # Only store substantial messages
            importance = ImportanceLevel.LOW
            if any(keyword in content.lower() for keyword in 
                   ["strategy", "goal", "important", "priority", "plan", "decision"]):
                importance = ImportanceLevel.MEDIUM
            
            await self.store_memory(
                content=f"{role}: {content}",
                memory_type=MemoryType.CONVERSATION,
                importance=importance,
                tags=["conversation", role],
                metadata={"context": context, "timestamp": conversation_memory["timestamp"]}
            )
    
    async def get_conversation_context(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent conversation context"""
        return self.conversation_context[-limit:] if limit else self.conversation_context
    
    async def identify_learning_patterns(self) -> List[LearningPattern]:
        """Identify patterns in user behavior and preferences"""
        
        patterns = []
        
        try:
            # Analyze conversation patterns
            user_messages = [turn for turn in self.conversation_context if turn["role"] == "user"]
            
            if len(user_messages) >= 5:
                # Common topics pattern
                all_content = " ".join([msg["content"].lower() for msg in user_messages])
                common_words = self._extract_common_words(all_content)
                
                if common_words:
                    pattern = LearningPattern(
                        pattern_id="common_topics",
                        pattern_type="preference",
                        description=f"User frequently discusses: {', '.join(common_words[:5])}",
                        frequency=len(common_words),
                        confidence=0.8,
                        examples=[msg["content"][:100] for msg in user_messages[-3:]],
                        discovered_at=datetime.now()
                    )
                    patterns.append(pattern)
                
                # Question pattern analysis
                questions = [msg for msg in user_messages if "?" in msg["content"]]
                if len(questions) >= 3:
                    pattern = LearningPattern(
                        pattern_id="question_pattern",
                        pattern_type="workflow",
                        description=f"User asks {len(questions)} questions, prefers detailed explanations",
                        frequency=len(questions),
                        confidence=0.7,
                        examples=[q["content"] for q in questions[-3:]],
                        discovered_at=datetime.now()
                    )
                    patterns.append(pattern)
            
            # Store patterns
            for pattern in patterns:
                await self._store_learning_pattern(pattern)
            
        except Exception as e:
            self.logger.error(f"Error identifying learning patterns: {e}")
        
        return patterns
    
    def _extract_common_words(self, text: str, min_length: int = 4) -> List[str]:
        """Extract common meaningful words from text"""
        # Simple word frequency analysis
        words = text.split()
        word_counts = {}
        
        # Filter and count words
        for word in words:
            word = word.strip('.,!?":()[]{}').lower()
            if len(word) >= min_length and word.isalpha():
                word_counts[word] = word_counts.get(word, 0) + 1
        
        # Return top words
        sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
        return [word for word, count in sorted_words if count >= 2]
    
    async def _store_learning_pattern(self, pattern: LearningPattern) -> None:
        """Store learning pattern in PostgreSQL database"""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO learning_patterns
                    (pattern_id, pattern_type, description, frequency, confidence, examples, discovered_at)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (pattern_id) DO UPDATE SET
                        frequency = $4,
                        confidence = $5,
                        examples = $6
                """,
                pattern.pattern_id,
                pattern.pattern_type,
                pattern.description,
                pattern.frequency,
                pattern.confidence,
                pattern.examples if pattern.examples else None,  # Pass list directly, not JSON
                pattern.discovered_at
            )
        except Exception as e:
            self.logger.error(f"Error storing learning pattern: {e}")
            raise
    
    async def _update_knowledge_clusters(self, memory: Memory):
        """Update knowledge clusters with new memory"""
        
        # Simple clustering based on memory type and tags
        cluster_key = f"{memory.memory_type.value}_{'-'.join(memory.tags or ['general'])}"
        
        if cluster_key in self.knowledge_clusters:
            cluster = self.knowledge_clusters[cluster_key]
            if memory.id not in cluster.memories:
                cluster.memories.append(memory.id)
            cluster.last_updated = datetime.now()
            cluster.importance_score = self._calculate_cluster_importance(cluster)
        else:
            # Create new cluster with UUID
            cluster_id = str(uuid4())
            cluster = KnowledgeCluster(
                id=cluster_id,
                name=f"{memory.memory_type.value.replace('_', ' ').title()} Knowledge",
                description=f"Knowledge cluster for {memory.memory_type.value}",
                memories=[memory.id],
                confidence=memory.confidence,
                last_updated=datetime.now(),
                importance_score=memory.importance.value,
                topics=memory.tags if memory.tags else []
            )
            self.knowledge_clusters[cluster_key] = cluster
        
        # Persist cluster
        await self._persist_knowledge_cluster(cluster)
    
    def _calculate_cluster_importance(self, cluster: KnowledgeCluster) -> float:
        """Calculate importance score for a knowledge cluster"""
        # Simple importance calculation based on memory count and recency
        base_score = len(cluster.memories) * 0.1
        recency_bonus = 1.0 if (datetime.now() - cluster.last_updated).days < 7 else 0.5
        return min(5.0, base_score + recency_bonus)
    
    async def _persist_knowledge_cluster(self, cluster: KnowledgeCluster) -> None:
        """Persist knowledge cluster to PostgreSQL database"""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO knowledge_clusters
                    (id, name, description, memories, confidence, last_updated, importance_score, topics)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO UPDATE SET
                        memories = $4,
                        confidence = $5,
                        last_updated = $6,
                        importance_score = $7
                """,
                cluster.id,
                cluster.name,
                cluster.description,
                cluster.memories if cluster.memories else None,  # Pass list directly, not JSON
                cluster.confidence,
                cluster.last_updated,
                cluster.importance_score,
                cluster.topics if cluster.topics else None  # Pass list directly
            )
        except Exception as e:
            self.logger.error(f"Error persisting knowledge cluster: {e}")
            raise
    
    async def get_contextual_knowledge(self, query: str, context_type: str = "general") -> Dict[str, Any]:
        """Get contextual knowledge relevant to current situation"""
        
        # Recall relevant memories
        relevant_memories = await self.recall_memories(query, limit=15)
        
        # Get user preferences
        preferences = await self.get_user_preferences()
        
        # Get conversation context
        conversation_context = await self.get_conversation_context(limit=5)
        
        # Get relevant knowledge clusters
        relevant_clusters = []
        for cluster in self.knowledge_clusters.values():
            if any(topic and topic.lower() in query.lower() for topic in (cluster.topics or [])):
                relevant_clusters.append(cluster)
        
        return {
            "relevant_memories": [asdict(memory) for memory in relevant_memories],
            "user_preferences": preferences,
            "conversation_context": conversation_context,
            "knowledge_clusters": [asdict(cluster) for cluster in relevant_clusters],
            "context_type": context_type,
            "generated_at": datetime.now().isoformat()
        }
    
    async def forget_outdated_memories(self, days_threshold: int = 90) -> None:
        """Forget or archive old, low-importance memories from PostgreSQL"""
        try:
            cutoff_date = datetime.now() - timedelta(days=days_threshold)
            
            async with self.db_pool.acquire() as conn:
                # Find outdated, low-importance memories
                outdated_rows = await conn.fetch("""
                    SELECT id FROM memories
                    WHERE last_accessed < $1 
                    AND importance <= $2
                    AND access_count < 3
                """, cutoff_date, ImportanceLevel.LOW.value)
                
                if outdated_rows:
                    memory_ids = [row['id'] for row in outdated_rows]
                    
                    # Delete from database
                    # Use ANY operator for PostgreSQL list comparison with UUID type
                    deleted = await conn.execute("""
                        DELETE FROM memories WHERE id = ANY($1::uuid[])
                    """, memory_ids)
                    
                    # Remove from cache
                    self.recent_memories = [m for m in self.recent_memories if m.id not in memory_ids]
                    self.important_memories = [m for m in self.important_memories if m.id not in memory_ids]
                    
                    self.logger.info(f"Forgot {len(memory_ids)} outdated memories")
        except Exception as e:
            self.logger.error(f"Error forgetting outdated memories: {e}")
            raise
    
    
    async def get_memory_summary(self) -> Dict[str, Any]:
        """Get comprehensive memory system summary from PostgreSQL"""
        try:
            async with self.db_pool.acquire() as conn:
                # Memory statistics
                total_memories = await conn.fetchval("SELECT COUNT(*) FROM memories")
                
                memory_by_type_rows = await conn.fetch("""
                    SELECT memory_type, COUNT(*) as count FROM memories GROUP BY memory_type
                """)
                memory_by_type = {row['memory_type']: row['count'] for row in memory_by_type_rows}
                
                total_preferences = await conn.fetchval("SELECT COUNT(*) FROM user_preferences")
                total_clusters = await conn.fetchval("SELECT COUNT(*) FROM knowledge_clusters")
                total_patterns = await conn.fetchval("SELECT COUNT(*) FROM learning_patterns")
            
            return {
                "total_memories": total_memories,
                "memory_by_type": memory_by_type,
                "total_preferences": total_preferences,
                "total_knowledge_clusters": total_clusters,
                "total_learning_patterns": total_patterns,
                "recent_memories_count": len(self.recent_memories),
                "important_memories_count": len(self.important_memories),
                "conversation_turns": len(self.conversation_context),
                "embedding_model_active": self.embedding_model is not None,
                "last_updated": datetime.now().isoformat()
            }
        except Exception as e:
            self.logger.error(f"Error getting memory summary: {e}")
            raise
    
# Example usage (requires database service with connection pool)
async def main(db_pool: asyncpg.Pool):
    """Test the AI memory system with PostgreSQL"""
    logging.basicConfig(level=logging.INFO)
    
    memory_system = AIMemorySystem(db_pool=db_pool)
    await memory_system.initialize()
    
    print("🧠 Testing AI Memory System...")
    
    # Store some test memories
    await memory_system.store_memory(
        "Glad Labs focuses on AI content automation for small businesses",
        MemoryType.BUSINESS_FACT,
        ImportanceLevel.HIGH,
        tags=["business_model", "target_market"]
    )
    
    await memory_system.store_memory(
        "User prefers cost-effective AI solutions and local models for development",
        MemoryType.USER_PREFERENCE,
        ImportanceLevel.MEDIUM,
        tags=["cost_optimization", "development"]
    )
    
    await memory_system.store_memory(
        "Content creation rate should be 3+ pieces per day for market competitiveness",
        MemoryType.STRATEGIC_INSIGHT,
        ImportanceLevel.HIGH,
        tags=["content_strategy", "performance"]
    )
    
    # Test conversation storage
    await memory_system.store_conversation_turn(
        "user", 
        "I want to build a comprehensive AI co-founder that understands my business",
        {"topic": "ai_development", "priority": "high"}
    )
    
    await memory_system.store_conversation_turn(
        "assistant",
        "I'll help you create an intelligent AI co-founder with full business context awareness",
        {"response_type": "commitment", "capability": "business_intelligence"}
    )
    
    # Test memory recall
    print("\n🔍 Testing memory recall...")
    memories = await memory_system.recall_memories("AI business automation")
    print(f"Found {len(memories)} relevant memories")
    
    for memory in memories:
        print(f"  • {memory.memory_type.value}: {memory.content[:60]}...")
    
    # Test user preferences
    await memory_system.learn_user_preference("ai_cost_preference", "cost_optimized")
    await memory_system.learn_user_preference("development_environment", "local_models")
    
    preferences = await memory_system.get_user_preferences()
    print(f"\n👤 User preferences: {len(preferences)} stored")
    
    # Get contextual knowledge
    context = await memory_system.get_contextual_knowledge("business strategy and AI automation")
    print(f"\n📚 Contextual knowledge gathered:")
    print(f"  • Relevant memories: {len(context['relevant_memories'])}")
    print(f"  • User preferences: {len(context['user_preferences'])}")
    print(f"  • Knowledge clusters: {len(context['knowledge_clusters'])}")
    
    # Get system summary
    summary = await memory_system.get_memory_summary()
    print(f"\n📊 Memory System Summary:")
    print(f"  • Total memories: {summary['total_memories']}")
    print(f"  • Memory types: {summary['memory_by_type']}")
    print(f"  • User preferences: {summary['total_preferences']}")
    print(f"  • Knowledge clusters: {summary['total_knowledge_clusters']}")
    print(f"  • Embedding model: {'Active' if summary['embedding_model_active'] else 'Inactive'}")


if __name__ == "__main__":
    print("❌ This module must be used with a PostgreSQL database connection pool.")
    print("   Use from within the FastAPI application context during lifespan initialization.")