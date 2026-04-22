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
