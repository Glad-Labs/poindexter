"""
Tests for utils/route_registration.py

Covers:
- register_all_routes: returns status dict with known keys
- register_all_routes: marks pre-excluded routers as False
- register_all_routes: ImportError for a route sets status to False (no crash)
- register_all_routes: unknown Exception for a route sets status to False (no crash)
- register_workflow_history_routes: returns False when either service is None
- register_workflow_history_routes: returns False on ImportError
- register_workflow_history_routes: returns False on generic Exception
- register_workflow_history_routes: success path calls initialize_history_service + include_router
- _ROUTE_MANIFEST: structure is valid (4-tuples with non-empty strings)
"""

from unittest.mock import MagicMock, patch

from utils.route_registration import (
    _ROUTE_MANIFEST,
    register_all_routes,
    register_workflow_history_routes,
)


def _make_app():
    """Return a minimal mock FastAPI app."""
    app = MagicMock()
    app.include_router = MagicMock()
    return app


class TestRouteManifestStructure:
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

    def test_approval_router_before_task_router(self):
        """Order matters: approval_router must precede task_router."""
        status_keys = [entry[2] for entry in _ROUTE_MANIFEST]
        approval_idx = status_keys.index("approval_router")
        task_idx = status_keys.index("task_router")
        assert approval_idx < task_idx


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
