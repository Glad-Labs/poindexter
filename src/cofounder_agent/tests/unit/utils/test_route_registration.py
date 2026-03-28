"""
Tests for utils/route_registration.py

Covers:
- register_all_routes: returns status dict with known keys
- register_all_routes: marks pre-excluded routers as False
- register_all_routes: ImportError for a route sets status to False (no crash)
- register_all_routes: unknown Exception for a route sets status to False (no crash)
- register_all_routes: deployment_mode selects correct manifest
- register_workflow_history_routes: returns False when either service is None
- register_workflow_history_routes: returns False on ImportError
- register_workflow_history_routes: returns False on generic Exception
- register_workflow_history_routes: success path calls initialize_history_service + include_router
- _ROUTE_MANIFEST / _COORDINATOR_ROUTES / _WORKER_ROUTES: structure is valid
"""

from unittest.mock import MagicMock, patch

from utils.route_registration import (
    _COORDINATOR_ROUTES,
    _ROUTE_MANIFEST,
    _WORKER_ROUTES,
    register_all_routes,
    register_workflow_history_routes,
)


def _make_app():
    """Return a minimal mock FastAPI app."""
    app = MagicMock()
    app.include_router = MagicMock()
    return app


class TestRouteManifestStructure:
    """Validate structure of all route manifests."""

    def test_manifest_is_non_empty(self):
        assert len(_ROUTE_MANIFEST) > 0

    def test_each_entry_has_four_elements(self):
        for entry in _ROUTE_MANIFEST:
            assert len(entry) == 4, f"Expected 4-tuple, got {entry}"

    def test_all_module_paths_are_non_empty_strings(self):
        for module_path, _, _, _ in _ROUTE_MANIFEST:
            assert isinstance(module_path, str) and module_path

    def test_all_router_attrs_are_non_empty_strings(self):
        for _, router_attr, _, _ in _ROUTE_MANIFEST:
            assert isinstance(router_attr, str) and router_attr

    def test_all_status_keys_are_non_empty_strings(self):
        for _, _, status_key, _ in _ROUTE_MANIFEST:
            assert isinstance(status_key, str) and status_key

    def test_all_descriptions_are_non_empty_strings(self):
        for _, _, _, description in _ROUTE_MANIFEST:
            assert isinstance(description, str) and description

    def test_status_keys_are_unique(self):
        keys = [entry[2] for entry in _ROUTE_MANIFEST]
        assert len(keys) == len(set(keys)), "Duplicate status keys found in manifest"

    def test_task_router_is_first(self):
        """Task router should be first in the active manifest."""
        status_keys = [entry[2] for entry in _ROUTE_MANIFEST]
        assert status_keys[0] == "task_router"

    def test_coordinator_manifest_has_7_active_routes(self):
        """Coordinator manifest should have exactly 7 route entries."""
        assert len(_COORDINATOR_ROUTES) == 7

    def test_manifest_alias_equals_coordinator(self):
        """_ROUTE_MANIFEST should be an alias for _COORDINATOR_ROUTES."""
        assert _ROUTE_MANIFEST is _COORDINATOR_ROUTES

    def test_worker_manifest_is_subset_of_coordinator(self):
        """Every worker route should also exist in coordinator manifest."""
        coordinator_keys = {entry[2] for entry in _COORDINATOR_ROUTES}
        for entry in _WORKER_ROUTES:
            assert entry[2] in coordinator_keys, f"Worker route {entry[2]} not in coordinator"

    def test_worker_manifest_has_3_routes(self):
        """Worker manifest should have exactly 3 route entries."""
        assert len(_WORKER_ROUTES) == 3

    def test_worker_task_router_is_first(self):
        """Task router should be first in the worker manifest."""
        assert _WORKER_ROUTES[0][2] == "task_router"

    def test_worker_manifest_structure_valid(self):
        """All worker manifest entries should be valid 4-tuples."""
        for entry in _WORKER_ROUTES:
            assert len(entry) == 4
            for field in entry:
                assert isinstance(field, str) and field


class TestRegisterAllRoutes:
    def test_returns_dict(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app)
        assert isinstance(result, dict)

    def test_pre_excluded_routers_are_false(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app)
        # These are intentionally excluded
        assert result["sample_upload_router"] is False
        assert result["workflow_history_router"] is False

    def test_successful_route_status_is_true(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app)
        # All manifest routes should succeed (mock returns a router)
        for _, _, status_key, _ in _ROUTE_MANIFEST:
            assert result[status_key] is True, f"Expected True for {status_key}"

    def test_import_error_sets_status_to_false(self):
        app = _make_app()
        # Make every import fail
        with patch(
            "utils.route_registration.importlib.import_module",
            side_effect=ImportError("no module"),
        ):
            result = register_all_routes(app)
        for _, _, status_key, _ in _ROUTE_MANIFEST:
            assert result[status_key] is False, f"Expected False for {status_key}"

    def test_generic_exception_sets_status_to_false(self):
        app = _make_app()
        with patch(
            "utils.route_registration.importlib.import_module",
            side_effect=RuntimeError("boom"),
        ):
            result = register_all_routes(app)
        for _, _, status_key, _ in _ROUTE_MANIFEST:
            assert result[status_key] is False

    def test_does_not_raise_on_import_failure(self):
        app = _make_app()
        with patch(
            "utils.route_registration.importlib.import_module",
            side_effect=Exception("crash"),
        ):
            # Should not raise
            result = register_all_routes(app)
        assert isinstance(result, dict)

    def test_include_router_called_for_each_successful_route(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            register_all_routes(app)
        # Should have been called once per manifest entry
        assert app.include_router.call_count == len(_ROUTE_MANIFEST)

    def test_include_router_not_called_on_import_failure(self):
        app = _make_app()
        with patch(
            "utils.route_registration.importlib.import_module",
            side_effect=ImportError("no module"),
        ):
            register_all_routes(app)
        app.include_router.assert_not_called()

    def test_partial_failure_does_not_prevent_other_routes(self):
        app = _make_app()

        call_count = {"n": 0}

        def _import_side_effect(module_path):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ImportError("first import fails")
            return MagicMock()

        with patch(
            "utils.route_registration.importlib.import_module",
            side_effect=_import_side_effect,
        ):
            result = register_all_routes(app)

        # First manifest entry should be False, others True
        first_key = _ROUTE_MANIFEST[0][2]
        assert result[first_key] is False
        # At least some others should have succeeded
        assert any(
            v
            for k, v in result.items()
            if k != first_key and k not in ("sample_upload_router", "workflow_history_router")
        )

    def test_worker_mode_registers_fewer_routes(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app, deployment_mode="worker")
        # Worker mode should register exactly the worker routes
        registered = [k for k, v in result.items() if v is True]
        assert len(registered) == len(_WORKER_ROUTES)

    def test_worker_mode_does_not_include_cms_router(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app, deployment_mode="worker")
        assert "cms_router" not in result or result.get("cms_router") is not True

    def test_coordinator_mode_includes_all_7_routes(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app, deployment_mode="coordinator")
        assert app.include_router.call_count == len(_COORDINATOR_ROUTES)

    def test_default_mode_is_coordinator(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app)
        # Default should register coordinator route count
        assert app.include_router.call_count == len(_COORDINATOR_ROUTES)


class TestRegisterWorkflowHistoryRoutes:
    def test_returns_false_when_database_service_is_none(self):
        app = _make_app()
        result = register_workflow_history_routes(
            app, database_service=None, workflow_history_service=MagicMock()
        )
        assert result is False

    def test_returns_false_when_workflow_history_service_is_none(self):
        app = _make_app()
        result = register_workflow_history_routes(
            app, database_service=MagicMock(), workflow_history_service=None
        )
        assert result is False

    def test_returns_false_when_both_services_are_none(self):
        app = _make_app()
        result = register_workflow_history_routes(
            app, database_service=None, workflow_history_service=None
        )
        assert result is False

    def test_returns_false_on_import_error(self):
        """When routes.workflow_history cannot be imported, return False."""
        import sys

        app = _make_app()
        mock_db = MagicMock()
        mock_wh = MagicMock()

        # Force an ImportError by injecting None for the module (simulates missing module)
        with patch.dict("sys.modules", {"routes.workflow_history": None}):  # type: ignore[dict-item]
            result = register_workflow_history_routes(
                app, database_service=mock_db, workflow_history_service=mock_wh
            )
        assert result is False

    def test_returns_false_on_exception(self):
        """A generic Exception in the try block should return False."""
        app = _make_app()
        mock_db = MagicMock()
        mock_db.pool = MagicMock()
        mock_wh = MagicMock()

        # Simulate an exception by making the app raise on include_router
        app.include_router.side_effect = RuntimeError("db schema error")

        # Patch the workflow_history import chain
        mock_router = MagicMock()
        mock_alias_router = MagicMock()
        mock_init_fn = MagicMock()
        mock_wh_module = MagicMock(
            router=mock_router,
            alias_router=mock_alias_router,
            initialize_history_service=mock_init_fn,
        )

        with patch.dict(
            "sys.modules",
            {"routes.workflow_history": mock_wh_module},
        ):
            result = register_workflow_history_routes(
                app, database_service=mock_db, workflow_history_service=mock_wh
            )

        assert result is False

    def test_success_path_calls_initialize_and_include_router(self):
        app = _make_app()
        mock_db = MagicMock()
        mock_db.pool = MagicMock(name="pool")
        mock_wh = MagicMock()

        mock_router = MagicMock()
        mock_alias = MagicMock()
        mock_init = MagicMock()
        mock_wh_module = MagicMock()
        mock_wh_module.router = mock_router
        mock_wh_module.alias_router = mock_alias
        mock_wh_module.initialize_history_service = mock_init

        with patch.dict("sys.modules", {"routes.workflow_history": mock_wh_module}):
            result = register_workflow_history_routes(
                app, database_service=mock_db, workflow_history_service=mock_wh
            )

        assert result is True
        mock_init.assert_called_once_with(mock_db.pool)
        assert app.include_router.call_count == 1
