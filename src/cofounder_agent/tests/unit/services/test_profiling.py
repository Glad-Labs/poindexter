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


# ---------------------------------------------------------------------------
# PR #245 edge cases — preserved alongside the HEAD edge-case suites above.
# These overlap with TestEnablePyroscopeParsing / TestSiteConfigUnavailable /
# TestDefaultsAndFallbacks but exercise slightly different scenarios
# (separate `test_empty_environment_string_*` and `test_default_environment_*`
# cases, an exact-equality `tags == {...}` assertion, etc.). Keeping both
# sets per merge instructions: additive coverage on profiling guarantees.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetupPyroscopeEdgeCases:
    """Edge cases not covered by TestSetupPyroscope:

    - default ``service_name`` when omitted
    - default ``pyroscope_server_url`` when not configured
    - empty ``environment`` value falling back via the ``or 'development'`` clause
    - mixed-case ``enable_pyroscope`` values (the lower() guard)
    - ``site_config`` import failure path
    - tag completeness
    - success-path logging
    """

    def test_default_service_name_used_when_omitted(self):
        """Calling setup_pyroscope() with no args must use the default
        application_name 'cofounder-agent' (matches the worker process)."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": "true"}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["application_name"] == "cofounder-agent"
        assert kwargs["tags"]["service"] == "cofounder-agent"

    def test_default_server_url_when_setting_missing(self):
        """When ``pyroscope_server_url`` is not in app_settings the fallback
        ``http://pyroscope:4040`` (the docker-compose service hostname)
        must be passed as ``server_address``."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            # Only enable_pyroscope is set; server URL falls through to default.
            return {"enable_pyroscope": "true"}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["server_address"] == "http://pyroscope:4040"

    def test_empty_environment_string_falls_back_to_development(self):
        """``site_config.get('environment', 'development')`` can still
        return an empty string when the key exists but is blank. The
        ``or 'development'`` clause guards that — verify the tag ends
        up as 'development', not ''."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {
                "enable_pyroscope": "true",
                "environment": "",  # explicitly blank — bug-class trap
            }.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["tags"]["environment"] == "development"

    def test_default_environment_is_development_when_unset(self):
        """When ``environment`` is missing entirely the ``get``-default
        path ('development') must apply."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": "true"}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["tags"]["environment"] == "development"

    @pytest.mark.parametrize("flag_value", ["TRUE", "True", "tRuE"])
    def test_mixed_case_true_enables_agent(self, flag_value):
        """The ``.lower() == 'true'`` guard must accept any casing —
        operator config edits are free-form strings."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": flag_value}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        fake_pyroscope.configure.assert_called_once()

    @pytest.mark.parametrize("flag_value", ["1", "yes", "on", ""])
    def test_non_true_strings_disable_agent(self, flag_value):
        """Only the literal string 'true' (case-insensitive) enables
        Pyroscope — '1', 'yes', 'on', or empty must NOT trip configure.
        Avoids accidental truthy interpretations diverging from the
        documented ``enable_pyroscope=true`` contract."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {"enable_pyroscope": flag_value}.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope()

        fake_pyroscope.configure.assert_not_called()

    def test_site_config_import_failure_returns_cleanly(self, caplog):
        """If ``services.site_config`` cannot be imported (broken DB
        bootstrap, missing module), setup_pyroscope must log at DEBUG
        and return without raising. Profiling is best-effort and must
        never block worker startup."""
        import builtins

        from services.profiling import setup_pyroscope

        real_import = builtins.__import__

        def _raising_import(name, *args, **kwargs):
            if name == "services.site_config":
                raise RuntimeError("simulated bootstrap failure")
            return real_import(name, *args, **kwargs)

        with caplog.at_level("DEBUG", logger="services.profiling"), \
             patch.object(builtins, "__import__", side_effect=_raising_import):
            # Must not raise.
            setup_pyroscope()

        msgs = " ".join(r.message for r in caplog.records)
        assert "site_config unavailable" in msgs

    def test_tags_include_both_service_and_environment(self):
        """Both required tag keys must be present — Pyroscope queries
        slice on these in Grafana dashboards (`service`, `environment`)."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {
                "enable_pyroscope": "true",
                "environment": "staging",
            }.get(key, default)

        with patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope("brain-daemon")

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["tags"] == {
            "service": "brain-daemon",
            "environment": "staging",
        }

    def test_logs_info_on_successful_configuration(self, caplog):
        """The success path must emit an INFO log so operators can
        confirm Pyroscope wired up at startup (otherwise a silent miss
        looks identical to it being disabled)."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        def _fake_get(key: str, default: str = "") -> str:
            return {
                "enable_pyroscope": "true",
                "pyroscope_server_url": "http://pyro.local:4040",
                "environment": "production",
            }.get(key, default)

        with caplog.at_level("INFO", logger="services.profiling"), \
             patch("services.site_config.site_config.get", side_effect=_fake_get), \
             patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope("worker-x")

        msgs = " ".join(r.message for r in caplog.records)
        assert "agent configured" in msgs
        assert "worker-x" in msgs
        assert "http://pyro.local:4040" in msgs
        assert "production" in msgs
