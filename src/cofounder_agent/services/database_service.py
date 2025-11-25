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
from datetime import datetime, timedelta, timezone
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
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1::uuid", user_id)
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
    # OAUTH - Get or Create User from OAuth Provider
    # ========================================================================

    async def get_or_create_oauth_user(
        self,
        provider: str,
        provider_user_id: str,
        provider_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Get existing OAuth user or create new one from provider data.
        
        Args:
            provider: OAuth provider name ('github', 'google', etc.)
            provider_user_id: User ID from provider
            provider_data: User data from provider {username, email, avatar_url, etc.}
        
        Returns:
            User dict with id, email, username, is_active, created_at, updated_at
        
        Logic:
        1. Check if OAuthAccount already exists (prevent duplicate linking)
        2. If OAuth account exists, return existing user
        3. If OAuth account doesn't exist but email exists, link OAuth to existing user
        4. If nothing exists, create new user and link OAuth account
        """
        import json
        
        async with self.pool.acquire() as conn:
            # Step 1: Check if OAuthAccount already linked
            oauth_row = await conn.fetchrow(
                """
                SELECT oa.user_id 
                FROM oauth_accounts oa
                WHERE oa.provider = $1 AND oa.provider_user_id = $2
                """,
                provider,
                provider_user_id,
            )
            
            if oauth_row:
                # OAuth account already linked, get existing user
                user_id = oauth_row["user_id"]
                logger.info(f"✅ OAuth account found, getting user: {user_id}")
                
                user = await conn.fetchrow(
                    "SELECT * FROM users WHERE id = $1",
                    user_id,
                )
                return dict(user) if user else None
            
            # Step 2: Check if user with same email already exists
            email = provider_data.get("email")
            existing_user = None
            
            if email:
                existing_user = await conn.fetchrow(
                    "SELECT * FROM users WHERE email = $1",
                    email,
                )
            
            if existing_user:
                # Email exists, link OAuth account to existing user
                user_id = existing_user["id"]
                logger.info(f"✅ Email found, linking OAuth to user: {user_id}")
                
                # Create OAuth account link
                provider_data_json = json.dumps(provider_data)
                await conn.execute(
                    """
                    INSERT INTO oauth_accounts (
                        id, user_id, provider, provider_user_id, provider_data, created_at, last_used
                    )
                    VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                    """,
                    str(uuid4()),
                    user_id,
                    provider,
                    provider_user_id,
                    provider_data_json,
                )
                
                return dict(existing_user)
            
            # Step 3: Create new user and OAuth account
            user_id = str(uuid4())
            logger.info(f"✅ Creating new user from OAuth: {user_id}")
            
            # Create user
            user = await conn.fetchrow(
                """
                INSERT INTO users (
                    id, email, username, is_active, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, NOW(), NOW())
                RETURNING *
                """,
                user_id,
                email,
                provider_data.get("username", email.split("@")[0] if email else "user"),
                True,  # OAuth users are active by default
            )
            
            # Create OAuth account link
            provider_data_json = json.dumps(provider_data)
            await conn.execute(
                """
                INSERT INTO oauth_accounts (
                    id, user_id, provider, provider_user_id, provider_data, created_at, last_used
                )
                VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                """,
                str(uuid4()),
                user_id,
                provider,
                provider_user_id,
                provider_data_json,
            )
            
            logger.info(f"✅ Created new OAuth user: {user_id}")
            return dict(user) if user else None

    async def get_oauth_accounts(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all OAuth accounts linked to a user"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, provider, provider_user_id, provider_data, created_at, last_used
                FROM oauth_accounts
                WHERE user_id = $1::uuid
                ORDER BY last_used DESC
                """,
                user_id,
            )
            return [dict(row) for row in rows]

    async def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        """Unlink OAuth account from user"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM oauth_accounts
                WHERE user_id = $1::uuid AND provider = $2
                """,
                user_id,
                provider,
            )
            # Result is a string like "DELETE 1"
            return "1" in result or "1" == result

    # ========================================================================
    # TASKS
    # ========================================================================

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
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        
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
        last_run = last_run or datetime.now(timezone.utc)
        
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
    # TASK MANAGEMENT (pure asyncpg, replaces SQLAlchemy-based methods)
    # ========================================================================

    async def add_task(self, task_data: Dict[str, Any]) -> str:
        """
        Add a new task to the database (pure asyncpg).
        
        Args:
            task_data: Task data dict with keys: id, task_name, topic, status, agent_id, etc.
            
        Returns:
            Task ID (UUID string)
        """
        import json
        from datetime import datetime
        
        task_id = task_data.get("id", str(__import__('uuid').uuid4()))
        
        sql = """
            INSERT INTO tasks (
                id, task_name, task_type, topic, status, agent_id,
                primary_keyword, target_audience, category,
                style, tone, target_length,
                tags, task_metadata,
                created_at, updated_at,
                approval_status
            VALUES (
                $1::uuid, $2, $3, $4, $5, $6,
                $7, $8, $9,
                $10, $11, $12,
                $13, $14,
                $15, $16,
                $17
            )
            RETURNING id
        """
        
        try:
            now = datetime.now(timezone.utc)
            
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    sql,
                    task_id,  # id
                    task_data.get("task_name"),  # task_name
                    task_data.get("task_type", "generic"),  # task_type
                    task_data.get("topic", ""),  # topic
                    task_data.get("status", "pending"),  # status
                    task_data.get("agent_id", "content-agent"),  # agent_id
                    task_data.get("primary_keyword"),  # primary_keyword
                    task_data.get("target_audience"),  # target_audience
                    task_data.get("category"),  # category
                    task_data.get("style"),  # style
                    task_data.get("tone"),  # tone
                    task_data.get("target_length"),  # target_length
                    json.dumps(task_data.get("tags", [])),  # tags (JSONB)
                    json.dumps(task_data.get("metadata", {})),  # metadata (JSONB)
                    now,  # created_at
                    now,  # updated_at
                    "pending",  # approval_status
                )
                
                logger.info(f"✅ Task added: {task_id}")
                return str(result)
        except Exception as e:
            logger.error(f"❌ Failed to add task: {e}")
            raise

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a task by ID (pure asyncpg).
        
        Args:
            task_id: UUID of the task
            
        Returns:
            Task dict or None
        """
        sql = "SELECT * FROM tasks WHERE id = $1::uuid"
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, task_id)
                if row:
                    return self._convert_row_to_dict(row)
                return None
        except Exception as e:
            logger.error(f"❌ Failed to get task {task_id}: {e}")
            raise

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update task status and optionally result (pure asyncpg).
        
        Args:
            task_id: UUID of task
            status: New status
            result: Optional result JSON
            
        Returns:
            Updated task dict or None
        """
        # from datetime import datetime
        
        now = datetime.now(timezone.utc)
        
        if result:
            sql = """
                UPDATE tasks
                SET status = $2, result = $3, updated_at = $4
                WHERE id = $1::uuid
                RETURNING *
            """
            params = [task_id, status, result, now]
        else:
            sql = """
                UPDATE tasks
                SET status = $2, updated_at = $3
                WHERE id = $1::uuid
                RETURNING *
            """
            params = [task_id, status, now]
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                if row:
                    logger.info(f"✅ Task status updated: {task_id} → {status}")
                    return self._convert_row_to_dict(row)
                return None
        except Exception as e:
            logger.error(f"❌ Failed to update task status {task_id}: {e}")
            raise

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Update arbitrary task fields (pure asyncpg).
        
        Args:
            task_id: UUID of task
            updates: Dictionary of fields to update
            
        Returns:
            Updated task dict or None
        """
        if not updates:
            return await self.get_task(task_id)
            
        set_clauses = []
        params = [task_id]
        
        for key, value in updates.items():
            set_clauses.append(f"{key} = ${len(params) + 1}")
            params.append(value)
            
        sql = f"""
            UPDATE tasks
            SET {', '.join(set_clauses)}, updated_at = NOW()
            WHERE id = $1::uuid
            RETURNING *
        """
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                return self._convert_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to update task {task_id}: {e}")
            raise

    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Get paginated tasks with optional filtering (pure asyncpg).
        
        Args:
            offset: Pagination offset
            limit: Results per page
            status: Optional status filter
            category: Optional category filter
            
        Returns:
            Tuple of (tasks list, total count)
        """
        where_clauses = []
        params = []
        
        if status:
            where_clauses.append(f"status = ${len(params) + 1}")
            params.append(status)
        
        if category:
            where_clauses.append(f"category = ${len(params) + 1}")
            params.append(category)
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        sql_count = f"SELECT COUNT(*) FROM tasks WHERE {where_sql}"
        sql_list = f"""
            SELECT * FROM tasks
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
        """
        
        try:
            async with self.pool.acquire() as conn:
                # Get count
                count_result = await conn.fetchval(sql_count, *params)
                total = count_result or 0
                
                # Get tasks
                params.extend([limit, offset])
                rows = await conn.fetch(sql_list, *params)
                
                tasks = [self._convert_row_to_dict(row) for row in rows]
                logger.info(f"✅ Listed {len(tasks)} tasks (total: {total})")
                return tasks, total
        except Exception as e:
            logger.error(f"❌ Failed to list tasks: {e}")
            raise

    async def get_task_counts(self) -> Dict[str, int]:
        """Get task counts by status"""
        sql = """
            SELECT status, COUNT(*) as count
            FROM tasks
            GROUP BY status
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql)
                counts = {row['status']: row['count'] for row in rows}
                return {
                    "total": sum(counts.values()),
                    "pending": counts.get("pending", 0),
                    "in_progress": counts.get("in_progress", 0),
                    "completed": counts.get("completed", 0),
                    "failed": counts.get("failed", 0)
                }
        except Exception as e:
            logger.error(f"❌ Failed to get task counts: {e}")
            return {"total": 0, "pending": 0, "in_progress": 0, "completed": 0, "failed": 0}

    async def get_queued_tasks(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top queued tasks"""
        sql = """
            SELECT * FROM tasks
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT $1
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, limit)
                return [self._convert_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get queued tasks: {e}")
            return []

    # ========================================================================
    # POSTS (CMS Content)
    # ========================================================================

    async def create_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new post"""
        post_id = post_data.get("id") or str(uuid4())
        
        async with self.pool.acquire() as conn:
            # Check if posts table exists, if not create it (temporary fix for dev)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id UUID PRIMARY KEY,
                    title TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    content TEXT,
                    excerpt TEXT,
                    category TEXT,
                    status TEXT DEFAULT 'draft',
                    featured_image TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            
            row = await conn.fetchrow(
                """
                INSERT INTO posts (
                    id, title, slug, content, excerpt, category, status, featured_image, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
                RETURNING *
                """,
                post_id,
                post_data.get("title"),
                post_data.get("slug"),
                post_data.get("content"),
                post_data.get("excerpt"),
                post_data.get("category"),
                post_data.get("status", "draft"),
                post_data.get("featured_image"),
            )
            return dict(row)

    @staticmethod
    def _convert_row_to_dict(row: Any) -> Dict[str, Any]:
        """Convert asyncpg Record to dict with proper type handling"""
        import json
        
        if hasattr(row, 'keys'):
            data = dict(row)
        else:
            data = row
        
        # Convert UUID to string
        if 'id' in data and data['id']:
            data['id'] = str(data['id'])
        
        # Handle JSONB fields
        for key in ['tags', 'task_metadata', 'result', 'progress']:
            if key in data:
                if isinstance(data[key], str):
                    try:
                        data[key] = json.loads(data[key])
                    except (json.JSONDecodeError, TypeError):
                        data[key] = {} if key != 'tags' else []
        
        # Convert timestamps to ISO strings
        for key in ['created_at', 'updated_at', 'started_at', 'completed_at']:
            if key in data and data[key]:
                if hasattr(data[key], 'isoformat'):
                    data[key] = data[key].isoformat()
        
        return data

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

    # ========================================================================
    # TASK OPERATIONS (Additional methods for content_router_service)
    # ========================================================================

    async def delete_task(self, task_id: str) -> bool:
        """Delete task from database"""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM tasks WHERE id = $1::uuid",
                    task_id
                )
                return result == "DELETE 1"
        except Exception as e:
            logger.error(f"❌ Error deleting task {task_id}: {e}")
            return False

    async def get_drafts(
        self, limit: int = 20, offset: int = 0
    ) -> tuple:
        """Get list of draft tasks"""
        try:
            async with self.pool.acquire() as conn:
                # Get drafts
                rows = await conn.fetch(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'draft'
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                    """,
                    limit,
                    offset
                )
                
                # Get total count
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'draft'"
                )
                
                drafts = [self._convert_row_to_dict(row) for row in rows]
                return (drafts, total or 0)
        except Exception as e:
            logger.error(f"❌ Error getting drafts: {e}")
            return ([], 0)
