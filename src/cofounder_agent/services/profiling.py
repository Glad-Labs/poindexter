"""Continuous profiling via Pyroscope.

LGTM+P stack piece. When ``enable_pyroscope`` is true in app_settings,
the ``pyroscope-io`` Python agent ships CPU profiles to the Pyroscope
server at ``pyroscope_server_url`` (default ``http://pyroscope:4040``
in docker-compose.local.yml).

Opt-in via app_settings, not env vars — matches the ``enable_tracing``
pattern in services/telemetry.py. Safe to call even when the package
isn't installed; the function logs and returns cleanly.

Phase H (GH#95): ``site_config`` is now an explicit parameter instead
of a module-level singleton import.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def setup_pyroscope(
    site_config: Any,
    service_name: str = "cofounder-agent",
) -> None:
    """Configure the pyroscope-io agent if ``enable_pyroscope`` is true.

    Runs once at startup. The agent then samples the interpreter in a
    background thread and ships frames to the Pyroscope server.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95). Must be
            passed explicitly — the module-level singleton import was
            removed.
        service_name: Application label surfaced in Pyroscope.
    """
    enabled = site_config.get("enable_pyroscope", "false").lower() == "true"
    if not enabled:
        logger.debug("[PYROSCOPE] disabled via app_settings.enable_pyroscope")
        return

    server_url = site_config.get("pyroscope_server_url", "http://pyroscope:4040")
    try:
        import pyroscope  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "[PYROSCOPE] pyroscope-io not installed but enable_pyroscope=true. "
            "Install with: pip install pyroscope-io"
        )
        return

    environment = site_config.get("environment", "development") or "development"

    try:
        pyroscope.configure(
            application_name=service_name,
            server_address=server_url,
            tags={
                "service": service_name,
                "environment": environment,
            },
        )
        logger.info(
            "[PYROSCOPE] agent configured — app=%s server=%s env=%s",
            service_name, server_url, environment,
        )
    except Exception as e:
        logger.warning("[PYROSCOPE] configure failed: %s", e, exc_info=True)
