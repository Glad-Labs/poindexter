"""
Startup Manager - Orchestrates application initialization and shutdown

Handles all startup and shutdown operations for Poindexter (the AI cofounder pipeline):
- Database initialization (PostgreSQL + asyncpg)
- Migrations + module migrations
- Settings load (app_settings into the injected SiteConfig, before any step reads it)
- Cache setup (Redis)
- Retention janitor
- Route service registration
- Graceful shutdown

Task dispatch lives in the Prefect server at ``http://localhost:4200``
(Glad-Labs/poindexter#410). The legacy in-process polling daemon
(``services/task_executor.py``) was deleted in Stage 4 of that cutover
(2026-05-16).
"""

import asyncio
import os
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

from poindexter.services.logger_config import get_logger
from poindexter.services.site_config import SiteConfig

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Ollama model-setting validation helpers (Glad-Labs/poindexter#941)           #
#                                                                              #
# Not every `*_model` app_setting addresses Ollama. The pipeline configures    #
# image-gen, wan, speaches/whisper, a sentence-transformers reranker and the   #
# chatterbox sidecar through identically-shaped keys — and `gpu_model` is not  #
# a model at all, it holds a hardware description. Checking them all against   #
# `/api/tags` produced 15 false positives per boot that buried the one real    #
# finding, so the validator needs to know what it is looking at.               #
# --------------------------------------------------------------------------- #

# Values meaning "decide at runtime", not a model name to look up.
_MODEL_SENTINELS = frozenset({"auto", "default", "none"})

# Bare (no-slash) values whose key addresses a non-Ollama backend. A slash-ed
# value is classified structurally by its provider prefix instead, and a
# HuggingFace revision pin by `_hf_revision_pinned_keys`, so only the remaining
# ambiguous bare ones need naming. Operators extend this via
# `ollama_model_validation_skip_keys` rather than editing code.
# test_ollama_model_validator.py derives every seeded bare `*_model` value and
# fails until its key is named here or in the test's list of bare Ollama keys.
_NON_OLLAMA_MODEL_KEYS = frozenset({
    "gpu_model",                 # hardware description, e.g. "NVIDIA RTX 5090 (32GB VRAM)"
    "image_generation_model",    # image-gen server REGISTRY name, e.g. "z_image_turbo"
    "voice_agent_whisper_model",  # faster-whisper size, e.g. "medium"
    "voice_bridge_stt_model",    # faster-whisper size, e.g. "base.en"
    # Gemini plugin: bare names handed to google.genai, e.g. "gemini-2.5-flash"
    "plugin.llm_provider.gemini.default_model",
    "plugin.llm_provider.gemini.embed_model",  # e.g. "text-embedding-004"
})

# LiteLLM's two spellings of the local Ollama engine: `ollama/` dispatches to
# /api/generate and `ollama_chat/` to /api/chat, which agentic pins need for
# tool calls. Two endpoints of one server with one /api/tags list, so a value
# under either prefix names a model this validator must find. The dispatch
# side treats the same pair as local (llm_providers/coldload_guard.py).
_OLLAMA_PREFIXES = ("ollama/", "ollama_chat/")

# Weights-file suffixes. A value ending in one of these names a checkpoint FILE
# on a sidecar's disk (ComfyUI's `wan2.2_i2v_high_noise_14B_fp8_scaled.
# safetensors`, `qwen_image_2512_fp8_e4m3fn.safetensors`), never an Ollama tag —
# Ollama addresses models as `name:tag` and no tag carries a file extension.
# Structural like the slash rule above, so a new ComfyUI checkpoint key stops
# needing a code change to avoid a false MISSING every boot.
_CHECKPOINT_SUFFIXES = (".safetensors", ".ckpt", ".gguf", ".pt", ".pth", ".bin")

# HuggingFace weight pins (poindexter#879). A `<key>_revision` row pins the
# model named in `<key>` to a Hub commit SHA, and '' means "track upstream
# main", so the row's existence is the signal, not its value. Ollama addresses
# models as `name:tag` and has no revisions, so a pinned key names a Hub model
# loaded by sentence-transformers or a cross-encoder. Nothing else about a bare
# Hub name gives it away: `all-MiniLM-L6-v2` (topic_dedup_embedding_model)
# reads like any Ollama tag, and Ollama's library has an `all-minilm` of its
# own. That key was reported MISSING on every boot until this rule. Derived
# from the table, so the next pinned key needs no code change.
_REVISION_SUFFIX = "_revision"


def _hf_revision_pinned_keys(keys: Iterable[str]) -> frozenset[str]:
    """The ``*_model`` keys a ``*_model_revision`` row pins to a Hub commit."""
    return frozenset(
        key[: -len(_REVISION_SUFFIX)]
        for key in keys
        if key.endswith("_model" + _REVISION_SUFFIX)
    )


def _strip_ollama_prefix(value: str) -> str | None:
    """``value`` without its local-Ollama prefix; ``None`` if it has none.

    ``ollama_chat/qwen2.5:7b`` -> ``qwen2.5:7b``. A bare value and another
    provider's namespace (``anthropic/…``) both return ``None``.
    """
    lowered = value.lower()
    for prefix in _OLLAMA_PREFIXES:
        if lowered.startswith(prefix):
            return value[len(prefix):]
    return None


def _ollama_name_variants(name: str) -> set[str]:
    """Every spelling of ``name`` that refers to the same Ollama model.

    Ollama's ``/api/tags`` always reports an explicit tag, so an untagged
    config value like ``nomic-embed-text`` never string-matches the installed
    ``nomic-embed-text:latest`` even though they are the same model. Compare
    across variants instead of the raw strings.
    """
    name = name.strip()
    if not name:
        return set()
    if ":" in name:
        base, _, tag = name.rpartition(":")
        return {name, base} if tag == "latest" else {name}
    return {name, f"{name}:latest"}


def _is_ollama_model_value(
    key: str,
    value: str,
    *,
    skip_keys: frozenset[str],
    hf_pinned_keys: frozenset[str] = frozenset(),
) -> bool:
    """True when ``value`` is an Ollama model this validator should check.

    Five rules, cheapest first:

    1. Sentinels (``auto``) select a model at runtime; there is nothing to look up.
    2. A value containing ``/`` declares its own namespace. ``ollama/…`` and
       ``ollama_chat/…`` are ours (see ``_OLLAMA_PREFIXES``); anything else is
       another provider (``anthropic/claude-sonnet-5``) or a HuggingFace repo
       (``Systran/faster-whisper-medium``, ``Wan-AI/Wan2.2-TI2V-5B``,
       ``cross-encoder/ms-marco-MiniLM-L-6-v2``). This replaces an allowlist
       of cloud prefixes that could only ever recognise the providers someone
       had already been bitten by. Until 2026-09-28 only ``ollama/`` counted,
       so every ``ollama_chat/`` pin went unchecked.
    3. A value ending in a weights-file suffix is a checkpoint file on a
       sidecar's disk, not an Ollama tag (see ``_CHECKPOINT_SUFFIXES``).
    4. A bare value under a key with a HuggingFace revision pin names a Hub
       model (see ``_hf_revision_pinned_keys``).
    5. Other bare values are ambiguous by inspection, so the KEY decides.
    """
    raw = (value or "").strip()
    if not raw or raw.lower() in _MODEL_SENTINELS:
        return False
    if "/" in raw:
        return _strip_ollama_prefix(raw) is not None
    if raw.lower().endswith(_CHECKPOINT_SUFFIXES):
        return False
    if key in hf_pinned_keys:
        return False
    return key not in skip_keys


class StartupManager:
    """Manages all startup and shutdown operations for the FastAPI application"""

    def __init__(self, *, site_config: SiteConfig):
        """Initialize startup manager with empty service references.

        Args:
            site_config: The SiteConfig every startup step reads: main.py's
                lifespan instance, threaded into each sub-service that needs
                DB-backed config at startup (database pool, Redis cache, model
                validator, retention janitor). ``initialize_all_services()``
                loads it from app_settings once migrations have run
                (``_load_site_config``), so it is the ONE instance the load
                must reach. Required: the old ``None`` default made each step
                build its own env-fallback SiteConfig, which no load could
                touch.

        Raises:
            TypeError: if ``site_config`` is missing.
        """
        if site_config is None:
            raise TypeError(
                "StartupManager requires a site_config kwarg: the SiteConfig "
                "every startup step reads, loaded from app_settings by "
                "initialize_all_services()."
            )
        self._site_config = site_config
        self.database_service: Any = None
        self.redis_cache: Any = None
        self.startup_error: Any = None
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
            from poindexter.brain.operator_notifier import notify_operator

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

            # Step 2a: Load app_settings into the SiteConfig every later step
            # reads, and publish it with the DB service for notify_operator.
            # Must stay ahead of the first step that reads a setting.
            await self._load_site_config()
            self._publish_integrations_context()

            # Step 2b: Self-heal any fully-unstamped active graph_def rows
            # (poindexter#755). graph_def *reseed* migrations write the raw
            # spec with no per-node contract fingerprints (to stay importable
            # in the migrations-smoke env), which un-stamps the active row and
            # trips the load-time drift gate (assert_graph_def_current) on the
            # next boot — halting every pipeline run. Baseline-stamp only
            # never-stamped rows here so genuine drift in stamped rows is still
            # caught. Runs after migrations because the reseed IS a migration.
            await self._ensure_active_graph_defs_stamped()

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

            # Step 10b: Validate *_model settings against installed Ollama
            # models (glad-labs-stack#1284). Best-effort -- never aborts
            # startup; only alerts the operator.
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
                from poindexter.services.retention_janitor import RetentionJanitor
                if self.database_service and self.database_service.pool:
                    _janitor = RetentionJanitor(site_config=self._site_config)
                    # Held like the pool monitor so shutdown cancels it.
                    janitor_task = asyncio.create_task(
                        _janitor.run_forever(self.database_service.pool),
                        name="retention_janitor",
                    )
                    self._background_tasks.add(janitor_task)
                    janitor_task.add_done_callback(self._background_tasks.discard)
                    logger.info("[retention_janitor] Started background loop")
            except Exception as rj_err:
                logger.warning(
                    "[retention_janitor] Failed to start: %s", rj_err,
                )

            # No image-gen warmup step: the image-gen server lazy-loads its
            # model on the first /generate and unloads it once idle
            # (IDLE_TIMEOUT in scripts/image-gen-server.py), so a startup
            # render could keep nothing warm. It would only take
            # gpu.lock("image_gen") and evict Ollama on every worker restart.

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
            from poindexter.services.database_service import DatabaseService

            # #272 Phase-2g: DatabaseService takes a REQUIRED site_config.
            # Pass the instance threaded into this manager. It is not loaded
            # yet: loading needs this pool, and ``_load_site_config`` runs
            # once migrations have settled app_settings. So
            # ``initialize()`` pre-reads its pool-size keys over a direct
            # connection instead of asking the SiteConfig.
            self.database_service = DatabaseService(site_config=self._site_config)

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
                    from poindexter.utils.connection_health import ConnectionPoolHealth

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
                # brain is poindexter.brain (poindexter#1046 step 2) -- a sibling
                # package, importable wherever this module is; no path walk.

                from poindexter.brain.operator_notifier import notify_operator

                notify_operator(
                    title="Worker cannot start — database connection failed",
                    detail=(
                        f"{startup_error}\n\n"
                        "Fix: check that Postgres is running and reachable, "
                        "and that DATABASE_URL (or DATABASE_HOST/USER/...) is "
                        "set correctly.\n\n"
                        "For local dev the DSN usually looks like:\n"
                        "  postgresql://poindexter:<password>@localhost:5433/poindexter_brain"
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
            from poindexter.services.migrations import run_migrations

            await run_migrations(self.database_service)
            logger.info("   [OK] Database migrations completed successfully")
        except Exception as e:
            startup_error = f"FATAL: Database migration failed: {e!s}"
            logger.error(f"  {startup_error}", exc_info=True)
            try:

                from poindexter.brain.operator_notifier import notify_operator

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
            from poindexter.services.settings_defaults import seed_all_defaults

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

        # Operator overlay — re-apply Glad Labs custom model pins + personal
        # settings over the public OSS defaults (no-op on OSS installs, where the
        # private services.operator_overrides module is stripped from the mirror).
        try:
            from poindexter.services.settings_defaults import apply_operator_overrides

            if self.database_service and self.database_service.pool:
                overridden = await apply_operator_overrides(
                    self.database_service.pool
                )
                if overridden:
                    logger.info(
                        "   [OK] operator overlay re-applied %d override(s)",
                        overridden,
                    )
        except Exception as e:
            logger.warning(
                f"   [WARNING] operator overlay apply failed: {e!s}",
                exc_info=True,
            )

        # Local publishing mode — give an install with no site identity a
        # working one (site_url -> {api_url}/site, site_name -> a placeholder),
        # so the first publish doesn't raise on require("site_url"). On the
        # Docker stacks the brain's seed has already refilled these; this is
        # the backstop where no brain runs (e.g. `npm run dev`). Fills EMPTY
        # values only, and only when storage_provider=local; runs after the
        # seed + overlay so anything they set wins.
        try:
            from poindexter.services.local_site import fill_local_site_identity

            if self.database_service and self.database_service.pool:
                await fill_local_site_identity(self.database_service.pool)
        except Exception as e:
            logger.warning(
                f"   [WARNING] local-site identity fill failed: {e!s}",
                exc_info=True,
            )

        # Operator overlay — bootstrap the community-draft subreddit profiles on
        # a FRESH install (seed-if-empty). No-op on OSS (overlay stripped) and
        # once the table has any row, so runtime `community profiles` CRUD stays
        # authoritative and a deleted profile never resurrects on boot.
        try:
            from poindexter.services.settings_defaults import (
                seed_operator_subreddit_profiles,
            )

            if self.database_service and self.database_service.pool:
                seeded = await seed_operator_subreddit_profiles(
                    self.database_service.pool
                )
                if seeded:
                    logger.info(
                        "   [OK] operator overlay seeded %d subreddit profile(s)",
                        seeded,
                    )
        except Exception as e:
            logger.warning(
                f"   [WARNING] operator subreddit-profile seed failed: {e!s}",
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

            from poindexter.plugins.registry import get_modules
            from poindexter.services.module_runner import run_module_migrations

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
            from poindexter.services.jwt_blocklist_service import jwt_blocklist

            await jwt_blocklist.initialize(self.database_service.pool)
            # Purge any expired rows carried over from previous runs
            await jwt_blocklist.cleanup()
            logger.info("   [OK] JWT blocklist service initialized")
        except Exception as e:
            logger.warning(f"   [WARNING] JWT blocklist init failed: {e!s}", exc_info=True)

    async def _load_site_config(self) -> None:
        """Load ``app_settings`` into the injected SiteConfig (step 2a).

        Every later step reads settings through ``self._site_config``: Redis
        (``redis_enabled``, and the ``redis_url`` secret, which ``get_secret``
        can only query once ``load`` has handed the SiteConfig its pool), the
        Ollama model validator and the retention janitor. Until 2026-09-28
        main.py loaded it only after this whole method returned, so those reads
        resolved from env vars and code defaults while ``SiteConfig.get``
        stamped ``last_read_at`` as if the stored value had been used. Prod
        stored 'true' for the (since retired) image-gen warmup switch for three
        months while every boot logged the warmup as skipped.

        Runs after migrations, not as soon as the pool opens: migrations, the
        defaults seeder and the operator overlay all write ``app_settings`` in
        step 2, and a fresh database has no such table before it. Nothing in
        steps 1-2 reads the SiteConfig; ``DatabaseService.initialize`` pre-reads
        its own pool-size keys.

        A failed load keeps the env fallbacks and warns (inside
        ``SiteConfig.load``). The lifespan's ``build_container`` re-queries
        ``app_settings`` and fails loud if it is still unreadable.
        """
        pool = self.database_service.pool if self.database_service else None
        if pool is None:
            logger.warning(
                "[startup] No DB pool: startup settings resolve from env vars "
                "and code defaults"
            )
            return
        await self._site_config.load(pool)

    def _publish_integrations_context(self) -> None:
        """Publish the DB service and SiteConfig for ``notify_operator``.

        ``notify_operator`` finds both through
        ``services.integrations.shared_context``. main.py published them only
        after ``initialize_all_services()`` returned, so a page raised by a
        startup step (the model validator's missing-model, suspect-template
        and Ollama-unreachable alerts) found neither and was dropped at DEBUG:
        the validator logged its finding and alerted no one. main.py's
        ``wire_site_config_modules`` later publishes the same SiteConfig again.
        """
        if not (self.database_service and self.database_service.pool):
            return
        from poindexter.services.integrations.shared_context import (
            set_database_service,
            set_site_config,
        )

        set_database_service(self.database_service)
        set_site_config(self._site_config)

    async def _ensure_active_graph_defs_stamped(self) -> None:
        """Baseline-stamp any active graph_def a reseed migration left fully
        unstamped (poindexter#755).

        Delegates to
        :func:`services.pipeline_architect.ensure_active_graph_defs_stamped`,
        which stamps ONLY never-stamped rows (so genuine atom-contract drift in
        a stamped row is still caught by the load-time gate). Best-effort: a
        missing pool, an unimportable pipeline_architect stack (minimal
        coordinator deploy), or any runtime error is logged and skipped —
        never aborts startup. Same posture as the #379 settings seeder and the
        #1284 Ollama-model validator.
        """
        if not (self.database_service and self.database_service.pool):
            logger.debug("[graph_def_stamp] no DB pool — skipping")
            return
        try:
            from poindexter.services.pipeline_architect import (
                ensure_active_graph_defs_stamped,
            )
        except Exception as exc:  # noqa: BLE001 — stack unavailable ⇒ no-op
            logger.warning(
                "[graph_def_stamp] pipeline_architect unavailable, "
                "skipping (%s)",
                exc,
            )
            return
        try:
            stamped = await ensure_active_graph_defs_stamped(
                self.database_service.pool
            )
            if stamped:
                logger.info(
                    "   [OK] graph_def self-heal: baseline-stamped %d "
                    "fully-unstamped active graph_def(s)",
                    stamped,
                )
            else:
                logger.debug(
                    "   [INFO] graph_def self-heal: no fully-unstamped active "
                    "graph_def(s) to stamp"
                )
        except Exception as exc:  # noqa: BLE001 — best-effort, never abort boot
            logger.warning(
                "[graph_def_stamp] self-heal raised unexpectedly: %s",
                exc,
                exc_info=True,
            )

    async def _setup_redis_cache(self) -> None:
        """Initialize Redis cache for query optimization.

        Constructed via :meth:`RedisCache.create` with the injected
        SiteConfig (2026-05-28 DI migration — RedisCache no longer reads
        a module-level singleton), which step 2a has already loaded, so
        ``redis_enabled`` and the ``redis_url`` secret come from app_settings.
        """
        logger.info("  [INFO] Initializing Redis cache for query optimization...")
        try:
            from poindexter.services.redis_cache import RedisCache

            self.redis_cache = await RedisCache.create(site_config=self._site_config)
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
        """Validate *_model settings against installed Ollama models.

        Reads every ``app_settings`` key matching ``*_model``, then:

        1. Fetches the installed model list from Ollama (``GET /api/tags``).
        2. For each configured model value: strips an ``ollama/`` or
           ``ollama_chat/`` prefix if present (the DB stores
           ``ollama/gemma3:27b``; Ollama reports ``gemma3:27b``).
        3. Warns if the model is not in the installed list.
        4. Fetches ``POST /api/show`` for installed models and checks for
           suspicious chat-template tokens (``<|turn>``, ``<turn|>``,
           ``<|im_turn|>``) without any established delimiter pattern
           (``<start_of_turn>``, ``<|im_start|>``, ``[INST]``, ``<|user|>``).
        5. If any model is uninstalled or has a suspect template, calls
           :func:`notify_operator` to alert via Discord/Telegram.

        **Only Ollama-destined values are checked** (Glad-Labs/poindexter#941).
        Not every ``*_model`` setting addresses Ollama — the pipeline also
        configures image-gen, wan, speaches/whisper, a sentence-transformers
        reranker and the chatterbox sidecar through identically-named keys.
        Measured 2026-07-29, the un-filtered check reported 16 missing models
        of which **15 were false positives**, burying the one real finding
        (an uninstalled ``ollama/``-prefixed voice model). Four filters keep
        the warning worth reading — see ``_is_ollama_model_value`` (which
        also reads the HuggingFace revision pins the query fetches), the
        ``ESCAPE`` on the key query, the ``:latest`` normalization in
        ``_ollama_name_variants``, and the de-duplication of both report lists.

        Gated by ``ollama_model_validation_enabled`` (default ``true``); a
        new non-Ollama bare key is exempted through
        ``ollama_model_validation_skip_keys``. Both are read from the
        SiteConfig step 2a loaded, so their stored values apply at boot.
        Never hard-fails -- startup continues even when Ollama is unreachable.

        Root cause for which this was added: a writer model setting (then
        ``cost_tier.standard.model``, now the per-step ``pipeline_writer_model``)
        was set to ``gemma-4-31B-it-qat:latest`` with a malformed Modelfile
        template using ``<|turn>`` pseudo-tokens (should be
        ``<start_of_turn>``) that caused reasoning-channel bleed into all
        canonical_blog drafts for hours (2026-06-09 incident).
        Glad-Labs/glad-labs-stack#1284.
        """
        sc = self._site_config
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
                # ESCAPE matters: `_` is a single-character LIKE wildcard, so
                # the unescaped '%_model' also matched `.model` keys —
                # dragging in sidecar settings like
                # plugin.tts_provider.chatterbox.model that Ollama was never
                # going to have installed. The `*_model_revision` rows come
                # back empty or not: a revision pin is what marks its model
                # key as a HuggingFace Hub model (`_hf_revision_pinned_keys`).
                rows = await conn.fetch(
                    r"SELECT key, value FROM app_settings"
                    r" WHERE (key LIKE '%\_model' ESCAPE '\'"
                    r"        AND value IS NOT NULL AND value != '')"
                    r"    OR key LIKE '%\_model\_revision' ESCAPE '\'"
                    r" ORDER BY key"
                )
        except Exception as db_err:
            logger.warning("[model_validator] DB query failed: %s", db_err)
            return

        hf_pinned = _hf_revision_pinned_keys(row["key"] for row in rows)
        model_rows = [row for row in rows if not row["key"].endswith(_REVISION_SUFFIX)]
        if not model_rows:
            logger.debug("[model_validator] No *_model keys found")
            return

        # Operator-extensible skip list, on top of the built-in one — a new
        # non-Ollama `*_model` key shouldn't require a code change to stop it
        # being reported as a missing model every boot.
        extra_skip = {
            k.strip() for k in
            (sc.get("ollama_model_validation_skip_keys", "") or "").split(",")
            if k.strip()
        }
        skip_keys = _NON_OLLAMA_MODEL_KEYS | frozenset(extra_skip)

        # key -> raw value (may include ollama/ prefix)
        configured: dict[str, str] = {
            row["key"]: row["value"] for row in model_rows
            if _is_ollama_model_value(
                row["key"], row["value"], skip_keys=skip_keys, hf_pinned_keys=hf_pinned,
            )
        }
        skipped = len(model_rows) - len(configured)
        logger.debug(
            "[model_validator] %d model key(s) to validate (%d non-Ollama skipped)",
            len(configured), skipped,
        )

        # ------------------------------------------------------------------ #
        # Fetch installed Ollama models                                       #
        # ------------------------------------------------------------------ #
        import httpx as _httpx

        # Prefer the lifespan-bound shared client; create a per-call client
        # only when one has not been wired yet (early-boot / tests).
        from poindexter.services.integrations import operator_notify as _on_mod
        from poindexter.services.integrations.operator_notify import notify_operator
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
        # `<|turn>` / `<turn|>` are ESTABLISHED, not suspect: that paired form is
        # Gemma 4's published turn format, and the model declares it itself --
        # `ollama show gemma-4-31B-it-qat` lists `stop "<|turn>"` and
        # `stop "<turn|>"` in its own parameters (family=gemma4). Gemma 4 shipped
        # after this check landed (#1430, 2026-06-11), so the original lists only
        # knew Gemma 2/3's `<start_of_turn>` and flagged every Gemma 4 model at
        # boot. That was a pure false positive on a model backing 20 *_model
        # settings and ~13.7k calls/14d, i.e. exactly the kind of noise that
        # trains operators to ignore startup warnings.
        #
        # `<|im_turn|>` stays suspect: no published family uses it, so it still
        # reads as a mangled/hallucinated ChatML variant.
        _SUSPECT_TOKENS = ("<|im_turn|>",)
        _ESTABLISHED_DELIMITERS = (
            "<start_of_turn>",  # Gemma 2 / 3
            "<|im_start|>",     # ChatML (Qwen, many others)
            "[INST]",           # Llama 2 / Mistral
            "<|user|>",         # Zephyr / TinyLlama et al
            "<|turn>",          # Gemma 4
        )

        template_fetch_failures: list[str] = []

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
                template_fetch_failures.append(f"{model_name}: {te}")
                return None

        # ------------------------------------------------------------------ #
        # Validate each configured model                                      #
        # ------------------------------------------------------------------ #
        missing_models: list[str] = []
        suspect_models: list[tuple] = []  # (model, reason)

        # One model is typically referenced by many keys (gemma-4-31B is the
        # writer for ~20 of them). Report per MODEL, not per key, or a single
        # bad template prints twenty identical lines.
        checked_models: set[str] = set()

        for key, raw_value in configured.items():
            # Strip the ollama/ or ollama_chat/ prefix that the DB uses but
            # Ollama itself does not. Non-Ollama values never reach here —
            # _is_ollama_model_value filtered them out when `configured` was
            # built.
            model_name = raw_value.strip()
            bare = _strip_ollama_prefix(model_name)
            if bare is not None:
                model_name = bare

            if not model_name or model_name in checked_models:
                continue
            checked_models.add(model_name)

            # Match across tag variants: Ollama always reports an explicit tag,
            # so a bare `nomic-embed-text` would otherwise look missing against
            # an installed `nomic-embed-text:latest`.
            variants = _ollama_name_variants(model_name)
            if not (variants & installed_names):
                missing_models.append(model_name)
                logger.warning(
                    "[model_validator] MISSING: key=%r references model %r "
                    "which is not installed in Ollama",
                    key, model_name,
                )
            else:
                # Model is installed -- check its chat template. Ask by the
                # name Ollama itself reports, not the config's spelling, so an
                # untagged setting doesn't turn into a /api/show miss that
                # would be filed as a template-fetch failure.
                installed_name = next(iter(variants & installed_names))
                template = await _fetch_template(installed_name)
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

        if template_fetch_failures:
            from poindexter.utils.findings import emit_finding

            emit_finding(
                source="startup_manager",
                kind="model_template_fetch_failed",
                title=(
                    f"Chat-template fetch failed for "
                    f"{len(template_fetch_failures)} installed model(s)"
                ),
                body=(
                    f"_fetch_template: {template_fetch_failures}. Those "
                    "models skipped chat-template validation this boot — "
                    "a stale/mismatched template wouldn't be caught."
                ),
                dedup_key="model_template_fetch_failed",
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
                "app_settings *_model keys."
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
            # "Task was destroyed but it is pending!" (Glad-Labs/glad-labs-stack#997).
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
