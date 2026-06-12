"""
Startup Manager - Orchestrates application initialization and shutdown

Handles all startup and shutdown operations for Poindexter (the AI cofounder pipeline):
- Database initialization (PostgreSQL + asyncpg)
- Cache setup (Redis)
- Migrations + module migrations
- Retention janitor + SDXL warmup
- Route service registration
- Graceful shutdown

Task dispatch lives in the Prefect server at ``http://localhost:4200``
(Glad-Labs/poindexter#410). The legacy in-process polling daemon
(``services/task_executor.py``) was deleted in Stage 4 of that cutover
(2026-05-16).
"""

import asyncio
import os
from contextlib import suppress
from pathlib import Path
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

    @staticmethod
    def _scan_syntax_errors(modules_dir) -> list[tuple[str, str]]:
        """Return (path, error_message) for every .py file under modules_dir with a SyntaxError.

        Skips __pycache__ directories. Called by _check_module_syntax() and
        directly in tests.
        """
        errors: list[tuple[str, str]] = []
        checked = 0
        for py_file in sorted(Path(modules_dir).rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            checked += 1
            try:
                source = py_file.read_bytes()
                compile(source, str(py_file), "exec")
            except SyntaxError as exc:
                errors.append((str(py_file), f"{exc.msg} (line {exc.lineno})"))
        logger.debug("[startup] Syntax scan: %d file(s) checked, %d error(s)", checked, len(errors))
        return errors

    def _check_module_syntax(self) -> None:
        """Syntax-check every .py file under modules/ before importing anything.

        Catches git merge conflict markers (<<<<<<< / ======= / >>>>>>>) and
        any other SyntaxErrors introduced into bind-mounted code before uvicorn
        gets a chance to import the broken file and crash-loop with exit code 0.

        Uses py_compile.compile() — pure bytecode compilation, no execution.
        Fails loud: logs the offending file + line, notifies the operator, and
        exits 1 so Docker restart policy kicks in with an obvious error rather
        than a cryptic exit-0 loop.
        """
        import sys as _sys

        modules_dir = Path(__file__).parent.parent / "modules"
        if not modules_dir.is_dir():
            logger.warning("[startup] modules/ dir not found at %s — skipping syntax check", modules_dir)
            return

        errors = self._scan_syntax_errors(modules_dir)

        if not errors:
            logger.info("[startup] Syntax check OK (%d file(s) in modules/)", sum(
                1 for f in modules_dir.rglob("*.py") if "__pycache__" not in f.parts
            ))
            return

        for path, msg in errors:
            logger.critical("[startup] SYNTAX ERROR in %s: %s", path, msg)

        detail_lines = "\n".join(f"  {p}: {m}" for p, m in errors)
        detail = (
            f"{len(errors)} syntax error(s) found in modules/ at startup:\n{detail_lines}\n\n"
            "Most likely cause: unresolved git merge conflict markers (<<<<<<< / ======= / >>>>>>>).\n"
            "Fix: resolve the conflict in the host checkout and restart the worker."
        )

        try:
            _here = Path(__file__).resolve()
            for _candidate in list(_here.parents) + [Path("/opt/poindexter")]:
                if (_candidate / "brain").is_dir():
                    if str(_candidate) not in _sys.path:
                        _sys.path.insert(0, str(_candidate))
                    break
            from brain.operator_notifier import notify_operator

            notify_operator(
                title=f"Worker cannot start — {len(errors)} syntax error(s) in modules/",
                detail=detail,
                source="worker.startup_manager",
                severity="critical",
            )
        except Exception as notify_err:
            logger.error("[startup] operator_notifier failed: %s", notify_err)

        _sys.exit(1)

    async def initialize_all_services(self) -> dict[str, Any]:
        """
        Initialize all services in sequence.

        Returns dict with all initialized services:
        {
            'database': DatabaseService,
            'redis_cache': RedisCache,
            'startup_error': str | None,
        }

        Task dispatch is owned by the Prefect server (Glad-Labs/poindexter#410);
        the legacy in-process ``TaskExecutor`` polling daemon was deleted
        in Stage 4 of that cutover (2026-05-16), so no executor is
        constructed or returned here.
        """
        try:
            logger.info("🚀 Starting Poindexter application...")
            logger.info(f"  Environment: {os.getenv('ENVIRONMENT', 'production')}")

            # Step 0: Validate secrets before any heavy initialization
            self._validate_secrets()

            # Step 0.5: Syntax-check modules/ before any imports — catches merge
            # conflict markers that cause exit-0 crash-loops (glad-labs-stack#621).
            self._check_module_syntax()

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

            # Step 6: (#410) Task dispatch lives in Prefect now — nothing
            # to start in-process. The Prefect deployment is registered
            # by ``scripts/deploy_content_flow.py`` against the local
            # Prefect server at http://localhost:4200.
            logger.info("  task dispatch: prefect (http://localhost:4200)")

            # Step 7: Verify connections
            await self._verify_connections()

            # Step 10: Register services with routes
            await self._register_route_services()

            # Step 10b: Validate *_model / cost_tier.*.model settings against
            # installed Ollama models (glad-labs-stack#1284). Best-effort --
            # never aborts startup; only alerts the operator.
            if self.database_service and self.database_service.pool:
                try:
                    await self._validate_ollama_model_settings(
                        self.database_service.pool
                    )
                except Exception as vm_err:
                    logger.warning(
                        "[startup] Ollama model validation raised unexpectedly: %s",
                        vm_err, exc_info=True,
                    )

            # Step 13b: Start retention janitor (internal tracker Phase 4.1) —
            # periodically prunes unbounded high-churn tables. Runs in the
            # background; retention windows configurable per table via
            # app_settings.retention_days__<table>.
            try:
                # SiteConfig DI migration (#272 leaf batch 3): retention_janitor
                # is now a ``RetentionJanitor`` class. Build one per-call from
                # the lifespan-bound SiteConfig (caller-bridge) until
                # startup_manager itself reaches for the AppContainer.
                from services.retention_janitor import RetentionJanitor
                if self.database_service and self.database_service.pool:
                    _janitor = RetentionJanitor(site_config=self._site_config)
                    asyncio.create_task(
                        _janitor.run_forever(self.database_service.pool),
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
            # #272 Phase-2g: DatabaseService takes a REQUIRED site_config.
            # Pass the lifespan-bound instance threaded into this manager —
            # it's still empty here (loaded in-place later by
            # ``site_config.load(pool)`` in main.py's lifespan), so the
            # pool-size reads in ``initialize()`` use defaults exactly as
            # before. A bare-boot path with no injected SiteConfig falls
            # back to a fresh env-fallback instance.
            from services.site_config import SiteConfig
            self.database_service = DatabaseService(
                local_database_url=config.local_database_url,
                site_config=self._site_config or SiteConfig(),
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

            await run_migrations(self.database_service)
            logger.info("   [OK] Database migrations completed successfully")
        except Exception as e:
            startup_error = f"FATAL: Database migration failed: {e!s}"
            logger.error(f"  {startup_error}", exc_info=True)
            try:
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
                    title="Worker cannot start — database migration failed",
                    detail=(
                        f"{startup_error}\n\n"
                        "A failed migration is NOT recorded in schema_migrations so it "
                        "will be retried on next startup. Fix the migration, then restart "
                        "the worker.\n\n"
                        "Review logs for the full traceback."
                    ),
                    source="worker.startup_manager",
                    severity="critical",
                )
            except Exception as notify_err:
                logger.error(
                    "  operator_notifier failed: %s", notify_err, exc_info=True
                )
            raise SystemExit(1) from e

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
            from services.module_runner import run_module_migrations

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
        """Initialize Redis cache for query optimization.

        Constructed via :meth:`RedisCache.create` with the injected
        SiteConfig (2026-05-28 DI migration — RedisCache no longer reads
        a module-level singleton). If the StartupManager was instantiated
        without a SiteConfig (early-boot / bare-test path), build a fresh
        env-fallback instance so create() still gets a valid dependency.
        """
        logger.info("  [INFO] Initializing Redis cache for query optimization...")
        try:
            from services.redis_cache import RedisCache
            from services.site_config import SiteConfig

            sc = self._site_config if self._site_config is not None else SiteConfig()
            self.redis_cache = await RedisCache.create(site_config=sc)
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
        """DEPRECATED: Content critique runs as a stage inside the
        Prefect content_generation_flow via UnifiedQualityService."""
        logger.debug(
            "⏭️  Skipping _initialize_content_critique (now handled by UnifiedQualityService)"
        )

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

    async def _validate_ollama_model_settings(self, pool: Any) -> None:
        """Validate *_model and cost_tier.*.model settings against installed Ollama models.

        Reads every ``app_settings`` key matching ``*_model`` or
        ``cost_tier.*.model``, then:

        1. Fetches the installed model list from Ollama (``GET /api/tags``).
        2. For each configured model value: strips an ``ollama/`` prefix if
           present (the DB stores ``ollama/gemma3:27b``; Ollama reports
           ``gemma3:27b``).
        3. Warns if the model is not in the installed list.
        4. Fetches ``POST /api/show`` for installed models and checks for
           suspicious chat-template tokens (``<|turn>``, ``<turn|>``,
           ``<|im_turn|>``) without any established delimiter pattern
           (``<start_of_turn>``, ``<|im_start|>``, ``[INST]``, ``<|user|>``).
        5. If any model is uninstalled or has a suspect template, calls
           :func:`notify_operator` to alert via Discord/Telegram.

        Gated by ``ollama_model_validation_enabled`` (default ``true``).
        Never hard-fails -- startup continues even when Ollama is unreachable.

        Root cause for which this was added: ``cost_tier.standard.model`` was
        set to ``gemma-4-31B-it-qat:latest`` with a malformed Modelfile
        template using ``<|turn>`` pseudo-tokens (should be
        ``<start_of_turn>``) that caused reasoning-channel bleed into all
        canonical_blog drafts for hours (2026-06-09 incident).
        Glad-Labs/poindexter#1284.
        """
        sc = self._site_config
        if sc is None:
            logger.debug("[model_validator] No SiteConfig -- skipping")
            return

        enabled = sc.get("ollama_model_validation_enabled", "true")
        if enabled.lower() not in ("true", "1", "yes"):
            logger.debug("[model_validator] Disabled via ollama_model_validation_enabled")
            return

        ollama_base_url = sc.get(
            "ollama_base_url", "http://host.docker.internal:11434"
        ).rstrip("/")
        logger.info("[model_validator] Validating model settings against %s", ollama_base_url)

        # ------------------------------------------------------------------ #
        # Collect configured model values                                     #
        # ------------------------------------------------------------------ #
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT key, value FROM app_settings"
                    " WHERE (key LIKE '%_model' OR key LIKE 'cost_tier.%.model')"
                    " AND value IS NOT NULL AND value != ''"
                    " ORDER BY key"
                )
        except Exception as db_err:
            logger.warning("[model_validator] DB query failed: %s", db_err)
            return

        if not rows:
            logger.debug("[model_validator] No *_model / cost_tier.*.model keys found")
            return

        # key -> raw value (may include ollama/ prefix)
        configured: dict[str, str] = {row["key"]: row["value"] for row in rows}
        logger.debug("[model_validator] %d model key(s) to validate", len(configured))

        # ------------------------------------------------------------------ #
        # Fetch installed Ollama models                                       #
        # ------------------------------------------------------------------ #
        import httpx as _httpx
        from services.integrations.operator_notify import notify_operator

        # Prefer the lifespan-bound shared client; create a per-call client
        # only when one has not been wired yet (early-boot / tests).
        from services.integrations import operator_notify as _on_mod
        _shared = getattr(_on_mod, "http_client", None)

        installed_names: set[str] = set()
        tags_url = f"{ollama_base_url}/api/tags"

        try:
            if _shared is not None:
                resp = await _shared.get(tags_url, timeout=10.0)
            else:
                async with _httpx.AsyncClient(timeout=10.0) as _cli:
                    resp = await _cli.get(tags_url)
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("models", []):
                name = m.get("name", "")
                if name:
                    installed_names.add(name)
            logger.debug(
                "[model_validator] Ollama reports %d installed model(s)",
                len(installed_names),
            )
        except Exception as reach_err:
            msg = (
                f"[model_validator] Ollama unreachable at {tags_url}: {reach_err}\n"
                "Cannot validate model settings -- check that Ollama is running."
            )
            logger.warning(msg)
            try:
                await notify_operator(msg)
            except Exception:
                pass
            return

        # ------------------------------------------------------------------ #
        # Suspicious template tokens (chat-template quality check)           #
        # ------------------------------------------------------------------ #
        _SUSPECT_TOKENS = ("<|turn>", "<turn|>", "<|im_turn|>")
        _ESTABLISHED_DELIMITERS = (
            "<start_of_turn>", "<|im_start|>", "[INST]", "<|user|>"
        )

        async def _fetch_template(model_name: str):
            """Return the raw Modelfile template string for an installed model."""
            show_url = f"{ollama_base_url}/api/show"
            try:
                payload = {"name": model_name}
                if _shared is not None:
                    r = await _shared.post(show_url, json=payload, timeout=15.0)
                else:
                    async with _httpx.AsyncClient(timeout=15.0) as _cli:
                        r = await _cli.post(show_url, json=payload)
                r.raise_for_status()
                d = r.json()
                return d.get("template") or ""
            except Exception as te:
                logger.debug(
                    "[model_validator] /api/show failed for %r: %s", model_name, te
                )
                return None

        # ------------------------------------------------------------------ #
        # Validate each configured model                                      #
        # ------------------------------------------------------------------ #
        missing_models: list[str] = []
        suspect_models: list[tuple] = []  # (model, reason)

        for key, raw_value in configured.items():
            # Strip the ollama/ prefix that the DB uses but Ollama itself does not
            model_name = raw_value.strip()
            if model_name.startswith("ollama/"):
                model_name = model_name[len("ollama/"):]

            if not model_name:
                continue

            # Skip non-Ollama model references (e.g. "openai/gpt-4o") --
            # only validate Ollama-destined values.
            raw_lower = raw_value.lower()
            non_ollama_prefixes = (
                "openai/", "anthropic/", "gemini/",
                "groq/", "fireworks/", "together/",
            )
            if (
                not raw_value.startswith("ollama/")
                and any(p in raw_lower for p in non_ollama_prefixes)
            ):
                logger.debug(
                    "[model_validator] Skipping non-Ollama model key=%r value=%r",
                    key, raw_value,
                )
                continue

            if model_name not in installed_names:
                missing_models.append(model_name)
                logger.warning(
                    "[model_validator] MISSING: key=%r references model %r "
                    "which is not installed in Ollama",
                    key, model_name,
                )
            else:
                # Model is installed -- check its chat template
                template = await _fetch_template(model_name)
                if template is None:
                    continue
                has_suspect = any(tok in template for tok in _SUSPECT_TOKENS)
                has_established = any(delim in template for delim in _ESTABLISHED_DELIMITERS)
                if has_suspect and not has_established:
                    suspect_toks = [t for t in _SUSPECT_TOKENS if t in template]
                    reason = f"template uses {suspect_toks} without established delimiters"
                    suspect_models.append((model_name, reason))
                    logger.warning(
                        "[model_validator] SUSPECT TEMPLATE: key=%r model=%r -- %s",
                        key, model_name, reason,
                    )

        # ------------------------------------------------------------------ #
        # Notify operator if anything is wrong                                #
        # ------------------------------------------------------------------ #
        if missing_models or suspect_models:
            lines = ["**Ollama model validation warning at startup:**"]
            if missing_models:
                lines.append(f"Missing (not installed): {', '.join(missing_models)}")
            if suspect_models:
                for sm, sr in suspect_models:
                    lines.append(f"Suspect template -- {sm}: {sr}")
            lines.append(
                "Fix: `ollama pull <model>` for missing models, or correct the "
                "Modelfile template for suspect ones. Then update the relevant "
                "app_settings *_model / cost_tier.*.model keys."
            )
            msg = "\n".join(lines)
            logger.warning("[model_validator] %s", msg)
            try:
                await notify_operator(msg)
            except Exception as ne:
                logger.warning("[model_validator] notify_operator failed: %s", ne)
        else:
            logger.info(
                "[model_validator] All %d configured model(s) validated OK",
                len(configured),
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
            from services.site_config import SiteConfig

            # Create image service
            image_service = ImageService(sc or SiteConfig())

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
        logger.info("  - Task dispatch: prefect (http://localhost:4200)")
        logger.info(f"  - Startup Error: {self.startup_error}")

    async def shutdown(self) -> None:
        """Gracefully shutdown all services.

        Task dispatch lives in Prefect (Glad-Labs/poindexter#410); the
        in-process polling daemon was deleted in Stage 4 (2026-05-16),
        so there's nothing to stop here for dispatch. Prefect's own
        worker subprocess shuts down with its container.
        """
        try:
            logger.info("[STOP] Shutting down Poindexter application...")

            # Cancel long-running background tasks (e.g. the connection-pool
            # health monitor started in ``_initialize_database``) BEFORE we
            # close the pool — otherwise an in-flight ``check_pool_health``
            # acquire would race the pool teardown. A task that is never
            # cancelled here is a real prod leak (``auto_health_check`` is an
            # infinite ``while True`` loop) and shows up in test runs as
            # "Task was destroyed but it is pending!" (Glad-Labs/poindexter#997).
            await self._cancel_background_tasks()

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

    async def _cancel_background_tasks(self) -> None:
        """Cancel and await every long-running background task.

        ``_background_tasks`` holds strong refs to tasks like the
        ConnectionPoolHealth monitor (``auto_health_check``) so asyncio's
        weakref tracking doesn't GC them mid-loop (ruff RUF006). They are
        infinite loops, so they must be explicitly cancelled at shutdown or
        they leak past the event loop's lifetime. Each task's
        ``add_done_callback(self._background_tasks.discard)`` mutates the set
        as it completes, so we snapshot it first.
        """
        if not self._background_tasks:
            return

        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        logger.info("  Cancelled %d background task(s)", len(tasks))
