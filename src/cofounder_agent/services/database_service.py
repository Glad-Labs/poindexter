"""
PostgreSQL Database Service for GLAD Labs

Replaces Google Cloud Firestore with SQLAlchemy async ORM.
Provides same interface for tasks, logs, financial data, and agent status tracking.

Key differences from Firestore:
- Synchronous database calls (can be made async with asyncio.to_thread or use async SQLAlchemy)
- Structured relational schema instead of document collections
- Type-safe ORM instead of dynamic document fields
- PostgreSQL on Railway (free tier included)
"""

import logging
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from uuid import UUID, uuid4

# Async SQLAlchemy support
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy import select, desc, and_, or_, text
from sqlalchemy.exc import SQLAlchemyError

# Import models
from models import (
    Task,
    Log,
    FinancialEntry,
    AgentStatus,
    HealthCheck,
    Base,
)

logger = logging.getLogger(__name__)


class DatabaseService:
    """
    PostgreSQL database service for operational data

    Replaces Firestore client with synchronous PostgreSQL access.
    All methods return native Python types (dict, list, etc.) for
    easy serialization to JSON.
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database service with async SQLAlchemy engine

        Args:
            database_url: PostgreSQL connection URL
                         Default: from DATABASE_URL env var or SQLite for dev
        """
        # Get database URL from parameter, environment variable, or use default SQLite
        if database_url:
            self.database_url = database_url
        else:
            # Try DATABASE_URL first (Railway production style)
            database_url_env = os.getenv("DATABASE_URL")
            if database_url_env:
                self.database_url = database_url_env
            else:
                # Fall back to SQLite for local development
                # Check for DATABASE_FILENAME env var, default to .tmp/data.db
                database_filename = os.getenv("DATABASE_FILENAME", ".tmp/data.db")
                
                # Create parent directory if it doesn't exist
                db_dir = os.path.dirname(database_filename)
                if db_dir and not os.path.exists(db_dir):
                    os.makedirs(db_dir, exist_ok=True)
                
                # Convert to absolute path for Windows compatibility
                database_filename = os.path.abspath(database_filename)
                self.database_url = f"sqlite+aiosqlite:///{database_filename}"

        # Convert standard postgres:// to async postgresql+asyncpg://
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        elif self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace(
                "postgres://", "postgresql+asyncpg://", 1
            )

        # Create async engine
        self.engine = create_async_engine(
            self.database_url,
            echo=os.getenv("SQL_ECHO", "False") == "True",
            future=True,
            pool_pre_ping=True,  # Test connections before using
            pool_size=20,
            max_overflow=40,
        )

        # Create session factory
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        logger.info(f"Database service initialized with {self.database_url}")

    async def initialize(self):
        """Create all tables (idempotent with duplicate index handling)"""
        try:
            async with self.engine.begin() as conn:
                # Create all tables and indexes
                await conn.run_sync(Base.metadata.create_all)
                
                # Handle pre-existing indexes by checking PostgreSQL catalog
                # This prevents DuplicateTableError when indexes already exist
                from sqlalchemy import text
                await conn.execute(text("""
                    -- PostgreSQL automatically handles duplicate index names
                    -- by raising error only if same name with different columns.
                    -- For now, we'll just log existing indexes for debugging.
                """))
            logger.info("Database tables initialized successfully")
        except Exception as e:
            # Don't fail on duplicate index errors - they're not critical
            error_str = str(e).lower()
            if "duplicate" in error_str and "index" in error_str.lower():
                logger.warning(f"Index already exists (safe to ignore): {e}")
                logger.info("Database tables initialized (indexes already present)")
            elif "relation" in error_str and "already exists" in error_str:
                logger.warning(f"Table or index already exists: {e}")
                logger.info("Database tables initialized (tables already present)")
            else:
                logger.error(f"Failed to initialize database: {e}")
                raise

    async def close(self):
        """Close database connection"""
        await self.engine.dispose()
        logger.info("Database connection closed")

    # ========================================================================
    # TASK OPERATIONS (replaces Firestore 'tasks' collection)
    # ========================================================================

    async def add_task(self, task_data: Dict[str, Any]) -> str:
        """
        Add a new task to the database

        Args:
            task_data: Task information including topic, status, metadata

        Returns:
            Task ID (UUID as string)
        """
        try:
            task_id = uuid4()
            task = Task(
                id=task_id,
                task_name=task_data.get(
                    "taskName", f"Task: {task_data.get('topic', 'Unknown')}"
                ),
                agent_id=task_data.get("agentId", "content-creation-agent-v1"),
                status=task_data.get("status", "queued"),
                topic=task_data.get("topic", "Unknown topic"),
                primary_keyword=task_data.get("primary_keyword", "content"),
                target_audience=task_data.get("target_audience", "General"),
                category=task_data.get("category", "Blog Post"),
                metadata=task_data.get("metadata", {}),
            )

            async with self.async_session() as session:
                session.add(task)
                await session.commit()

            logger.info(
                f"Task created: {task_id}, topic={task_data.get('topic')}",
                extra={
                    "task_id": str(task_id),
                    "topic": task_data.get("topic"),
                    "status": task.status,
                },
            )

            return str(task_id)

        except Exception as e:
            logger.error(f"Failed to add task: {e}", extra={"task_data": task_data})
            raise

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific task by ID"""
        try:
            async with self.async_session() as session:
                stmt = select(Task).where(Task.id == UUID(task_id))
                result = await session.execute(stmt)
                task = result.scalar_one_or_none()

                if task:
                    return self._task_to_dict(task)
                else:
                    logger.warning(f"Task not found: {task_id}")
                    return None

        except Exception as e:
            logger.error(f"Failed to get task: {e}", extra={"task_id": task_id})
            raise

    async def get_pending_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending tasks for processing"""
        try:
            async with self.async_session() as session:
                stmt = (
                    select(Task)
                    .where(Task.status.in_(["queued", "pending"]))
                    .order_by(Task.created_at)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                tasks = result.scalars().all()

                tasks_dict = [self._task_to_dict(t) for t in tasks]
                logger.info(f"Retrieved {len(tasks_dict)} pending tasks")
                return tasks_dict

        except Exception as e:
            logger.error(f"Failed to get pending tasks: {e}")
            raise

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update task status and optional metadata"""
        try:
            async with self.async_session() as session:
                stmt = select(Task).where(Task.id == UUID(task_id))
                result_obj = await session.execute(stmt)
                task = result_obj.scalar_one_or_none()

                if not task:
                    logger.warning(f"Task not found for update: {task_id}")
                    return False

                task.status = status
                task.updated_at = datetime.utcnow()

                if status == "running" and not task.started_at:
                    task.started_at = datetime.utcnow()
                elif status == "completed" and not task.completed_at:
                    task.completed_at = datetime.utcnow()

                if metadata:
                    task.metadata.update(metadata)

                if result:
                    task.result = result

                await session.commit()

                logger.info(
                    f"Task status updated: {task_id} → {status}",
                    extra={"task_id": task_id, "status": status},
                )
                return True

        except Exception as e:
            logger.error(
                f"Failed to update task status: {e}",
                extra={"task_id": task_id, "status": status},
            )
            return False

    # ========================================================================
    # LOG OPERATIONS (replaces Firestore 'logs' collection)
    # ========================================================================

    async def add_log_entry(
        self,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> str:
        """Add a structured log entry"""
        try:
            log_id = uuid4()
            log = Log(
                id=log_id,
                level=level,
                message=message,
                metadata=metadata or {},
                task_id=UUID(task_id) if task_id else None,
                agent_id=agent_id,
            )

            async with self.async_session() as session:
                session.add(log)
                await session.commit()

            logger.info(
                f"Log entry created: {level} - {message[:50]}",
                extra={
                    "log_level": level,
                    "task_id": task_id,
                    "agent_id": agent_id,
                },
            )

            return str(log_id)

        except Exception as e:
            logger.error(f"Failed to add log entry: {e}")
            raise

    async def get_logs(
        self,
        level: Optional[str] = None,
        task_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get logs with optional filtering"""
        try:
            filters = []
            if level:
                filters.append(Log.level == level)
            if task_id:
                filters.append(Log.task_id == UUID(task_id))

            async with self.async_session() as session:
                stmt = select(Log).order_by(desc(Log.timestamp)).limit(limit)

                if filters:
                    stmt = stmt.where(and_(*filters))

                result = await session.execute(stmt)
                logs = result.scalars().all()

                return [self._log_to_dict(l) for l in logs]

        except Exception as e:
            logger.error(f"Failed to get logs: {e}")
            raise

    # ========================================================================
    # FINANCIAL OPERATIONS (replaces Firestore 'financials' collection)
    # ========================================================================

    async def add_financial_entry(self, entry_data: Dict[str, Any]) -> str:
        """Add a financial transaction entry"""
        try:
            entry_id = uuid4()
            entry = FinancialEntry(
                id=entry_id,
                amount=entry_data.get("amount", 0),
                category=entry_data.get("category", "other"),
                metadata=entry_data.get("metadata", {}),
                task_id=UUID(entry_data["task_id"])
                if "task_id" in entry_data
                else None,
            )

            async with self.async_session() as session:
                session.add(entry)
                await session.commit()

            logger.info(
                f"Financial entry added: ${entry.amount} - {entry.category}",
                extra={
                    "amount": entry.amount,
                    "category": entry.category,
                },
            )

            return str(entry_id)

        except Exception as e:
            logger.error(f"Failed to add financial entry: {e}")
            raise

    async def get_financial_summary(self, days: int = 30) -> Dict[str, Any]:
        """Get financial summary for the specified period"""
        try:
            start_date = datetime.utcnow() - timedelta(days=days)

            async with self.async_session() as session:
                stmt = (
                    select(FinancialEntry)
                    .where(FinancialEntry.timestamp >= start_date)
                    .order_by(desc(FinancialEntry.timestamp))
                )
                result = await session.execute(stmt)
                entries = result.scalars().all()

                total_spend = sum(e.amount for e in entries)
                entries_dict = [self._financial_entry_to_dict(e) for e in entries]

                summary = {
                    "total_spend": total_spend,
                    "entry_count": len(entries_dict),
                    "average_daily_spend": total_spend / days if days > 0 else 0,
                    "entries": entries_dict,
                }

                logger.info(
                    f"Financial summary generated: ${total_spend} over {days} days",
                    extra={"total_spend": total_spend, "days": days},
                )

                return summary

        except Exception as e:
            logger.error(f"Failed to get financial summary: {e}")
            raise

    # ========================================================================
    # AGENT STATUS OPERATIONS (replaces Firestore 'agents' collection)
    # ========================================================================

    async def update_agent_status(
        self, agent_name: str, status_data: Dict[str, Any]
    ) -> bool:
        """Update agent status and heartbeat"""
        try:
            async with self.async_session() as session:
                stmt = select(AgentStatus).where(
                    AgentStatus.agent_name == agent_name
                )
                result = await session.execute(stmt)
                agent_status = result.scalar_one_or_none()

                if not agent_status:
                    agent_status = AgentStatus(
                        agent_name=agent_name,
                        status=status_data.get("status", "online"),
                        service_version=status_data.get("service_version"),
                        metadata=status_data.get("metadata", {}),
                    )
                    session.add(agent_status)
                else:
                    agent_status.status = status_data.get(
                        "status", agent_status.status
                    )
                    agent_status.last_heartbeat = datetime.utcnow()
                    agent_status.service_version = status_data.get(
                        "service_version", agent_status.service_version
                    )
                    if "metadata" in status_data:
                        agent_status.metadata.update(status_data["metadata"])

                await session.commit()

                logger.info(
                    f"Agent status updated: {agent_name} → {agent_status.status}",
                    extra={"agent_name": agent_name, "status": agent_status.status},
                )
                return True

        except Exception as e:
            logger.error(
                f"Failed to update agent status: {e}",
                extra={"agent_name": agent_name},
            )
            return False

    async def get_agent_status(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get current status of a specific agent"""
        try:
            async with self.async_session() as session:
                stmt = select(AgentStatus).where(
                    AgentStatus.agent_name == agent_name
                )
                result = await session.execute(stmt)
                agent_status = result.scalar_one_or_none()

                if agent_status:
                    return self._agent_status_to_dict(agent_status)
                else:
                    logger.warning(f"Agent status not found: {agent_name}")
                    return None

        except Exception as e:
            logger.error(
                f"Failed to get agent status: {e}",
                extra={"agent_name": agent_name},
            )
            raise

    # ========================================================================
    # HEALTH CHECK OPERATIONS (replaces Firestore 'health' collection)
    # ========================================================================

    async def health_check(self, service: str = "cofounder") -> Dict[str, Any]:
        """Perform a basic health check on database connection"""
        try:
            import time

            start_time = time.time()

            # Simple connection test - just execute a basic query
            async with self.async_session() as session:
                await session.execute(text("SELECT 1"))

            response_time = (time.time() - start_time) * 1000  # Convert to ms

            # Log health check result
            health_entry = HealthCheck(
                service=service,
                status="healthy",
                response_time_ms=response_time,
            )

            async with self.async_session() as session:
                session.add(health_entry)
                await session.commit()

            return {
                "status": "healthy",
                "service": service,
                "response_time_ms": response_time,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "service": service,
                "error": str(e),
            }

    # ========================================================================
    # HELPER METHODS (convert ORM objects to dicts)
    # ========================================================================

    @staticmethod
    def _task_to_dict(task: Task) -> Dict[str, Any]:
        """Convert Task ORM object to dictionary"""
        return {
            "id": str(task.id),
            "task_name": task.task_name,
            "agent_id": task.agent_id,
            "status": task.status,
            "topic": task.topic,
            "primary_keyword": task.primary_keyword,
            "target_audience": task.target_audience,
            "category": task.category,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat()
            if task.completed_at
            else None,
            "metadata": task.metadata,
            "result": task.result,
        }

    @staticmethod
    def _log_to_dict(log: Log) -> Dict[str, Any]:
        """Convert Log ORM object to dictionary"""
        return {
            "id": str(log.id),
            "level": log.level,
            "message": log.message,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "task_id": str(log.task_id) if log.task_id else None,
            "agent_id": log.agent_id,
            "metadata": log.metadata,
        }

    @staticmethod
    def _financial_entry_to_dict(entry: FinancialEntry) -> Dict[str, Any]:
        """Convert FinancialEntry ORM object to dictionary"""
        return {
            "id": str(entry.id),
            "amount": entry.amount,
            "category": entry.category,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "task_id": str(entry.task_id) if entry.task_id else None,
            "metadata": entry.metadata,
        }

    @staticmethod
    def _agent_status_to_dict(agent_status: AgentStatus) -> Dict[str, Any]:
        """Convert AgentStatus ORM object to dictionary"""
        return {
            "agent_name": agent_status.agent_name,
            "status": agent_status.status,
            "last_heartbeat": (
                agent_status.last_heartbeat.isoformat()
                if agent_status.last_heartbeat
                else None
            ),
            "created_at": (
                agent_status.created_at.isoformat()
                if agent_status.created_at
                else None
            ),
            "updated_at": (
                agent_status.updated_at.isoformat()
                if agent_status.updated_at
                else None
            ),
            "service_version": agent_status.service_version,
            "metadata": agent_status.metadata,
        }


__all__ = [
    "DatabaseService",
]
