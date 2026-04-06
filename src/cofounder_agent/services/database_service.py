"""
PostgreSQL Database Service Coordinator

Orchestrates access to 6 specialized database modules:
- UsersDatabase: User and OAuth operations
- TasksDatabase: Task management and filtering
- ContentDatabase: Posts, quality evaluations, metrics
- AdminDatabase: Logging, financial tracking, settings, health
- WritingStyleDatabase: Writing samples for RAG style matching
- EmbeddingsDatabase: Vector embeddings for similarity search (pgvector)

Supports dual database connections:
- Cloud pool (self.pool): ContentDatabase, UsersDatabase, AdminDatabase
- Local pool (self.local_pool): TasksDatabase, WritingStyleDatabase, EmbeddingsDatabase

When LOCAL_DATABASE_URL is not set, self.local_pool = self.pool (backward compatible).

All existing methods are delegated to appropriate modules.
"""

from services.logger_config import get_logger
import os
from typing import Dict, List, Optional

import asyncpg

from config import get_config

from .admin_db import AdminDatabase
from .audit_log import AuditLogger, init_global_audit_logger
from .content_db import ContentDatabase
from .embeddings_db import EmbeddingsDatabase
from .tasks_db import TasksDatabase
from .users_db import UsersDatabase
from .writing_style_db import WritingStyleDatabase

logger = get_logger(__name__)


class DatabaseService:
    """
    PostgreSQL database service coordinator.

    Delegates to 6 specialized modules:
    - self.users: User/OAuth operations (cloud pool)
    - self.tasks: Task management (local pool)
    - self.content: Posts/quality/metrics (cloud pool)
    - self.admin: Logging/financial/settings (cloud pool)
    - self.writing_style: Writing samples for style matching (local pool)
    - self.embeddings: Vector embeddings for similarity search (local pool)

    Supports dual database connections via LOCAL_DATABASE_URL.
    When LOCAL_DATABASE_URL is not set, local_pool = pool (backward compatible).
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        local_database_url: Optional[str] = None,
    ):
        """
        Initialize database service coordinator with asyncpg.

        Args:
            database_url: PostgreSQL connection URL (cloud/primary)
                         Required: DATABASE_URL env var or passed explicitly
            local_database_url: PostgreSQL connection URL (local brain DB)
                               Optional: LOCAL_DATABASE_URL env var or passed explicitly
                               When not set, local_pool = pool (backward compatible)
        """
        if database_url:
            self.database_url = database_url
        else:
            database_url_env = os.getenv("DATABASE_URL")
            if not database_url_env:
                raise ValueError(
                    "❌ DATABASE_URL environment variable is required. "
                    "PostgreSQL is REQUIRED for all development and production environments. "
                    "Local development must use glad_labs_dev PostgreSQL database."
                )
            self.database_url = database_url_env

        # Local database URL (optional — falls back to primary pool when unset)
        self.local_database_url = local_database_url or os.getenv("LOCAL_DATABASE_URL") or None

        logger.info(f"DatabaseService initialized with PostgreSQL: {self.database_url[:50]}...")
        if self.local_database_url:
            logger.info(
                f"DatabaseService local pool configured: {self.local_database_url[:50]}..."
            )

        self.pool = None
        self.local_pool = None

        # Delegate modules will be initialized after pool is created
        self.users: Optional[UsersDatabase] = None
        self.tasks: Optional[TasksDatabase] = None
        self.content: Optional[ContentDatabase] = None
        self.admin: Optional[AdminDatabase] = None
        self.writing_style: Optional[WritingStyleDatabase] = None
        self.embeddings: Optional[EmbeddingsDatabase] = None
        self.audit: Optional[AuditLogger] = None

    async def initialize(self) -> None:
        """Initialize connection pool(s) and all delegate modules."""
        try:
            # PostgreSQL requires connection pooling
            _config = get_config()
            is_dev = _config.environment.lower() in (
                "development",
                "dev",
                "local",
            )
            min_size = int(os.getenv("DATABASE_POOL_MIN_SIZE", "5" if is_dev else "20"))
            max_size = int(os.getenv("DATABASE_POOL_MAX_SIZE", "20" if is_dev else "50"))

            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=min_size,
                max_size=max_size,
                timeout=30,
                command_timeout=30,  # Query execution timeout
            )
            logger.info(
                f"✅ Database pool initialized (size: {min_size}-{max_size}, query timeout: 30s)"
            )

            # Create local pool if LOCAL_DATABASE_URL is configured, otherwise reuse primary
            if self.local_database_url:
                local_min = int(os.getenv("LOCAL_DATABASE_POOL_MIN_SIZE", "3" if is_dev else "5"))
                local_max = int(os.getenv("LOCAL_DATABASE_POOL_MAX_SIZE", "10" if is_dev else "20"))
                self.local_pool = await asyncpg.create_pool(
                    self.local_database_url,
                    min_size=local_min,
                    max_size=local_max,
                    timeout=30,
                    command_timeout=30,
                )
                logger.info(
                    f"✅ Local database pool initialized (size: {local_min}-{local_max})"
                )

                # Worker mode: flip pools so .pool = local (internal ops default)
                # and .cloud_pool = production DB (public content only)
                deployment_mode = os.getenv("DEPLOYMENT_MODE", "coordinator")
                if deployment_mode == "worker":
                    self.cloud_pool = self.pool  # Cloud DB for publishing
                    self.pool = self.local_pool  # Local for everything else
                    logger.info("🔄 Worker mode: pool=local, cloud_pool=cloud")
                else:
                    self.cloud_pool = self.pool  # Coordinator: both point to cloud DB
            else:
                self.local_pool = self.pool
                self.cloud_pool = self.pool
                logger.info("ℹ️  No LOCAL_DATABASE_URL — local_pool = pool (single-pool mode)")

            # Initialize delegate modules routed to appropriate pools
            # Cloud pool: users, content, admin (production DB for public-facing data)
            self.users = UsersDatabase(self.cloud_pool)
            self.content = ContentDatabase(self.cloud_pool)
            self.admin = AdminDatabase(self.cloud_pool)

            # Local pool: tasks, writing_style, embeddings, audit
            self.tasks = TasksDatabase(self.local_pool)
            self.writing_style = WritingStyleDatabase(self.local_pool)
            self.embeddings = EmbeddingsDatabase(self.local_pool)
            self.audit = init_global_audit_logger(self.local_pool)

            logger.info(
                "✅ All database modules initialized "
                "(users, tasks, content, admin, writing_style, embeddings, audit)"
            )
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}", exc_info=True)
            raise

    async def close(self) -> None:
        """Close all connection pools."""
        if self.local_pool and self.local_pool is not self.pool:
            await self.local_pool.close()
            logger.info("Local database pool closed")
        if self.pool:
            await self.pool.close()
            logger.info("Database pool closed")

    # ========================================================================
    # BACKWARD COMPATIBILITY: Delegation Methods
    # ========================================================================
    # These methods maintain 100% backward compatibility with the original
    # DatabaseService API. Each method delegates to the appropriate module.

    # USER OPERATIONS
    async def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """Delegate to users module."""
        return await self.users.get_user_by_id(user_id)

    async def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Delegate to users module."""
        return await self.users.get_user_by_email(email)

    async def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Delegate to users module."""
        return await self.users.get_user_by_username(username)

    async def create_user(self, user_data: dict) -> Dict:
        """Delegate to users module."""
        return await self.users.create_user(user_data)

    async def get_or_create_oauth_user(
        self, provider: str, provider_user_id: str, provider_data: dict
    ) -> Dict:
        """Delegate to users module."""
        return await self.users.get_or_create_oauth_user(provider, provider_user_id, provider_data)

    async def get_oauth_accounts(self, user_id: str) -> List[Dict]:
        """Delegate to users module."""
        return await self.users.get_oauth_accounts(user_id)

    async def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        """Delegate to users module."""
        return await self.users.unlink_oauth_account(user_id, provider)

    # TASK OPERATIONS
    async def add_task(self, task_data: dict) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.add_task(task_data)

    async def get_task(self, task_id: str) -> Optional[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_task(task_id)

    async def update_task_status(
        self, task_id: str, status: str, result: Optional[str] = None
    ) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.update_task_status(task_id, status, result)

    async def get_tasks_by_ids(self, task_ids: list) -> dict:
        """Delegate bulk task fetch to tasks module (1 query for all IDs)."""
        return await self.tasks.get_tasks_by_ids(task_ids)

    async def bulk_update_task_statuses(self, task_ids: list, new_status: str) -> dict:
        """Delegate bulk status update to tasks module (2 queries regardless of batch size)."""
        return await self.tasks.bulk_update_task_statuses(task_ids, new_status)

    async def update_task(self, task_id: str, updates: dict) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.update_task(task_id, updates)

    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_paginated(offset, limit, status, category, search)

    async def get_task_counts(self) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.get_task_counts()

    async def get_pending_tasks(self, limit: int = 10) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_pending_tasks(limit)

    async def get_all_tasks(self, limit: int = 100) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_all_tasks(limit)

    async def get_queued_tasks(self, limit: int = 5) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_queued_tasks(limit)

    async def get_tasks_by_date_range(
        self, start_date=None, end_date=None, status: Optional[str] = None, limit: int = 500
    ) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_by_date_range(start_date, end_date, status, limit)

    async def get_kpi_aggregates(self, start_date=None, end_date=None) -> Dict:
        """Delegate to tasks module — single-query KPI aggregation (issue #696)."""
        return await self.tasks.get_kpi_aggregates(start_date, end_date)

    async def delete_task(self, task_id: str) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.delete_task(task_id)

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_drafts(limit, offset)

    async def sweep_stale_tasks(
        self, timeout_minutes: int = 60, max_retries: int = 3
    ) -> Dict:
        """Delegate to tasks module — reset stuck in_progress tasks."""
        return await self.tasks.sweep_stale_tasks(
            stale_threshold_minutes=timeout_minutes, max_retries=max_retries
        )

    # CONTENT OPERATIONS
    async def create_post(self, post_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_post(post_data)

    async def get_post_by_slug(self, slug: str) -> Optional[Dict]:
        """Delegate to content module."""
        return await self.content.get_post_by_slug(slug)

    async def update_post(self, post_id: int, updates: dict) -> bool:
        """Delegate to content module."""
        return await self.content.update_post(post_id, updates)

    async def get_all_categories(self) -> List[Dict]:
        """Delegate to content module."""
        return await self.content.get_all_categories()

    async def get_all_tags(self) -> List[Dict]:
        """Delegate to content module."""
        return await self.content.get_all_tags()

    async def get_author_by_name(self, name: str) -> Optional[Dict]:
        """Delegate to content module."""
        return await self.content.get_author_by_name(name)

    async def create_quality_evaluation(self, eval_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_quality_evaluation(eval_data)

    async def create_quality_improvement_log(self, log_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_quality_improvement_log(log_data)

    async def get_metrics(self) -> Dict:
        """Delegate to content module."""
        return await self.content.get_metrics()

    async def create_orchestrator_training_data(self, train_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_orchestrator_training_data(train_data)

    # ADMIN OPERATIONS
    async def add_log_entry(
        self, agent_name: str, level: str, message: str, context: Optional[dict] = None
    ) -> Dict:
        """Delegate to admin module."""
        return await self.admin.add_log_entry(agent_name, level, message, context)

    async def get_logs(
        self, agent_name: Optional[str] = None, level: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_logs(agent_name, level, limit)

    async def add_financial_entry(self, entry_data: dict) -> Dict:
        """Delegate to admin module."""
        return await self.admin.add_financial_entry(entry_data)

    async def get_financial_summary(self, days: int = 30) -> Dict:
        """Delegate to admin module."""
        return await self.admin.get_financial_summary(days)

    async def log_cost(self, cost_log: dict) -> Dict:
        """Delegate to admin module."""
        return await self.admin.log_cost(cost_log)

    async def get_task_costs(self, task_id: str) -> Dict:
        """Delegate to admin module."""
        return await self.admin.get_task_costs(task_id)

    async def update_agent_status(
        self, agent_name: str, status: str, last_run=None, metadata: Optional[dict] = None
    ) -> bool:
        """Delegate to admin module."""
        return await self.admin.update_agent_status(agent_name, status, last_run, metadata)

    async def get_agent_status(self, agent_name: str) -> Optional[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_agent_status(agent_name)

    async def health_check(self, service: str = "cofounder") -> Dict:
        """Delegate to admin module."""
        return await self.admin.health_check(service)

    async def get_setting(self, key: str) -> Optional[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_setting(key)

    async def get_all_settings(self, category: Optional[str] = None) -> List[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_all_settings(category)

    async def set_setting(
        self,
        key: str,
        value,
        category: Optional[str] = None,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict:
        """Delegate to admin module."""
        return await self.admin.set_setting(key, value, category, display_name, description)

    async def delete_setting(self, key: str) -> bool:
        """Delegate to admin module."""
        return await self.admin.delete_setting(key)

    async def get_setting_value(self, key: str, default=None) -> any:
        """Delegate to admin module."""
        return await self.admin.get_setting_value(key, default)

    async def setting_exists(self, key: str) -> bool:
        """Delegate to admin module."""
        return await self.admin.setting_exists(key)

    # EMBEDDING OPERATIONS
    async def store_embedding(
        self,
        source_type: str,
        source_id: str,
        content_hash: str,
        embedding: list,
        metadata: Optional[Dict] = None,
    ) -> str:
        """Delegate to embeddings module."""
        return await self.embeddings.store_embedding(
            source_type, source_id, content_hash, embedding, metadata
        )

    async def search_similar(
        self,
        embedding: list,
        limit: int = 10,
        source_type: Optional[str] = None,
        min_similarity: float = 0.0,
    ) -> List[Dict]:
        """Delegate to embeddings module."""
        return await self.embeddings.search_similar(embedding, limit, source_type, min_similarity)

    async def get_embedding(self, source_type: str, source_id: str) -> Optional[Dict]:
        """Delegate to embeddings module."""
        return await self.embeddings.get_embedding(source_type, source_id)

    async def delete_embeddings(self, source_type: str, source_id: Optional[str] = None) -> int:
        """Delegate to embeddings module."""
        return await self.embeddings.delete_embeddings(source_type, source_id)

    async def needs_reembedding(
        self, source_type: str, source_id: str, content_hash: str
    ) -> bool:
        """Delegate to embeddings module."""
        return await self.embeddings.needs_reembedding(source_type, source_id, content_hash)
