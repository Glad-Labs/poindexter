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
