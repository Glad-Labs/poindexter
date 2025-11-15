"""
PostgreSQL Database Service using asyncpg (async driver, no SQLAlchemy)

Replaces Google Cloud Firestore with asyncpg directly.
All methods are async and return plain dicts for easy JSON serialization.

Benefits:
- No SQLAlchemy complexity
- Pure async (no greenlet issues)
- Simple SQL queries
- Smaller deployment
- Easier debugging
"""

import logging
import os
import asyncpg
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class DatabaseService:
    """
    PostgreSQL database service using asyncpg (pure async)
    
    All methods are async and return plain dicts.
    Connection pool handles concurrency automatically.
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database service with asyncpg
        
        Args:
            database_url: PostgreSQL connection URL
                         Required: DATABASE_URL env var or passed explicitly
        """
        if database_url:
            self.database_url = database_url
        else:
            # Require DATABASE_URL env var - no fallback to SQLite
            database_url_env = os.getenv("DATABASE_URL")
            if not database_url_env:
                raise ValueError(
                    "❌ DATABASE_URL environment variable is required. "
                    "PostgreSQL is REQUIRED for all development and production environments. "
                    "Local development must use glad_labs_dev PostgreSQL database."
                )
            self.database_url = database_url_env

        logger.info(f"DatabaseService initialized with PostgreSQL: {self.database_url[:50]}...")
        
        self.pool = None

    async def initialize(self):
        """Initialize connection pool for PostgreSQL"""
        try:
            # PostgreSQL requires connection pooling
            min_size = int(os.getenv("DATABASE_POOL_MIN_SIZE", "10"))
            max_size = int(os.getenv("DATABASE_POOL_MAX_SIZE", "20"))
            
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=min_size,
                max_size=max_size,
                timeout=30,
            )
            logger.info(f"✅ Database pool initialized (size: {min_size}-{max_size})")
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}")
            raise

    async def close(self):
        """Close connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database pool closed")

    # ========================================================================
    # USERS
    # ========================================================================

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
            return dict(row) if row else None

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
            return dict(row) if row else None

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE username = $1", username)
            return dict(row) if row else None

    async def create_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new user"""
        user_id = user_data.get("id") or str(uuid4())
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (
                    id, email, username, password_hash, is_active, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                RETURNING *
                """,
                user_id,
                user_data.get("email"),
                user_data.get("username"),
                user_data.get("password_hash"),
                user_data.get("is_active", True),
            )
            return dict(row)

    # ========================================================================
    # TASKS
    # ========================================================================

    async def add_task(self, task_data: Dict[str, Any]) -> str:
        """Create new task"""
        import json
        task_id = task_data.get("id") or str(uuid4())
        
        logger.info(f"📝 [DB_ADD_TASK] Starting task insert for id: {task_id}")
        logger.info(f"📝 [DB_ADD_TASK] Task data keys: {list(task_data.keys())}")
        
        # PostgreSQL path (with connection pooling)
        try:
            async with self.pool.acquire() as conn:
                logger.info(f"🔌 [DB_ADD_TASK] Connection acquired from pool")
                
                # Convert metadata dict to JSON string for JSONB storage
                metadata = task_data.get("metadata", {})
                if isinstance(metadata, dict):
                    metadata_json = json.dumps(metadata)
                    logger.info(f"📦 [DB_ADD_TASK] Metadata JSON: {metadata_json}")
                else:
                    metadata_json = metadata
                    logger.info(f"📦 [DB_ADD_TASK] Metadata already JSON: {metadata_json}")
                
                # Log all the values being inserted
                logger.info(f"📊 [DB_ADD_TASK] Insert values:")
                logger.info(f"   id: {task_id}")
                logger.info(f"   task_name: {task_data.get('task_name')}")
                logger.info(f"   topic: {task_data.get('topic')}")
                logger.info(f"   primary_keyword: {task_data.get('primary_keyword')}")
                logger.info(f"   target_audience: {task_data.get('target_audience')}")
                logger.info(f"   category: {task_data.get('category')}")
                logger.info(f"   status: {task_data.get('status')}")
                logger.info(f"   agent_id: {task_data.get('agent_id')}")
                logger.info(f"   user_id: {task_data.get('user_id')}")
                
                # Insert task with new schema (content generation fields)
                sql_query = """
                INSERT INTO tasks (
                    id, task_name, topic, primary_keyword, target_audience,
                    category, status, agent_id, task_metadata, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
                """
                logger.info(f"🔍 [DB_ADD_TASK] Executing SQL INSERT...")
                logger.info(f"🔍 [DB_ADD_TASK] SQL: {sql_query.strip()}")
                
                result = await conn.execute(
                    sql_query,
                    task_id,
                    task_data.get("task_name", "Untitled Task"),
                    task_data.get("topic", ""),
                    task_data.get("primary_keyword", ""),
                    task_data.get("target_audience", ""),
                    task_data.get("category", "general"),
                    task_data.get("status", "pending"),
                    task_data.get("agent_id", "content-agent"),
                    metadata_json,
                )
                logger.info(f"✅ [DB_ADD_TASK] INSERT executed successfully - Result: {result}")
                
                # Verify insert
                verify_row = await conn.fetchrow(
                    "SELECT id, task_name, status, created_at FROM tasks WHERE id = $1",
                    task_id
                )
                if verify_row:
                    logger.info(f"✅ [DB_ADD_TASK] Verification SUCCESS - Task found after insert!")
                    logger.info(f"   - id: {verify_row['id']}")
                    logger.info(f"   - task_name: {verify_row['task_name']}")
                    logger.info(f"   - status: {verify_row['status']}")
                    logger.info(f"   - created_at: {verify_row['created_at']}")
                else:
                    logger.error(f"❌ [DB_ADD_TASK] Verification FAILED - Task NOT found after insert!")
                
                logger.info(f"✅ [DB_ADD_TASK] Task created: {task_id}")
                return task_id
                
        except Exception as e:
            logger.error(f"❌ [DB_ADD_TASK] Exception during insert: {str(e)}", exc_info=True)
            raise

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)
            return dict(row) if row else None

    async def get_pending_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending tasks"""
        try:
            if not self.pool:
                return []
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            # Table might not exist in fresh database
            if "tasks" in str(e) or "does not exist" in str(e) or "relation" in str(e):
                return []
            # Log but don't raise - tasks are optional
            import logging
            logging.warning(f"Error fetching pending tasks: {str(e)}")
            return []

    async def get_all_tasks(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all tasks"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM tasks
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(row) for row in rows]

    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Get tasks with pagination and optional filtering.
        
        Args:
            offset: Number of records to skip
            limit: Number of records to return (max 100)
            status: Filter by status (optional)
            category: Filter by category (optional)
        
        Returns:
            Tuple of (task_list, total_count)
        """
        async with self.pool.acquire() as conn:
            # Build WHERE clause based on filters
            where_clauses = []
            params = []
            param_idx = 1
            
            if status:
                where_clauses.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1
            
            if category:
                where_clauses.append(f"category = ${param_idx}")
                params.append(category)
                param_idx += 1
            
            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            # Get total count
            count_query = f"SELECT COUNT(*) as count FROM tasks WHERE {where_clause}"
            count_row = await conn.fetchrow(count_query, *params)
            total = count_row["count"] if count_row else 0
            
            # Get paginated results
            params.extend([limit, offset])
            query = f"""
                SELECT * FROM tasks
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx}
                OFFSET ${param_idx + 1}
            """
            rows = await conn.fetch(query, *params)
            tasks = [dict(row) for row in rows]
            
            return tasks, total

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update task status"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE tasks
                SET status = $2, result = $3, updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                task_id,
                status,
                result,
            )
            return dict(row) if row else None

    # ========================================================================
    # LOGS
    # ========================================================================

    async def add_log_entry(
        self,
        agent_name: str,
        level: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add log entry"""
        log_id = str(uuid4())
        
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO logs (
                    id, agent_name, level, message, context, created_at
                )
                VALUES ($1, $2, $3, $4, $5, NOW())
                """,
                log_id,
                agent_name,
                level,
                message,
                context,  # Will be stored as JSONB
            )
        return log_id

    async def get_logs(
        self,
        agent_name: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get logs with optional filtering"""
        query = "SELECT * FROM logs WHERE 1=1"
        params = []
        
        if agent_name:
            query += f" AND agent_name = ${len(params) + 1}"
            params.append(agent_name)
        
        if level:
            query += f" AND level = ${len(params) + 1}"
            params.append(level)
        
        query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1}"
        params.append(limit)
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    # ========================================================================
    # FINANCIAL
    # ========================================================================

    async def add_financial_entry(self, entry_data: Dict[str, Any]) -> str:
        """Add financial entry"""
        entry_id = entry_data.get("id") or str(uuid4())
        
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO financial_entries (
                    id, category, amount, description, tags, created_at
                )
                VALUES ($1, $2, $3, $4, $5, NOW())
                """,
                entry_id,
                entry_data.get("category"),
                entry_data.get("amount"),
                entry_data.get("description"),
                entry_data.get("tags"),  # Will be stored as JSONB
            )
        return entry_id

    async def get_financial_summary(self, days: int = 30) -> Dict[str, Any]:
        """Get financial summary for last N days"""
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_entries,
                    SUM(amount) as total_amount,
                    AVG(amount) as avg_amount,
                    MIN(amount) as min_amount,
                    MAX(amount) as max_amount
                FROM financial_entries
                WHERE created_at >= $1
                """,
                cutoff_date,
            )
            return dict(row) if row else {}

    # ========================================================================
    # AGENT STATUS
    # ========================================================================

    async def update_agent_status(
        self,
        agent_name: str,
        status: str,
        last_run: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update or create agent status"""
        last_run = last_run or datetime.utcnow()
        
        async with self.pool.acquire() as conn:
            # Try to update first
            row = await conn.fetchrow(
                """
                UPDATE agent_status
                SET status = $2, last_run = $3, metadata = $4, updated_at = NOW()
                WHERE agent_name = $1
                RETURNING *
                """,
                agent_name,
                status,
                last_run,
                metadata,
            )
            
            # If no rows updated, insert new
            if not row:
                row = await conn.fetchrow(
                    """
                    INSERT INTO agent_status (
                        agent_name, status, last_run, metadata, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, NOW(), NOW())
                    RETURNING *
                    """,
                    agent_name,
                    status,
                    last_run,
                    metadata,
                )
            
            return dict(row)

    async def get_agent_status(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get agent status"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM agent_status WHERE agent_name = $1",
                agent_name,
            )
            return dict(row) if row else None

    # ========================================================================
    # HEALTH CHECK
    # ========================================================================

    async def health_check(self, service: str = "cofounder") -> Dict[str, Any]:
        """Check database health"""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT NOW()")
                
                return {
                    "status": "healthy",
                    "service": service,
                    "database": "postgresql",
                    "timestamp": result.isoformat() if result else None,
                }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "service": service,
                "error": str(e),
            }

    # ========================================================================
    # METRICS (from tasks and logs)
    # ========================================================================

    async def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics from database"""
        async with self.pool.acquire() as conn:
            # Get task counts
            total_tasks = await conn.fetchval("SELECT COUNT(*) FROM tasks")
            completed_tasks = await conn.fetchval(
                "SELECT COUNT(*) FROM tasks WHERE status = 'completed'"
            )
            failed_tasks = await conn.fetchval(
                "SELECT COUNT(*) FROM tasks WHERE status = 'failed'"
            )
            
            # Calculate rates
            success_rate = (
                (completed_tasks / (completed_tasks + failed_tasks) * 100)
                if (completed_tasks + failed_tasks) > 0
                else 0
            )
            
            return {
                "totalTasks": total_tasks or 0,
                "completedTasks": completed_tasks or 0,
                "failedTasks": failed_tasks or 0,
                "successRate": round(success_rate, 2),
                "avgExecutionTime": 0,  # TODO: Calculate from task data
                "totalCost": 0,  # TODO: Calculate from financial data
            }
