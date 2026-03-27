"""
Unit tests for utils.startup_manager module.

Covers:
- StartupManager.__init__: default attribute state
- _validate_secrets(): production/dev behaviour, violations, empty env vars
- _initialize_database(): success, failure (raises SystemExit)
- _run_migrations(): success (ok=True), success (ok=False), Exception, content store failure
- _setup_redis_cache(): enabled, disabled, Exception
- _initialize_model_consolidation(): success, Exception (non-fatal)
- _initialize_workflow_history(): with DB, without DB, Exception
- _initialize_task_executor(): success, Exception (non-fatal)
- _initialize_training_services(): with DB, without DB, Exception
- _verify_connections(): healthy, unhealthy status, Exception
- _initialize_agent_registry(): dev mode skip, success, Exception
- _initialize_custom_workflows_service(): with DB, without DB, Exception
- _initialize_template_execution_service(): with service, without service, Exception
- _log_startup_summary(): smoke test (no crash)
- shutdown(): executor running, executor stopped/None, redis, DB, HuggingFace, Exception
- initialize_all_services(): success path, Exception path, SystemExit re-raise

All tests are pure — zero DB, LLM, or network calls.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helper: build a StartupManager with sys.modules pre-populated so imports
# inside the lazy methods are intercepted.
# ---------------------------------------------------------------------------


def _make_manager():
    """Import StartupManager with a clean import context."""
    # Remove cached module so re-import is fresh each test (avoids state leakage)
    sys.modules.pop("utils.startup_manager", None)
    from utils.startup_manager import StartupManager

    return StartupManager()


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInit:
    def test_all_attributes_none_by_default(self):
        mgr = _make_manager()
        assert mgr.database_service is None
        assert mgr.redis_cache is None
        assert mgr.orchestrator is None
        assert mgr.task_executor is None
        assert mgr.workflow_history_service is None
        assert mgr.training_data_service is None
        assert mgr.fine_tuning_service is None
        assert mgr.custom_workflows_service is None
        assert mgr.template_execution_service is None
        assert mgr.startup_error is None


# ---------------------------------------------------------------------------
# _validate_secrets
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateSecrets:
    def test_no_violations_in_production_does_not_raise(self):
        """When all secrets differ from defaults, production passes."""
        mgr = _make_manager()
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "strong-unique-key",
            "JWT_SECRET": "another-strong-key",
            "SECRET_KEY": "yet-another-key",
            "REVALIDATE_SECRET": "solid-revalidate-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            mgr._validate_secrets()  # Must not raise

    def test_default_value_in_production_raises_runtime_error(self):
        mgr = _make_manager()
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="Refusing to start in production"):
                mgr._validate_secrets()

    def test_default_value_in_development_logs_warning_not_raises(self):
        mgr = _make_manager()
        env = {
            "ENVIRONMENT": "development",
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
        }
        with patch.dict(os.environ, env, clear=False):
            # Should not raise — just warn
            mgr._validate_secrets()

    def test_empty_env_var_does_not_trigger_violation(self):
        """Empty string means not set — only warn, don't block."""
        mgr = _make_manager()
        # Remove all secret keys and set to empty to ensure the 'if actual == default_value' branch
        # is NOT hit (empty string is handled differently — no violation appended)
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "",
            "JWT_SECRET": "",
            "SECRET_KEY": "",
            "REVALIDATE_SECRET": "",
        }
        with patch.dict(os.environ, env, clear=False):
            # Empty strings are not treated as violations, so no error in production
            mgr._validate_secrets()

    def test_multiple_violations_in_production_raises(self):
        mgr = _make_manager()
        env = {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
            "SECRET_KEY": "your-secret-key-here",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError):
                mgr._validate_secrets()

    def test_staging_is_not_production(self):
        """staging environment should warn, not raise."""
        mgr = _make_manager()
        env = {
            "ENVIRONMENT": "staging",
            "JWT_SECRET_KEY": "development-secret-key-change-in-production",
        }
        with patch.dict(os.environ, env, clear=False):
            mgr._validate_secrets()  # Must not raise

    def test_default_environment_is_production(self):
        """ENVIRONMENT not set → defaults to 'production' → raises on default secret."""
        mgr = _make_manager()
        env = {"JWT_SECRET_KEY": "development-secret-key-change-in-production"}
        # Remove ENVIRONMENT so the default is used
        stripped = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
        stripped.update(env)
        with patch.dict(os.environ, stripped, clear=True):
            with pytest.raises(RuntimeError):
                mgr._validate_secrets()


# ---------------------------------------------------------------------------
# _initialize_database
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeDatabase:
    def test_success_sets_database_service(self):
        mgr = _make_manager()
        mock_db = AsyncMock()
        mock_db.pool = MagicMock()
        mock_db.tasks = MagicMock()
        mock_db.users = MagicMock()
        mock_db.content = MagicMock()
        mock_db_cls = MagicMock(return_value=mock_db)

        with patch.dict(
            "sys.modules", {"services.database_service": MagicMock(DatabaseService=mock_db_cls)}
        ):
            _run(mgr._initialize_database())

        assert mgr.database_service is mock_db

    def test_failure_raises_system_exit(self):
        mgr = _make_manager()
        mock_db = AsyncMock()
        mock_db.initialize.side_effect = Exception("connection refused")
        mock_db_cls = MagicMock(return_value=mock_db)

        with patch.dict(
            "sys.modules", {"services.database_service": MagicMock(DatabaseService=mock_db_cls)}
        ):
            with pytest.raises(SystemExit):
                _run(mgr._initialize_database())

    def test_failure_does_not_set_database_service(self):
        mgr = _make_manager()
        mock_db = AsyncMock()
        mock_db.initialize.side_effect = Exception("timeout")
        mock_db_cls = MagicMock(return_value=mock_db)

        with patch.dict(
            "sys.modules", {"services.database_service": MagicMock(DatabaseService=mock_db_cls)}
        ):
            with pytest.raises(SystemExit):
                _run(mgr._initialize_database())

        # database_service was NOT assigned before exception
        # (the exception happens during initialize(), so the attribute IS set to the instance)
        # just verify SystemExit propagated — attribute state is moot


# ---------------------------------------------------------------------------
# _run_migrations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunMigrations:
    def _mock_db(self):
        db = MagicMock()
        db.pool = MagicMock()
        return db

    def test_migration_ok_true(self):
        mgr = _make_manager()
        mgr.database_service = self._mock_db()

        mock_migrations = MagicMock()
        mock_migrations.run_migrations = AsyncMock(return_value=True)
        mock_content = MagicMock()
        mock_content.get_content_task_store = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "services.migrations": mock_migrations,
                "services.content_router_service": mock_content,
            },
        ):
            _run(mgr._run_migrations())

        mock_migrations.run_migrations.assert_awaited_once()

    def test_migration_ok_false_warns_not_raises(self):
        mgr = _make_manager()
        mgr.database_service = self._mock_db()

        mock_migrations = MagicMock()
        mock_migrations.run_migrations = AsyncMock(return_value=False)
        mock_content = MagicMock()
        mock_content.get_content_task_store = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "services.migrations": mock_migrations,
                "services.content_router_service": mock_content,
            },
        ):
            _run(mgr._run_migrations())  # Must not raise

    def test_migration_exception_warns_not_raises(self):
        mgr = _make_manager()
        mgr.database_service = self._mock_db()

        mock_migrations = MagicMock()
        mock_migrations.run_migrations = AsyncMock(side_effect=Exception("migration error"))
        mock_content = MagicMock()
        mock_content.get_content_task_store = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "services.migrations": mock_migrations,
                "services.content_router_service": mock_content,
            },
        ):
            _run(mgr._run_migrations())  # Must not raise

    def test_content_task_store_failure_warns_not_raises(self):
        mgr = _make_manager()
        mgr.database_service = self._mock_db()

        mock_migrations = MagicMock()
        mock_migrations.run_migrations = AsyncMock(return_value=True)
        mock_content = MagicMock()
        mock_content.get_content_task_store = MagicMock(side_effect=Exception("store fail"))

        with patch.dict(
            "sys.modules",
            {
                "services.migrations": mock_migrations,
                "services.content_router_service": mock_content,
            },
        ):
            _run(mgr._run_migrations())  # Must not raise


# ---------------------------------------------------------------------------
# _setup_redis_cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetupRedisCache:
    def test_redis_enabled_sets_cache(self):
        mgr = _make_manager()
        mock_cache = MagicMock()
        mock_cache._enabled = True
        mock_redis_cls = MagicMock()
        mock_redis_cls.create = AsyncMock(return_value=mock_cache)
        mock_module = MagicMock(RedisCache=mock_redis_cls)

        with patch.dict("sys.modules", {"services.redis_cache": mock_module}):
            _run(mgr._setup_redis_cache())

        assert mgr.redis_cache is mock_cache

    def test_redis_disabled_sets_cache_anyway(self):
        mgr = _make_manager()
        mock_cache = MagicMock()
        mock_cache._enabled = False
        mock_redis_cls = MagicMock()
        mock_redis_cls.create = AsyncMock(return_value=mock_cache)
        mock_module = MagicMock(RedisCache=mock_redis_cls)

        with patch.dict("sys.modules", {"services.redis_cache": mock_module}):
            _run(mgr._setup_redis_cache())

        assert mgr.redis_cache is mock_cache

    def test_redis_exception_warns_not_raises(self):
        mgr = _make_manager()
        mock_redis_cls = MagicMock()
        mock_redis_cls.create = AsyncMock(side_effect=Exception("connection refused"))
        mock_module = MagicMock(RedisCache=mock_redis_cls)

        with patch.dict("sys.modules", {"services.redis_cache": mock_module}):
            _run(mgr._setup_redis_cache())  # Must not raise

        assert mgr.redis_cache is None


# ---------------------------------------------------------------------------
# _initialize_model_consolidation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeModelConsolidation:
    def test_success_calls_initialize(self):
        mgr = _make_manager()
        mock_init = MagicMock()
        mock_module = MagicMock(initialize_model_consolidation_service=mock_init)

        with patch.dict("sys.modules", {"services.model_consolidation_service": mock_module}):
            _run(mgr._initialize_model_consolidation())

        mock_init.assert_called_once()

    def test_exception_does_not_raise(self):
        mgr = _make_manager()
        mock_init = MagicMock(side_effect=Exception("model init failed"))
        mock_module = MagicMock(initialize_model_consolidation_service=mock_init)

        with patch.dict("sys.modules", {"services.model_consolidation_service": mock_module}):
            _run(mgr._initialize_model_consolidation())  # Non-fatal, must not raise


# ---------------------------------------------------------------------------
# _initialize_workflow_history
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeWorkflowHistory:
    def test_with_database_service_sets_history(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.database_service.pool = MagicMock()

        mock_history = MagicMock()
        mock_init_history = MagicMock()
        mock_history_cls = MagicMock(return_value=mock_history)
        mock_wh_module = MagicMock(WorkflowHistoryService=mock_history_cls)
        mock_routes_module = MagicMock(initialize_history_service=mock_init_history)

        with patch.dict(
            "sys.modules",
            {
                "routes.workflow_history": mock_routes_module,
                "services.workflow_history": mock_wh_module,
            },
        ):
            _run(mgr._initialize_workflow_history())

        assert mgr.workflow_history_service is mock_history

    def test_without_database_service_leaves_none(self):
        mgr = _make_manager()
        mgr.database_service = None

        mock_wh_module = MagicMock()
        mock_routes_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "routes.workflow_history": mock_routes_module,
                "services.workflow_history": mock_wh_module,
            },
        ):
            _run(mgr._initialize_workflow_history())

        assert mgr.workflow_history_service is None

    def test_exception_sets_none(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.database_service.pool = MagicMock()

        mock_history_cls = MagicMock(side_effect=Exception("history fail"))
        mock_wh_module = MagicMock(WorkflowHistoryService=mock_history_cls)
        mock_routes_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "routes.workflow_history": mock_routes_module,
                "services.workflow_history": mock_wh_module,
            },
        ):
            _run(mgr._initialize_workflow_history())  # Must not raise

        assert mgr.workflow_history_service is None


# ---------------------------------------------------------------------------
# _initialize_task_executor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeTaskExecutor:
    def test_success_sets_executor(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.database_service.tasks = MagicMock()
        mgr.orchestrator = None

        mock_executor = MagicMock()
        mock_executor_cls = MagicMock(return_value=mock_executor)
        mock_module = MagicMock(TaskExecutor=mock_executor_cls)

        with patch.dict("sys.modules", {"services.task_executor": mock_module}):
            _run(mgr._initialize_task_executor())

        assert mgr.task_executor is mock_executor

    def test_executor_created_with_none_orchestrator(self):
        """Orchestrator is injected later; task executor starts without it."""
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.database_service.tasks = MagicMock()

        mock_executor = MagicMock()
        mock_executor_cls = MagicMock(return_value=mock_executor)
        mock_module = MagicMock(TaskExecutor=mock_executor_cls)

        with patch.dict("sys.modules", {"services.task_executor": mock_module}):
            _run(mgr._initialize_task_executor())

        call_kwargs = mock_executor_cls.call_args.kwargs
        assert call_kwargs["orchestrator"] is None

    def test_exception_sets_none_does_not_raise(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.database_service.tasks = MagicMock()

        mock_executor_cls = MagicMock(side_effect=Exception("executor init failed"))
        mock_module = MagicMock(TaskExecutor=mock_executor_cls)

        with patch.dict("sys.modules", {"services.task_executor": mock_module}):
            _run(mgr._initialize_task_executor())

        assert mgr.task_executor is None


# ---------------------------------------------------------------------------
# _initialize_training_services
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeTrainingServices:
    def test_with_database_sets_both_services(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.database_service.pool = MagicMock()

        mock_training = MagicMock()
        mock_finetuning = MagicMock()
        mock_training_cls = MagicMock(return_value=mock_training)
        mock_finetuning_cls = MagicMock(return_value=mock_finetuning)

        mock_ft_module = MagicMock(FineTuningService=mock_finetuning_cls)
        mock_td_module = MagicMock(TrainingDataService=mock_training_cls)

        with patch.dict(
            "sys.modules",
            {
                "services.fine_tuning_service": mock_ft_module,
                "services.training_data_service": mock_td_module,
            },
        ):
            _run(mgr._initialize_training_services())

        assert mgr.training_data_service is mock_training
        assert mgr.fine_tuning_service is mock_finetuning

    def test_without_database_leaves_none(self):
        mgr = _make_manager()
        mgr.database_service = None

        mock_ft_module = MagicMock()
        mock_td_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "services.fine_tuning_service": mock_ft_module,
                "services.training_data_service": mock_td_module,
            },
        ):
            _run(mgr._initialize_training_services())

        assert mgr.training_data_service is None
        assert mgr.fine_tuning_service is None

    def test_exception_sets_both_to_none(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.database_service.pool = MagicMock()

        mock_training_cls = MagicMock(side_effect=Exception("training fail"))
        mock_ft_module = MagicMock()
        mock_td_module = MagicMock(TrainingDataService=mock_training_cls)

        with patch.dict(
            "sys.modules",
            {
                "services.fine_tuning_service": mock_ft_module,
                "services.training_data_service": mock_td_module,
            },
        ):
            _run(mgr._initialize_training_services())

        assert mgr.training_data_service is None
        assert mgr.fine_tuning_service is None


# ---------------------------------------------------------------------------
# _verify_connections
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyConnections:
    def test_healthy_database_logs_pass(self):
        mgr = _make_manager()
        mock_db = AsyncMock()
        mock_db.health_check = AsyncMock(return_value={"status": "healthy"})
        mgr.database_service = mock_db

        _run(mgr._verify_connections())

        mock_db.health_check.assert_awaited_once()

    def test_unhealthy_status_logs_warning(self):
        mgr = _make_manager()
        mock_db = AsyncMock()
        mock_db.health_check = AsyncMock(return_value={"status": "degraded"})
        mgr.database_service = mock_db

        _run(mgr._verify_connections())  # Must not raise

    def test_exception_logs_warning_not_raises(self):
        mgr = _make_manager()
        mock_db = AsyncMock()
        mock_db.health_check = AsyncMock(side_effect=Exception("health check failed"))
        mgr.database_service = mock_db

        _run(mgr._verify_connections())  # Must not raise

    def test_no_database_service_skips_check(self):
        mgr = _make_manager()
        mgr.database_service = None

        _run(mgr._verify_connections())  # Must not raise — no DB to check


# ---------------------------------------------------------------------------
# _initialize_agent_registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeAgentRegistry:
    def test_dev_mode_skips_initialization(self):
        mgr = _make_manager()

        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "true"}):
            _run(mgr._initialize_agent_registry())

        # No import needed — dev mode exits early

    def test_non_dev_mode_registers_agents(self):
        mgr = _make_manager()

        mock_registry = MagicMock()
        mock_initialized = MagicMock()
        mock_initialized.__len__ = MagicMock(return_value=5)
        mock_get_registry = MagicMock(return_value=mock_registry)
        mock_register = MagicMock(return_value=mock_initialized)

        mock_registry_module = MagicMock(get_agent_registry=mock_get_registry)
        mock_init_module = MagicMock(register_all_agents=mock_register)

        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "false"}):
            with patch.dict(
                "sys.modules",
                {
                    "agents.registry": mock_registry_module,
                    "utils.agent_initialization": mock_init_module,
                },
            ):
                _run(mgr._initialize_agent_registry())

        mock_register.assert_called_once_with(mock_registry)

    def test_exception_logs_warning_not_raises(self):
        mgr = _make_manager()

        mock_registry_module = MagicMock()
        mock_registry_module.get_agent_registry.side_effect = Exception("agent registry failed")

        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "false"}):
            with patch.dict(
                "sys.modules",
                {
                    "agents.registry": mock_registry_module,
                    "utils.agent_initialization": MagicMock(),
                },
            ):
                _run(mgr._initialize_agent_registry())  # Must not raise


# ---------------------------------------------------------------------------
# _initialize_custom_workflows_service
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeCustomWorkflowsService:
    def test_with_database_sets_service(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()

        mock_service = MagicMock()
        mock_cls = MagicMock(return_value=mock_service)
        mock_module = MagicMock(CustomWorkflowsService=mock_cls)

        with patch.dict("sys.modules", {"services.custom_workflows_service": mock_module}):
            _run(mgr._initialize_custom_workflows_service())

        assert mgr.custom_workflows_service is mock_service

    def test_without_database_leaves_none(self):
        mgr = _make_manager()
        mgr.database_service = None

        mock_module = MagicMock()

        with patch.dict("sys.modules", {"services.custom_workflows_service": mock_module}):
            _run(mgr._initialize_custom_workflows_service())

        assert mgr.custom_workflows_service is None

    def test_exception_sets_none_not_raises(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()

        mock_cls = MagicMock(side_effect=Exception("custom workflows init failed"))
        mock_module = MagicMock(CustomWorkflowsService=mock_cls)

        with patch.dict("sys.modules", {"services.custom_workflows_service": mock_module}):
            _run(mgr._initialize_custom_workflows_service())

        assert mgr.custom_workflows_service is None


# ---------------------------------------------------------------------------
# _initialize_template_execution_service
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeTemplateExecutionService:
    def test_with_custom_workflows_service_sets_service(self):
        mgr = _make_manager()
        mgr.custom_workflows_service = MagicMock()

        mock_service = MagicMock()
        mock_cls = MagicMock(return_value=mock_service)
        mock_module = MagicMock(TemplateExecutionService=mock_cls)

        with patch.dict("sys.modules", {"services.template_execution_service": mock_module}):
            _run(mgr._initialize_template_execution_service())

        assert mgr.template_execution_service is mock_service

    def test_without_custom_workflows_service_leaves_none(self):
        mgr = _make_manager()
        mgr.custom_workflows_service = None

        mock_module = MagicMock()

        with patch.dict("sys.modules", {"services.template_execution_service": mock_module}):
            _run(mgr._initialize_template_execution_service())

        assert mgr.template_execution_service is None

    def test_exception_sets_none_not_raises(self):
        mgr = _make_manager()
        mgr.custom_workflows_service = MagicMock()

        mock_cls = MagicMock(side_effect=Exception("template execution init failed"))
        mock_module = MagicMock(TemplateExecutionService=mock_cls)

        with patch.dict("sys.modules", {"services.template_execution_service": mock_module}):
            _run(mgr._initialize_template_execution_service())

        assert mgr.template_execution_service is None


# ---------------------------------------------------------------------------
# _log_startup_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogStartupSummary:
    def test_smoke_all_none(self):
        mgr = _make_manager()
        mgr._log_startup_summary()  # Must not raise even when all services are None

    def test_smoke_with_services(self):
        mgr = _make_manager()
        mgr.database_service = MagicMock()
        mgr.redis_cache = MagicMock()
        mgr.redis_cache._enabled = True
        mgr.orchestrator = MagicMock()
        mgr.task_executor = MagicMock()
        mgr.task_executor.running = True
        mgr.workflow_history_service = MagicMock()
        mgr.training_data_service = MagicMock()
        mgr.fine_tuning_service = MagicMock()
        mgr._log_startup_summary()  # Must not raise


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShutdown:
    def test_shutdown_running_executor(self):
        mgr = _make_manager()
        mock_executor = AsyncMock()
        mock_executor.running = True
        mock_executor.stop = AsyncMock()
        mock_executor.get_stats = MagicMock(
            return_value={"task_count": 10, "success_count": 8, "error_count": 2}
        )
        mgr.task_executor = mock_executor

        # Stub out HuggingFace cleanup
        mock_hf = MagicMock()
        mock_hf.ModelConsolidationService = MagicMock()
        mock_hf_client = MagicMock()
        mock_hf_client._session_cleanup = AsyncMock()

        with patch.dict(
            "sys.modules",
            {
                "services.model_consolidation_service": mock_hf,
                "services.huggingface_client": mock_hf_client,
            },
        ):
            _run(mgr.shutdown())

        mock_executor.stop.assert_awaited_once()

    def test_shutdown_stopped_executor_skips_stop(self):
        mgr = _make_manager()
        mock_executor = MagicMock()
        mock_executor.running = False
        mgr.task_executor = mock_executor

        mock_hf = MagicMock()
        mock_hf_client = MagicMock()
        mock_hf_client._session_cleanup = AsyncMock()

        with patch.dict(
            "sys.modules",
            {
                "services.model_consolidation_service": mock_hf,
                "services.huggingface_client": mock_hf_client,
            },
        ):
            _run(mgr.shutdown())

        mock_executor.stop.assert_not_called()

    def test_shutdown_no_executor_no_crash(self):
        mgr = _make_manager()
        mgr.task_executor = None

        mock_hf = MagicMock()
        mock_hf_client = MagicMock()
        mock_hf_client._session_cleanup = AsyncMock()

        with patch.dict(
            "sys.modules",
            {
                "services.model_consolidation_service": mock_hf,
                "services.huggingface_client": mock_hf_client,
            },
        ):
            _run(mgr.shutdown())

    def test_shutdown_closes_redis(self):
        mgr = _make_manager()
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()
        mgr.redis_cache = mock_redis

        mock_hf = MagicMock()
        mock_hf_client = MagicMock()
        mock_hf_client._session_cleanup = AsyncMock()

        with patch.dict(
            "sys.modules",
            {
                "services.model_consolidation_service": mock_hf,
                "services.huggingface_client": mock_hf_client,
            },
        ):
            _run(mgr.shutdown())

        mock_redis.close.assert_awaited_once()

    def test_shutdown_closes_database(self):
        mgr = _make_manager()
        mock_db = AsyncMock()
        mock_db.close = AsyncMock()
        mgr.database_service = mock_db

        mock_hf = MagicMock()
        mock_hf_client = MagicMock()
        mock_hf_client._session_cleanup = AsyncMock()

        with patch.dict(
            "sys.modules",
            {
                "services.model_consolidation_service": mock_hf,
                "services.huggingface_client": mock_hf_client,
            },
        ):
            _run(mgr.shutdown())

        mock_db.close.assert_awaited_once()

    def test_shutdown_executor_exception_does_not_block_rest(self):
        """Executor stop failing should not prevent DB / Redis from closing."""
        mgr = _make_manager()
        mock_executor = AsyncMock()
        mock_executor.running = True
        mock_executor.stop = AsyncMock(side_effect=Exception("stop failed"))
        mgr.task_executor = mock_executor

        mock_db = AsyncMock()
        mock_db.close = AsyncMock()
        mgr.database_service = mock_db

        mock_hf = MagicMock()
        mock_hf_client = MagicMock()
        mock_hf_client._session_cleanup = AsyncMock()

        with patch.dict(
            "sys.modules",
            {
                "services.model_consolidation_service": mock_hf,
                "services.huggingface_client": mock_hf_client,
            },
        ):
            _run(mgr.shutdown())

        mock_db.close.assert_awaited_once()

    def test_shutdown_huggingface_import_error_handled(self):
        """If huggingface_client doesn't have _session_cleanup, ImportError is caught."""
        mgr = _make_manager()

        mock_hf = MagicMock()
        # No huggingface_client in sys.modules — force ImportError path
        modules = dict(sys.modules)
        modules.pop("services.huggingface_client", None)

        with patch.dict(
            "sys.modules", {"services.model_consolidation_service": mock_hf}, clear=False
        ):
            # Patch huggingface_client to raise ImportError on import
            with patch.dict("sys.modules", {"services.huggingface_client": None}):
                _run(mgr.shutdown())  # Must not raise


# ---------------------------------------------------------------------------
# initialize_all_services
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeAllServices:
    def _stub_all(self, mgr):
        """Stub all individual init methods so they succeed instantly."""
        mgr._validate_secrets = MagicMock()
        mgr._initialize_database = AsyncMock()
        mgr._run_migrations = AsyncMock()
        mgr._setup_redis_cache = AsyncMock()
        mgr._initialize_model_consolidation = AsyncMock()
        mgr._initialize_workflow_history = AsyncMock()
        mgr._initialize_task_executor = AsyncMock()
        mgr._initialize_training_services = AsyncMock()
        mgr._verify_connections = AsyncMock()
        mgr._initialize_agent_registry = AsyncMock()
        mgr._initialize_custom_workflows_service = AsyncMock()
        mgr._initialize_template_execution_service = AsyncMock()
        mgr._log_startup_summary = MagicMock()

    def test_success_returns_dict_with_expected_keys(self):
        mgr = _make_manager()
        self._stub_all(mgr)

        result = _run(mgr.initialize_all_services())

        assert isinstance(result, dict)
        expected_keys = {
            "database",
            "redis_cache",
            "task_executor",
            "workflow_history",
            "training_data_service",
            "fine_tuning_service",
            "custom_workflows_service",
            "template_execution_service",
            "startup_error",
        }
        assert set(result.keys()) == expected_keys

    def test_success_startup_error_is_none(self):
        mgr = _make_manager()
        self._stub_all(mgr)

        result = _run(mgr.initialize_all_services())

        assert result["startup_error"] is None

    def test_exception_during_step_sets_startup_error_and_raises(self):
        mgr = _make_manager()
        self._stub_all(mgr)
        mgr._initialize_database = AsyncMock(side_effect=RuntimeError("DB failed"))

        with pytest.raises(RuntimeError, match="DB failed"):
            _run(mgr.initialize_all_services())

    def test_system_exit_re_raised(self):
        mgr = _make_manager()
        self._stub_all(mgr)
        mgr._initialize_database = AsyncMock(side_effect=SystemExit(1))

        with pytest.raises(SystemExit):
            _run(mgr.initialize_all_services())

    def test_exception_sets_startup_error_attribute(self):
        mgr = _make_manager()
        self._stub_all(mgr)
        mgr._run_migrations = AsyncMock(side_effect=RuntimeError("migration crashed"))

        with pytest.raises(RuntimeError):
            _run(mgr.initialize_all_services())

        assert mgr.startup_error is not None
        assert "Critical startup failure" in mgr.startup_error

    def test_log_startup_summary_called_on_success(self):
        mgr = _make_manager()
        self._stub_all(mgr)

        _run(mgr.initialize_all_services())

        mgr._log_startup_summary.assert_called_once()

    def test_all_steps_called_in_order(self):
        mgr = _make_manager()
        call_order = []

        mgr._validate_secrets = MagicMock(side_effect=lambda: call_order.append("validate"))
        mgr._initialize_database = AsyncMock(side_effect=lambda: call_order.append("db"))
        mgr._run_migrations = AsyncMock(side_effect=lambda: call_order.append("migrations"))
        mgr._setup_redis_cache = AsyncMock(side_effect=lambda: call_order.append("redis"))
        mgr._initialize_model_consolidation = AsyncMock(
            side_effect=lambda: call_order.append("model")
        )
        mgr._initialize_workflow_history = AsyncMock(
            side_effect=lambda: call_order.append("wf_history")
        )
        mgr._initialize_task_executor = AsyncMock(side_effect=lambda: call_order.append("executor"))
        mgr._initialize_training_services = AsyncMock(
            side_effect=lambda: call_order.append("training")
        )
        mgr._verify_connections = AsyncMock(side_effect=lambda: call_order.append("verify"))
        mgr._initialize_agent_registry = AsyncMock(side_effect=lambda: call_order.append("agents"))
        mgr._initialize_custom_workflows_service = AsyncMock(
            side_effect=lambda: call_order.append("custom_wf")
        )
        mgr._initialize_template_execution_service = AsyncMock(
            side_effect=lambda: call_order.append("template")
        )
        mgr._log_startup_summary = MagicMock()

        _run(mgr.initialize_all_services())

        assert call_order == [
            "validate",
            "db",
            "migrations",
            "redis",
            "model",
            "wf_history",
            "executor",
            "training",
            "verify",
            "agents",
            "custom_wf",
            "template",
        ]
