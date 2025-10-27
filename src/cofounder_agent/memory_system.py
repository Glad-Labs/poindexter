"""
"""AI Memory & Knowledge Management System for Glad Labs Co-Founder"""

This module provides persistent memory, knowledge base management, and learning
capabilities for the AI co-founder system. It enables the AI to remember conversations,
learn from interactions, build domain expertise, and maintain context across sessions.
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
import sqlite3
import hashlib

import numpy as np
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
    1. Persistent conversation memory
    2. Business knowledge base
    3. User preference learning
    4. Strategic insight retention
    5. Contextual information retrieval
    6. Knowledge graph relationships
    7. Adaptive forgetting and importance weighting
    """
    
    def __init__(self, memory_dir: str = "ai_memory_system"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(exist_ok=True)
        
        self.logger = logging.getLogger("ai_memory_system")
        
        # Database for structured memory storage
        self.db_path = self.memory_dir / "memory.db"
        self._init_database()
        
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
        
        # Initialize from persistent storage
        asyncio.create_task(self._load_persistent_memory())
    
    def _init_database(self):
        """Initialize SQLite database for memory storage"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Memories table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
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
            """)
            
            # Knowledge clusters table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_clusters (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    memories TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    last_updated TEXT NOT NULL,
                    importance_score REAL NOT NULL,
                    topics TEXT
                )
            """)
            
            # Learning patterns table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS learning_patterns (
                    pattern_id TEXT PRIMARY KEY,
                    pattern_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    frequency INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    examples TEXT,
                    discovered_at TEXT NOT NULL
                )
            """)
            
            # User preferences table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    source TEXT
                )
            """)
            
            # Conversation sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    message_count INTEGER DEFAULT 0,
                    topics TEXT,
                    summary TEXT,
                    importance REAL DEFAULT 0.5
                )
            """)
            
            conn.commit()
    
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
    
    async def _load_persistent_memory(self):
        """Load persistent memory from database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Load recent and important memories
                cursor.execute("""
                    SELECT * FROM memories 
                    ORDER BY last_accessed DESC 
                    LIMIT ?
                """, (self.max_recent_memories,))
                
                rows = cursor.fetchall()
                self.recent_memories = [self._row_to_memory(row) for row in rows]
                
                # Load user preferences
                cursor.execute("SELECT key, value, confidence FROM user_preferences")
                prefs = cursor.fetchall()
                self.user_preferences = {
                    key: json.loads(value) for key, value, _ in prefs
                }
                
                # Load knowledge clusters
                cursor.execute("SELECT * FROM knowledge_clusters")
                clusters = cursor.fetchall()
                self.knowledge_clusters = {
                    row[0]: self._row_to_cluster(row) for row in clusters
                }
                
                self.logger.info(f"Loaded {len(self.recent_memories)} memories, {len(self.user_preferences)} preferences")
                
        except Exception as e:
            self.logger.error(f"Error loading persistent memory: {e}")
    
    def _row_to_memory(self, row: Tuple) -> Memory:
        """Convert database row to Memory object"""
        embedding = None
        if row[11]:  # embedding blob
            try:
                embedding = pickle.loads(row[11])
            except Exception:
                pass
        
        return Memory(
            id=row[0],
            content=row[1],
            memory_type=MemoryType(row[2]),
            importance=ImportanceLevel(row[3]),
            confidence=row[4],
            created_at=datetime.fromisoformat(row[5]),
            last_accessed=datetime.fromisoformat(row[6]),
            access_count=row[7],
                tags=json.loads(row[8]) if row[8] else [],
                related_memories=json.loads(row[9]) if row[9] else [],
                metadata=json.loads(row[10]) if row[10] else {},
            embedding=embedding
        )
    
    def _row_to_cluster(self, row: Tuple) -> KnowledgeCluster:
        """Convert database row to KnowledgeCluster object"""
        return KnowledgeCluster(
            id=row[0],
            name=row[1],
            description=row[2],
            memories=json.loads(row[3]),
            confidence=row[4],
            last_updated=datetime.fromisoformat(row[5]),
            importance_score=row[6],
              topics=json.loads(row[7]) if row[7] else []
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
    
    async def _persist_memory(self, memory: Memory):
        """Persist memory to database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            embedding_blob = None
            if memory.embedding:
                embedding_blob = pickle.dumps(memory.embedding)
            
            cursor.execute("""
                INSERT OR REPLACE INTO memories 
                (id, content, memory_type, importance, confidence, created_at, 
                 last_accessed, access_count, tags, related_memories, metadata, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory.id,
                memory.content,
                memory.memory_type.value,
                memory.importance.value,
                memory.confidence,
                memory.created_at.isoformat(),
                memory.last_accessed.isoformat(),
                memory.access_count,
                json.dumps(memory.tags) if memory.tags else None,
                json.dumps(memory.related_memories) if memory.related_memories else None,
                json.dumps(memory.metadata) if memory.metadata else None,
                embedding_blob
            ))
            
            conn.commit()
    
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
    
    async def _update_memory_access(self, memory: Memory):
        """Update memory access information in database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE memories 
                SET last_accessed = ?, access_count = ?
                WHERE id = ?
            """, (memory.last_accessed.isoformat(), memory.access_count, memory.id))
            conn.commit()
    
    async def learn_user_preference(self, preference_key: str, preference_value: Any,
                                  confidence: float = 1.0, source: str = "conversation"):
        """Learn and store user preferences"""
        
        # Store in cache
        self.user_preferences[preference_key] = preference_value
        
        # Store in database
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO user_preferences
                (key, value, confidence, updated_at, source)
                VALUES (?, ?, ?, ?, ?)
            """, (
                preference_key,
                json.dumps(preference_value),
                confidence,
                datetime.now().isoformat(),
                source
            ))
            conn.commit()
        
        self.logger.info(f"Learned user preference: {preference_key} = {preference_value}")
    
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
    
    async def _store_learning_pattern(self, pattern: LearningPattern):
        """Store learning pattern in database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO learning_patterns
                (pattern_id, pattern_type, description, frequency, confidence, examples, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                pattern.pattern_id,
                pattern.pattern_type,
                pattern.description,
                pattern.frequency,
                pattern.confidence,
                json.dumps(pattern.examples),
                pattern.discovered_at.isoformat()
            ))
            conn.commit()
    
    async def _update_knowledge_clusters(self, memory: Memory):
        """Update knowledge clusters with new memory"""
        
        # Simple clustering based on memory type and tags
        cluster_key = f"{memory.memory_type.value}_{'-'.join(memory.tags or ['general'])}"
        
        if cluster_key in self.knowledge_clusters:
            cluster = self.knowledge_clusters[cluster_key]
            cluster.memories.append(memory.id)
            cluster.last_updated = datetime.now()
            cluster.importance_score = self._calculate_cluster_importance(cluster)
        else:
            # Create new cluster
            cluster = KnowledgeCluster(
                id=cluster_key,
                name=f"{memory.memory_type.value.replace('_', ' ').title()} Knowledge",
                description=f"Knowledge cluster for {memory.memory_type.value}",
                memories=[memory.id],
                confidence=memory.confidence,
                last_updated=datetime.now(),
                importance_score=memory.importance.value,
                topics=memory.tags
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
    
    async def _persist_knowledge_cluster(self, cluster: KnowledgeCluster):
        """Persist knowledge cluster to database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO knowledge_clusters
                (id, name, description, memories, confidence, last_updated, importance_score, topics)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cluster.id,
                cluster.name,
                cluster.description,
                json.dumps(cluster.memories),
                cluster.confidence,
                cluster.last_updated.isoformat(),
                cluster.importance_score,
                json.dumps(cluster.topics) if cluster.topics else None
            ))
            conn.commit()
    
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
    
    async def forget_outdated_memories(self, days_threshold: int = 90):
        """Forget or archive old, low-importance memories"""
        
        cutoff_date = datetime.now() - timedelta(days=days_threshold)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Find outdated, low-importance memories
            cursor.execute("""
                SELECT id FROM memories
                WHERE last_accessed < ? 
                AND importance <= ?
                AND access_count < 3
            """, (cutoff_date.isoformat(), ImportanceLevel.LOW.value))
            
            outdated_memories = cursor.fetchall()
            
            if outdated_memories:
                memory_ids = [row[0] for row in outdated_memories]
                
                # Delete from database
                placeholders = ','.join(['?' for _ in memory_ids])
                cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", memory_ids)
                
                conn.commit()
                
                # Remove from cache
                self.recent_memories = [m for m in self.recent_memories if m.id not in memory_ids]
                self.important_memories = [m for m in self.important_memories if m.id not in memory_ids]
                
                self.logger.info(f"Forgot {len(memory_ids)} outdated memories")
    
    async def get_memory_summary(self) -> Dict[str, Any]:
        """Get comprehensive memory system summary"""
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Memory statistics
            cursor.execute("SELECT COUNT(*) FROM memories")
            total_memories = cursor.fetchone()[0]
            
            cursor.execute("SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type")
            memory_by_type = dict(cursor.fetchall())
            
            cursor.execute("SELECT COUNT(*) FROM user_preferences")
            total_preferences = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM knowledge_clusters")
            total_clusters = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM learning_patterns")
            total_patterns = cursor.fetchone()[0]
        
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


# Example usage
async def main():
    """Test the AI memory system"""
    logging.basicConfig(level=logging.INFO)
    
    memory_system = AIMemorySystem()
    
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
    asyncio.run(main())