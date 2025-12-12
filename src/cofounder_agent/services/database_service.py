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
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


def serialize_value_for_postgres(value: Any) -> Any:
    """
    Serialize Python values for PostgreSQL asyncpg.
    
    Handles:
    - dict/list → JSON string (for JSONB columns)
    - datetime → as-is (asyncpg handles this, don't convert to string!)
    - UUID → string
    - Other types → as-is
    
    Args:
        value: Python value to serialize
        
    Returns:
        PostgreSQL-compatible value
    """
    if isinstance(value, dict):
        # JSONB fields need JSON strings, not dicts
        return json.dumps(value)
    elif isinstance(value, list):
        # Arrays of objects need JSON strings
        return json.dumps(value)
    elif isinstance(value, datetime):
        # IMPORTANT: asyncpg handles datetime objects directly
        # Do NOT convert to string - asyncpg will encode it properly
        return value
    elif isinstance(value, UUID):
        return str(value)
    else:
        return value


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
            return self._convert_row_to_dict(row) if row else None

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
            return self._convert_row_to_dict(row) if row else None

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE username = $1", username)
            return self._convert_row_to_dict(row) if row else None

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
            return self._convert_row_to_dict(row)

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
    # TASKS - CONSOLIDATED (uses content_tasks as single source of truth)
    # ========================================================================

    async def get_pending_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending tasks from content_tasks"""
        try:
            if not self.pool:
                return []
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM content_tasks
                    WHERE status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [self._convert_row_to_dict(row) for row in rows]
        except Exception as e:
            # Table might not exist in fresh database
            if "content_tasks" in str(e) or "does not exist" in str(e) or "relation" in str(e):
                return []
            logger.warning(f"Error fetching pending tasks: {str(e)}")
            return []

    async def get_all_tasks(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all tasks from content_tasks"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM content_tasks
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [self._convert_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching all tasks: {e}")
            return []



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
                json.dumps(context or {}),  # Serialize context for JSONB column
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
                json.dumps(entry_data.get("tags", [])),  # Serialize tags for JSONB column
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
            return self._convert_row_to_dict(row) if row else {}

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
                json.dumps(metadata or {}),  # Serialize metadata for JSONB column
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
                    json.dumps(metadata or {}),  # Serialize metadata for JSONB column
                )
            
            return self._convert_row_to_dict(row)

    async def get_agent_status(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get agent status"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM agent_status WHERE agent_name = $1",
                agent_name,
            )
            return self._convert_row_to_dict(row) if row else None

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
    # TASK MANAGEMENT (CONSOLIDATED: content_tasks is single source of truth)
    # ========================================================================

    async def add_task(self, task_data: Dict[str, Any]) -> str:
        """
        Add a new task to the database using content_tasks table.
        
        Consolidates both manual and poindexter task creation pipelines into one table.
        
        Args:
            task_data: Task data dict with: task_name, topic, task_type, status, agent_id, style, tone, etc.
            
        Returns:
            Task ID (string)
        """
        task_id = task_data.get("id", task_data.get("task_id", str(uuid4())))
        if isinstance(task_id, UUID):
            task_id = str(task_id)
        
        # Extract metadata for normalization
        metadata = task_data.get("task_metadata") or task_data.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        
        try:
            # Use naive UTC datetime for PostgreSQL 'timestamp without time zone' columns
            now = datetime.utcnow()
            
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    """
                    INSERT INTO content_tasks (
                        task_id, id, request_type, task_type, status, topic,
                        style, tone, target_length, agent_id,
                        primary_keyword, target_audience, category,
                        content, excerpt, featured_image_url, featured_image_data,
                        featured_image_prompt, qa_feedback, quality_score,
                        seo_title, seo_description, seo_keywords,
                        stage, percentage, message,
                        tags, task_metadata, model_used, error_message,
                        approval_status, publish_mode,
                        created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10,
                        $11, $12, $13,
                        $14, $15, $16, $17,
                        $18, $19, $20,
                        $21, $22, $23,
                        $24, $25, $26,
                        $27, $28, $29, $30,
                        $31, $32,
                        $33, $34
                    )
                    RETURNING task_id
                    """,
                    task_id,
                    task_id,  # id as UUID reference
                    task_data.get("request_type", "content_generation"),
                    task_data.get("task_type", "blog_post"),
                    task_data.get("status", "pending"),
                    task_data.get("topic", ""),
                    task_data.get("style", "technical"),
                    task_data.get("tone", "professional"),
                    task_data.get("target_length", 1500),
                    task_data.get("agent_id", "content-agent"),
                    task_data.get("primary_keyword"),
                    task_data.get("target_audience"),
                    task_data.get("category"),
                    metadata.get("content") or task_data.get("content"),
                    metadata.get("excerpt") or task_data.get("excerpt"),
                    metadata.get("featured_image_url") or task_data.get("featured_image_url"),
                    json.dumps(metadata.get("featured_image_data") or task_data.get("featured_image_data")) if (metadata.get("featured_image_data") or task_data.get("featured_image_data")) else None,
                    task_data.get("featured_image_prompt"),
                    metadata.get("qa_feedback"),
                    metadata.get("quality_score") or task_data.get("quality_score"),
                    metadata.get("seo_title"),
                    metadata.get("seo_description"),
                    metadata.get("seo_keywords"),
                    metadata.get("stage", "pending"),
                    metadata.get("percentage", 0),
                    metadata.get("message"),
                    json.dumps(task_data.get("tags", [])),
                    json.dumps(metadata or {}),
                    task_data.get("model_used"),
                    task_data.get("error_message"),
                    task_data.get("approval_status", "pending"),
                    task_data.get("publish_mode", "draft"),
                    now,
                    now,
                )
                logger.info(f"✅ Task added to content_tasks: {task_id}")
                return str(result)
        except Exception as e:
            logger.error(f"❌ Failed to add task: {e}")
            raise

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task from content_tasks by ID"""
        sql = "SELECT * FROM content_tasks WHERE task_id = $1"
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, str(task_id))
                if row:
                    return self._convert_row_to_dict(row)
                return None
        except Exception as e:
            logger.error(f"❌ Failed to get task {task_id}: {e}")
            return None

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update task status in content_tasks"""
        now = datetime.utcnow()
        
        if result:
            sql = """
                UPDATE content_tasks
                SET status = $2, result = $3::jsonb, updated_at = $4
                WHERE task_id = $1
                RETURNING *
            """
            params = [str(task_id), status, result, now]
        else:
            sql = """
                UPDATE content_tasks
                SET status = $2, updated_at = $3
                WHERE task_id = $1
                RETURNING *
            """
            params = [str(task_id), status, now]
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                if row:
                    logger.info(f"✅ Task status updated: {task_id} → {status}")
                    return self._convert_row_to_dict(row)
                return None
        except Exception as e:
            logger.error(f"❌ Failed to update task status {task_id}: {e}")
            return None

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Update task fields in content_tasks.
        
        Extracts and normalizes fields from task_metadata into dedicated columns.
        """
        if not updates:
            return await self.get_task(task_id)
        
        # Extract task_metadata for normalization
        task_metadata = updates.get("task_metadata", {})
        if isinstance(task_metadata, str):
            task_metadata = json.loads(task_metadata)
        elif task_metadata is None:
            task_metadata = {}
        
        # Prepare normalized updates
        normalized_updates = dict(updates)
        
        # Extract specific fields to dedicated columns
        if task_metadata:
            if "content" not in normalized_updates and "content" in task_metadata:
                normalized_updates["content"] = task_metadata.get("content")
            if "excerpt" not in normalized_updates and "excerpt" in task_metadata:
                normalized_updates["excerpt"] = task_metadata.get("excerpt")
            if "featured_image_url" not in normalized_updates and "featured_image_url" in task_metadata:
                normalized_updates["featured_image_url"] = task_metadata.get("featured_image_url")
            if "featured_image_data" not in normalized_updates and "featured_image_data" in task_metadata:
                normalized_updates["featured_image_data"] = task_metadata.get("featured_image_data")
            if "qa_feedback" not in normalized_updates and "qa_feedback" in task_metadata:
                qa_fb = task_metadata.get("qa_feedback")
                if isinstance(qa_fb, list):
                    qa_fb = json.dumps(qa_fb) if qa_fb else None
                normalized_updates["qa_feedback"] = qa_fb
            if "quality_score" not in normalized_updates and "quality_score" in task_metadata:
                normalized_updates["quality_score"] = task_metadata.get("quality_score")
            if "seo_title" not in normalized_updates and "seo_title" in task_metadata:
                normalized_updates["seo_title"] = task_metadata.get("seo_title")
            if "seo_description" not in normalized_updates and "seo_description" in task_metadata:
                normalized_updates["seo_description"] = task_metadata.get("seo_description")
            if "seo_keywords" not in normalized_updates and "seo_keywords" in task_metadata:
                normalized_updates["seo_keywords"] = task_metadata.get("seo_keywords")
            if "stage" not in normalized_updates and "stage" in task_metadata:
                normalized_updates["stage"] = task_metadata.get("stage")
            if "percentage" not in normalized_updates and "percentage" in task_metadata:
                normalized_updates["percentage"] = task_metadata.get("percentage")
            if "message" not in normalized_updates and "message" in task_metadata:
                normalized_updates["message"] = task_metadata.get("message")
        
        set_clauses = []
        params = [str(task_id)]
        
        for key, value in normalized_updates.items():
            serialized_value = serialize_value_for_postgres(value)
            set_clauses.append(f"{key} = ${len(params) + 1}")
            params.append(serialized_value)
        
        sql = f"""
            UPDATE content_tasks
            SET {', '.join(set_clauses)}, updated_at = NOW()
            WHERE task_id = $1
            RETURNING *
        """
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                return self._convert_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to update task {task_id}: {e}")
            return None

    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Get paginated tasks from content_tasks with optional filtering"""
        where_clauses = []
        params = []
        
        if status:
            where_clauses.append(f"status = ${len(params) + 1}")
            params.append(status)
        
        if category:
            where_clauses.append(f"category = ${len(params) + 1}")
            params.append(category)
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        sql_count = f"SELECT COUNT(*) FROM content_tasks WHERE {where_sql}"
        sql_list = f"""
            SELECT * FROM content_tasks
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
        """
        
        try:
            async with self.pool.acquire() as conn:
                count_result = await conn.fetchval(sql_count, *params)
                total = count_result or 0
                
                params.extend([limit, offset])
                rows = await conn.fetch(sql_list, *params)
                
                tasks = [self._convert_row_to_dict(row) for row in rows]
                logger.info(f"✅ Listed {len(tasks)} tasks (total: {total})")
                return tasks, total
        except Exception as e:
            logger.error(f"❌ Failed to list tasks: {e}")
            return [], 0

    async def get_task_counts(self) -> Dict[str, int]:
        """Get task counts by status from content_tasks"""
        sql = """
            SELECT status, COUNT(*) as count
            FROM content_tasks
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
                    "failed": counts.get("failed", 0),
                    "awaiting_approval": counts.get("awaiting_approval", 0),
                    "approved": counts.get("approved", 0),
                }
        except Exception as e:
            logger.error(f"❌ Failed to get task counts: {e}")
            return {
                "total": 0, "pending": 0, "in_progress": 0, "completed": 0, 
                "failed": 0, "awaiting_approval": 0, "approved": 0
            }

    async def get_queued_tasks(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top queued/pending tasks from content_tasks"""
        sql = """
            SELECT * FROM content_tasks
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT $1
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, limit)
                return [self._convert_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get queued tasks: {e}")
            return []

    async def delete_task(self, task_id: str) -> bool:
        """Delete task from content_tasks"""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM content_tasks WHERE task_id = $1",
                    str(task_id)
                )
                deleted = "DELETE 1" in result or result == "DELETE 1"
                if deleted:
                    logger.info(f"✅ Task deleted: {task_id}")
                return deleted
        except Exception as e:
            logger.error(f"❌ Error deleting task {task_id}: {e}")
            return False

    async def get_drafts(
        self, limit: int = 20, offset: int = 0
    ) -> tuple:
        """Get draft tasks from content_tasks"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM content_tasks
                    WHERE status = 'pending' OR approval_status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                    """,
                    limit,
                    offset
                )
                
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM content_tasks WHERE status = 'pending' OR approval_status = 'pending'"
                )
                
                drafts = [self._convert_row_to_dict(row) for row in rows]
                return (drafts, total or 0)
        except Exception as e:
            logger.error(f"❌ Error getting drafts: {e}")
            return ([], 0)

    # ========================================================================
    # POSTS (CMS Content)
    # ========================================================================

    async def create_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new post in posts table"""
        post_id = post_data.get("id") or str(uuid4())
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO posts (
                    id, 
                    title, 
                    slug, 
                    content, 
                    excerpt, 
                    featured_image_url,
                    status, 
                    seo_title,
                    seo_description,
                    seo_keywords,
                    created_at, 
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
                RETURNING id, title, slug, content, excerpt, status, created_at, updated_at
                """,
                post_id,
                post_data.get("title"),
                post_data.get("slug"),
                post_data.get("content"),
                post_data.get("excerpt"),
                post_data.get("featured_image"),
                post_data.get("status", "draft"),
                post_data.get("seo_title") or post_data.get("title"),  # Default to title if not provided
                post_data.get("seo_description") or post_data.get("excerpt"),  # Default to excerpt if not provided
                post_data.get("seo_keywords", ""),
            )
            return self._convert_row_to_dict(row)

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
    # METRICS (from content_tasks)
    # ========================================================================

    async def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics from content_tasks database"""
        try:
            async with self.pool.acquire() as conn:
                # Get task counts from content_tasks
                total_tasks = await conn.fetchval("SELECT COUNT(*) FROM content_tasks")
                completed_tasks = await conn.fetchval(
                    "SELECT COUNT(*) FROM content_tasks WHERE status = 'completed'"
                )
                failed_tasks = await conn.fetchval(
                    "SELECT COUNT(*) FROM content_tasks WHERE status = 'failed'"
                )
                
                # Calculate rates
                success_rate = (
                    (completed_tasks / (completed_tasks + failed_tasks) * 100)
                    if (completed_tasks + failed_tasks) > 0
                    else 0
                )
                
                # Calculate average execution time from completed tasks
                avg_execution_time = 0
                try:
                    time_query = """
                        SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) as avg_seconds
                        FROM content_tasks
                        WHERE status = 'completed' AND updated_at IS NOT NULL
                    """
                    time_result = await conn.fetchrow(time_query)
                    if time_result and time_result['avg_seconds']:
                        avg_execution_time = round(float(time_result['avg_seconds']), 2)
                except Exception as e:
                    logger.warning(f"Could not calculate avg execution time: {e}")
                
                # Calculate total cost from financial tracking (if implemented)
                total_cost = 0
                try:
                    cost_query = """
                        SELECT SUM(cost_usd) as total
                        FROM task_costs
                        WHERE created_at >= NOW() - INTERVAL '30 days'
                    """
                    cost_result = await conn.fetchrow(cost_query)
                    if cost_result and cost_result['total']:
                        total_cost = round(float(cost_result['total']), 2)
                except Exception:
                    logger.debug("Cost tracking not available (task_costs table may not exist)")
                
                return {
                    "totalTasks": total_tasks or 0,
                    "completedTasks": completed_tasks or 0,
                    "failedTasks": failed_tasks or 0,
                    "successRate": round(success_rate, 2),
                    "avgExecutionTime": avg_execution_time,
                    "totalCost": total_cost,
                }
        except Exception as e:
            logger.error(f"❌ Failed to get metrics: {e}")
            return {
                "totalTasks": 0, "completedTasks": 0, "failedTasks": 0,
                "successRate": 0, "avgExecutionTime": 0, "totalCost": 0
            }

    # ========================================================================
    # QUALITY_EVALUATIONS TABLE METHODS
    # ========================================================================

    async def create_quality_evaluation(self, eval_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create quality evaluation record
        
        Args:
            eval_data: Dict with content_id, task_id, overall_score, criteria scores, etc.
            
        Returns:
            Created quality_evaluation record
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO quality_evaluations (
                        content_id, task_id, overall_score, clarity, accuracy, 
                        completeness, relevance, seo_quality, readability, engagement,
                        passing, feedback, suggestions, evaluated_by, evaluation_method,
                        evaluation_timestamp
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, NOW())
                    RETURNING *
                """,
                    eval_data['content_id'],
                    eval_data.get('task_id'),
                    eval_data['overall_score'],
                    eval_data.get('criteria', {}).get('clarity', 0),
                    eval_data.get('criteria', {}).get('accuracy', 0),
                    eval_data.get('criteria', {}).get('completeness', 0),
                    eval_data.get('criteria', {}).get('relevance', 0),
                    eval_data.get('criteria', {}).get('seo_quality', 0),
                    eval_data.get('criteria', {}).get('readability', 0),
                    eval_data.get('criteria', {}).get('engagement', 0),
                    eval_data['overall_score'] >= 7.0,
                    eval_data.get('feedback'),
                    json.dumps(eval_data.get('suggestions', [])),
                    eval_data.get('evaluated_by', 'QualityEvaluator'),
                    eval_data.get('evaluation_method', 'pattern-based')
                )
                logger.info(f"✅ Created quality_evaluation for {eval_data['content_id']}")
                return self._convert_row_to_dict(row)
        except Exception as e:
            logger.error(f"❌ Error creating quality_evaluation: {e}")
            raise

    # ========================================================================
    # QUALITY_IMPROVEMENT_LOGS TABLE METHODS
    # ========================================================================

    async def create_quality_improvement_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Log content quality improvement through refinement
        
        Args:
            log_data: Dict with content_id, initial_score, improved_score, refinement_type, etc.
            
        Returns:
            Created quality_improvement_log record
        """
        try:
            initial = log_data['initial_score']
            improved = log_data['improved_score']
            
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO quality_improvement_logs (
                        content_id, initial_score, improved_score, score_improvement,
                        refinement_type, changes_made, refinement_timestamp, passed_after_refinement
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7)
                    RETURNING *
                """,
                    log_data['content_id'],
                    initial,
                    improved,
                    improved - initial,
                    log_data.get('refinement_type', 'auto-critique'),
                    log_data.get('changes_made'),
                    improved >= 7.0
                )
                logger.info(f"✅ Created quality_improvement_log: {initial:.1f} → {improved:.1f}")
                return self._convert_row_to_dict(row)
        except Exception as e:
            logger.error(f"❌ Error creating quality_improvement_log: {e}")
            raise

    # ========================================================================
    # ORCHESTRATOR_TRAINING_DATA TABLE METHODS
    # ========================================================================

    async def create_orchestrator_training_data(self, train_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Capture execution for training/learning pipeline
        
        Args:
            train_data: Dict with execution_id, user_request, intent, execution_result, quality_score, success, tags, etc.
            
        Returns:
            Created training_data record
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO orchestrator_training_data (
                        execution_id, user_request, intent, business_state, execution_result,
                        quality_score, success, tags, created_at, source_agent
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), $9)
                    RETURNING *
                """,
                    train_data['execution_id'],
                    train_data.get('user_request'),
                    train_data.get('intent'),
                    json.dumps(train_data.get('business_state', {})),
                    train_data.get('execution_result'),
                    train_data.get('quality_score'),
                    train_data.get('success', False),
                    json.dumps(train_data.get('tags', [])),
                    train_data.get('source_agent', 'content_agent')
                )
                logger.info(f"✅ Created orchestrator_training_data: {train_data['execution_id']}")
                return self._convert_row_to_dict(row)
        except Exception as e:
            logger.error(f"❌ Error creating orchestrator_training_data: {e}")
            raise
