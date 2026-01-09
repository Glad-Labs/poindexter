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

import logging
import os
import asyncpg
from typing import Optional

from .users_db import UsersDatabase
from .tasks_db import TasksDatabase
from .content_db import ContentDatabase
from .admin_db import AdminDatabase
from .writing_style_db import WritingStyleDatabase

logger = logging.getLogger(__name__)


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
            )
            logger.info(f"✅ Database pool initialized (size: {min_size}-{max_size})")
            
            # Initialize all delegate modules
            self.users = UsersDatabase(self.pool)
            self.tasks = TasksDatabase(self.pool)
            self.content = ContentDatabase(self.pool)
            self.admin = AdminDatabase(self.pool)
            self.writing_style = WritingStyleDatabase(self.pool)
            
            logger.info("✅ All database modules initialized (users, tasks, content, admin, writing_style)")
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}")
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
    async def get_user_by_id(self, user_id: str):
        """Delegate to users module."""
        return await self.users.get_user_by_id(user_id)

    async def get_user_by_email(self, email: str):
        """Delegate to users module."""
        return await self.users.get_user_by_email(email)

    async def get_user_by_username(self, username: str):
        """Delegate to users module."""
        return await self.users.get_user_by_username(username)

    async def create_user(self, user_data: dict):
        """Delegate to users module."""
        return await self.users.create_user(user_data)

    async def get_or_create_oauth_user(self, provider: str, provider_user_id: str, provider_data: dict):
        """Delegate to users module."""
        return await self.users.get_or_create_oauth_user(provider, provider_user_id, provider_data)

    async def get_oauth_accounts(self, user_id: str):
        """Delegate to users module."""
        return await self.users.get_oauth_accounts(user_id)

    async def unlink_oauth_account(self, user_id: str, provider: str):
        """Delegate to users module."""
        return await self.users.unlink_oauth_account(user_id, provider)

    # TASK OPERATIONS
    async def add_task(self, task_data: dict):
        """Delegate to tasks module."""
        return await self.tasks.add_task(task_data)

    async def get_task(self, task_id: str):
        """Delegate to tasks module."""
        return await self.tasks.get_task(task_id)

    async def update_task_status(self, task_id: str, status: str, result: Optional[str] = None):
        """Delegate to tasks module."""
        return await self.tasks.update_task_status(task_id, status, result)

    async def update_task(self, task_id: str, updates: dict):
        """Delegate to tasks module."""
        return await self.tasks.update_task(task_id, updates)

    async def get_tasks_paginated(self, offset: int = 0, limit: int = 20, status: Optional[str] = None, category: Optional[str] = None):
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_paginated(offset, limit, status, category)

    async def get_task_counts(self):
        """Delegate to tasks module."""
        return await self.tasks.get_task_counts()

    async def get_pending_tasks(self, limit: int = 10):
        """Delegate to tasks module."""
        return await self.tasks.get_pending_tasks(limit)

    async def get_all_tasks(self, limit: int = 100):
        """Delegate to tasks module."""
        return await self.tasks.get_all_tasks(limit)

    async def get_queued_tasks(self, limit: int = 5):
        """Delegate to tasks module."""
        return await self.tasks.get_queued_tasks(limit)

    async def get_tasks_by_date_range(self, start_date=None, end_date=None, status: Optional[str] = None, limit: int = 10000):
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_by_date_range(start_date, end_date, status, limit)

    async def delete_task(self, task_id: str):
        """Delegate to tasks module."""
        return await self.tasks.delete_task(task_id)

    async def get_drafts(self, limit: int = 20, offset: int = 0):
        """Delegate to tasks module."""
        return await self.tasks.get_drafts(limit, offset)

    # CONTENT OPERATIONS
    async def create_post(self, post_data: dict):
        """Delegate to content module."""
        return await self.content.create_post(post_data)

    async def get_post_by_slug(self, slug: str):
        """Delegate to content module."""
        return await self.content.get_post_by_slug(slug)

    async def update_post(self, post_id: int, updates: dict):
        """Delegate to content module."""
        return await self.content.update_post(post_id, updates)

    async def get_all_categories(self):
        """Delegate to content module."""
        return await self.content.get_all_categories()

    async def get_all_tags(self):
        """Delegate to content module."""
        return await self.content.get_all_tags()

    async def get_author_by_name(self, name: str):
        """Delegate to content module."""
        return await self.content.get_author_by_name(name)

    async def create_quality_evaluation(self, eval_data: dict):
        """Delegate to content module."""
        return await self.content.create_quality_evaluation(eval_data)

    async def create_quality_improvement_log(self, log_data: dict):
        """Delegate to content module."""
        return await self.content.create_quality_improvement_log(log_data)

    async def get_metrics(self):
        """Delegate to content module."""
        return await self.content.get_metrics()

    async def create_orchestrator_training_data(self, train_data: dict):
        """Delegate to content module."""
        return await self.content.create_orchestrator_training_data(train_data)

    # ADMIN OPERATIONS
    async def add_log_entry(self, agent_name: str, level: str, message: str, context: Optional[dict] = None):
        """Delegate to admin module."""
        return await self.admin.add_log_entry(agent_name, level, message, context)

    async def get_logs(self, agent_name: Optional[str] = None, level: Optional[str] = None, limit: int = 100):
        """Delegate to admin module."""
        return await self.admin.get_logs(agent_name, level, limit)

    async def add_financial_entry(self, entry_data: dict):
        """Delegate to admin module."""
        return await self.admin.add_financial_entry(entry_data)

    async def get_financial_summary(self, days: int = 30):
        """Delegate to admin module."""
        return await self.admin.get_financial_summary(days)

    async def log_cost(self, cost_log: dict):
        """Delegate to admin module."""
        return await self.admin.log_cost(cost_log)

    async def get_task_costs(self, task_id: str):
        """Delegate to admin module."""
        return await self.admin.get_task_costs(task_id)

    async def update_agent_status(self, agent_name: str, status: str, last_run=None, metadata: Optional[dict] = None):
        """Delegate to admin module."""
        return await self.admin.update_agent_status(agent_name, status, last_run, metadata)

    async def get_agent_status(self, agent_name: str):
        """Delegate to admin module."""
        return await self.admin.get_agent_status(agent_name)

    async def health_check(self, service: str = "cofounder"):
        """Delegate to admin module."""
        return await self.admin.health_check(service)

    async def get_setting(self, key: str):
        """Delegate to admin module."""
        return await self.admin.get_setting(key)

    async def get_all_settings(self, category: Optional[str] = None):
        """Delegate to admin module."""
        return await self.admin.get_all_settings(category)

    async def set_setting(self, key: str, value, category: Optional[str] = None, display_name: Optional[str] = None, description: Optional[str] = None):
        """Delegate to admin module."""
        return await self.admin.set_setting(key, value, category, display_name, description)

    async def delete_setting(self, key: str):
        """Delegate to admin module."""
        return await self.admin.delete_setting(key)

    async def get_setting_value(self, key: str, default=None):
        """Delegate to admin module."""
        return await self.admin.get_setting_value(key, default)

    async def setting_exists(self, key: str):
        """Delegate to admin module."""
        return await self.admin.setting_exists(key)
