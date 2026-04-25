"""Unit tests for services/profiling.py.

Covers the enable_pyroscope gate, missing-package graceful path, and
the pyroscope.configure call shape.

Phase H (GH#95): setup_pyroscope now takes site_config as an explicit
first positional argument. Tests build a mock and pass it in.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_sc(values: dict | None = None) -> MagicMock:
    """SiteConfig mock passed to setup_pyroscope.

    ``values`` maps app_settings keys to stub return values. Missing keys
    return the caller-provided default, matching the real get() contract.
    """
    vals = values or {}
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": vals.get(k, d)
    sc.get_bool.side_effect = lambda k, d=False: vals.get(k, d)
    sc.get_int.side_effect = lambda k, d=0: vals.get(k, d)
    return sc


@pytest.mark.unit
class TestSetupPyroscope:
    def test_skips_when_disabled(self):
        from services.profiling import setup_pyroscope

        # Should exit cleanly without importing pyroscope.
        setup_pyroscope(_mock_sc({"enable_pyroscope": "false"}))

    def test_warns_when_enabled_but_package_missing(self, caplog):
        from services.profiling import setup_pyroscope

        sc = _mock_sc({"enable_pyroscope": "true"})
        with patch.dict("sys.modules", {"pyroscope": None}):
            with caplog.at_level("WARNING"):
                setup_pyroscope(sc)

        msgs = " ".join(r.message for r in caplog.records)
        assert "pyroscope-io not installed" in msgs

    def test_configure_called_on_enabled_with_package(self):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock()

        sc = _mock_sc({
            "enable_pyroscope": "true",
            "pyroscope_server_url": "http://pyroscope:4040",
            "environment": "production",
        })

        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc, service_name="test-service")

        fake_pyroscope.configure.assert_called_once()
        call_kwargs = fake_pyroscope.configure.call_args.kwargs
        assert call_kwargs["application_name"] == "test-service"
        assert call_kwargs["server_address"] == "http://pyroscope:4040"
        assert call_kwargs["tags"]["environment"] == "production"

    def test_configure_exception_does_not_raise(self):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock(side_effect=RuntimeError("boom"))

        sc = _mock_sc({"enable_pyroscope": "true"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            # Must not raise — profiling failure should never kill startup.
            setup_pyroscope(sc)

    def test_default_service_name_used_when_omitted(self):
        """service_name kwarg defaults to 'cofounder-agent' on both
        application_name and the tags['service'] label."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "true"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc)

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["application_name"] == "cofounder-agent"
        assert kwargs["tags"]["service"] == "cofounder-agent"

    def test_default_server_url_when_setting_absent(self):
        """Falls back to the docker-compose pyroscope address when the
        pyroscope_server_url setting is not configured."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "true"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc)

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["server_address"] == "http://pyroscope:4040"

    def test_default_environment_when_setting_absent(self):
        """environment tag defaults to 'development' when unset."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "true"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc)

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["tags"]["environment"] == "development"

    def test_environment_falls_back_when_empty_string(self):
        """The `or 'development'` guard kicks in when the setting exists
        but is the empty string."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "true", "environment": ""})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc)

        kwargs = fake_pyroscope.configure.call_args.kwargs
        assert kwargs["tags"]["environment"] == "development"

    def test_uppercase_TRUE_treated_as_enabled(self):
        """enable_pyroscope parses case-insensitively via .lower()."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "TRUE"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc)

        assert fake_pyroscope.configure.called

    def test_mixed_case_True_treated_as_enabled(self):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "True"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc)

        assert fake_pyroscope.configure.called

    def test_non_true_string_treated_as_disabled(self):
        """Truthy-looking values like 'yes' do NOT enable — only 'true'."""
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "yes"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            setup_pyroscope(sc)

        fake_pyroscope.configure.assert_not_called()

    def test_disabled_skips_pyroscope_import(self):
        """Disabled path must short-circuit before the import — blocking
        pyroscope in sys.modules should not break the call."""
        from services.profiling import setup_pyroscope

        sc = _mock_sc({"enable_pyroscope": "false"})
        with patch.dict("sys.modules", {"pyroscope": None}):
            setup_pyroscope(sc)  # would surface ImportError if import ran

    def test_info_log_emitted_on_successful_configure(self, caplog):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        sc = _mock_sc({"enable_pyroscope": "true"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            with caplog.at_level("INFO", logger="services.profiling"):
                setup_pyroscope(sc, service_name="my-svc")

        msgs = " ".join(r.message for r in caplog.records)
        assert "agent configured" in msgs
        assert "my-svc" in msgs

    def test_warning_log_emitted_when_configure_raises(self, caplog):
        from services.profiling import setup_pyroscope

        fake_pyroscope = MagicMock()
        fake_pyroscope.configure = MagicMock(side_effect=RuntimeError("oops"))
        sc = _mock_sc({"enable_pyroscope": "true"})
        with patch.dict("sys.modules", {"pyroscope": fake_pyroscope}):
            with caplog.at_level("WARNING", logger="services.profiling"):
                setup_pyroscope(sc)

        msgs = " ".join(r.message for r in caplog.records)
        assert "configure failed" in msgs
