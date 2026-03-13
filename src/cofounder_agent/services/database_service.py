"""
PostgreSQL Database Service Coordinator

Orchestrates access to 5 specialized database modules:
- UsersDatabase: User and OAuth operations
- TasksDatabase: Task management and filtering
- ContentDatabase: Posts, quality evaluations, metrics
- AdminDatabase: Logging, financial tracking, settings, health
- WritingStyleDatabase: Writing samples for RAG style matching

This maintains 100% backward compatibility while providing cleaner
architecture and domain-specific separation of concerns.

All existing methods are delegated to appropriate modules.
Connection pool is shared across all modules.
"""

from services.logger_config import get_logger
import os
from typing import Dict, List, Optional

import asyncpg

from .admin_db import AdminDatabase
from .content_db import ContentDatabase
from .tasks_db import TasksDatabase
from .users_db import UsersDatabase
from .writing_style_db import WritingStyleDatabase

logger = get_logger(__name__)
class DatabaseService:
    """
    PostgreSQL database service coordinator.

    Delegates to 5 specialized modules:
    - self.users: User/OAuth operations
    - self.tasks: Task management
    - self.content: Posts/quality/metrics
    - self.admin: Logging/financial/settings
    - self.writing_style: Writing samples for style matching

    Maintains 100% backward compatibility with original DatabaseService.
    All existing method calls still work via delegation.
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize database service coordinator with asyncpg.

        Args:
            database_url: PostgreSQL connection URL
                         Required: DATABASE_URL env var or passed explicitly
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

        logger.info(f"DatabaseService initialized with PostgreSQL: {self.database_url[:50]}...")

        self.pool = None

        # Delegate modules will be initialized after pool is created
        self.users: Optional[UsersDatabase] = None
        self.tasks: Optional[TasksDatabase] = None
        self.content: Optional[ContentDatabase] = None
        self.admin: Optional[AdminDatabase] = None
        self.writing_style: Optional[WritingStyleDatabase] = None

    async def initialize(self) -> None:
        """Initialize connection pool and all delegate modules."""
        try:
            # PostgreSQL requires connection pooling
            min_size = int(os.getenv("DATABASE_POOL_MIN_SIZE", "20"))
            max_size = int(os.getenv("DATABASE_POOL_MAX_SIZE", "50"))

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

            # Initialize all delegate modules
            self.users = UsersDatabase(self.pool)
            self.tasks = TasksDatabase(self.pool)
            self.content = ContentDatabase(self.pool)
            self.admin = AdminDatabase(self.pool)
            self.writing_style = WritingStyleDatabase(self.pool)

            logger.info(
                "✅ All database modules initialized (users, tasks, content, admin, writing_style)"
            )
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}", exc_info=True)
            raise

    async def close(self) -> None:
        """Close connection pool."""
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
        return await self.users.get_user_by_id(user_id)  # type: ignore[union-attr, return-value]

    async def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Delegate to users module."""
        return await self.users.get_user_by_email(email)  # type: ignore[union-attr, return-value]

    async def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Delegate to users module."""
        return await self.users.get_user_by_username(username)  # type: ignore[union-attr, return-value]

    async def create_user(self, user_data: dict) -> Dict:
        """Delegate to users module."""
        return await self.users.create_user(user_data)  # type: ignore[union-attr, return-value]

    async def get_or_create_oauth_user(
        self, provider: str, provider_user_id: str, provider_data: dict
    ) -> Dict:
        """Delegate to users module."""
        return await self.users.get_or_create_oauth_user(provider, provider_user_id, provider_data)  # type: ignore[union-attr, return-value]

    async def get_oauth_accounts(self, user_id: str) -> List[Dict]:
        """Delegate to users module."""
        return await self.users.get_oauth_accounts(user_id)  # type: ignore[union-attr, return-value]

    async def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        """Delegate to users module."""
        return await self.users.unlink_oauth_account(user_id, provider)  # type: ignore[union-attr, return-value]

    # TASK OPERATIONS
    async def add_task(self, task_data: dict) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.add_task(task_data)  # type: ignore[union-attr, return-value]

    async def get_task(self, task_id: str) -> Optional[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_task(task_id)  # type: ignore[union-attr, return-value]

    async def update_task_status(
        self, task_id: str, status: str, result: Optional[str] = None
    ) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.update_task_status(task_id, status, result)  # type: ignore[union-attr, return-value]

    async def update_task(self, task_id: str, updates: dict) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.update_task(task_id, updates)  # type: ignore[union-attr, return-value]

    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_paginated(offset, limit, status, category, search)  # type: ignore[union-attr, return-value]

    async def get_task_counts(self) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.get_task_counts()  # type: ignore[union-attr, return-value]

    async def get_pending_tasks(self, limit: int = 10) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_pending_tasks(limit)  # type: ignore[union-attr, return-value]

    async def get_all_tasks(self, limit: int = 100) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_all_tasks(limit)  # type: ignore[union-attr, return-value]

    async def get_queued_tasks(self, limit: int = 5) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_queued_tasks(limit)  # type: ignore[union-attr, return-value]

    async def get_tasks_by_date_range(
        self, start_date=None, end_date=None, status: Optional[str] = None, limit: int = 500
    ) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_by_date_range(start_date, end_date, status, limit)  # type: ignore[union-attr, return-value]

    async def delete_task(self, task_id: str) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.delete_task(task_id)  # type: ignore[union-attr, return-value]

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> List[Dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_drafts(limit, offset)  # type: ignore[union-attr, return-value]

    async def sweep_stale_tasks(
        self, stale_threshold_minutes: int = 60, max_retries: int = 3
    ) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.sweep_stale_tasks(stale_threshold_minutes, max_retries)  # type: ignore[union-attr, return-value]

    async def bulk_update_task_statuses(
        self, task_ids: List[str], new_status: str
    ) -> Dict:
        """Delegate to tasks module."""
        return await self.tasks.bulk_update_task_statuses(task_ids, new_status)  # type: ignore[union-attr, return-value]

    # CONTENT OPERATIONS
    async def create_post(self, post_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_post(post_data)  # type: ignore[union-attr, return-value]

    async def get_post_by_slug(self, slug: str) -> Optional[Dict]:
        """Delegate to content module."""
        return await self.content.get_post_by_slug(slug)  # type: ignore[union-attr, return-value]

    async def update_post(self, post_id: int, updates: dict) -> bool:
        """Delegate to content module."""
        return await self.content.update_post(post_id, updates)  # type: ignore[union-attr, return-value]

    async def get_all_categories(self) -> List[Dict]:
        """Delegate to content module."""
        return await self.content.get_all_categories()  # type: ignore[union-attr, return-value]

    async def get_all_tags(self) -> List[Dict]:
        """Delegate to content module."""
        return await self.content.get_all_tags()  # type: ignore[union-attr, return-value]

    async def get_author_by_name(self, name: str) -> Optional[Dict]:
        """Delegate to content module."""
        return await self.content.get_author_by_name(name)  # type: ignore[union-attr, return-value]

    async def create_quality_evaluation(self, eval_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_quality_evaluation(eval_data)  # type: ignore[union-attr, return-value]

    async def create_quality_improvement_log(self, log_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_quality_improvement_log(log_data)  # type: ignore[union-attr, return-value]

    async def get_metrics(self) -> Dict:
        """Delegate to content module."""
        return await self.content.get_metrics()  # type: ignore[union-attr, return-value]

    async def create_orchestrator_training_data(self, train_data: dict) -> Dict:
        """Delegate to content module."""
        return await self.content.create_orchestrator_training_data(train_data)  # type: ignore[union-attr, return-value]

    # ADMIN OPERATIONS
    async def add_log_entry(
        self, agent_name: str, level: str, message: str, context: Optional[dict] = None
    ) -> Dict:
        """Delegate to admin module."""
        return await self.admin.add_log_entry(agent_name, level, message, context)  # type: ignore[union-attr, return-value]

    async def get_logs(
        self, agent_name: Optional[str] = None, level: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_logs(agent_name, level, limit)  # type: ignore[union-attr, return-value]

    async def add_financial_entry(self, entry_data: dict) -> Dict:
        """Delegate to admin module."""
        return await self.admin.add_financial_entry(entry_data)  # type: ignore[union-attr, return-value]

    async def get_financial_summary(self, days: int = 30) -> Dict:
        """Delegate to admin module."""
        return await self.admin.get_financial_summary(days)  # type: ignore[union-attr, return-value]

    async def log_cost(self, cost_log: dict) -> Dict:
        """Delegate to admin module."""
        return await self.admin.log_cost(cost_log)  # type: ignore[union-attr, return-value]

    async def get_task_costs(self, task_id: str) -> Dict:
        """Delegate to admin module."""
        return await self.admin.get_task_costs(task_id)  # type: ignore[union-attr, return-value]

    async def update_agent_status(
        self, agent_name: str, status: str, last_run=None, metadata: Optional[dict] = None
    ) -> bool:
        """Delegate to admin module."""
        return await self.admin.update_agent_status(agent_name, status, last_run, metadata)  # type: ignore[union-attr, return-value]

    async def get_agent_status(self, agent_name: str) -> Optional[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_agent_status(agent_name)  # type: ignore[union-attr, return-value]

    async def health_check(self, service: str = "cofounder") -> Dict:
        """Delegate to admin module."""
        return await self.admin.health_check(service)  # type: ignore[union-attr, return-value]

    async def get_setting(self, key: str) -> Optional[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_setting(key)  # type: ignore[union-attr, return-value]

    async def get_all_settings(self, category: Optional[str] = None) -> List[Dict]:
        """Delegate to admin module."""
        return await self.admin.get_all_settings(category)  # type: ignore[union-attr, return-value]

    async def set_setting(
        self,
        key: str,
        value,
        category: Optional[str] = None,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict:
        """Delegate to admin module."""
        return await self.admin.set_setting(key, value, category, display_name, description)  # type: ignore[union-attr, return-value]

    async def delete_setting(self, key: str) -> bool:
        """Delegate to admin module."""
        return await self.admin.delete_setting(key)  # type: ignore[union-attr, return-value]

    async def get_setting_value(self, key: str, default=None) -> any:  # type: ignore[union-attr, return-value]
        """Delegate to admin module."""
        return await self.admin.get_setting_value(key, default)  # type: ignore[union-attr, return-value]

    async def setting_exists(self, key: str) -> bool:
        """Delegate to admin module."""
        return await self.admin.setting_exists(key)  # type: ignore[union-attr, return-value]
