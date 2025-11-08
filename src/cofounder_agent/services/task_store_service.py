"""
Persistent Task Store Service

Replaces in-memory task storage with database persistence.
Provides unified interface for task CRUD operations across all services.

Supports:
- PostgreSQL (production) and SQLite (development)
- Automatic table creation
- Task filtering and pagination
- Synchronous operations (compatible with FastAPI)
- Connection pooling
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import uuid
import logging
import os

from sqlalchemy import Column, String, DateTime, Text, JSON, Integer, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)

# ============================================================================
# DATABASE MODELS
# ============================================================================

Base = declarative_base()


class ContentTask(Base):
    """SQLAlchemy model for persistent task storage"""

    __tablename__ = "content_tasks"

    # Primary key
    task_id = Column(String(64), primary_key=True, index=True)

    # Task metadata
    request_type = Column(String(50), nullable=False)  # basic, enhanced
    status = Column(String(50), nullable=False, index=True)  # pending, processing, completed, failed
    topic = Column(String(500), nullable=False)
    style = Column(String(50), nullable=False)
    tone = Column(String(50), nullable=False)
    target_length = Column(Integer, default=2000)

    # Content data
    content = Column(Text, nullable=True)  # Generated blog post
    excerpt = Column(Text, nullable=True)  # Short excerpt
    featured_image_prompt = Column(Text, nullable=True)
    featured_image_url = Column(String(500), nullable=True)
    featured_image_data = Column(JSON, nullable=True)  # Metadata about image

    # Publishing data
    publish_mode = Column(String(50), default="draft")  # draft, published, archived
    strapi_id = Column(String(100), nullable=True, index=True)  # Strapi post ID
    strapi_url = Column(String(500), nullable=True)

    # Metadata
    tags = Column(JSON, default=list)  # List of tags
    task_metadata = Column(JSON, nullable=True)  # Additional metadata
    model_used = Column(String(100), nullable=True)  # AI model that generated content
    quality_score = Column(Integer, nullable=True)  # Quality score 1-100

    # Progress tracking
    progress = Column(JSON, nullable=True)  # {stage, percentage, message}
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "task_id": self.task_id,
            "request_type": self.request_type,
            "status": self.status,
            "topic": self.topic,
            "style": self.style,
            "tone": self.tone,
            "target_length": self.target_length,
            "content": self.content,
            "excerpt": self.excerpt,
            "featured_image_prompt": self.featured_image_prompt,
            "featured_image_url": self.featured_image_url,
            "featured_image_data": self.featured_image_data,
            "publish_mode": self.publish_mode,
            "strapi_id": self.strapi_id,
            "strapi_url": self.strapi_url,
            "tags": self.tags or [],
            "metadata": self.task_metadata or {},
            "model_used": self.model_used,
            "quality_score": self.quality_score,
            "progress": self.progress or {"stage": "pending", "percentage": 0},
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# ============================================================================
# SYNCHRONOUS DATABASE CONNECTION
# ============================================================================


class SyncTaskStoreDatabase:
    """Synchronous database connection manager for task storage"""

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize synchronous database

        Args:
            database_url: PostgreSQL database URL (required)
                         Must be provided or set in DATABASE_URL environment variable
        """
        if database_url is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                logger.error("❌ DATABASE_URL not set! Cannot initialize task store without PostgreSQL")
                raise ValueError("DATABASE_URL environment variable must be set")

        self.database_url = database_url
        self.engine = None
        self.session_factory = None
        logger.info(f"🗄️ Initializing task store database: {database_url[:50]}...")

    def initialize(self):
        """Initialize database engine and create tables"""
        try:
            # Create engine for PostgreSQL with connection pooling
            self.engine = create_engine(
                self.database_url,
                echo=False,
                poolclass=QueuePool,
                pool_size=20,
                max_overflow=40,
                pool_pre_ping=True,
            )

            # Create session factory
            self.session_factory = sessionmaker(bind=self.engine)

            # Create tables
            Base.metadata.create_all(self.engine)
            logger.info("✅ Database tables created/verified in PostgreSQL")

        except Exception as e:
            logger.error(f"❌ Error initializing database: {e}")
            raise

    def close(self):
        """Close database connection"""
        if self.engine:
            self.engine.dispose()
            logger.info("Database connection closed")

    def get_session(self) -> Session:
        """Get new database session"""
        if not self.session_factory:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self.session_factory()


# ============================================================================
# PERSISTENT TASK STORE SERVICE
# ============================================================================


class PersistentTaskStore:
    """Unified persistent task storage service using database"""

    def __init__(self, database: SyncTaskStoreDatabase):
        """
        Initialize persistent task store

        Args:
            database: SyncTaskStoreDatabase instance
        """
        self.database = database

    def create_task(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: Optional[List[str]] = None,
        request_type: str = "basic",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a new task and store in database

        Args:
            topic: Blog post topic
            style: Content style
            tone: Content tone
            target_length: Target word count
            tags: Tags for categorization
            request_type: Type of request (basic, enhanced)
            metadata: Additional metadata

        Returns:
            Task ID for tracking
        """
        logger.debug(f"🟡 PersistentTaskStore.create_task() called - Topic: {topic}")
        task_id = f"blog_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        logger.debug(f"  🆔 Generated task_id: {task_id}")

        session = self.database.get_session()
        logger.debug(f"  📊 Got database session: {session is not None}")
        
        try:
            task = ContentTask(
                task_id=task_id,
                request_type=request_type,
                status="pending",
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags=tags or [],
                task_metadata=metadata or {},
                progress={
                    "stage": "queued",
                    "percentage": 0,
                    "message": "Task created and queued",
                },
            )
            logger.debug(f"  📋 ContentTask object created: {task_id}")
            
            session.add(task)
            logger.debug(f"  ➕ Task added to session")
            
            session.commit()
            logger.info(f"✅✅ Task COMMITTED TO DATABASE: {task_id}")
            logger.debug(f"  💾 Database commit successful")
            
            return task_id

        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error creating task in database: {e}", exc_info=True)
            raise
        finally:
            session.close()
            logger.debug(f"  🔐 Database session closed")

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task by ID from database"""
        logger.debug(f"🟡 PersistentTaskStore.get_task() called - task_id: {task_id}")
        
        session = self.database.get_session()
        try:
            task = session.query(ContentTask).filter_by(task_id=task_id).first()
            
            if task:
                task_dict = task.to_dict()
                logger.debug(f"✅ Task FOUND in database: {task_id} - status: {task.status}")
                return task_dict
            else:
                logger.warning(f"⚠️ Task NOT FOUND in database: {task_id}")
                return None

        except Exception as e:
            logger.error(f"❌ Error getting task: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """Update task in database"""
        session = self.database.get_session()
        try:
            task = session.query(ContentTask).filter_by(task_id=task_id).first()

            if not task:
                logger.warning(f"Task not found: {task_id}")
                return False

            # Update fields
            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)

            task.updated_at = datetime.utcnow()
            session.commit()
            logger.debug(f"✅ Task updated: {task_id}")
            return True

        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error updating task: {e}")
            raise
        finally:
            session.close()

    def delete_task(self, task_id: str) -> bool:
        """Delete task from database"""
        session = self.database.get_session()
        try:
            task = session.query(ContentTask).filter_by(task_id=task_id).first()

            if task:
                session.delete(task)
                session.commit()
                logger.info(f"✅ Task deleted: {task_id}")
                return True

            return False

        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error deleting task: {e}")
            raise
        finally:
            session.close()

    def list_tasks(
        self, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        List tasks with optional filtering

        Args:
            status: Filter by status (pending, processing, completed, failed)
            limit: Number of tasks to return
            offset: Pagination offset

        Returns:
            Tuple of (tasks, total_count)
        """
        logger.debug(f"🟡 PersistentTaskStore.list_tasks() called - status: {status}, limit: {limit}, offset: {offset}")
        
        session = self.database.get_session()
        try:
            query = session.query(ContentTask)

            if status:
                query = query.filter_by(status=status)
                logger.debug(f"  🔍 Filtering by status: {status}")

            # Count total
            total = query.count()
            logger.debug(f"  📊 Total tasks in database: {total}")

            # Get paginated results
            results = query.order_by(ContentTask.created_at.desc()).offset(offset).limit(limit).all()
            tasks = [task.to_dict() for task in results]
            
            logger.info(f"✅ Listed {len(tasks)} tasks from database - total: {total} (status={status})")
            logger.debug(f"  📋 Returned tasks: {[t['task_id'] for t in tasks]}")
            return tasks, total

        except Exception as e:
            logger.error(f"❌ Error listing tasks: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def get_drafts(
        self, limit: int = 20, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Get list of draft tasks (completed but not published)

        Returns:
            Tuple of (drafts, total_count)
        """
        session = self.database.get_session()
        try:
            query = session.query(ContentTask).filter(
                ContentTask.status == "completed",
                ContentTask.publish_mode == "draft",
            )

            # Count total
            total = query.count()

            # Get paginated results
            drafts = [
                task.to_dict()
                for task in query.order_by(ContentTask.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            ]

            logger.debug(f"✅ Listed drafts: {len(drafts)} of {total}")
            return drafts, total

        except Exception as e:
            logger.error(f"❌ Error getting drafts: {e}")
            raise
        finally:
            session.close()

    def get_stats(self) -> Dict[str, Any]:
        """Get task statistics"""
        session = self.database.get_session()
        try:
            total = session.query(ContentTask).count()
            pending = session.query(ContentTask).filter_by(status="pending").count()
            processing = session.query(ContentTask).filter_by(status="processing").count()
            completed = session.query(ContentTask).filter_by(status="completed").count()
            failed = session.query(ContentTask).filter_by(status="failed").count()

            return {
                "total_tasks": total,
                "pending": pending,
                "processing": processing,
                "completed": completed,
                "failed": failed,
            }

        except Exception as e:
            logger.error(f"❌ Error getting stats: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    def close(self):
        """Close database connection"""
        self.database.close()


# ============================================================================
# GLOBAL PERSISTENT TASK STORE
# ============================================================================

_persistent_task_store: Optional[PersistentTaskStore] = None


def initialize_task_store(database_url: Optional[str] = None):
    """
    Initialize global persistent task store

    Args:
        database_url: Database connection URL (optional)
                     Uses DATABASE_URL env var if not provided
    """
    global _persistent_task_store

    database = SyncTaskStoreDatabase(database_url)
    database.initialize()
    _persistent_task_store = PersistentTaskStore(database)
    logger.info("✅ Persistent task store initialized")


def get_persistent_task_store() -> PersistentTaskStore:
    """Get global persistent task store"""
    if not _persistent_task_store:
        raise RuntimeError("Task store not initialized. Call initialize_task_store() first.")
    return _persistent_task_store
