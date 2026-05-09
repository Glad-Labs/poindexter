"""Continuous profiling via Pyroscope.

LGTM+P stack piece. When ``enable_pyroscope`` is true in app_settings,
the ``pyroscope-io`` Python agent ships CPU profiles to the Pyroscope
server at ``pyroscope_server_url`` (default ``http://pyroscope:4040``
in docker-compose.local.yml).

Opt-in via app_settings, not env vars — matches the ``enable_tracing``
pattern in services/telemetry.py. Safe to call even when the package
isn't installed; the function logs and returns cleanly.

DI seam (Glad-Labs/poindexter#406)
----------------------------------
Callers should pass a loaded ``SiteConfig`` instance via the
``site_config`` keyword arg — the function then reads
``enable_pyroscope`` / ``pyroscope_server_url`` / ``environment``
through that DI seam, matching how every other lifespan-init helper
(``setup_sentry``, ``setup_telemetry``, ``configure_langfuse_callback``)
takes a ``SiteConfig``. The argument is keyword-only on top of the
existing positional ``service_name`` so the test harness from PR #245
(which calls ``setup_pyroscope("brain-daemon")`` and patches the
module singleton) keeps working.

When ``site_config`` is omitted, the function falls back to the
module-level ``services.site_config.site_config`` singleton — that
remains the path the existing test suite drives. Per
``feedback_module_singleton_gotcha`` the singleton is empty post-Phase
H **at module import time**, but the lifespan rebinds it (main.py
~L186) so a singleton-based call still sees real values once startup
completes. New code should pass ``site_config=`` explicitly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.site_config import SiteConfig

logger = logging.getLogger(__name__)


def setup_pyroscope(
    service_name: str = "cofounder-agent",
    *,
    site_config: SiteConfig | None = None,
) -> None:
    """Configure the pyroscope-io agent if ``enable_pyroscope`` is true.

    Runs once at startup. The agent then samples the interpreter in a
    background thread and ships frames to the Pyroscope server.

    Args:
        service_name: Tag attached to the shipped profiles
            (``application_name`` + ``tags["service"]``). Pyroscope's
            Grafana queries slice on this label so use a unique value
            per process — ``poindexter-worker``, ``poindexter-brain``,
            ``poindexter-voice-livekit``, ``poindexter-voice-webrtc``.
        site_config: DI seam onto app_settings. When omitted, the
            function falls back to the module-level
            ``services.site_config.site_config`` singleton — the path
            the PR #245 test suite drives. New call sites should pass
            their loaded SiteConfig explicitly.
    """
    cfg: Any = site_config
    if cfg is None:
        # Build a fresh env-fallback SiteConfig so the function still
        # works for callers that haven't been migrated to pass an
        # explicit instance. Production callers (main.py lifespan,
        # brain daemon) thread the loaded SiteConfig through.
        try:
            import services.site_config as _scm
        except Exception as e:
            logger.debug("[PYROSCOPE] site_config unavailable: %s — skipping", e)
            return
        # Test-compat: the existing test rig patches
        # ``services.site_config.site_config.get`` so we read the module
        # attribute (NOT the alias-form import that falls dangling
        # after the lifespan rebind). Once ``services.site_config:226``
        # is deleted in commit 5, this attribute access will KeyError;
        # at that point the fallback should be ``_scm.SiteConfig()``.
        cfg = getattr(_scm, "site_config", None) or _scm.SiteConfig()

    enabled = cfg.get("enable_pyroscope", "false").lower() == "true"
    if not enabled:
        logger.debug("[PYROSCOPE] disabled via app_settings.enable_pyroscope")
        return

    server_url = cfg.get("pyroscope_server_url", "http://pyroscope:4040")
    try:
        import pyroscope  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "[PYROSCOPE] pyroscope-io not installed but enable_pyroscope=true. "
            "Install with: pip install pyroscope-io"
        )
        return

    environment = cfg.get("environment", "development") or "development"

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
