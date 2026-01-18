"""
Startup Manager - Orchestrates application initialization and shutdown

Handles all startup and shutdown operations for the Glad Labs AI Co-Founder:
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

import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class StartupManager:
    """Manages all startup and shutdown operations for the FastAPI application"""

    def __init__(self):
        """Initialize startup manager with empty service references"""
        self.database_service = None
        self.redis_cache = None
        self.orchestrator = None
        self.task_executor = None
        self.workflow_history_service = None
        self.training_data_service = None
        self.fine_tuning_service = None
        self.legacy_data_service = None
        self.startup_error = None

    async def initialize_all_services(self) -> Dict[str, Any]:
        """
        Initialize all services in sequence.

        Returns dict with all initialized services:
        {
            'database': DatabaseService,
            'orchestrator': Orchestrator,
            'task_executor': TaskExecutor,
            'intelligent_orchestrator': IntelligentOrchestrator,
            'workflow_history': WorkflowHistoryService,
            'training_data_service': TrainingDataService,
            'fine_tuning_service': FineTuningService,
            'legacy_data_service': LegacyDataIntegrationService
        }
        """
        try:
            logger.info("🚀 Starting Glad Labs AI Co-Founder application...")
            logger.info(f"  Environment: {os.getenv('ENVIRONMENT', 'production')}")

            # Step 1: Initialize PostgreSQL database (MANDATORY)
            await self._initialize_database()

            # Step 2: Run migrations
            await self._run_migrations()

            # Step 3: Setup Redis cache
            await self._setup_redis_cache()

            # Step 4: Initialize model consolidation
            await self._initialize_model_consolidation()

            # Step 5: Initialize orchestrator
            await self._initialize_orchestrator()

            # Step 6: Initialize workflow history service
            await self._initialize_workflow_history()

            # Step 8: Initialize content critique loop
            await self._initialize_content_critique()

            # Step 9: Initialize background task executor
            await self._initialize_task_executor()

            # Step 10: Initialize training data services
            await self._initialize_training_services()

            # Step 11: Verify connections
            await self._verify_connections()

            # Step 12: Register services with routes
            await self._register_route_services()

            # Step 13: Warmup SDXL models (async, non-blocking)
            # Only if GPU is available - this prevents timeout issues when users first request SDXL
            try:
                await self._warmup_sdxl_models()
            except Exception as e:
                import traceback

                logger.warning(f"⚠️  SDXL warmup failed (non-critical): {type(e).__name__}: {e}")
                logger.debug(f"    Traceback: {traceback.format_exc()}")
                # Continue anyway - SDXL will load lazily when first used

            logger.info(" Application started successfully!")
            self._log_startup_summary()

            return {
                "database": self.database_service,
                "redis_cache": self.redis_cache,
                "task_executor": self.task_executor,
                "workflow_history": self.workflow_history_service,
                "training_data_service": self.training_data_service,
                "fine_tuning_service": self.fine_tuning_service,
                "legacy_data_service": self.legacy_data_service,
                "startup_error": self.startup_error,
            }

        except SystemExit:
            raise  # Re-raise SystemExit to stop startup
        except Exception as e:
            self.startup_error = f"Critical startup failure: {str(e)}"
            logger.error(f" {self.startup_error}", exc_info=True)
            raise

    async def _initialize_database(self) -> None:
        """Initialize PostgreSQL database connection"""
        logger.info("  Connecting to PostgreSQL (REQUIRED)...")

        db_url = os.getenv("DATABASE_URL", "Not set")
        logger.info(f"  DATABASE_URL: {db_url[:50] if db_url != 'Not set' else 'Not set'}...")

        try:
            from services.database_service import DatabaseService

            logger.debug("  [DEBUG] Creating DatabaseService instance...")
            self.database_service = DatabaseService()
            logger.debug(f"  [DEBUG] DatabaseService created: {self.database_service}")
            logger.debug(
                f"  [DEBUG] Before initialize(): pool={self.database_service.pool}, tasks={self.database_service.tasks}"
            )

            logger.debug("  [DEBUG] Calling await self.database_service.initialize()...")
            await self.database_service.initialize()
            logger.debug(
                f"  [DEBUG] After initialize(): pool={self.database_service.pool is not None}, tasks={self.database_service.tasks is not None}"
            )
            logger.debug(
                f"  [DEBUG] After initialize(): users={self.database_service.users is not None}, content={self.database_service.content is not None}"
            )

            logger.info("   PostgreSQL connected - ready for operations")
            logger.info(f"     Pool initialized: {self.database_service.pool is not None}")
            logger.info(f"     Tasks DB initialized: {self.database_service.tasks is not None}")
            logger.info(f"     Users DB initialized: {self.database_service.users is not None}")
            logger.info(f"     Content DB initialized: {self.database_service.content is not None}")
        except Exception as e:
            startup_error = f"FATAL: PostgreSQL connection failed: {str(e)}"
            logger.error(f"  {startup_error}", exc_info=True)
            logger.error("  🛑 PostgreSQL is REQUIRED - cannot continue")
            logger.error("   Set DATABASE_URL or DATABASE_USER environment variables")
            logger.error(
                "  Example DATABASE_URL: postgresql://user:password@localhost:5432/glad_labs_dev"
            )
            raise SystemExit(1)

    async def _run_migrations(self) -> None:
        """Run database migrations"""
        logger.info("  🔄 Running database migrations...")
        try:
            from services.migrations import run_migrations

            migrations_ok = await run_migrations(self.database_service)
            if migrations_ok:
                logger.info("   ✅ Database migrations completed successfully")
            else:
                logger.warning("   ⚠️ Database migrations failed (proceeding anyway)")
        except Exception as e:
            logger.warning(f"   ⚠️ Migration error: {str(e)} (proceeding anyway)")

        # Inject database service into content task store
        try:
            from services.content_router_service import get_content_task_store

            get_content_task_store(self.database_service)
        except Exception as e:
            logger.warning(f"   ⚠️ Content task store setup failed: {str(e)}")

    async def _setup_redis_cache(self) -> None:
        """Initialize Redis cache for query optimization"""
        logger.info("  🚀 Initializing Redis cache for query optimization...")
        try:
            from services.redis_cache import RedisCache

            self.redis_cache = await RedisCache.create()
            if self.redis_cache._enabled:
                logger.info(
                    "   ✅ Redis cache initialized (query performance optimization enabled)"
                )
            else:
                logger.info(
                    "   ℹ️  Redis cache not available (system will continue without caching)"
                )
        except Exception as e:
            logger.warning(f"   ⚠️ Redis cache error: {str(e)} (continuing without cache)")

    async def _initialize_model_consolidation(self) -> None:
        """Initialize unified model consolidation service"""
        logger.info("  🧠 Initializing unified model consolidation service...")
        try:
            from services.model_consolidation_service import initialize_model_consolidation_service

            initialize_model_consolidation_service()
            logger.info(
                "   Model consolidation service initialized (Ollama->HF->Google->Anthropic->OpenAI)"
            )
        except Exception as e:
            error_msg = f"Model consolidation initialization failed: {str(e)}"
            logger.error(f"   {error_msg}", exc_info=True)
            # Don't fail startup - models are optional

    async def _initialize_orchestrator(self) -> None:
        """Orchestrator initialization - SKIPPED

        The orchestrator is now created in main.py lifespan as UnifiedOrchestrator
        after all dependencies are properly initialized.

        This method is kept for backward compatibility but does nothing.
        """
        logger.info("  🤖 Orchestrator initialization (deferred to main.py lifespan)...")
        logger.info("     UnifiedOrchestrator will be created with all dependencies ready")
        # self.orchestrator stays None - will be set in main.py lifespan

    async def _initialize_workflow_history(self) -> None:
        """Initialize workflow history service (Phase 6)"""
        logger.info("  📊 Initializing workflow history service...")
        try:
            from services.workflow_history import WorkflowHistoryService
            from routes.workflow_history import initialize_history_service

            if self.database_service:
                self.workflow_history_service = WorkflowHistoryService(self.database_service.pool)
                initialize_history_service(self.database_service.pool)
                logger.info(
                    "   Workflow history service initialized - executions will be persisted to PostgreSQL"
                )
            else:
                logger.warning(
                    "   Workflow history service not available - executions will not be persisted"
                )
        except Exception as e:
            error_msg = f"Workflow history service initialization failed: {str(e)}"
            logger.warning(f"   {error_msg}", exc_info=True)
            self.workflow_history_service = None

    async def _initialize_content_critique(self) -> None:
        """Initialize content critique loop"""
        logger.info("  🔍 Initializing content critique loop...")
        try:
            from services.content_critique_loop import ContentCritiqueLoop

            critique_loop = ContentCritiqueLoop()
            logger.info("   Content critique loop initialized")
        except Exception as e:
            logger.warning(f"   Content critique loop initialization failed: {e}")

    async def _initialize_task_executor(self) -> None:
        """Initialize background task executor (WITHOUT starting it yet)

        IMPORTANT: We create the TaskExecutor but do NOT call .start() here.
        The executor will be started from main.py AFTER UnifiedOrchestrator is initialized.
        This prevents the executor from processing tasks with the legacy Orchestrator.
        """
        logger.info("  ⏳ Initializing background task executor (start deferred)...")
        try:
            from services.task_executor import TaskExecutor
            from services.content_critique_loop import ContentCritiqueLoop

            logger.debug(f"  [DEBUG] TaskExecutor init: database_service={self.database_service}")
            logger.debug(
                f"  [DEBUG] TaskExecutor init: database_service.tasks={self.database_service.tasks}"
            )
            logger.debug(f"  [DEBUG] TaskExecutor init: orchestrator={self.orchestrator}")

            critique_loop = ContentCritiqueLoop()
            logger.debug(f"  [DEBUG] ContentCritiqueLoop created: {critique_loop}")

            logger.debug("  [DEBUG] Creating TaskExecutor instance...")
            # Pass None for orchestrator - it will be injected from main.py lifespan
            self.task_executor = TaskExecutor(
                database_service=self.database_service,
                orchestrator=None,  # Will be injected in main.py AFTER UnifiedOrchestrator is created
                critique_loop=critique_loop,
                poll_interval=5,  # Poll every 5 seconds
            )
            logger.debug(f"  [DEBUG] TaskExecutor created: {self.task_executor}")
            logger.debug(
                f"  [DEBUG] TaskExecutor.database_service: {self.task_executor.database_service}"
            )
            logger.debug(
                f"  [DEBUG] TaskExecutor.orchestrator (initial): None (will be injected later)"
            )

            logger.info("   Background task executor initialized (not started yet)")
            logger.info(
                f"     ⏸️  Will be fully configured and started after UnifiedOrchestrator is injected in main.py"
            )
        except Exception as e:
            error_msg = f"Task executor initialization failed: {str(e)}"
            logger.error(f"   {error_msg}", exc_info=True)
            # Don't fail startup - task processing is optional
            self.task_executor = None

    async def _initialize_training_services(self) -> None:
        """Initialize training data management services (Phase 6)"""
        logger.info("  📚 Initializing training data management services...")

        try:
            from services.training_data_service import TrainingDataService
            from services.fine_tuning_service import FineTuningService
            from services.legacy_data_integration import LegacyDataIntegrationService

            if self.database_service:
                # Initialize training data service
                self.training_data_service = TrainingDataService(self.database_service.pool)
                logger.info("   Training data service initialized")

                # Initialize fine-tuning service
                self.fine_tuning_service = FineTuningService()
                logger.info(
                    "   Fine-tuning service initialized (Ollama, Gemini, Claude, GPT-4 support)"
                )

                # Initialize legacy data integration service
                self.legacy_data_service = LegacyDataIntegrationService(self.database_service.pool)
                logger.info("   Legacy data integration service initialized")

                logger.info("   All training services initialized successfully")
            else:
                logger.warning("   Training services not available - database service required")
                self.training_data_service = None
                self.fine_tuning_service = None
                self.legacy_data_service = None
        except Exception as e:
            error_msg = f"Training services initialization failed: {str(e)}"
            logger.warning(f"   {error_msg}", exc_info=True)
            self.training_data_service = None
            self.fine_tuning_service = None
            self.legacy_data_service = None

    async def _verify_connections(self) -> None:
        """Verify all connections are healthy"""
        if self.database_service:
            try:
                logger.info("  🔍 Verifying database connection...")
                health = await self.database_service.health_check()
                if health.get("status") == "healthy":
                    logger.info(f"   Database health check passed")
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
        """Warmup SDXL models to avoid timeout on first request"""
        import os

        # Check if torch is even available (optional dependency for SDXL)
        try:
            import torch
        except ModuleNotFoundError:
            logger.info("  SDXL warmup: torch not installed - SDXL disabled")
            logger.info("     To enable SDXL: pip install -r scripts/requirements-ml.txt")
            return

        # Check if GPU is available first
        if not torch.cuda.is_available():
            logger.info("  SDXL warmup: GPU not available, skipping model warmup")
            return

        # Check if user explicitly disabled SDXL warmup
        if os.getenv("DISABLE_SDXL_WARMUP", "").lower() == "true":
            logger.info("  SDXL warmup: Disabled via DISABLE_SDXL_WARMUP environment variable")
            return

        try:
            logger.info("  🎨 Warming up SDXL models (this may take 20-30 seconds)...")
            from services.image_service import ImageService
            import tempfile

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
                    use_refinement=False,
                    high_quality=False,
                )

                if success:
                    logger.info(
                        "  ✅ SDXL models loaded successfully! First requests will be fast."
                    )
                else:
                    logger.warning("  ⚠️ SDXL warmup generation failed (will initialize lazily)")

            finally:
                # Clean up temp file
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except (OSError, IOError):
                    pass

        except Exception as e:
            import traceback

            logger.warning(f"  ⚠️ SDXL warmup error (non-critical): {type(e).__name__}: {e}")
            logger.warning(f"     Full traceback:\n{traceback.format_exc()}")
            logger.info("     SDXL will initialize on first request")

    def _log_startup_summary(self) -> None:
        """Log summary of startup state"""
        logger.info(f"  - Database Service: {self.database_service is not None}")
        logger.info(
            f"  - Redis Cache: {self.redis_cache is not None and self.redis_cache._enabled}"
        )
        logger.info(f"  - Orchestrator: {self.orchestrator is not None}")
        logger.info(
            f"  - Task Executor: {self.task_executor is not None and self.task_executor.running}"
        )
        logger.info(f"  - Workflow History: {self.workflow_history_service is not None}")
        logger.info(f"  - Training Data Service: {self.training_data_service is not None}")
        logger.info(f"  - Fine-Tuning Service: {self.fine_tuning_service is not None}")
        logger.info(f"  - Legacy Data Service: {self.legacy_data_service is not None}")
        logger.info(f"  - Startup Error: {self.startup_error}")

    async def shutdown(self) -> None:
        """Gracefully shutdown all services"""
        try:
            logger.info("[STOP] Shutting down Glad Labs AI Co-Founder application...")

            # Stop background task executor
            try:
                if self.task_executor and self.task_executor.running:
                    logger.info("  Stopping background task executor...")
                    await self.task_executor.stop()
                    logger.info("   Task executor stopped")
                    stats = self.task_executor.get_stats()
                    logger.info(
                        f"     Tasks processed: {stats['total_processed']}, "
                        f"Success: {stats['successful']}, Failed: {stats['failed']}"
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

            # Close HuggingFace client session (prevents connection leak)
            try:
                from services.model_consolidation_service import (  # noqa: F401, E402
                    ModelConsolidationService,
                )

                # Close any HuggingFace adapter clients that may be cached
                logger.info("  Closing HuggingFace client sessions...")
                # The model consolidation service may have cached adapters
                # We'll clean up any aiohttp sessions they created
                import asyncio

                # Get all tasks and look for lingering aiohttp sessions
                try:
                    # Import at function level to avoid import errors if module not loaded
                    from services.huggingface_client import _session_cleanup

                    await _session_cleanup()
                    logger.info("   HuggingFace sessions closed")
                except (ImportError, AttributeError):
                    logger.debug("   HuggingFace client sessions already cleaned or not in use")
            except Exception as e:
                logger.debug(f"   HuggingFace cleanup (non-critical): {e}")

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
