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

import os

import asyncpg

from config import get_config
from services.logger_config import get_logger

from .admin_db import AdminDatabase
from .audit_log import AuditLogger, init_global_audit_logger
from .content_db import ContentDatabase
from .embeddings_db import EmbeddingsDatabase
from .tasks_db import TasksDatabase
from .users_db import UsersDatabase
from .writing_style_db import WritingStyleDatabase
from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


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
        database_url: str | None = None,
        local_database_url: str | None = None,
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
            # #198: check ~/.poindexter/bootstrap.toml FIRST so worker can
            # start on a fresh clone without a .env file. Falls back to
            # DATABASE_URL env var for Docker/CI contexts.
            #
            # Issue #169: when DATABASE_URL is unset AND bootstrap.toml has
            # the wrong key (e.g. ``database_dsn`` instead of
            # ``database_url``), this used to fall through to a plain
            # ``ValueError``. That was loud enough at the call site but
            # bypassed the operator-notification pipeline (Telegram /
            # Discord / alerts.log) the rest of the codebase relies on for
            # missing-config failures. Worse, callers that swallowed the
            # exception would end up dereferencing ``self.pool`` while it
            # was still None — the silent boot crash described in #169.
            # Match the "Fail loud + notify" principle in CLAUDE.md by
            # routing through ``brain.bootstrap.require_database_url``,
            # which calls ``notify_operator()`` then ``sys.exit(2)``.
            resolved = None
            _require = None
            _resolve = None
            try:
                import sys as _sys
                from pathlib import Path as _Path

                _here = _Path(__file__).resolve()
                for _p in _here.parents:
                    if (_p / "brain" / "bootstrap.py").is_file():
                        if str(_p) not in _sys.path:
                            _sys.path.insert(0, str(_p))
                        break
                from brain.bootstrap import require_database_url as _require
                from brain.bootstrap import resolve_database_url as _resolve

                resolved = _resolve()
            except Exception:
                # Bootstrap module unavailable (odd — Docker test contexts
                # without the brain mount). Fall through to env var.
                resolved = None

            if not resolved:
                resolved = os.getenv("DATABASE_URL")

            if not resolved:
                if _require is not None:
                    # Fail loud + notify operator (Telegram → Discord →
                    # alerts.log → stderr) then sys.exit(2). Does not
                    # return.
                    resolved = _require(source="services.database_service")
                else:
                    # Bootstrap unavailable — at minimum raise so callers
                    # can't dereference a None pool downstream (#169).
                    raise ValueError(
                        "DATABASE_URL is not configured. PostgreSQL is REQUIRED. "
                        "Run `poindexter setup` to create ~/.poindexter/bootstrap.toml, "
                        "or set DATABASE_URL in the environment."
                    )
            self.database_url = resolved

        # Local database URL (optional — falls back to primary pool when unset)
        self.local_database_url = local_database_url or os.getenv("LOCAL_DATABASE_URL") or None

        logger.info("DatabaseService initialized with PostgreSQL: %s...", self.database_url[:50])
        if self.local_database_url:
            logger.info(
                "DatabaseService local pool configured: %s...", self.local_database_url[:50]
            )

        self.pool = None
        self.local_pool = None

        # Delegate modules will be initialized after pool is created
        self.users: UsersDatabase | None = None
        self.tasks: TasksDatabase | None = None
        self.content: ContentDatabase | None = None
        self.admin: AdminDatabase | None = None
        self.writing_style: WritingStyleDatabase | None = None
        self.embeddings: EmbeddingsDatabase | None = None
        self.audit: AuditLogger | None = None

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
            # GH-92: keep ``min_size`` small in every environment. Pools that
            # pre-warm 20 connections reserve them against ``max_connections``
            # even when the worker is idle — a direct contributor to the
            # TooManyConnectionsError stress test that motivated GH-92.
            # ``max_size`` stays higher so bursts can grow the pool on demand.
            # Reads from app_settings (no silent env-var drift); defaults
            # preserve backward compat for max_size.
            min_size = int(site_config.get("database_pool_min_size", "2" if is_dev else "5"))
            max_size = int(site_config.get("database_pool_max_size", "20" if is_dev else "50"))

            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=min_size,
                max_size=max_size,
                timeout=30,
                command_timeout=30,  # Query execution timeout
            )
            logger.info(
                "Database pool initialized (size: %s-%s, query timeout: 30s)", min_size, max_size
            )

            # Create local pool if LOCAL_DATABASE_URL is configured, otherwise reuse primary
            if self.local_database_url:
                # GH-92: local pool min stays at 2 — rarely-called paths
                # (tasks, writing_style, embeddings) shouldn't hoard.
                local_min = int(site_config.get("local_database_pool_min_size", "2" if is_dev else "2"))
                local_max = int(site_config.get("local_database_pool_max_size", "10" if is_dev else "20"))
                self.local_pool = await asyncpg.create_pool(
                    self.local_database_url,
                    min_size=local_min,
                    max_size=local_max,
                    timeout=30,
                    command_timeout=30,
                )
                logger.info(
                    "Local database pool initialized (size: %s-%s)", local_min, local_max
                )

                # Worker mode: flip pools so .pool = local (internal ops default)
                # and .cloud_pool = production DB (public content only)
                deployment_mode = os.getenv("DEPLOYMENT_MODE", "coordinator")
                if deployment_mode == "worker":
                    self.cloud_pool = self.pool  # Cloud DB for publishing
                    self.pool = self.local_pool  # Local for everything else
                    logger.info("Worker mode: pool=local, cloud_pool=cloud")
                else:
                    self.cloud_pool = self.pool  # Coordinator: both point to cloud DB
            else:
                self.local_pool = self.pool
                self.cloud_pool = self.pool
                logger.info("No LOCAL_DATABASE_URL - local_pool = pool (single-pool mode)")

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
                "All database modules initialized "
                "(users, tasks, content, admin, writing_style, embeddings, audit)"
            )
        except Exception as e:
            logger.error("Failed to initialize database: %s", e, exc_info=True)
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
    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Delegate to users module."""
        return await self.users.get_user_by_id(user_id)

    async def get_user_by_email(self, email: str) -> dict | None:
        """Delegate to users module."""
        return await self.users.get_user_by_email(email)

    async def get_user_by_username(self, username: str) -> dict | None:
        """Delegate to users module."""
        return await self.users.get_user_by_username(username)

    async def create_user(self, user_data: dict) -> dict:
        """Delegate to users module."""
        return await self.users.create_user(user_data)

    async def get_or_create_oauth_user(
        self, provider: str, provider_user_id: str, provider_data: dict
    ) -> dict:
        """Delegate to users module."""
        return await self.users.get_or_create_oauth_user(provider, provider_user_id, provider_data)

    async def get_oauth_accounts(self, user_id: str) -> list[dict]:
        """Delegate to users module."""
        return await self.users.get_oauth_accounts(user_id)

    async def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        """Delegate to users module."""
        return await self.users.unlink_oauth_account(user_id, provider)

    # TASK OPERATIONS
    async def add_task(self, task_data: dict) -> dict:
        """Delegate to tasks module."""
        return await self.tasks.add_task(task_data)

    async def get_task(self, task_id: str) -> dict | None:
        """Delegate to tasks module."""
        return await self.tasks.get_task(task_id)

    async def update_task_status(
        self, task_id: str, status: str, result: str | None = None
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
        status: str | None = None,
        category: str | None = None,
        search: str | None = None,
    ) -> dict:
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_paginated(offset, limit, status, category, search)

    async def get_task_counts(self) -> dict:
        """Delegate to tasks module."""
        return await self.tasks.get_task_counts()

    async def get_pending_tasks(self, limit: int = 10) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_pending_tasks(limit)

    async def get_all_tasks(self, limit: int = 100) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_all_tasks(limit)

    async def get_queued_tasks(self, limit: int = 5) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_queued_tasks(limit)

    async def get_tasks_by_date_range(
        self, start_date=None, end_date=None, status: str | None = None, limit: int = 500
    ) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_tasks_by_date_range(start_date, end_date, status, limit)

    async def get_kpi_aggregates(self, start_date=None, end_date=None) -> dict:
        """Delegate to tasks module — single-query KPI aggregation (issue #696)."""
        return await self.tasks.get_kpi_aggregates(start_date, end_date)

    async def delete_task(self, task_id: str) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.delete_task(task_id)

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_drafts(limit, offset)

    async def sweep_stale_tasks(
        self, timeout_minutes: int = 60, max_retries: int = 3
    ) -> dict:
        """Delegate to tasks module — reset stuck in_progress tasks."""
        return await self.tasks.sweep_stale_tasks(
            stale_threshold_minutes=timeout_minutes, max_retries=max_retries
        )

    async def heartbeat_task(self, task_id: str) -> bool:
        """Delegate to tasks module — stamp updated_at during long stages (GH-90)."""
        return await self.tasks.heartbeat_task(task_id)

    async def update_task_status_guarded(
        self,
        task_id: str,
        new_status: str,
        allowed_from: tuple = ("in_progress", "pending"),
        **fields,
    ):
        """Delegate to tasks module — status-guarded terminal write (GH-90)."""
        return await self.tasks.update_task_status_guarded(
            task_id, new_status, allowed_from=allowed_from, **fields
        )

    # CONTENT OPERATIONS
    async def create_post(self, post_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_post(post_data)

    async def get_post_by_slug(self, slug: str) -> dict | None:
        """Delegate to content module."""
        return await self.content.get_post_by_slug(slug)

    async def update_post(self, post_id: int, updates: dict) -> bool:
        """Delegate to content module."""
        return await self.content.update_post(post_id, updates)

    async def get_all_categories(self) -> list[dict]:
        """Delegate to content module."""
        return await self.content.get_all_categories()

    async def get_all_tags(self) -> list[dict]:
        """Delegate to content module."""
        return await self.content.get_all_tags()

    async def get_author_by_name(self, name: str) -> dict | None:
        """Delegate to content module."""
        return await self.content.get_author_by_name(name)

    async def create_quality_evaluation(self, eval_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_quality_evaluation(eval_data)

    async def create_quality_improvement_log(self, log_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_quality_improvement_log(log_data)

    async def get_metrics(self) -> dict:
        """Delegate to content module."""
        return await self.content.get_metrics()

    async def create_orchestrator_training_data(self, train_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_orchestrator_training_data(train_data)

    # ADMIN OPERATIONS
    async def add_log_entry(
        self, agent_name: str, level: str, message: str, context: dict | None = None
    ) -> dict:
        """Delegate to admin module."""
        return await self.admin.add_log_entry(agent_name, level, message, context)

    async def get_logs(
        self, agent_name: str | None = None, level: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Delegate to admin module."""
        return await self.admin.get_logs(agent_name, level, limit)

    async def add_financial_entry(self, entry_data: dict) -> dict:
        """Delegate to admin module."""
        return await self.admin.add_financial_entry(entry_data)

    async def get_financial_summary(self, days: int = 30) -> dict:
        """Delegate to admin module."""
        return await self.admin.get_financial_summary(days)

    async def log_cost(self, cost_log: dict) -> dict:
        """Delegate to admin module."""
        return await self.admin.log_cost(cost_log)

    async def mark_model_performance_outcome(
        self,
        task_id: str,
        *,
        human_approved: bool | None = None,
        post_published: bool | None = None,
    ) -> None:
        """Delegate to admin module — part of gitea#271 Phase 3.A1."""
        await self.admin.mark_model_performance_outcome(
            task_id,
            human_approved=human_approved,
            post_published=post_published,
        )

    async def get_task_costs(self, task_id: str) -> dict:
        """Delegate to admin module."""
        return await self.admin.get_task_costs(task_id)

    async def update_agent_status(
        self, agent_name: str, status: str, last_run=None, metadata: dict | None = None
    ) -> bool:
        """Delegate to admin module."""
        return await self.admin.update_agent_status(agent_name, status, last_run, metadata)

    async def get_agent_status(self, agent_name: str) -> dict | None:
        """Delegate to admin module."""
        return await self.admin.get_agent_status(agent_name)

    async def health_check(self, service: str = "cofounder") -> dict:
        """Delegate to admin module."""
        return await self.admin.health_check(service)

    async def get_setting(self, key: str) -> dict | None:
        """Delegate to admin module."""
        return await self.admin.get_setting(key)

    async def get_all_settings(self, category: str | None = None) -> list[dict]:
        """Delegate to admin module."""
        return await self.admin.get_all_settings(category)

    async def set_setting(
        self,
        key: str,
        value,
        category: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict:
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
        metadata: dict | None = None,
    ) -> str:
        """Delegate to embeddings module."""
        return await self.embeddings.store_embedding(
            source_type, source_id, content_hash, embedding, metadata
        )

    async def search_similar(
        self,
        embedding: list,
        limit: int = 10,
        source_type: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[dict]:
        """Delegate to embeddings module."""
        return await self.embeddings.search_similar(embedding, limit, source_type, min_similarity)

    async def get_embedding(self, source_type: str, source_id: str) -> dict | None:
        """Delegate to embeddings module."""
        return await self.embeddings.get_embedding(source_type, source_id)

    async def delete_embeddings(self, source_type: str, source_id: str | None = None) -> int:
        """Delegate to embeddings module."""
        return await self.embeddings.delete_embeddings(source_type, source_id)

    async def needs_reembedding(
        self, source_type: str, source_id: str, content_hash: str
    ) -> bool:
        """Delegate to embeddings module."""
        return await self.embeddings.needs_reembedding(source_type, source_id, content_hash)
