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

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

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
        self.custom_workflows_service = None
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
            'fine_tuning_service': FineTuningService
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

            # Step 5: Initialize workflow history service
            await self._initialize_workflow_history()

            # Step 6: Initialize content critique loop
            await self._initialize_content_critique()

            # Step 7: Initialize background task executor
            await self._initialize_task_executor()

            # Step 8: Initialize training data services
            await self._initialize_training_services()

            # Step 9: Verify connections
            await self._verify_connections()

            # Step 10: Register services with routes
            await self._register_route_services()

            # Step 11: Initialize agent registry
            await self._initialize_agent_registry()

            # Step 12: Initialize custom workflows service
            await self._initialize_custom_workflows_service()

            # Step 13: Warmup SDXL models (async, non-blocking)
            # Only if GPU is available - this prevents timeout issues when users first request SDXL
            try:
                await self._warmup_sdxl_models()
            except Exception as e:
                import traceback

                logger.warning(f"[WARNING] SDXL warmup failed (non-critical): {type(e).__name__}: {e}")
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
                "custom_workflows_service": self.custom_workflows_service,
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
            logger.error("  [FATAL] PostgreSQL is REQUIRED - cannot continue")
            logger.error("   Set DATABASE_URL or DATABASE_USER environment variables")
            logger.error(
                "  Example DATABASE_URL: postgresql://user:password@localhost:5432/glad_labs_dev"
            )
            raise SystemExit(1)

    async def _run_migrations(self) -> None:
        """Run database migrations"""
        logger.info("  [INFO] Running database migrations...")
        try:
            from services.migrations import run_migrations

            migrations_ok = await run_migrations(self.database_service)
            if migrations_ok:
                logger.info("   [OK] Database migrations completed successfully")
            else:
                logger.warning("   [WARNING] Database migrations failed (proceeding anyway)")
        except Exception as e:
            logger.warning(f"   [WARNING] Migration error: {str(e)} (proceeding anyway)")

        # Inject database service into content task store
        try:
            from services.content_router_service import get_content_task_store

            get_content_task_store(self.database_service)
        except Exception as e:
            logger.warning(f"   [WARNING] Content task store setup failed: {str(e)}")

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
            logger.warning(f"   [WARNING] Redis cache error: {str(e)} (continuing without cache)")

    async def _initialize_model_consolidation(self) -> None:
        """Initialize unified model consolidation service"""
        logger.info("  [INFO] Initializing unified model consolidation service...")
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

    async def _initialize_workflow_history(self) -> None:
        """Initialize workflow history service (Phase 6)"""
        logger.info("  📊 Initializing workflow history service...")
        try:
            from routes.workflow_history import initialize_history_service
            from services.workflow_history import WorkflowHistoryService

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
        """DEPRECATED: Content critique is now handled by UnifiedQualityService in TaskExecutor"""
        logger.debug("⏭️  Skipping _initialize_content_critique (now handled by UnifiedQualityService)")

    async def _initialize_task_executor(self) -> None:
        """Initialize background task executor (WITHOUT starting it yet)

        IMPORTANT: We create the TaskExecutor but do NOT call .start() here.
        The executor will be started from main.py AFTER UnifiedOrchestrator is initialized.
        This prevents the executor from processing tasks with legacy systems.
        """
        logger.info("  ⏳ Initializing background task executor (start deferred)...")
        try:
            from services.task_executor import TaskExecutor

            logger.debug(f"  [DEBUG] TaskExecutor init: database_service={self.database_service}")
            logger.debug(
                f"  [DEBUG] TaskExecutor init: database_service.tasks={self.database_service.tasks}"
            )
            logger.debug(f"  [DEBUG] TaskExecutor init: orchestrator={self.orchestrator}")

            logger.debug("  [DEBUG] Creating TaskExecutor instance...")
            # Pass None for orchestrator - it will be injected from main.py lifespan
            self.task_executor = TaskExecutor(
                database_service=self.database_service,
                orchestrator=None,  # Will be injected in main.py AFTER UnifiedOrchestrator is created
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
            from services.fine_tuning_service import FineTuningService
            from services.training_data_service import TrainingDataService

            if self.database_service:
                # Initialize training data service
                self.training_data_service = TrainingDataService(self.database_service.pool)
                logger.info("   Training data service initialized")

                # Initialize fine-tuning service
                self.fine_tuning_service = FineTuningService()
                logger.info(
                    "   Fine-tuning service initialized (Ollama, Gemini, Claude, GPT-4 support)"
                )

                logger.info("   All training services initialized successfully")
            else:
                logger.warning("   Training services not available - database service required")
                self.training_data_service = None
                self.fine_tuning_service = None
        except Exception as e:
            error_msg = f"Training services initialization failed: {str(e)}"
            logger.warning(f"   {error_msg}", exc_info=True)
            self.training_data_service = None
            self.fine_tuning_service = None

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

    async def _initialize_agent_registry(self) -> None:
        """Initialize agent registry with all available agents"""
        try:
            from agents.registry import get_agent_registry
            from utils.agent_initialization import register_all_agents

            registry = get_agent_registry()
            initialized_registry = register_all_agents(registry)
            agent_count = len(initialized_registry)
            logger.info(f"  Agent registry initialized with {agent_count} agents")
        except Exception as e:
            logger.warning(f"[WARNING] Agent registry initialization failed (non-critical): {type(e).__name__}: {e}")
            # Continue anyway - system can function without agent registry

    async def _initialize_custom_workflows_service(self) -> None:
        """Initialize custom workflows service for workflow builder"""
        logger.info("  🔧 Initializing custom workflows service...")
        try:
            from services.custom_workflows_service import CustomWorkflowsService

            if self.database_service:
                self.custom_workflows_service = CustomWorkflowsService(self.database_service)
                logger.info("   Custom workflows service initialized - users can create custom workflows")
            else:
                logger.warning("   Custom workflows service not available - database service required")
                self.custom_workflows_service = None
        except Exception as e:
            logger.warning(f"   Custom workflows service initialization failed (non-critical): {type(e).__name__}: {e}")
            self.custom_workflows_service = None

    async def _warmup_sdxl_models(self) -> None:
        """Warmup SDXL models to avoid timeout on first request"""
        import os

        # Skip warmup if explicitly disabled
        if os.getenv("DISABLE_SDXL_WARMUP", "").lower() == "true":
            logger.info("  SDXL warmup: Disabled via DISABLE_SDXL_WARMUP environment variable")
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
            logger.debug("  SDXL warmup: GPU not available, skipping model warmup (lazy loading enabled)")
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
                    use_refinement=False,
                    high_quality=False,
                )

                if success:
                    logger.info(
                        "  [OK] SDXL models loaded successfully! First requests will be fast."
                    )
                else:
                    logger.warning("  [WARNING] SDXL warmup generation failed (will initialize lazily)")

            finally:
                # Clean up temp file
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except (OSError, IOError) as e:
                    logger.debug(f"  [DEBUG] Temp file cleanup failed (non-critical): {str(e)}")

        except Exception as e:
            import traceback

            logger.warning(f"  [WARNING] SDXL warmup error (non-critical): {type(e).__name__}: {e}")
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
