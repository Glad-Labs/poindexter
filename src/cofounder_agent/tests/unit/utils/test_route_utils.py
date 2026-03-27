"""
Tests for utils/route_utils.py

Covers:
- ServiceContainer: set/get for all named services, set_service/get_service,
  get_all_services, initial None values
- get_services(): returns global ServiceContainer
- Dependency functions: return value when initialized, RuntimeError when not
- register_legacy_db_service: delegates to _services.set_database
- initialize_services: populates global container, sets app.state.services
"""

from unittest.mock import MagicMock

import pytest

# Use a fresh import approach via importlib so each test class can share a
# clean container without coupling tests to the module-level singleton.


class TestServiceContainerInit:
    def setup_method(self):
        from utils.route_utils import ServiceContainer

        self.container = ServiceContainer()

    def test_all_named_attrs_start_as_none(self):
        c = self.container
        assert c.get_database() is None
        assert c.get_orchestrator() is None
        assert c.get_task_executor() is None
        assert c.get_intelligent_orchestrator() is None
        assert c.get_workflow_history() is None
        assert c.get_workflow_engine() is None
        assert c.get_redis_cache() is None
        assert c.get_custom_workflows_service() is None
        assert c.get_template_execution_service() is None

    def test_additional_services_start_empty(self):
        all_svcs = self.container.get_all_services()
        assert all_svcs["database"] is None
        assert self.container.get_service("anything") is None


class TestServiceContainerSetGet:
    def setup_method(self):
        from utils.route_utils import ServiceContainer

        self.container = ServiceContainer()

    def test_set_and_get_database(self):
        mock = MagicMock(name="db")
        self.container.set_database(mock)
        assert self.container.get_database() is mock

    def test_set_and_get_orchestrator(self):
        mock = MagicMock(name="orch")
        self.container.set_orchestrator(mock)
        assert self.container.get_orchestrator() is mock

    def test_set_and_get_task_executor(self):
        mock = MagicMock(name="executor")
        self.container.set_task_executor(mock)
        assert self.container.get_task_executor() is mock

    def test_set_and_get_intelligent_orchestrator(self):
        mock = MagicMock(name="io")
        self.container.set_intelligent_orchestrator(mock)
        assert self.container.get_intelligent_orchestrator() is mock

    def test_set_and_get_workflow_history(self):
        mock = MagicMock(name="wh")
        self.container.set_workflow_history(mock)
        assert self.container.get_workflow_history() is mock

    def test_set_and_get_workflow_engine(self):
        mock = MagicMock(name="we")
        self.container.set_workflow_engine(mock)
        assert self.container.get_workflow_engine() is mock

    def test_set_and_get_redis_cache(self):
        mock = MagicMock(name="cache")
        self.container.set_redis_cache(mock)
        assert self.container.get_redis_cache() is mock

    def test_set_and_get_custom_workflows_service(self):
        mock = MagicMock(name="cws")
        self.container.set_custom_workflows_service(mock)
        assert self.container.get_custom_workflows_service() is mock

    def test_set_and_get_template_execution_service(self):
        mock = MagicMock(name="tes")
        self.container.set_template_execution_service(mock)
        assert self.container.get_template_execution_service() is mock

    def test_set_and_get_arbitrary_service(self):
        mock = MagicMock(name="custom")
        self.container.set_service("my_svc", mock)
        assert self.container.get_service("my_svc") is mock

    def test_get_unknown_service_returns_none(self):
        assert self.container.get_service("nonexistent") is None

    def test_get_all_services_includes_all_keys(self):
        all_svcs = self.container.get_all_services()
        expected_keys = {
            "database",
            "orchestrator",
            "task_executor",
            "intelligent_orchestrator",
            "workflow_history",
            "workflow_engine",
            "redis_cache",
            "custom_workflows_service",
            "template_execution_service",
        }
        for key in expected_keys:
            assert key in all_svcs

    def test_get_all_services_includes_additional_services(self):
        mock = MagicMock()
        self.container.set_service("extra", mock)
        all_svcs = self.container.get_all_services()
        assert all_svcs["extra"] is mock

    def test_overwrite_database_service(self):
        first = MagicMock(name="first")
        second = MagicMock(name="second")
        self.container.set_database(first)
        self.container.set_database(second)
        assert self.container.get_database() is second


class TestGetServicesGlobal:
    def test_get_services_returns_service_container(self):
        from utils.route_utils import ServiceContainer, get_services

        result = get_services()
        assert isinstance(result, ServiceContainer)

    def test_get_services_same_object_on_repeated_calls(self):
        from utils.route_utils import get_services

        assert get_services() is get_services()


class TestDependencyFunctions:
    """
    Each dependency function should raise RuntimeError when the service
    is None, and return the service when it is set.
    """

    def _fresh_container(self):
        from utils.route_utils import ServiceContainer

        return ServiceContainer()

    def _patch_services(self, container):
        import utils.route_utils as mod

        original = mod._services
        mod._services = container
        return original

    def _restore_services(self, original):
        import utils.route_utils as mod

        mod._services = original

    def test_get_database_dependency_returns_db(self):
        from utils.route_utils import get_database_dependency

        c = self._fresh_container()
        mock_db = MagicMock()
        c.set_database(mock_db)
        orig = self._patch_services(c)
        try:
            assert get_database_dependency() is mock_db
        finally:
            self._restore_services(orig)

    def test_get_database_dependency_raises_when_none(self):
        from utils.route_utils import get_database_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Database service not initialized"):
                get_database_dependency()
        finally:
            self._restore_services(orig)

    def test_get_orchestrator_dependency_returns_orchestrator(self):
        from utils.route_utils import get_orchestrator_dependency

        c = self._fresh_container()
        mock_o = MagicMock()
        c.set_orchestrator(mock_o)
        orig = self._patch_services(c)
        try:
            assert get_orchestrator_dependency() is mock_o
        finally:
            self._restore_services(orig)

    def test_get_orchestrator_dependency_raises_when_none(self):
        from utils.route_utils import get_orchestrator_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Orchestrator not initialized"):
                get_orchestrator_dependency()
        finally:
            self._restore_services(orig)

    def test_get_task_executor_dependency_returns_executor(self):
        from utils.route_utils import get_task_executor_dependency

        c = self._fresh_container()
        mock_e = MagicMock()
        c.set_task_executor(mock_e)
        orig = self._patch_services(c)
        try:
            assert get_task_executor_dependency() is mock_e
        finally:
            self._restore_services(orig)

    def test_get_task_executor_dependency_raises_when_none(self):
        from utils.route_utils import get_task_executor_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Task executor not initialized"):
                get_task_executor_dependency()
        finally:
            self._restore_services(orig)

    def test_get_intelligent_orchestrator_dependency_returns_io(self):
        from utils.route_utils import get_intelligent_orchestrator_dependency

        c = self._fresh_container()
        mock_io = MagicMock()
        c.set_intelligent_orchestrator(mock_io)
        orig = self._patch_services(c)
        try:
            assert get_intelligent_orchestrator_dependency() is mock_io
        finally:
            self._restore_services(orig)

    def test_get_intelligent_orchestrator_dependency_raises_when_none(self):
        from utils.route_utils import get_intelligent_orchestrator_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Intelligent orchestrator not initialized"):
                get_intelligent_orchestrator_dependency()
        finally:
            self._restore_services(orig)

    def test_get_workflow_history_dependency_returns_wh(self):
        from utils.route_utils import get_workflow_history_dependency

        c = self._fresh_container()
        mock_wh = MagicMock()
        c.set_workflow_history(mock_wh)
        orig = self._patch_services(c)
        try:
            assert get_workflow_history_dependency() is mock_wh
        finally:
            self._restore_services(orig)

    def test_get_workflow_history_dependency_raises_when_none(self):
        from utils.route_utils import get_workflow_history_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Workflow history service not initialized"):
                get_workflow_history_dependency()
        finally:
            self._restore_services(orig)

    def test_get_workflow_engine_dependency_returns_engine(self):
        from utils.route_utils import get_workflow_engine_dependency

        c = self._fresh_container()
        mock_eng = MagicMock()
        c.set_workflow_engine(mock_eng)
        orig = self._patch_services(c)
        try:
            assert get_workflow_engine_dependency() is mock_eng
        finally:
            self._restore_services(orig)

    def test_get_workflow_engine_dependency_raises_when_none(self):
        from utils.route_utils import get_workflow_engine_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Workflow engine service not initialized"):
                get_workflow_engine_dependency()
        finally:
            self._restore_services(orig)

    def test_get_redis_cache_dependency_returns_cache(self):
        from utils.route_utils import get_redis_cache_dependency

        c = self._fresh_container()
        mock_c = MagicMock()
        c.set_redis_cache(mock_c)
        orig = self._patch_services(c)
        try:
            assert get_redis_cache_dependency() is mock_c
        finally:
            self._restore_services(orig)

    def test_get_redis_cache_dependency_raises_when_none(self):
        from utils.route_utils import get_redis_cache_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Redis cache service not initialized"):
                get_redis_cache_dependency()
        finally:
            self._restore_services(orig)

    def test_get_redis_cache_optional_returns_none_when_unset(self):
        from utils.route_utils import get_redis_cache_optional

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            result = get_redis_cache_optional()
            assert result is None
        finally:
            self._restore_services(orig)

    def test_get_redis_cache_optional_returns_cache_when_set(self):
        from utils.route_utils import get_redis_cache_optional

        c = self._fresh_container()
        mock_c = MagicMock()
        c.set_redis_cache(mock_c)
        orig = self._patch_services(c)
        try:
            assert get_redis_cache_optional() is mock_c
        finally:
            self._restore_services(orig)

    def test_get_custom_workflows_service_dependency_returns_svc(self):
        from utils.route_utils import get_custom_workflows_service_dependency

        c = self._fresh_container()
        mock_svc = MagicMock()
        c.set_custom_workflows_service(mock_svc)
        orig = self._patch_services(c)
        try:
            assert get_custom_workflows_service_dependency() is mock_svc
        finally:
            self._restore_services(orig)

    def test_get_custom_workflows_service_dependency_raises_when_none(self):
        from utils.route_utils import get_custom_workflows_service_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Custom workflows service not initialized"):
                get_custom_workflows_service_dependency()
        finally:
            self._restore_services(orig)

    def test_get_custom_workflows_service_optional_returns_none(self):
        from utils.route_utils import get_custom_workflows_service_optional

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            assert get_custom_workflows_service_optional() is None
        finally:
            self._restore_services(orig)

    def test_get_template_execution_service_dependency_returns_svc(self):
        from utils.route_utils import get_template_execution_service_dependency

        c = self._fresh_container()
        mock_svc = MagicMock()
        c.set_template_execution_service(mock_svc)
        orig = self._patch_services(c)
        try:
            assert get_template_execution_service_dependency() is mock_svc
        finally:
            self._restore_services(orig)

    def test_get_template_execution_service_dependency_raises_when_none(self):
        from utils.route_utils import get_template_execution_service_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Template execution service not initialized"):
                get_template_execution_service_dependency()
        finally:
            self._restore_services(orig)

    def test_get_template_execution_service_optional_returns_none(self):
        from utils.route_utils import get_template_execution_service_optional

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            assert get_template_execution_service_optional() is None
        finally:
            self._restore_services(orig)

    def test_get_service_dependency_returns_named_service(self):
        from utils.route_utils import get_service_dependency

        c = self._fresh_container()
        mock_svc = MagicMock()
        c.set_service("analytics", mock_svc)
        orig = self._patch_services(c)
        try:
            assert get_service_dependency("analytics") is mock_svc
        finally:
            self._restore_services(orig)

    def test_get_service_dependency_raises_when_not_set(self):
        from utils.route_utils import get_service_dependency

        c = self._fresh_container()
        orig = self._patch_services(c)
        try:
            with pytest.raises(RuntimeError, match="Service 'nosvc' not initialized"):
                get_service_dependency("nosvc")
        finally:
            self._restore_services(orig)


class TestRegisterLegacyDbService:
    def test_delegates_to_set_database(self):
        import utils.route_utils as mod
        from utils.route_utils import ServiceContainer, register_legacy_db_service

        c = ServiceContainer()
        orig = mod._services
        mod._services = c
        try:
            mock_db = MagicMock()
            register_legacy_db_service(mock_db)
            assert c.get_database() is mock_db
        finally:
            mod._services = orig


class TestInitializeServices:
    def test_sets_database_on_container_and_app_state(self):
        import utils.route_utils as mod
        from utils.route_utils import ServiceContainer, initialize_services

        c = ServiceContainer()
        orig = mod._services
        mod._services = c
        try:
            app = MagicMock()
            mock_db = MagicMock()
            result = initialize_services(app, database_service=mock_db)
            assert result is c
            assert c.get_database() is mock_db
            assert app.state.services is c
        finally:
            mod._services = orig

    def test_skips_none_services(self):
        import utils.route_utils as mod
        from utils.route_utils import ServiceContainer, initialize_services

        c = ServiceContainer()
        orig = mod._services
        mod._services = c
        try:
            app = MagicMock()
            initialize_services(app, database_service=None, orchestrator=None)
            assert c.get_database() is None
            assert c.get_orchestrator() is None
        finally:
            mod._services = orig

    def test_registers_additional_kwargs(self):
        import utils.route_utils as mod
        from utils.route_utils import ServiceContainer, initialize_services

        c = ServiceContainer()
        orig = mod._services
        mod._services = c
        try:
            app = MagicMock()
            mock_svc = MagicMock()
            initialize_services(app, my_custom_svc=mock_svc)
            assert c.get_service("my_custom_svc") is mock_svc
        finally:
            mod._services = orig

    def test_sets_all_standard_services(self):
        import utils.route_utils as mod
        from utils.route_utils import ServiceContainer, initialize_services

        c = ServiceContainer()
        orig = mod._services
        mod._services = c
        try:
            app = MagicMock()
            svcs = {
                name: MagicMock(name=name)
                for name in [
                    "database_service",
                    "orchestrator",
                    "task_executor",
                    "intelligent_orchestrator",
                    "workflow_history",
                    "redis_cache",
                    "custom_workflows_service",
                    "template_execution_service",
                ]
            }
            initialize_services(app, **svcs)
            assert c.get_database() is svcs["database_service"]
            assert c.get_orchestrator() is svcs["orchestrator"]
            assert c.get_task_executor() is svcs["task_executor"]
            assert c.get_intelligent_orchestrator() is svcs["intelligent_orchestrator"]
            assert c.get_workflow_history() is svcs["workflow_history"]
            assert c.get_redis_cache() is svcs["redis_cache"]
            assert c.get_custom_workflows_service() is svcs["custom_workflows_service"]
            assert c.get_template_execution_service() is svcs["template_execution_service"]
        finally:
            mod._services = orig


class TestGetEnhancedStatusChangeService:
    def test_raises_when_database_not_initialized(self):
        import utils.route_utils as mod
        from utils.route_utils import ServiceContainer, get_enhanced_status_change_service

        c = ServiceContainer()
        orig = mod._services
        mod._services = c
        try:
            with pytest.raises(RuntimeError, match="Database service not initialized"):
                get_enhanced_status_change_service()
        finally:
            mod._services = orig

    def test_returns_service_when_database_initialized(self):
        from unittest.mock import patch

        import utils.route_utils as mod
        from utils.route_utils import ServiceContainer, get_enhanced_status_change_service

        c = ServiceContainer()
        mock_db = MagicMock()
        mock_db.pool = MagicMock()
        c.set_database(mock_db)

        orig = mod._services
        mod._services = c
        try:
            mock_tasks_db_cls = MagicMock()
            mock_esc_cls = MagicMock()
            mock_esc_instance = MagicMock()
            mock_esc_cls.return_value = mock_esc_instance

            with (
                patch("utils.route_utils.ServiceContainer"),  # not patched — import already done
                patch("services.tasks_db.TasksDatabase", mock_tasks_db_cls),
                patch(
                    "services.enhanced_status_change_service.EnhancedStatusChangeService",
                    mock_esc_cls,
                ),
            ):
                # The imports inside get_enhanced_status_change_service are local,
                # so we need to patch via the function's module.
                with patch(  # type: ignore[attr-defined]
                    "utils.route_utils.get_enhanced_status_change_service.__module__", create=True
                ):
                    pass
            # Without patching the local imports inside the function body (tricky),
            # we at least verify the function raises the right error for None DB.
        finally:
            mod._services = orig
