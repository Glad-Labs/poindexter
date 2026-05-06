"""Unit tests for services/profiling.py.

Covers the enable_pyroscope gate, missing-package graceful path, and
the pyroscope.configure call shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestSetupPyroscope:
    def test_skips_when_disabled(self):
        from services.profiling import setup_pyroscope

        with patch(
            "services.site_config.site_config.get",
            return_value="false",
        ):
            # Should exit cleanly without importing pyroscope.
            setup_pyroscope()

    def test_warns_when_enabled_but_package_missing(self, caplog):
        from services.profiling import setup_pyroscope

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": "true"}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": None}):
            with caplog.at_level("WARNING"):
                setup_pyroscope()

        msgs = " ".join(r.message for r in caplog.records)
        assert "pyroscope-io not installed" in msgs

    def test_configure_called_on_enabled_with_package(self):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {
                "enable_pyroscope": "true",
                "pyroscope_server_url": "http://pyroscope:4040",
                "environment": "production",
            }.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope("test-service")

        fake_pyroscope.configure.assert_called_once()
        call_kwargs = fake_pyroscope.configure.call_args.kwargs
        assert call_kwargs["application_name"] == "test-service"
        assert call_kwargs["server_address"] == "http://pyroscope:4040"
        assert call_kwargs["tags"]["environment"] == "production"

    def test_configure_exception_does_not_raise(self):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock(side_effect=RuntimeError("boom"))

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": "true"}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            # Must not raise — profiling failure should never kill startup.
            setup_pyroscope()


# ---------------------------------------------------------------------------
# Edge case coverage — input parsing, defaults, fallbacks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnablePyroscopeParsing:
    """Verify the `.lower() == "true"` gate handles the values an
    operator might realistically set in app_settings."""

    @pytest.mark.parametrize("value", ["True", "TRUE", "tRuE"])
    def test_case_insensitive_true_enables(self, value):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": value}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        fake_pyroscope.configure.assert_called_once()

    @pytest.mark.parametrize("value", ["yes", "1", "on", "", "False", "no"])
    def test_truthy_but_not_true_does_not_enable(self, value):
        """Only the literal string 'true' (case-insensitive) enables.
        Common-but-wrong truthy values must NOT trigger configuration —
        the gate is intentionally strict to avoid surprise on ops."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": value}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        fake_pyroscope.configure.assert_not_called()


@pytest.mark.unit
class TestSiteConfigUnavailable:
    def test_site_config_import_failure_returns_cleanly(self, caplog):
        """If the site_config module itself blows up at import time the
        function must swallow the exception and return — startup must
        not fail because the profiler couldn't decide whether to run."""
        import builtins
        from services import profiling

        real_import = builtins.__import__

        def _raising_import(name, *args, **kwargs):
            if name == "services.site_config":
                raise RuntimeError("site_config explodes")
            return real_import(name, *args, **kwargs)

        with caplog.at_level("DEBUG"):
            with patch("builtins.__import__", side_effect=_raising_import):
                # Must return cleanly — no exception surfaces.
                profiling.setup_pyroscope()

        # And it should leave a debug breadcrumb so operators can find it.
        msgs = " ".join(r.message for r in caplog.records)
        assert "site_config unavailable" in msgs


@pytest.mark.unit
class TestDefaultsAndFallbacks:
    def test_default_service_name_used_when_omitted(self):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": "true"}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["application_name"] == "cofounder-agent"
        # The same name flows into the tags dict for label-based queries.
        assert kwargs["tags"]["service"] == "cofounder-agent"

    def test_default_server_url_when_setting_unset(self):
        """When pyroscope_server_url is not configured, the default
        compose-network address must be used. Drift here would silently
        ship profiles to the wrong host."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()

        # site_config.get returns the default we pass for unknown keys.
        def _fake_get(key: str, default: str = "") -> str:
            if key == "enable_pyroscope":
                return "true"
            # Return the caller's default for anything else (including
            # pyroscope_server_url and environment).
            return default

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["server_address"] == "http://pyroscope:4040"
        assert kwargs["tags"]["environment"] == "development"

    @pytest.mark.parametrize("env_value", ["", None])
    def test_falsy_environment_falls_back_to_development(self, env_value):
        """``site_config.get("environment", "development") or "development"``
        — the trailing ``or`` covers the case where the setting exists
        but is an empty string (or None). Must land on 'development'."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()

        def _fake_get(key: str, default: str = ""):
            return {
                "enable_pyroscope": "true",
                "environment": env_value,
            }.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["tags"]["environment"] == "development"

    def test_success_logs_info_with_resolved_values(self, caplog):
        """The success log line is the operator's confirmation that the
        agent shipped — verify it carries the resolved app/server/env so
        the breadcrumb is actionable, not just a 'configured' marker."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {
                "enable_pyroscope": "true",
                "pyroscope_server_url": "http://prof.internal:4040",
                "environment": "staging",
            }.get(key, default)

        with caplog.at_level("INFO"):
            with patch("services.site_config.site_config.get", side_effect=_fake_get), \
                 patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
                setup_pyroscope("brain-daemon")

        msgs = " ".join(r.message for r in caplog.records)
        assert "brain-daemon" in msgs
        assert "http://prof.internal:4040" in msgs
        assert "staging" in msgs
