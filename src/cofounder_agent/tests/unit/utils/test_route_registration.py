"""
Tests for utils/route_registration.py

Covers:
- register_all_routes: returns status dict with known keys
- register_all_routes: marks pre-excluded routers as False
- register_all_routes: ImportError for a route sets status to False (no crash)
- register_all_routes: unknown Exception for a route sets status to False (no crash)
- register_all_routes: deployment_mode selects correct manifest
- _ROUTE_MANIFEST / _COORDINATOR_ROUTES / _WORKER_ROUTES: structure is valid
"""

from unittest.mock import MagicMock, patch

from utils.route_registration import (
    _COORDINATOR_ROUTES,
    _ROUTE_MANIFEST,
    _WORKER_ROUTES,
    register_all_routes,
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
            assert isinstance(module_path, str)
            assert module_path

    def test_all_router_attrs_are_non_empty_strings(self):
        for _, router_attr, _, _ in _ROUTE_MANIFEST:
            assert isinstance(router_attr, str)
            assert router_attr

    def test_all_status_keys_are_non_empty_strings(self):
        for _, _, status_key, _ in _ROUTE_MANIFEST:
            assert isinstance(status_key, str)
            assert status_key

    def test_all_descriptions_are_non_empty_strings(self):
        for _, _, _, description in _ROUTE_MANIFEST:
            assert isinstance(description, str)
            assert description

    def test_status_keys_are_unique(self):
        keys = [entry[2] for entry in _ROUTE_MANIFEST]
        assert len(keys) == len(set(keys)), "Duplicate status keys found in manifest"

    def test_approval_router_precedes_task_router_in_worker(self):
        """Approval router must precede task router (concrete path before wildcard) in worker manifest."""
        status_keys = [entry[2] for entry in _WORKER_ROUTES]
        assert status_keys.index("approval_router") < status_keys.index("task_router")

    def test_coordinator_manifest_has_expected_routes(self):
        """Coordinator manifest should have 4 route entries (public site only)."""
        assert len(_COORDINATOR_ROUTES) == 4

    def test_manifest_alias_equals_coordinator(self):
        """_ROUTE_MANIFEST should be an alias for _COORDINATOR_ROUTES."""
        assert _ROUTE_MANIFEST is _COORDINATOR_ROUTES

    def test_coordinator_is_subset_of_worker(self):
        """Every coordinator route should also exist in worker manifest."""
        worker_keys = {entry[2] for entry in _WORKER_ROUTES}
        for entry in _COORDINATOR_ROUTES:
            assert entry[2] in worker_keys, f"Coordinator route {entry[2]} not in worker"

    def test_worker_manifest_has_expected_routes(self):
        """Worker manifest count guard — bump alongside _WORKER_ROUTES.

        Updated 2026-04-12: added pipeline_events (observability) +
        memory_dashboard (shared-memory stats/search) routes.
        Updated 2026-04-16 (#230): added topics_routes (URL-based seeding).
        Updated 2026-04-19 (Phase D4): added alertmanager_webhook_router
        for the Prometheus/Alertmanager consumer.
        Updated 2026-04-21 (gitea#271 Phase 3.B): added external_webhooks_router
        for Lemon Squeezy + Resend webhook sinks.
        Updated 2026-05-02 (PR #166 OAuth recovery): added oauth_metadata_router
        + oauth_token_router (RFC 8414 + RFC 6749 §4.4).
        Updated 2026-05-05 (#389): added voice_routes (LiveKit web join page).
        Updated 2026-05-06 (#347 step 3): added triage_router (firefighter
        ops LLM diagnosis route).
        """
        assert len(_WORKER_ROUTES) == 18

    def test_worker_approval_router_is_first(self):
        """OAuth metadata router is first now (PR #166); approval was first
        until OAuth recovery added the well-known endpoints at the top of the
        manifest."""
        assert _WORKER_ROUTES[0][2] == "oauth_metadata_router"

    def test_worker_manifest_structure_valid(self):
        """All worker manifest entries should be valid 4-tuples."""
        for entry in _WORKER_ROUTES:
            assert len(entry) == 4
            for field in entry:
                assert isinstance(field, str)
                assert field


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
            if k != first_key and k not in ("sample_upload_router",)
        )

    def test_worker_mode_registers_all_worker_routes(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app, deployment_mode="worker")
        # Worker mode should register exactly the worker routes
        registered = [k for k, v in result.items() if v is True]
        assert len(registered) == len(_WORKER_ROUTES)

    def test_worker_mode_includes_cms_router_for_preview(self):
        """Worker mode includes CMS routes for preview endpoint access."""
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            result = register_all_routes(app, deployment_mode="worker")
        assert result.get("cms_router") is True

    def test_coordinator_mode_includes_all_7_routes(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            register_all_routes(app, deployment_mode="coordinator")
        assert app.include_router.call_count == len(_COORDINATOR_ROUTES)

    def test_default_mode_is_coordinator(self):
        app = _make_app()
        with patch("utils.route_registration.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module
            register_all_routes(app)
        # Default should register coordinator route count
        assert app.include_router.call_count == len(_COORDINATOR_ROUTES)


class TestModuleV1RouteIteration:
    """Phase 4-lite — ``register_all_routes`` iterates ``get_modules()``
    after substrate routes mount, and calls ``register_routes(app)`` on
    each.

    Test strategy: use the REAL ``ContentModule`` (registered via
    core-samples in ``plugins/registry.py:_SAMPLES``). It's a Phase-4
    stub whose ``register_routes`` is a no-op — perfect for verifying
    the iteration fires without side effects. Trying to patch the
    registry from the test harness fights Python's import mechanics
    (multiple ``plugins.registry`` entries can exist in sys.modules
    under different qualified names with pytest's path layout); the
    real module is more reliable AND tests the actual code path."""

    def test_iteration_calls_register_routes_on_content_module(self):
        """The in-tree ContentModule (registered via _SAMPLES) shows
        up in the result dict as ``module:content`` after register_all_routes
        runs. Implicit assertion: ``ContentModule.register_routes(app)``
        ran without raising (Phase-3-lite no-op stub).

        NOTE: this test does NOT patch ``importlib.import_module`` like
        its sibling tests do — that patch corrupts core-samples
        discovery (ContentModule would load as a MagicMock and fail
        validation). The substrate routes may fail to load against the
        unconfigured test environment; we only care about the module
        iteration here, which appends to the same status dict."""
        app = _make_app()
        result = register_all_routes(app)

        assert "module:content" in result, (
            f"expected 'module:content' in result, got module keys: "
            f"{[k for k in result if k.startswith('module:')]}"
        )
        assert result["module:content"] is True

    def test_iteration_calls_register_routes_on_finance_module(self):
        """FinanceModule (also registered via _SAMPLES) appears in the
        result dict as ``module:finance`` after register_all_routes runs.
        Pins the Phase 4 wiring added 2026-05-16 (route auto-discovery
        actually mounts ``/api/finance/*``)."""
        app = _make_app()
        result = register_all_routes(app)

        assert "module:finance" in result, (
            f"expected 'module:finance' in result, got module keys: "
            f"{[k for k in result if k.startswith('module:')]}"
        )
        assert result["module:finance"] is True
