"""
Startup Manager - Orchestrates application initialization and shutdown

Handles all startup and shutdown operations for Poindexter (the AI cofounder pipeline):
- Database initialization (PostgreSQL + asyncpg)
- Cache setup (Redis)
- Model consolidation service
- Orchestrator initialization
- Workflow history service
- Intelligent orchestrator
- Content critique loop
- Background task executor
- Route service registration
- Graceful shutdown
"""

import asyncio
import os
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


class StartupManager:
    """Manages all startup and shutdown operations for the FastAPI application"""

    def __init__(self, site_config=None):
        """Initialize startup manager with empty service references.

        Args:
            site_config: SiteConfig instance — threaded from main.py lifespan
                into every sub-service that needs DB-backed config at startup
                (Redis cache, retention janitor, SDXL warmup). Phase H (GH#95)
                dropped the transitional module-singleton imports in favour
                of this single construction site. Defaults to None so any
                test that constructs StartupManager() bare still works.
        """
        self._site_config = site_config
        self.database_service = None
        self.redis_cache = None
        self.task_executor = None
        self.startup_error = None
        # Hold strong refs to long-running background tasks so asyncio's
        # weakref tracking doesn't GC them mid-loop. (ruff RUF006)
        self._background_tasks: set = set()

    def _validate_secrets(self) -> None:
        """Check that secrets have been set (auto-generated or explicit).

        Config.__init__ auto-generates secrets when missing/placeholder and
        writes them to os.environ, so by the time this runs all secrets should
        have real values. This method just logs confirmation.
        """
        _DEFAULTS = {
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
            "JWT_SECRET": "development-secret-key-change-in-production",
            "SECRET_KEY": "your-secret-key-here",
            "REVALIDATE_SECRET": "dev-secret-key",
        }
        violations = []
        for var, default_value in _DEFAULTS.items():
            actual = os.getenv(var, "")
            if not actual or actual == default_value:
                violations.append(var)
        if violations:
            # This shouldn't happen if get_config() ran first, but log just in case
            logger.warning(
                f"[startup] Secrets still at default/empty (should have been auto-generated): "
                f"{', '.join(violations)}"
            )
        else:
            logger.info("[startup] All secrets validated OK")

    async def initialize_all_services(self) -> dict[str, Any]:
        """
        Initialize all services in sequence.

        Returns dict with all initialized services:
        {
            'database': DatabaseService,
            'task_executor': TaskExecutor,
        }
        """
        try:
            logger.info("🚀 Starting Poindexter application...")
            logger.info(f"  Environment: {os.getenv('ENVIRONMENT', 'production')}")

            # Step 0: Validate secrets before any heavy initialization
            self._validate_secrets()

            # Step 1: Initialize PostgreSQL database (MANDATORY)
            await self._initialize_database()

            # Step 2: Run migrations
            await self._run_migrations()

            # Step 3: Setup Redis cache
            await self._setup_redis_cache()

            # Step 4: (v2.4) ModelConsolidationService removed — LLM access
            # now flows through the plugin registry (OllamaNativeProvider +
            # OpenAICompatProvider). Nothing to initialize at startup.

            # Step 5: Initialize content critique loop
            await self._initialize_content_critique()

            # Step 6: Initialize background task executor
            await self._initialize_task_executor()

            # Step 7: Verify connections
            await self._verify_connections()

            # Step 10: Register services with routes
            await self._register_route_services()

            # Step 13b: Start retention janitor (gitea#271 Phase 4.1) —
            # periodically prunes unbounded high-churn tables. Runs in the
            # background; retention windows configurable per table via
            # app_settings.retention_days__<table>.
            try:
                from services.retention_janitor import run_forever as _retention_loop
                if self.database_service and self.database_service.pool:
                    asyncio.create_task(
                        _retention_loop(
                            self.database_service.pool,
                            site_config=self._site_config,
                        ),
                        name="retention_janitor",
                    )
                    logger.info("[retention_janitor] Started background loop")
            except Exception as rj_err:
                logger.warning(
                    "[retention_janitor] Failed to start: %s", rj_err,
                )

            # Step 14: Warmup SDXL models (async, non-blocking)
            # Only if GPU is available - this prevents timeout issues when users first request SDXL
            try:
                await self._warmup_sdxl_models()
            except Exception as e:
                import traceback

                logger.warning(
                    f"[WARNING] SDXL warmup failed (non-critical): {type(e).__name__}: {e}",
                    exc_info=True,
                )
                logger.debug(f"    Traceback: {traceback.format_exc()}")
                # Continue anyway - SDXL will load lazily when first used

            logger.info(" Application started successfully!")
            self._log_startup_summary()

            return {
                "database": self.database_service,
                "redis_cache": self.redis_cache,
                "task_executor": self.task_executor,
                "startup_error": self.startup_error,
            }

        except SystemExit:
            raise  # Re-raise SystemExit to stop startup
        except Exception as e:
            self.startup_error = f"Critical startup failure: {e!s}"
            logger.error(f" {self.startup_error}", exc_info=True)
            raise

    async def _initialize_database(self) -> None:
        """Initialize PostgreSQL database connection with retry.

        During `docker compose pull/up`, Postgres and the worker can
        restart concurrently — the worker's first connect attempt can
        lose the race and fail. Retry with exponential backoff for up
        to ~30 seconds before notifying the operator (#198 follow-up).
        """
        logger.info("  Connecting to PostgreSQL (REQUIRED)...")
        import asyncio

        max_attempts = 5  # 1 + 2 + 4 + 8 + 16 = 31s max backoff
        backoff_s = 1.0

        try:
            from config import get_config
            from services.database_service import DatabaseService

            config = get_config()
            self.database_service = DatabaseService(
                local_database_url=config.local_database_url,
            )

            for attempt in range(1, max_attempts + 1):
                try:
                    await self.database_service.initialize()
                    break
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    logger.warning(
                        "  PostgreSQL connect attempt %d/%d failed (%s) — "
                        "retrying in %.0fs", attempt, max_attempts, e, backoff_s,
                    )
                    await asyncio.sleep(backoff_s)
                    backoff_s *= 2

            logger.info("   PostgreSQL connected (pool + 5 delegate modules ready)")

            # Start connection pool health monitor if pool is available
            if self.database_service.pool is not None:
                try:
                    from utils.connection_health import ConnectionPoolHealth

                    pool_monitor = ConnectionPoolHealth(self.database_service.pool)
                    import asyncio

                    task = asyncio.create_task(pool_monitor.auto_health_check())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                    logger.info("   ConnectionPoolHealth monitor started")
                except Exception as monitor_err:
                    logger.warning(
                        f"  ConnectionPoolHealth monitor failed to start: {monitor_err}",
                        exc_info=True,
                    )
        except Exception as e:
            startup_error = f"FATAL: PostgreSQL connection failed: {e!s}"
            logger.error(f"  {startup_error}", exc_info=True)
            logger.error("  [FATAL] PostgreSQL is REQUIRED - cannot continue", exc_info=True)

            # Notify the operator via every channel we have (Telegram, Discord,
            # alerts.log, stderr) before exiting. Import locally so a broken
            # notifier doesn't prevent the logger output above. (#198)
            try:
                # brain/ is either a sibling of the repo (host) or mounted at
                # /opt/poindexter/brain (worker container). Walk up __file__
                # for the host layout; fall back to /opt/poindexter for the
                # container. Parents[3] from /app/utils/ overshoots the
                # filesystem root and raises IndexError in the container —
                # that was silently breaking the operator notifier exactly
                # in the scenario it was meant to cover (DB down at startup).
                import sys as _sys
                from pathlib import Path as _Path

                _here = _Path(__file__).resolve()
                for _candidate in list(_here.parents) + [_Path("/opt/poindexter")]:
                    if (_candidate / "brain").is_dir():
                        if str(_candidate) not in _sys.path:
                            _sys.path.insert(0, str(_candidate))
                        break
                from brain.operator_notifier import notify_operator

                notify_operator(
                    title="Worker cannot start — database connection failed",
                    detail=(
                        f"{startup_error}\n\n"
                        "Fix: check that Postgres is running and reachable, "
                        "and that DATABASE_URL (or DATABASE_HOST/USER/...) is "
                        "set correctly.\n\n"
                        "For local dev the DSN usually looks like:\n"
                        "  postgresql://poindexter:<password>@localhost:15432/poindexter_brain"
                    ),
                    source="worker.startup_manager",
                    severity="critical",
                )
            except Exception as notify_err:
                logger.error(
                    "  operator_notifier failed: %s", notify_err, exc_info=True
                )
            raise SystemExit(1) from e

    async def _run_migrations(self) -> None:
        """Run database migrations, then seed any missing app_settings defaults.

        ``seed_all_defaults`` runs AFTER ``run_migrations`` so any keys
        the migrations explicitly seeded keep their migration value
        (the seeder uses ``ON CONFLICT DO NOTHING``). Closes the
        fresh-DB gap documented in #379 — out of the box every
        ``site_config.get(key, default)`` call site has a real DB row
        to read instead of falling through to the inline default.
        """
        logger.info("  [INFO] Running database migrations...")
        try:
            from services.migrations import run_migrations

            migrations_ok = await run_migrations(self.database_service)
            if migrations_ok:
                logger.info("   [OK] Database migrations completed successfully")
            else:
                logger.warning("   [WARNING] Database migrations failed (proceeding anyway)")
        except Exception as e:
            logger.warning(
                f"   [WARNING] Migration error: {e!s} (proceeding anyway)", exc_info=True
            )

        # Seed any code-side defaults that migrations didn't cover (#379).
        # Best-effort — a failure here doesn't abort startup; the lazy
        # SettingsService default path still works as a fallback.
        try:
            from services.settings_defaults import seed_all_defaults

            if self.database_service and self.database_service.pool:
                inserted = await seed_all_defaults(self.database_service.pool)
                if inserted:
                    logger.info(
                        "   [OK] settings_defaults seeded %d missing app_settings key(s)",
                        inserted,
                    )
                else:
                    logger.debug(
                        "   [INFO] settings_defaults: no missing keys to seed"
                    )
        except Exception as e:
            logger.warning(
                f"   [WARNING] settings_defaults seed failed: {e!s} "
                "(falling back to lazy defaults)",
                exc_info=True,
            )

        # Module v1 Phase 2 — per-module migrations. Substrate migrations
        # (including the module_schema_migrations table itself) have
        # already run above; now walk every registered Module and apply
        # its own. Best-effort: a module migration failure logs +
        # continues. Blast radius of one broken module's migration is
        # one module.
        try:
            from pathlib import Path

            from plugins.registry import get_modules
            from services.module_migrations import run_module_migrations

            modules = get_modules()
            if not modules:
                logger.debug("   [INFO] module_migrations: no modules registered")
            else:
                pool = self.database_service.pool if self.database_service else None
                if pool is None:
                    logger.warning(
                        "   [WARNING] module_migrations: no pool — skipping"
                    )
                else:
                    for mod in modules:
                        try:
                            manifest = mod.manifest()
                            mod_name = manifest.name
                            # Discovery: prefer an explicit migrations_dir
                            # attr (test hook), then fall back to
                            # <package>/migrations/ next to the module
                            # source.
                            migrations_dir = getattr(mod, "migrations_dir", None)
                            if migrations_dir is None:
                                import sys
                                mod_pkg = sys.modules.get(type(mod).__module__)
                                pkg_file = getattr(mod_pkg, "__file__", None) if mod_pkg else None
                                if pkg_file:
                                    migrations_dir = Path(pkg_file).parent / "migrations"
                            if migrations_dir is None:
                                logger.info(
                                    "   [INFO] module_migrations: %s — "
                                    "no migrations/ resolvable, skipping",
                                    mod_name,
                                )
                                continue
                            result = await run_module_migrations(
                                pool, mod_name, Path(migrations_dir),
                            )
                            logger.info(
                                "   [OK] module_migrations: %s — "
                                "applied=%d skipped=%d failed=%d",
                                mod_name, result.applied, result.skipped,
                                result.failed,
                            )
                        except Exception as inner:
                            logger.warning(
                                "   [WARNING] module_migrations: module "
                                "%r failed — %s",
                                mod, inner, exc_info=True,
                            )
        except Exception as e:
            logger.warning(
                f"   [WARNING] module_migrations bootstrap error: {e!s} "
                "(proceeding anyway)",
                exc_info=True,
            )

        # ContentTaskStore: no longer a singleton (Phase G1). Routes that
        # need a store instance construct one inline via Depends(db).

        # Initialize JWT blocklist service (issue #721 — server-side token invalidation)
        try:
            from services.jwt_blocklist_service import jwt_blocklist

            await jwt_blocklist.initialize(self.database_service.pool)
            # Purge any expired rows carried over from previous runs
            await jwt_blocklist.cleanup()
            logger.info("   [OK] JWT blocklist service initialized")
        except Exception as e:
            logger.warning(f"   [WARNING] JWT blocklist init failed: {e!s}", exc_info=True)

    async def _setup_redis_cache(self) -> None:
        """Initialize Redis cache for query optimization"""
        logger.info("  [INFO] Initializing Redis cache for query optimization...")
        try:
            from services.redis_cache import RedisCache

            self.redis_cache = await RedisCache.create()
            if self.redis_cache._enabled:
                logger.info(
                    "   [OK] Redis cache initialized (query performance optimization enabled)"
                )
            else:
                logger.info(
                    "   [INFO] Redis cache not available (system will continue without caching)"
                )
        except Exception as e:
            logger.warning(
                f"   [WARNING] Redis cache error: {e!s} (continuing without cache)",
                exc_info=True,
            )

    async def _initialize_content_critique(self) -> None:
        """DEPRECATED: Content critique is now handled by UnifiedQualityService in TaskExecutor"""
        logger.debug(
            "⏭️  Skipping _initialize_content_critique (now handled by UnifiedQualityService)"
        )

    async def _initialize_task_executor(self) -> None:
        """Initialize background task executor (WITHOUT starting it yet).

        We create the TaskExecutor but do NOT call .start() here. main.py's
        lifespan handler starts it once the rest of the app state is wired.
        """
        logger.info("  ⏳ Initializing background task executor (start deferred)...")
        try:
            from services.task_executor import TaskExecutor

            self.task_executor = TaskExecutor(
                database_service=self.database_service,
                poll_interval=5,
            )
            logger.info("   Background task executor initialized (not started yet)")
        except Exception as e:
            error_msg = f"Task executor initialization failed: {e!s}"
            logger.error(f"   {error_msg}", exc_info=True)
            # Don't fail startup - task processing is optional
            self.task_executor = None

    async def _verify_connections(self) -> None:
        """Verify all connections are healthy"""
        if self.database_service:
            try:
                logger.info("  🔍 Verifying database connection...")
                health = await self.database_service.health_check()
                if health.get("status") == "healthy":
                    logger.info("   Database health check passed")
                else:
                    logger.warning(f"   Database health check returned: {health}")
            except Exception as e:
                logger.warning(f"   Database health check failed: {e}", exc_info=True)

    async def _register_route_services(self) -> None:
        """Register database service with all route modules (deprecated - now using dependency injection)"""
        # Service injection is now handled via Depends(get_database_dependency) in routes
        # This method is kept for backward compatibility but no longer performs any operations
        if self.database_service:
            logger.debug(
                "   Database service available via dependency injection (get_database_dependency)"
            )

    async def _warmup_sdxl_models(self) -> None:
        """Warmup SDXL models to avoid timeout on first request.

        Disabled by default — SDXL loads lazily on first image generation
        request. Enable with ENABLE_SDXL_WARMUP=true if you want faster
        first-image response at the cost of 20-30s slower startup.
        """
        import os

        # Skip warmup unless explicitly enabled (lazy loading is the default).
        # Uses the DI-seam'd SiteConfig from the constructor (glad-labs-stack#330).
        sc = self._site_config
        warmup_flag = sc.get("enable_sdxl_warmup", "") if sc is not None else ""
        if warmup_flag.lower() not in ("true", "1", "yes"):
            logger.info(
                "  SDXL warmup: Skipped (lazy loading on first request). Set ENABLE_SDXL_WARMUP=true to pre-load."
            )
            return

        # Check if torch is even available (optional dependency for SDXL)
        try:
            import torch
        except ModuleNotFoundError:
            logger.info("  SDXL warmup: torch not installed - SDXL disabled")
            logger.info("     To enable SDXL: pip install -r scripts/requirements-ml.txt")
            return

        # Skip warmup if GPU is not available (SDXL only works on GPU)
        if not torch.cuda.is_available():
            logger.debug(
                "  SDXL warmup: GPU not available, skipping model warmup (lazy loading enabled)"
            )
            return

        try:
            logger.info("  🎨 Warming up SDXL models (this may take 20-30 seconds)...")
            import tempfile

            from services.image_service import ImageService

            # Create image service
            image_service = ImageService()

            # Generate a minimal test image just to load the models
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                output_path = tmp.name

            try:
                # Single-step generation just to load models
                success = await image_service.generate_image(
                    prompt="warmup",
                    output_path=output_path,
                    num_inference_steps=1,
                    guidance_scale=7.5,
                )

                if success:
                    logger.info(
                        "  [OK] SDXL models loaded successfully! First requests will be fast."
                    )
                else:
                    logger.warning(
                        "  [WARNING] SDXL warmup generation failed (will initialize lazily)"
                    )

            finally:
                # Clean up temp file
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except OSError as e:
                    logger.debug(f"  [DEBUG] Temp file cleanup failed (non-critical): {e!s}")

        except Exception as e:
            import traceback

            logger.warning(
                f"  [WARNING] SDXL warmup error (non-critical): {type(e).__name__}: {e}",
                exc_info=True,
            )
            logger.warning(f"     Full traceback:\n{traceback.format_exc()}", exc_info=True)
            logger.info("     SDXL will initialize on first request")

    def _log_startup_summary(self) -> None:
        """Log summary of startup state"""
        logger.info(f"  - Database Service: {self.database_service is not None}")
        logger.info(
            f"  - Redis Cache: {self.redis_cache is not None and self.redis_cache._enabled}"
        )
        logger.info(
            f"  - Task Executor: {self.task_executor is not None and self.task_executor.running}"
        )
        logger.info(f"  - Startup Error: {self.startup_error}")

    async def shutdown(self) -> None:
        """Gracefully shutdown all services"""
        try:
            logger.info("[STOP] Shutting down Poindexter application...")

            # Stop background task executor
            try:
                if self.task_executor and self.task_executor.running:
                    logger.info("  Stopping background task executor...")
                    await self.task_executor.stop()
                    logger.info("   Task executor stopped")
                    stats = self.task_executor.get_stats()
                    logger.info(
                        f"     Tasks processed: {stats.get('task_count', 0)}, "
                        f"Success: {stats.get('success_count', 0)}, Failed: {stats.get('error_count', 0)}"
                    )
            except Exception as e:
                logger.error(f"   Error stopping task executor: {e}", exc_info=True)

            # Close Redis connection
            if self.redis_cache:
                try:
                    logger.info("  Closing Redis cache connection...")
                    await self.redis_cache.close()
                    logger.info("   Redis cache connection closed")
                except Exception as e:
                    logger.error(f"   Error closing Redis cache: {e}", exc_info=True)

            # (v2.8) HuggingFace client cleanup block removed — the HF path
            # is gone per the no-paid-APIs policy, so there are no sessions
            # to close. The shutdown tests stopped patching the import too.

            # Close database connection
            if self.database_service:
                try:
                    logger.info("  Closing database connection...")
                    await self.database_service.close()
                    logger.info("   Database connection closed")
                except Exception as e:
                    logger.error(f"   Error closing database: {e}", exc_info=True)

            logger.info(" Application shut down successfully!")

        except Exception as e:
            logger.error(f" Error during shutdown: {e}", exc_info=True)
