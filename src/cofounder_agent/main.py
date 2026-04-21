"""
Poindexter — AI content pipeline (built by Glad Labs LLC).
FastAPI application serving as the central orchestrator for the Poindexter pipeline.
Implements PostgreSQL database with REST API command queue integration.
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from importlib.util import find_spec
from typing import Any

# Third-party imports
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, field_validator

# Import configuration
from config import get_config
from middleware.api_token_auth import verify_api_token
from services.container import service_container

# Import services
from services.logger_config import get_logger
from services.quality_service import UnifiedQualityService

try:
    from services.sentry_integration import setup_sentry
except ImportError:
    def setup_sentry(*_args, **_kwargs):
        """Stub when Sentry is not installed."""
        return

from services.telemetry import setup_telemetry
from utils.connection_health import ConnectionPoolHealth

# Local application imports (must come after path setup)
from utils.exception_handlers import register_exception_handlers
from utils.middleware_config import MiddlewareConfig
from utils.route_registration import register_all_routes
from utils.route_utils import initialize_services
from utils.startup_manager import StartupManager

# Load configuration
config = get_config()

SENTRY_AVAILABLE = find_spec("sentry_sdk") is not None

# PostgreSQL database service is now the primary service
DATABASE_SERVICE_AVAILABLE = True

logger = get_logger(__name__)


# ============================================================================
# LIFESPAN: Application Startup and Shutdown
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):  # pylint: disable=redefined-outer-name
    """
    Application lifespan manager - handles startup and shutdown.

    Uses StartupManager to orchestrate all service initialization
    in the correct order with proper error handling.
    """
    startup_manager = StartupManager()
    scheduled_publisher_task = None
    pool_health_task = None

    try:
        logger.info("=" * 80)
        logger.info("[LIFESPAN] Starting application startup sequence. ..")
        logger.info("=" * 80)

        # Initialize all services
        logger.info("[LIFESPAN] Calling startup_manager.initialize_all_services(). ..")
        services = await startup_manager.initialize_all_services()
        logger.info("[LIFESPAN] ✅ All services initialized by startup_manager")
        logger.debug(f"[LIFESPAN] Services dict keys: {services.keys()}")

        # Inject services into app state for access in routes
        logger.info("[LIFESPAN] Injecting services into app.state. ..")
        app.state.database = services["database"]
        app.state.redis_cache = services["redis_cache"]
        # app.state.orchestrator will be set to UnifiedOrchestrator below
        # (removed legacy Orchestrator)
        app.state.task_executor = services["task_executor"]
        app.state.custom_workflows_service = services.get("custom_workflows_service")
        app.state.legacy_data_service = services.get("legacy_data_service")
        app.state.startup_error = services["startup_error"]
        app.state.startup_complete = True
        logger.debug("[LIFESPAN] ✅ All services injected into app.state")

        # Capability system removed (dead code, no consumers)

        # Initialize settings service (DB-backed key-value config)
        logger.info("[LIFESPAN] Initializing settings service. ..")
        try:
            from services.settings_service import SettingsService
            db_pool = services["database"].pool
            settings_service = SettingsService(db_pool)
            await settings_service.refresh_cache()
            app.state.settings_service = settings_service
            service_container.register("settings", settings_service)
            logger.info("[LIFESPAN] Settings service initialized")

            # Sync secrets: DB is the source of truth.
            # On first run, auto-generated secrets are saved to DB.
            # On subsequent runs, DB values override the auto-generated ones.
            _secret_keys = {
                "secret_key": ("SECRET_KEY", "auth"),
                "jwt_secret_key": ("JWT_SECRET_KEY", "auth"),
                "jwt_secret": ("JWT_SECRET", "auth"),
                "revalidate_secret": ("REVALIDATE_SECRET", "integrations"),
                "api_auth_token": ("API_TOKEN", "security"),
            }
            secrets_loaded = 0
            secrets_saved = 0
            for db_key, (env_var, category) in _secret_keys.items():
                db_val = await settings_service.get(db_key)
                env_val = os.environ.get(env_var, "")
                if db_val:
                    # DB has a value — use it (source of truth)
                    os.environ[env_var] = db_val
                    secrets_loaded += 1
                elif env_val:
                    # DB empty but env has a value — persist to DB
                    await settings_service.set(
                        db_key, env_val, category=category,
                        description=f"Auto-persisted from env var {env_var}",
                        is_secret=True,
                    )
                    secrets_saved += 1
            if secrets_loaded or secrets_saved:
                logger.info(
                    "[LIFESPAN] Secrets synced: %d loaded from DB, %d saved to DB",
                    secrets_loaded, secrets_saved,
                )
        except Exception as e:
            logger.warning(f"[LIFESPAN] Settings service failed (non-critical): {e}", exc_info=True)
            app.state.settings_service = None

        # Load site config from DB (identity, settings — replaces env vars).
        # Stash on app.state so routes + stages can Depends() it instead of
        # reaching into the module-level singleton (Gitea #242).
        try:
            from services.site_config import site_config
            db_pool = services["database"].pool
            loaded = await site_config.load(db_pool)
            app.state.site_config = site_config
            logger.info("[LIFESPAN] Site config loaded: %d settings from DB", loaded)
        except Exception as e:
            logger.warning("[LIFESPAN] Site config load failed (using env fallbacks): %s", e)
            # Still attach the module singleton so Depends() works — it will
            # fall back to env/defaults for misses until the DB is reachable.
            try:
                from services.site_config import site_config
                app.state.site_config = site_config
            except Exception:
                app.state.site_config = None

        # Re-initialize observability stack now that site_config is loaded from
        # DB. Module-level setup() calls earlier saw empty values — this is the
        # first point where sentry_dsn / enable_pyroscope / enable_tracing are
        # actually populated. Each setup is guarded internally.
        try:
            setup_sentry(app, service_name="cofounder-agent")
        except Exception as e:
            logger.warning("[LIFESPAN] sentry re-init failed: %s", e)
        try:
            setup_telemetry(app)
        except Exception as e:
            logger.warning("[LIFESPAN] telemetry re-init failed: %s", e)
        try:
            from services.profiling import setup_pyroscope
            setup_pyroscope()
        except Exception as e:
            logger.warning("[LIFESPAN] pyroscope re-init failed: %s", e)

        # Load prompt templates from DB (overrides YAML files)
        try:
            from services.prompt_manager import get_prompt_manager
            pm = get_prompt_manager()
            db_pool = services["database"].pool
            loaded = await pm.load_from_db(db_pool)
            logger.info("[LIFESPAN] Prompt templates loaded from DB: %d", loaded)
        except Exception as e:
            logger.warning("[LIFESPAN] Prompt DB load failed (using YAML fallback): %s", e)

        # Initialize quality service
        logger.info("[LIFESPAN] Initializing quality service. ..")
        quality_service = UnifiedQualityService()
        service_container.register("quality", quality_service)
        logger.info("[LIFESPAN] ✅ Quality service initialized")

        # Initialize template execution service for blog_post workflow
        logger.info("[LIFESPAN] Initializing template execution service...")
        try:
            from services.template_execution_service import TemplateExecutionService

            custom_workflows_svc = services.get("custom_workflows_service")
            template_execution_service = TemplateExecutionService(
                custom_workflows_service=custom_workflows_svc,
            )
            logger.info("[LIFESPAN] ✅ Template execution service initialized")
        except Exception as e:
            template_execution_service = None
            logger.warning(f"[LIFESPAN] ⚠️ Template execution service failed: {e}", exc_info=True)

        # Register services in the global DI container for dependency injection
        logger.info("[LIFESPAN] Registering services in global DI container. ..")
        initialize_services(
            app,
            database_service=services["database"],
            orchestrator=services.get("orchestrator"),
            task_executor=services["task_executor"],
            intelligent_orchestrator=services.get("intelligent_orchestrator"),
            workflow_history=services.get("workflow_history"),
            custom_workflows_service=services.get("custom_workflows_service"),
            template_execution_service=template_execution_service,
        )
        logger.info("[LIFESPAN] ✅ Services registered in global DI container")

        # Branch startup behaviour based on deployment mode
        deployment_mode = _deployment_mode
        logger.info(f"[LIFESPAN] Deployment mode: {deployment_mode}")

        if deployment_mode == "worker":
            # Worker mode: register worker, start heartbeat, start task executor
            try:
                from services.worker_service import WorkerService

                worker_service = WorkerService(services["database"].pool)
                await worker_service.register()
                await worker_service.start_heartbeat()
                app.state.worker_service = worker_service
                logger.info("[LIFESPAN] Worker: registered and heartbeat started")
            except ImportError:
                logger.warning("[LIFESPAN] Worker: worker_service module not yet available, skipping")
            except Exception as e:
                logger.error(f"[LIFESPAN] Worker: failed to start worker service: {e}", exc_info=True)

            # Start task executor (claims tasks from queue)
            task_executor = services.get("task_executor")
            if task_executor:
                await task_executor.start()
                logger.info("[LIFESPAN] Worker: task executor started")
        else:
            # Coordinator mode: start webhook delivery, scheduled publisher
            # Do NOT start task executor (workers handle that)
            try:
                from services.webhook_delivery_service import WebhookDeliveryService

                webhook_service = WebhookDeliveryService(services["database"].pool)
                await webhook_service.start()
                app.state.webhook_service = webhook_service
                logger.info("[LIFESPAN] Coordinator: webhook delivery started")
            except ImportError:
                logger.warning("[LIFESPAN] Coordinator: webhook_delivery_service not yet available, skipping")
            except Exception as e:
                logger.error(f"[LIFESPAN] Coordinator: failed to start webhook delivery: {e}", exc_info=True)

            # Start the scheduled post publisher (publishes posts at their scheduled time)
            from services.scheduled_publisher import run_scheduled_publisher

            db_pool = services["database"].pool

            async def _get_pool():
                return db_pool

            scheduled_publisher_task = asyncio.create_task(run_scheduled_publisher(_get_pool))
            logger.info("[LIFESPAN] Coordinator: scheduled post publisher started")

        # Start connection pool health monitor (#819)
        db_service = services.get("database")
        if db_service and getattr(db_service, "pool", None):
            pool_health = ConnectionPoolHealth(db_service.pool)
            pool_health_task = asyncio.create_task(pool_health.auto_health_check())
            app.state.pool_health = pool_health
            logger.info("[LIFESPAN] Connection pool health monitor started")

        # Initialize global model router singleton and seed spend counter from
        # cost_logs so budget enforcement survives restarts (issue #1385).
        try:
            from services.model_router import get_model_router, initialize_model_router

            _router = get_model_router()
            if _router is None:
                _router = initialize_model_router()
            if _router and getattr(db_service, "pool", None):
                await _router.seed_spend_from_db(db_service.pool)
                logger.info("[LIFESPAN] Model router spend seeded from cost_logs")
        except Exception as e:
            logger.warning(f"[LIFESPAN] Failed to seed model router spend: {e}", exc_info=True)

        # Plugin scheduler — apscheduler + entry_point + core-sample Jobs.
        # Runs housekeeping (sync_page_views, db_backup, render_prometheus_rules, ...)
        # on schedules declared by the Job class; PluginConfig in app_settings
        # overrides per install. Worker mode only — coordinator has no pool
        # it owns (reads cloud DB read-only).
        app.state.plugin_scheduler = None
        if _deployment_mode == "worker" and db_service and getattr(db_service, "pool", None):
            try:
                from plugins.registry import get_core_samples, get_jobs
                from plugins.scheduler import PluginScheduler

                scheduler = PluginScheduler(db_service.pool)
                # entry_point-discovered jobs (third-party installs) + core
                # samples loaded imperatively (see registry.get_core_samples).
                jobs = list(get_jobs()) + list(get_core_samples().get("jobs", []))
                # Deduplicate by name — a core sample that also ships as an
                # entry_point shouldn't register twice.
                seen: set[str] = set()
                unique_jobs = []
                for job in jobs:
                    if job.name in seen:
                        continue
                    seen.add(job.name)
                    unique_jobs.append(job)
                accepted = await scheduler.register_all(unique_jobs)
                scheduler.start()
                app.state.plugin_scheduler = scheduler
                logger.info(
                    "[LIFESPAN] PluginScheduler started with %d jobs: %s",
                    len(accepted), accepted,
                )
            except Exception as e:
                logger.warning(
                    "[LIFESPAN] PluginScheduler failed (non-critical): %s", e,
                    exc_info=True,
                )

        logger.info("[OK] Lifespan: Yielding control to FastAPI application. ..")
        try:
            logger.info("[OK] Application is now running")
        except UnicodeEncodeError:
            logger.info("[OK] Application is now running")

        yield  # Application runs here

    except Exception as e:
        logger.error(f"Critical startup failure: {e!s}", exc_info=True)
        try:
            logger.error(f"[ERROR] EXCEPTION IN LIFESPAN: {e!s}", exc_info=True)
        except UnicodeEncodeError:
            logger.error(f"[ERROR] EXCEPTION IN LIFESPAN: {e!s}", exc_info=True)
        app.state.startup_error = str(e)
        app.state.startup_complete = True
        raise

    finally:
        try:
            logger.info("[STOP] Shutting down application")
        except UnicodeEncodeError:
            logger.info("[STOP] Shutting down application")
        if scheduled_publisher_task is not None:
            scheduled_publisher_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduled_publisher_task
        if pool_health_task is not None:
            pool_health_task.cancel()
            with suppress(asyncio.CancelledError):
                await pool_health_task
        # Stop PluginScheduler before shutting the pool down — the scheduler
        # will try to run DB queries on the way out otherwise.
        if getattr(app.state, "plugin_scheduler", None) is not None:
            try:
                await app.state.plugin_scheduler.shutdown(wait=False)
                logger.info("[STOP] PluginScheduler stopped")
            except Exception as e:
                logger.error(f"[STOP] Error stopping PluginScheduler: {e}", exc_info=True)
        # Stop worker service if running in worker mode
        if hasattr(app.state, "worker_service"):
            try:
                await app.state.worker_service.stop()
                logger.info("[STOP] Worker service stopped")
            except Exception as e:
                logger.error(f"[STOP] Error stopping worker service: {e}", exc_info=True)
        # Stop webhook delivery service if running in coordinator mode
        if hasattr(app.state, "webhook_service"):
            try:
                await app.state.webhook_service.stop()
                logger.info("[STOP] Webhook delivery service stopped")
            except Exception as e:
                logger.error(f"[STOP] Error stopping webhook delivery: {e}", exc_info=True)
        await startup_manager.shutdown()


_deployment_mode = os.getenv("DEPLOYMENT_MODE", "coordinator")
_is_production = config.environment == "production"

# Late import — site_config depends on services/database_service which
# can't load until config is ready above, which in turn pulls env vars.
from services.site_config import site_config as _site_cfg  # noqa: E402

_site_name = _site_cfg.get("site_name", "AI Content Pipeline")

app = FastAPI(
    title=f"{_site_name} ({_deployment_mode})",
    description=f"""
## AI-powered content pipeline and business co-founder

**Deployment mode: `{_deployment_mode}`** — {"always-on lightweight coordinator (cloud)" if _deployment_mode == "coordinator" else "heavy-compute worker (local PC)"}

This system provides autonomous agents and intelligent orchestration
for complete business operations including:
- **Task Planning & Execution**: Intelligent task decomposition and multi-agent execution
- **Content Generation**: AI-powered content creation with quality evaluation and multi-channel publishing
- **Business Intelligence**: Market analysis, trend detection, and strategic recommendations
- **CMS & Media Management**: Content management, featured image generation, and media organization
- **Social Media Integration**: Multi-platform content distribution and engagement tracking
- **Workflow Orchestration**: Complex business process automation with persistence and monitoring
- **Model Management**: Unified LLM access across Ollama, HuggingFace, OpenAI, Anthropic, and Google

### Quick Links
- **Documentation**: [View Full Docs](./docs/00-README.md)
- **Architecture**: [System Design](./docs/02-ARCHITECTURE_AND_DESIGN.md)
- **API Base URL**: http://localhost:8000

### Authentication
API endpoints require `Authorization: Bearer <API_TOKEN>` header.
Admin panel at `/admin`.
""",
    version=config.app_version,
    lifespan=lifespan,
    contact={
        "name": f"{_site_name} Support",
        "email": _site_cfg.get("support_email", "support@example.com"),
        "url": _site_cfg.get("site_url", "https://localhost:3000"),
    },
    license_info={"name": "AGPL-3.0", "url": "https://www.gnu.org/licenses/agpl-3.0.html"},
    openapi_url=None if _is_production else "/api/openapi.json",
    docs_url=None if _is_production else "/api/docs",
    redoc_url=None if _is_production else "/api/redoc",
    swagger_ui_parameters={"defaultModelsExpandDepth": 1},
)

# Initialize SQLAdmin panel at /admin
try:
    from admin import setup_admin

    setup_admin(app)
    logger.info("[ADMIN] SQLAdmin panel mounted at /admin")
except Exception as e:
    logger.warning(f"[ADMIN] SQLAdmin not available: {e}")

# Initialize OpenTelemetry tracing
setup_telemetry(app)

# Initialize Pyroscope continuous profiling (opt-in via
# app_settings.enable_pyroscope). LGTM+P stack, GH #75.
try:
    from services.profiling import setup_pyroscope
    setup_pyroscope()
except Exception as _e:
    logger.debug(f"[PYROSCOPE] setup skipped: {_e}")

# ===== EXCEPTION HANDLERS =====
# Register all exception handlers (centralized in utils.exception_handlers)
register_exception_handlers(app)

# ===== ERROR TRACKING: SENTRY INTEGRATION =====
# Captures exceptions, performance metrics, and error tracking
setup_sentry(app, service_name="cofounder-agent")

# ===== MIDDLEWARE CONFIGURATION =====
# Register all middleware (centralized in utils.middleware_config)
middleware_config = MiddlewareConfig()
middleware_config.register_all_middleware(app)

# ===== ROUTE REGISTRATION =====
# Register API routes based on deployment mode (coordinator or worker)
logger.info("[STARTUP] Registering routes for deployment mode: %s", _deployment_mode)
register_all_routes(app, deployment_mode=_deployment_mode)
logger.info("[STARTUP] ✅ Routes registered (mode=%s)", _deployment_mode)

# ===== UNIFIED HEALTH CHECK ENDPOINT =====
# Consolidated from: /api/health, /status, /metrics/health, and route-specific health endpoints


@app.get("/api/health")
async def api_health():
    """
    Unified health check endpoint for cloud deployment and load balancers.

    Returns comprehensive status of all critical services:
    - Startup status (starting/degraded/healthy)
    - Database connectivity and health
    - Orchestrator initialization and status
    - LLM providers availability
    - Timestamp for monitoring systems

    Used by: Cloud load balancers, monitoring systems, external health checks
    Authentication: Not required (critical for load balancers)
    """
    try:
        # Build comprehensive health response
        health_data = {
            "status": "healthy",
            "service": "cofounder-agent",
            "version": config.app_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }

        # Check startup status
        startup_error = getattr(app.state, "startup_error", None)
        startup_complete = getattr(app.state, "startup_complete", False)

        if startup_error:
            health_data["status"] = "degraded"
            health_data["startup_error"] = startup_error
            health_data["startup_complete"] = startup_complete
            logger.warning(f"Health check returning degraded status: {startup_error}")
        elif not startup_complete:
            health_data["status"] = "starting"
            health_data["startup_complete"] = False

        # Include database status if available
        database_service = getattr(app.state, "database", None)
        if database_service:
            try:
                db_health = await database_service.health_check()
                health_data["components"]["database"] = db_health.get("status", "unknown")
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    "Database health check failed in /api/health: %s", str(e), exc_info=True
                )
                health_data["components"]["database"] = "degraded"
        else:
            health_data["components"]["database"] = "unavailable"

        # Connection pool stats (#140)
        pool_health: ConnectionPoolHealth | None = getattr(app.state, "pool_health", None)
        if database_service and getattr(database_service, "pool", None):
            pool = database_service.pool
            pool_stats = {
                "size": pool.get_size(),
                "idle": pool.get_idle_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
            }
            # Include local pool stats if it's a separate pool
            local_pool = getattr(database_service, "local_pool", None)
            if local_pool and local_pool is not pool:
                pool_stats["local"] = {
                    "size": local_pool.get_size(),
                    "idle": local_pool.get_idle_size(),
                    "min_size": local_pool.get_min_size(),
                    "max_size": local_pool.get_max_size(),
                }
            health_data["components"]["connection_pool"] = pool_stats
            # Flag degraded if pool health monitor reports issues
            if pool_health and pool_health.is_pool_degraded():
                if health_data["status"] == "healthy":
                    health_data["status"] = "degraded"
                health_data["components"]["connection_pool"]["degraded"] = True
            if pool_health and pool_health.is_pool_critical():
                health_data["components"]["connection_pool"]["critical"] = True

        # Include task executor liveness and queue depth (#580)
        task_executor = getattr(app.state, "task_executor", None)
        if task_executor is not None:
            try:
                executor_stats = task_executor.get_stats()
                # Fetch pending/in-progress counts from DB for queue-depth monitoring
                pending_count = 0
                in_progress_count = 0
                if database_service:
                    try:
                        task_counts = await database_service.tasks.get_task_counts()
                        pending_count = getattr(task_counts, "pending", 0)
                        in_progress_count = getattr(task_counts, "in_progress", 0)
                    except Exception:  # pylint: disable=broad-except
                        pass  # Non-critical — executor stats still returned
                health_data["components"]["task_executor"] = {
                    "running": executor_stats.get("running", False),
                    "pending_task_count": pending_count,
                    "in_progress_count": in_progress_count,
                    "total_processed": executor_stats.get("task_count", 0),
                    "success_count": executor_stats.get("success_count", 0),
                    "error_count": executor_stats.get("error_count", 0),
                }
                # Degrade overall status if executor is not running
                # Skip in coordinator mode — executor is intentionally not started there
                if (
                    not executor_stats.get("running", False)
                    and health_data["status"] == "healthy"
                    and _deployment_mode != "coordinator"
                ):
                    health_data["status"] = "degraded"
                    health_data["components"]["task_executor"][
                        "degraded_reason"
                    ] = "executor_not_running"
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Task executor health check failed: %s", str(e), exc_info=True)
                health_data["components"]["task_executor"] = "unavailable"
        else:
            health_data["components"]["task_executor"] = "unavailable"

        # GPU scheduler status (gaming detection)
        try:
            from services.gpu_scheduler import gpu
            health_data["components"]["gpu"] = gpu.status
        except Exception:
            pass

        return health_data
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Health check failed: %s", str(e), exc_info=True)
        return {"status": "unhealthy", "service": "cofounder-agent", "error": "health_check_failed"}


@app.get("/health")
async def health():
    """
    Quick health check endpoint (no dependencies) - for load balancers and monitoring.

    Returns: 200 OK if app is running
    Usage: External load balancers, uptime monitors, basic connectivity checks
    Performance: Instant response (doesn't check database)
    """
    return {"status": "ok", "service": "cofounder-agent"}


# ===== PROMETHEUS METRICS ENDPOINT =====
# Phase D (GitHub #68): Prometheus exposition-format metrics at /metrics.
# Additive — the existing /api/metrics JSON endpoint below is unchanged.
# Once Alertmanager rules are in place and parallel-run confidence is high,
# the brain daemon's probe loop can start deleting functions that now have
# metric counterparts. See services/metrics_exporter.py.


@app.get("/metrics")
async def prometheus_metrics_canonical():
    """Prometheus scrape endpoint (Phase D canonical path).

    Returns exposition-format text via ``services.metrics_exporter``.
    The legacy ``/api/prometheus`` endpoint below hand-builds its own
    output; this one uses ``prometheus_client`` properly. Migration of
    the legacy endpoint's metrics into this exporter is tracked as a
    Phase D follow-up — both endpoints work in parallel meanwhile.
    """
    from fastapi import Response

    from services.metrics_exporter import refresh_metrics, render_exposition

    # app.state.database is the DatabaseService; its .pool is the asyncpg pool.
    db_service = getattr(app.state, "database", None)
    pool = getattr(db_service, "pool", None) if db_service else None
    # Ollama URL: read from app_settings via site_config if wired, else default
    try:
        from services.site_config import site_config
        ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
    except Exception:
        ollama_url = "http://host.docker.internal:11434"

    if pool is not None:
        try:
            await refresh_metrics(pool, ollama_url)
        except Exception as e:
            # Never fail /metrics — Prometheus will alert on "endpoint down"
            # which is not what we want for a refresh hiccup.
            import logging as _logging
            _logging.getLogger(__name__).warning("/metrics refresh failed: %s", e)

    body, content_type = render_exposition()
    return Response(content=body, media_type=content_type)


# ===== METRICS ENDPOINT =====
# Consolidated from: /api/metrics, /metrics, /tasks/metrics, etc.


@app.get("/api/metrics")
async def get_metrics_endpoint():
    """
    Aggregated task and system metrics endpoint.

    Returns comprehensive metrics for the oversight dashboard:
    - Task statistics (total, completed, failed, pending)
    - Success rate percentage
    - Average execution time
    - Estimated costs

    **Returns:**
    - total_tasks: Total number of tasks created
    - completed_tasks: Successfully completed tasks
    - failed_tasks: Failed tasks
    - pending_tasks: Queued or in-progress tasks
    - success_rate: Success percentage (0-100)
    - avg_execution_time: Average task duration in seconds
    - total_cost: Estimated total cost in USD
    """
    try:
        database_service = getattr(app.state, "database", None)
        if database_service:
            metrics = await database_service.get_metrics()
            return metrics

        # Return mock metrics if database unavailable
        return {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "pending_tasks": 0,
            "success_rate": 0.0,
            "avg_execution_time": 0.0,
            "total_cost": 0.0,
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Metrics retrieval failed: %s", str(e), exc_info=True)
        return {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "pending_tasks": 0,
            "success_rate": 0.0,
            "avg_execution_time": 0.0,
            "total_cost": 0.0,
            "error": "metrics_retrieval_failed",
        }


@app.get("/api/prometheus")
async def prometheus_metrics():
    """Prometheus-format metrics for scraping by Prometheus/Grafana."""
    from fastapi.responses import PlainTextResponse
    lines = []

    # GPU scheduler metrics
    try:
        from services.gpu_scheduler import gpu
        s = gpu.status
        lines.append("# HELP poindexter_gpu_gaming_detected Whether gaming/external GPU workload is detected (1=yes)")
        lines.append("# TYPE poindexter_gpu_gaming_detected gauge")
        lines.append(f'poindexter_gpu_gaming_detected {1 if s["gaming_detected"] else 0}')
        lines.append("# HELP poindexter_gpu_gaming_paused_seconds Current gaming pause duration in seconds")
        lines.append("# TYPE poindexter_gpu_gaming_paused_seconds gauge")
        lines.append(f'poindexter_gpu_gaming_paused_seconds {s["gaming_paused_s"]}')
        lines.append("# HELP poindexter_gpu_gaming_paused_total_seconds Total time paused for gaming since worker start")
        lines.append("# TYPE poindexter_gpu_gaming_paused_total_seconds counter")
        lines.append(f'poindexter_gpu_gaming_paused_total_seconds {s["total_gaming_paused_s"]}')
        lines.append("# HELP poindexter_gpu_busy Whether the GPU lock is held by the pipeline")
        lines.append("# TYPE poindexter_gpu_busy gauge")
        lines.append(f'poindexter_gpu_busy {1 if s["busy"] else 0}')
    except Exception:
        pass

    # Connection pool metrics (#140)
    try:
        database_service = getattr(app.state, "database", None)
        if database_service and getattr(database_service, "pool", None):
            pool = database_service.pool
            lines.append("# HELP poindexter_db_pool_size Current number of connections in pool")
            lines.append("# TYPE poindexter_db_pool_size gauge")
            lines.append(f'poindexter_db_pool_size{{pool="cloud"}} {pool.get_size()}')
            lines.append("# HELP poindexter_db_pool_idle Number of idle connections in pool")
            lines.append("# TYPE poindexter_db_pool_idle gauge")
            lines.append(f'poindexter_db_pool_idle{{pool="cloud"}} {pool.get_idle_size()}')
            lines.append("# HELP poindexter_db_pool_min_size Minimum pool size setting")
            lines.append("# TYPE poindexter_db_pool_min_size gauge")
            lines.append(f'poindexter_db_pool_min_size{{pool="cloud"}} {pool.get_min_size()}')
            lines.append("# HELP poindexter_db_pool_max_size Maximum pool size setting")
            lines.append("# TYPE poindexter_db_pool_max_size gauge")
            lines.append(f'poindexter_db_pool_max_size{{pool="cloud"}} {pool.get_max_size()}')
            # Local pool (if separate)
            local_pool = getattr(database_service, "local_pool", None)
            if local_pool and local_pool is not pool:
                lines.append(f'poindexter_db_pool_size{{pool="local"}} {local_pool.get_size()}')
                lines.append(f'poindexter_db_pool_idle{{pool="local"}} {local_pool.get_idle_size()}')
                lines.append(f'poindexter_db_pool_min_size{{pool="local"}} {local_pool.get_min_size()}')
                lines.append(f'poindexter_db_pool_max_size{{pool="local"}} {local_pool.get_max_size()}')
    except Exception:
        pass

    # GH-92: server-wide Postgres connection utilization. The per-pool
    # metrics above show what the *worker* is holding; pg_connections_*
    # show what the whole cluster is holding (all apps + ad-hoc psql).
    # The 80%-utilization Grafana alert fires against these.
    try:
        database_service = getattr(app.state, "database", None)
        pool = getattr(database_service, "pool", None) if database_service else None
        if pool is not None:
            async with pool.acquire() as conn:
                used = await conn.fetchval("SELECT COUNT(*) FROM pg_stat_activity")
                max_conn = await conn.fetchval(
                    "SELECT current_setting('max_connections')::int"
                )
            lines.append("# HELP pg_connections_used Total server-side Postgres backends from pg_stat_activity")
            lines.append("# TYPE pg_connections_used gauge")
            lines.append(f"pg_connections_used {int(used or 0)}")
            lines.append("# HELP pg_connections_max Postgres max_connections server setting")
            lines.append("# TYPE pg_connections_max gauge")
            lines.append(f"pg_connections_max {int(max_conn or 0)}")
    except Exception:
        pass

    # Task executor metrics
    try:
        database_service = getattr(app.state, "database", None)
        if database_service:
            task_counts = await database_service.tasks.get_task_counts()
            lines.append("# HELP poindexter_tasks_pending Number of pending content tasks")
            lines.append("# TYPE poindexter_tasks_pending gauge")
            lines.append(f"poindexter_tasks_pending {getattr(task_counts, 'pending', 0)}")
            lines.append("# HELP poindexter_tasks_in_progress Number of in-progress content tasks")
            lines.append("# TYPE poindexter_tasks_in_progress gauge")
            lines.append(f"poindexter_tasks_in_progress {getattr(task_counts, 'in_progress', 0)}")
            lines.append("# HELP poindexter_tasks_awaiting_approval Number of tasks awaiting approval")
            lines.append("# TYPE poindexter_tasks_awaiting_approval gauge")
            lines.append(f"poindexter_tasks_awaiting_approval {getattr(task_counts, 'awaiting_approval', 0)}")
    except Exception:
        pass

    # Pipeline throttle metrics (GH-89 AC#2).
    # - pipeline_throttle_active : 1 when the approval queue is at/above
    #   max_approval_queue, 0 otherwise. Panel this in Grafana and alert
    #   on `pipeline_throttle_active == 1 for > 15m` to catch silent stalls.
    # - pipeline_throttle_seconds_total : cumulative seconds spent throttled
    #   since worker boot. rate() this in Grafana to see "how much of the
    #   last hour was wasted behind the approval wall".
    try:
        from services.pipeline_throttle import get_state as _throttle_state

        _ts = _throttle_state()
        lines.append("# HELP poindexter_pipeline_throttle_active Approval queue is full and pipeline is not advancing (1=throttled)")
        lines.append("# TYPE poindexter_pipeline_throttle_active gauge")
        lines.append(f"poindexter_pipeline_throttle_active {1 if _ts['active'] else 0}")
        lines.append("# HELP poindexter_pipeline_throttle_seconds_total Cumulative seconds the pipeline spent throttled by the approval queue")
        lines.append("# TYPE poindexter_pipeline_throttle_seconds_total counter")
        lines.append(f"poindexter_pipeline_throttle_seconds_total {_ts['total_seconds']:.3f}")
        lines.append("# HELP poindexter_pipeline_throttle_queue_size Current awaiting_approval queue size as last observed by the throttle check")
        lines.append("# TYPE poindexter_pipeline_throttle_queue_size gauge")
        lines.append(f"poindexter_pipeline_throttle_queue_size {_ts['queue_size']}")
        lines.append("# HELP poindexter_pipeline_throttle_queue_limit Configured max_approval_queue as last observed by the throttle check")
        lines.append("# TYPE poindexter_pipeline_throttle_queue_limit gauge")
        lines.append(f"poindexter_pipeline_throttle_queue_limit {_ts['queue_limit']}")
    except Exception:
        pass

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


class CommandRequest(BaseModel):
    """Request model for processing a command.

    Attributes
    ----------
    command: The command string to be processed by the orchestrator.
    context: Optional context dictionary that can influence command execution.
    """

    command: str
    context: dict[str, Any] | None = None

    @field_validator("command")
    @classmethod
    def _command_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("command must be a non-empty string")
        return v


class CommandResponse(BaseModel):
    """Response model for the result of a command.

    Attributes
    ----------
    response: Human-readable response from the orchestrator.
    task_id: Optional identifier of a background task created by the command.
    metadata: Optional dictionary containing additional data returned by the orchestrator.
    """

    response: str
    task_id: str | None = None
    metadata: dict[str, Any] | None = None


@app.post("/command", response_model=CommandResponse)
async def process_command(
    request: Request,
    command: CommandRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_api_token),
):  # pylint: disable=unused-argument
    """
    Processes a command sent to the Co-Founder agent.

    This endpoint receives a command, delegates it to the orchestrator logic,
    and returns the result. Can optionally execute tasks in the background.
    """
    try:
        logger.info(f"Received command: {command.command}")

        orchestrator = getattr(request.app.state, "orchestrator", None)
        if orchestrator is None:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Execute the command asynchronously
        response = await orchestrator.process_command_async(command.command, command.context)

        return CommandResponse(
            response=response.get("response", "Command processed"),
            task_id=response.get("task_id"),
            metadata=response.get("metadata"),
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            f"Error processing command: {e!s} | command={command.command}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="An internal error occurred") from e


@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {
        "message": f"{_site_cfg.get('site_name', 'App')} AI Co-Founder is running",
        "version": config.app_version,
        "database_enabled": hasattr(app.state, "database") and app.state.database is not None,
    }


if __name__ == "__main__":
    # Watch the entire src directory for changes to support agent development
    # NOTE: Use 'python -m uvicorn main:app --reload' instead of 'python main.py'
    # This file is imported by uvicorn when using the module syntax, so running
    # uvicorn.run() here creates nested server conflicts.
    logger.error("ERROR: Do not run 'python main.py' directly.")
    logger.error("Instead, use:")
    logger.error("  python -m uvicorn main:app --reload --host 0.0.0.0 --port 8002")
    sys.exit(1)
