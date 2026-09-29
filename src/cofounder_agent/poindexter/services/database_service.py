"""
PostgreSQL Database Service Coordinator

Orchestrates access to 6 specialized database modules:
- UsersDatabase: User and OAuth operations
- TasksDatabase: Task management and filtering
- ContentDatabase: Posts, quality evaluations, metrics
- AdminDatabase: Logging, financial tracking, settings, health
- WritingStyleDatabase: Writing samples for RAG style matching
- EmbeddingsDatabase: Vector embeddings for similarity search (pgvector)

One connection pool (self.pool) backs every module. This service once supported
two databases: a local pool beside a hosted "cloud" pool, flipped in worker mode.
That mode was retired in Glad-Labs/poindexter#1115. The hosted database and the
sync that fed it are gone, and the two pools always pointed at the same database.

All existing methods are delegated to appropriate modules.
"""

import os
import sys
import warnings
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import asyncpg

from poindexter.config import get_config
from poindexter.schemas.typed_records import PaginatedTasksResult, TaskRecord
from poindexter.services.logger_config import get_logger
from poindexter.services.module_paths import resolve_module_path
from poindexter.services.site_config import SiteConfig

from .admin_db import AdminDatabase
from .audit_log import AuditLogger, drain_pending_writes, init_global_audit_logger
from .content_db import ContentDatabase
from .embeddings_db import EmbeddingsDatabase
from .tasks_db import TasksDatabase
from .users_db import UsersDatabase
from .writing_style_db import WritingStyleDatabase

# #272 Phase-2g: the module-level ``site_config`` global + ``set_site_config``
# setter are DELETED. injection is now mandatory — ``__init__`` takes a
# REQUIRED ``site_config`` and stores it on ``self._site_config``. Bootstrap
# nuance: ``DatabaseService`` is constructed very early (before site_config
# has loaded from the DB, which it does via this very pool), so the caller
# passes the app's lifespan-bound SiteConfig instance — initially empty,
# populated in-place by ``site_config.load(pool)`` AFTER construction. The
# pool-size reads in ``initialize()`` therefore pre-read their app_settings
# keys over a throwaway direct connection (see ``_preread_pool_size_settings``)
# and only then fall back to the literal defaults — before that fix the four
# seeded ``*_pool_*_size`` keys were silently inert (GlitchTip #560 triage).
# ``database_service`` is removed from ``di_wiring.WIRED_MODULES``.


logger = get_logger(__name__)

# Spellings of the loopback host that name the same database.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _database_identity(url: str) -> tuple[str, int, str] | None:
    """``(host, port, dbname)`` of a PostgreSQL URL, or ``None`` if it doesn't parse.

    Credentials and query options are deliberately left out. That is what makes
    two URLs comparable as "the same database", and what makes the result safe
    to log: this service used to log the first 50 characters of the URL, which
    carried the credentials.
    """
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port or 5432
    except ValueError:
        return None
    if parts.scheme not in ("postgres", "postgresql") or not host:
        return None
    return ("localhost" if host in _LOOPBACK_HOSTS else host), port, parts.path.lstrip("/")


def _describe_database(url: str) -> str:
    """Credential-free ``host:port/dbname`` for logs and error messages.

    Uses the host as written (``_database_identity`` folds loopback spellings
    together only so URLs compare equal), so an operator sees what they typed.
    """
    identity = _database_identity(url)
    if identity is None:
        return "<unparseable database URL>"
    _, port, dbname = identity
    host = urlsplit(url.strip()).hostname or ""
    return f"[{host}]:{port}/{dbname}" if ":" in host else f"{host}:{port}/{dbname}"


def _same_database(first: str, second: str) -> bool:
    """True when two URLs name the same host, port and database."""
    a, b = _database_identity(first), _database_identity(second)
    if a is None or b is None:
        return first.strip() == second.strip()
    return a == b


class DatabaseService:
    """
    PostgreSQL database service coordinator.

    Delegates to 6 specialized modules, all on the one pool (``self.pool``):
    - self.users: User/OAuth operations
    - self.tasks: Task management
    - self.content: Posts/quality/metrics
    - self.admin: Logging/financial/settings
    - self.writing_style: Writing samples for style matching
    - self.embeddings: Vector embeddings for similarity search
    """

    def __init__(
        self,
        database_url: str | None = None,
        local_database_url: str | None = None,
        *,
        site_config: SiteConfig,
    ):
        """
        Initialize database service coordinator with asyncpg.

        Args:
            database_url: PostgreSQL connection URL
                         Required: DATABASE_URL env var, bootstrap.toml, or passed explicitly
            local_database_url: DEPRECATED. The dual-pool mode it configured was retired
                               (Glad-Labs/poindexter#1115). A value naming the same
                               database as ``database_url`` is ignored with a
                               DeprecationWarning; one naming a different database
                               raises ``ValueError``. The ``LOCAL_DATABASE_URL`` env var is
                               held to the same rule, without the warning, when
                               ``database_url`` is resolved rather than passed: it is still
                               the legacy alias for the single database in the
                               URL-resolution chain.
            site_config: Injected SiteConfig (#272 Phase-2g). REQUIRED. Bootstrap
                         nuance: this service is constructed very early, before
                         ``site_config`` has loaded from the DB (it loads via this
                         very pool), so callers pass the app's lifespan-bound
                         SiteConfig instance — initially empty, populated in-place
                         by ``site_config.load(pool)`` after construction. Pool
                         sizes are pre-read directly from app_settings in
                         ``initialize()`` (see ``_preread_pool_size_settings``)
                         since ``.get()`` can only return defaults that early.
        """
        # #272 Phase-2g: injection is mandatory; store the run-bound instance.
        self._site_config = site_config
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
            # Stays None only when the bootstrap import fails; the no-URL
            # branch below then raises ValueError instead of notify + exit.
            _require: Callable[..., str] | None = None
            try:
                from poindexter.brain.bootstrap import require_database_url as _require
                from poindexter.brain.bootstrap import resolve_database_url

                resolved = resolve_database_url()
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

        # The dual-pool mode was retired (Glad-Labs/poindexter#1115). A second
        # URL that names the SAME database is harmless: LOCAL_DATABASE_URL is
        # still the legacy alias for the single database in the resolution
        # chain. One that names a DIFFERENT database can no longer be honoured,
        # and quietly ignoring it would point this service at the wrong
        # database, so fail loud instead. An explicit ``database_url=`` fully
        # determines the database, so a stray LOCAL_DATABASE_URL in the
        # environment is only consulted when the primary URL was itself
        # resolved from configuration.
        env_second = None if database_url else os.getenv("LOCAL_DATABASE_URL")
        second_url = local_database_url or env_second or None
        if second_url and not _same_database(second_url, self.database_url):
            raise ValueError(
                "DatabaseService no longer supports two databases. "
                f"DATABASE_URL names {_describe_database(self.database_url)} but the "
                f"local URL (LOCAL_DATABASE_URL / local_database_url=) names "
                f"{_describe_database(second_url)}. The dual-pool mode was removed in "
                "Glad-Labs/poindexter#1115. Point DATABASE_URL (or bootstrap.toml's "
                "database_url) at the single database that holds app_settings, and "
                "unset LOCAL_DATABASE_URL or set it to the same URL."
            )
        if local_database_url:
            warnings.warn(
                "DatabaseService(local_database_url=...) is deprecated and ignored: the "
                "dual-pool mode was retired (Glad-Labs/poindexter#1115). Pass "
                "database_url= only.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Credential-free on purpose. This used to log ``database_url[:50]``,
        # which put the credentials in every process's log line.
        logger.info("DatabaseService initialized with PostgreSQL: %s", _describe_database(self.database_url))

        self.pool: asyncpg.Pool = None  # type: ignore[assignment]

        # Delegate modules will be initialized after pool is created.
        # Typed as non-Optional so delegation methods typecheck without
        # per-method asserts. The None sentinel is intentional for
        # the window before initialize() runs. type: ignore[assignment]
        self.users: UsersDatabase = None  # type: ignore[assignment]
        self.tasks: TasksDatabase = None  # type: ignore[assignment]
        self.content: ContentDatabase = None  # type: ignore[assignment]
        self.admin: AdminDatabase = None  # type: ignore[assignment]
        self.writing_style: WritingStyleDatabase = None  # type: ignore[assignment]
        self.embeddings: EmbeddingsDatabase = None  # type: ignore[assignment]
        self.audit: AuditLogger = None  # type: ignore[assignment]

    async def _preread_pool_size_settings(self) -> dict[str, str]:
        """Read the pool-size app_settings keys via a throwaway connection.

        Bootstrap chicken-and-egg: ``SiteConfig`` loads from the DB via the
        very pool this method is sizing, so at pool-creation time
        ``site_config.get`` can only return literal defaults — which left
        the seeded ``*_pool_*_size`` keys silently inert (GlitchTip
        #560 triage 2026-07-02: bumping ``database_pool_max_size`` had no
        effect). One direct connection at boot makes them real.

        Reads from the database itself (that's where app_settings lives — the
        spinal cord). Any failure (fresh install mid-migration, DB briefly
        unavailable) returns ``{}`` and
        the caller falls back to defaults — if the DB is truly down, pool
        creation fails loud immediately after anyway.
        """
        keys = [
            "database_pool_min_size",
            "database_pool_max_size",
        ]
        dsn = self.database_url
        try:
            conn = await asyncpg.connect(dsn, timeout=10)
            try:
                rows = await conn.fetch(
                    "SELECT key, value FROM app_settings "
                    "WHERE key = ANY($1::text[]) AND is_active = true "
                    "AND COALESCE(value, '') <> ''",
                    keys,
                )
            finally:
                await conn.close()
            return {r["key"]: r["value"] for r in rows}
        except Exception as e:  # noqa: BLE001 — pre-read is best-effort
            # silent-ok: this runs BEFORE the connection pool (and this
            # service's own init) exists — a fresh install genuinely has
            # no app_settings table yet, and if the DB is truly down the
            # very next step (actual pool creation) already fails loud.
            # Nothing new to surface: emit_finding's own audit_log_bg
            # write would need a pool that doesn't exist at this point.
            logger.info(
                "Pool-size pre-read unavailable (%s) — using defaults", e
            )
            return {}

    async def initialize(self) -> None:
        """Initialize the connection pool and all delegate modules."""
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
            # Resolution: direct pre-read from app_settings (SiteConfig hasn't
            # loaded yet — see _preread_pool_size_settings) → site_config.get
            # (tests inject initial_config) → literal default.
            pre = await self._preread_pool_size_settings()
            min_size = int(
                pre.get("database_pool_min_size")
                or self._site_config.get("database_pool_min_size", "2" if is_dev else "5")
            )
            max_size = int(
                pre.get("database_pool_max_size")
                or self._site_config.get("database_pool_max_size", "20" if is_dev else "50")
            )

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

            # One pool backs every delegate module.
            self.users = UsersDatabase(self.pool)
            self.content = ContentDatabase(self.pool)
            self.admin = AdminDatabase(self.pool)
            self.tasks = TasksDatabase(self.pool)
            self.writing_style = WritingStyleDatabase(self.pool)
            self.embeddings = EmbeddingsDatabase(self.pool)
            self.audit = init_global_audit_logger(self.pool)

            logger.info(
                "All database modules initialized "
                "(users, tasks, content, admin, writing_style, embeddings, audit)"
            )
        except Exception as e:
            logger.error("Failed to initialize database: %s", e, exc_info=True)
            raise

    async def close(self) -> None:
        """Close the connection pool."""
        # Flush in-flight publish-tail fire-and-forget tasks (newsletter /
        # R2 upload / search-engine ping) FIRST: a short-lived owner (the
        # Prefect auto-publish flow subprocess, the CLI publish paths)
        # reaches close() while they are still running, and the newsletter
        # only *schedules* its newsletter_campaign_sent audit row once the
        # send completes — so it must finish before the audit drain below,
        # all while the pools are still open (GlitchTip #863 root cause B).
        # Resolved via sys.modules so a process that never imported the
        # (heavy) publish module doesn't pay the import at shutdown — if it
        # was never imported, nothing was spawned.
        # Key resolved through the module-path seam: after the poindexter.*
        # move this module is registered under the new name (poindexter#1046).
        publish_service = sys.modules.get(resolve_module_path("poindexter.services.publish_service"))
        if publish_service is not None:
            try:
                await publish_service.drain_background_tasks()
            except Exception:
                logger.warning(
                    "Draining publish background tasks raised during close",
                    exc_info=True,
                )
        # Flush in-flight fire-and-forget audit writes (audit_log_bg) before
        # closing the pool they run against. Without this, a warn/critical
        # finding emitted moments earlier — e.g. the spend-throttle engage
        # finding in a per-run Prefect flow subprocess that builds+closes its
        # own pool — races pool.close() and dies with
        # InterfaceError('pool is closing'), losing the finding the #303
        # loud-drop path exists to protect (GlitchTip #863). Bounded and never
        # raises, so teardown neither hangs nor is masked.
        await drain_pending_writes()
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
        return await self.users.get_user_by_id(user_id)  # type: ignore[return-value]

    async def get_user_by_email(self, email: str) -> dict | None:
        """Delegate to users module."""
        return await self.users.get_user_by_email(email)  # type: ignore[return-value]

    async def get_user_by_username(self, username: str) -> dict | None:
        """Delegate to users module."""
        return await self.users.get_user_by_username(username)  # type: ignore[return-value]

    async def create_user(self, user_data: dict) -> dict:
        """Delegate to users module."""
        return await self.users.create_user(user_data)  # type: ignore[return-value]

    async def get_or_create_oauth_user(
        self, provider: str, provider_user_id: str, provider_data: dict
    ) -> dict:
        """Delegate to users module."""
        return await self.users.get_or_create_oauth_user(provider, provider_user_id, provider_data)  # type: ignore[return-value]

    async def get_oauth_accounts(self, user_id: str) -> list[dict]:
        """Delegate to users module."""
        return await self.users.get_oauth_accounts(user_id)  # type: ignore[return-value]

    async def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        """Delegate to users module."""
        return await self.users.unlink_oauth_account(user_id, provider)  # type: ignore[return-value]

    # TASK OPERATIONS
    async def add_task(self, task_data: dict) -> str:
        """Delegate to tasks module."""
        return await self.tasks.add_task(task_data)

    async def get_task(self, task_id: str) -> TaskRecord | None:
        """Delegate to tasks module."""
        return await self.tasks.get_task(task_id)

    async def update_task_status(
        self, task_id: str, status: str, result: str | None = None
    ) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.update_task_status(task_id, status, result)  # type: ignore[return-value]

    async def get_tasks_by_ids(self, task_ids: list) -> dict:
        """Delegate bulk task fetch to tasks module (1 query for all IDs)."""
        return await self.tasks.get_tasks_by_ids(task_ids)

    async def bulk_update_task_statuses(self, task_ids: list, new_status: str) -> dict:
        """Delegate bulk status update to tasks module (2 queries regardless of batch size)."""
        return await self.tasks.bulk_update_task_statuses(task_ids, new_status)

    async def update_task(self, task_id: str, updates: dict) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.update_task(task_id, updates)  # type: ignore[return-value]

    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: str | None = None,
        category: str | None = None,
        search: str | None = None,
        light: bool = False,
    ) -> PaginatedTasksResult:
        """Delegate to tasks module.

        Returns ``(rows, total)`` — destructure at the call site. The
        prior ``-> dict`` annotation was a long-standing lie; callers
        always received the leaf's tuple. See #201.

        ``light=True`` returns a lean projection (content truncated to a
        preview, heavy view blobs + correlated subqueries pruned) for
        list/preview callers — see ``tasks.get_tasks_paginated`` (#619).
        """
        return await self.tasks.get_tasks_paginated(
            offset, limit, status, category, search, light=light
        )

    async def get_task_counts(self) -> dict:
        """Delegate to tasks module."""
        return await self.tasks.get_task_counts()  # type: ignore[return-value]

    async def get_pending_tasks(self, limit: int = 10) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_pending_tasks(limit)

    async def get_all_tasks(self, limit: int = 100) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_all_tasks(limit)  # type: ignore[return-value]

    async def get_queued_tasks(self, limit: int = 5) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_queued_tasks(limit)  # type: ignore[return-value]

    async def delete_task(self, task_id: str) -> bool:
        """Delegate to tasks module."""
        return await self.tasks.delete_task(task_id)

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Delegate to tasks module."""
        return await self.tasks.get_drafts(limit, offset)  # type: ignore[return-value]

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
        return await self.tasks.update_task_status_guarded(  # type: ignore[return-value]
            task_id, new_status, allowed_from=allowed_from, **fields
        )

    # CONTENT OPERATIONS
    async def create_post(self, post_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_post(post_data)  # type: ignore[return-value]

    async def get_post_by_slug(self, slug: str) -> dict | None:
        """Delegate to content module."""
        return await self.content.get_post_by_slug(slug)  # type: ignore[return-value]

    async def update_post(self, post_id: int, updates: dict) -> bool:
        """Delegate to content module."""
        return await self.content.update_post(post_id, updates)

    async def get_all_categories(self) -> list[dict]:
        """Delegate to content module."""
        return await self.content.get_all_categories()  # type: ignore[return-value]

    async def get_all_tags(self) -> list[dict]:
        """Delegate to content module."""
        return await self.content.get_all_tags()  # type: ignore[return-value]

    async def get_author_by_name(self, name: str) -> dict | None:
        """Delegate to content module."""
        return await self.content.get_author_by_name(name)  # type: ignore[return-value]

    async def create_quality_evaluation(self, eval_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_quality_evaluation(eval_data)  # type: ignore[return-value]

    async def create_quality_improvement_log(self, log_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_quality_improvement_log(log_data)  # type: ignore[return-value]

    async def get_metrics(self) -> dict:
        """Delegate to content module."""
        return await self.content.get_metrics()  # type: ignore[return-value]

    async def create_orchestrator_training_data(self, train_data: dict) -> dict:
        """Delegate to content module."""
        return await self.content.create_orchestrator_training_data(train_data)  # type: ignore[return-value]

    # ADMIN OPERATIONS
    async def add_financial_entry(self, entry_data: dict) -> dict:
        """Delegate to admin module."""
        return await self.admin.add_financial_entry(entry_data)

    async def get_financial_summary(self, days: int = 30) -> dict:
        """Delegate to admin module."""
        return await self.admin.get_financial_summary(days)

    async def log_cost(self, cost_log: dict) -> dict:
        """Delegate to admin module."""
        return await self.admin.log_cost(cost_log)  # type: ignore[return-value]

    async def mark_model_performance_outcome(
        self,
        task_id: str,
        *,
        human_approved: bool | None = None,
        post_published: bool | None = None,
    ) -> None:
        """Delegate to admin module — part of internal tracker Phase 3.A1."""
        await self.admin.mark_model_performance_outcome(
            task_id,
            human_approved=human_approved,
            post_published=post_published,
        )

    async def get_task_costs(self, task_id: str) -> dict:
        """Delegate to admin module."""
        return await self.admin.get_task_costs(task_id)  # type: ignore[return-value]

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
        return await self.admin.get_setting(key)  # type: ignore[return-value]

    async def get_all_settings(self, category: str | None = None) -> list[dict]:
        """Delegate to admin module."""
        return await self.admin.get_all_settings(category)  # type: ignore[return-value]

    async def set_setting(
        self,
        key: str,
        value,
        category: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Delegate to admin module."""
        return await self.admin.set_setting(key, value, category, display_name, description)  # type: ignore[return-value]

    async def delete_setting(self, key: str) -> bool:
        """Delegate to admin module."""
        return await self.admin.delete_setting(key)

    async def get_setting_value(self, key: str, default=None) -> Any:
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
