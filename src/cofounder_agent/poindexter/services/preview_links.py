"""Operator-facing preview links: the one resolver for ``/preview/{token}`` URLs.

The worker serves a rendered draft at ``/preview/{token}``. Every place that
hands that page to the OPERATOR builds the link here, so they cannot disagree
about the base URL:

- the awaiting-approval Discord/Telegram message
  (``services/post_pipeline_actions.py``);
- the ``preview_url`` graph channel (``stage.verify_task`` and
  ``content.compile_meta``), which approval-gate artifacts surface;
- the Grafana approval-queue panel, which mirrors this derivation in SQL
  (``infrastructure/grafana/dashboards/pipeline-merged.json``).

The link has to resolve on the operator's device, usually a phone on the
tailnet, so the base is ``app_settings.preview_base_url``: over Tailscale, the
MagicDNS name. The name follows the node. A tailnet IP does not: the IP stored
here before the Pop!_OS migration kept every approval link dead for months.
Left empty, the base is derived as ``http://{operator_service_host}:8002``,
the host the Grafana dashboards already use for their worker-API links, so one
setting moves both.

Nothing inside the stack should fetch the page through this URL. Containers
resolve names with public DNS (compose pins 1.1.1.1 / 8.8.8.8), where a
``*.ts.net`` name lands on the Tailscale Funnel ingress and :8002 is
unreachable. The rendered-preview QA leg therefore never fetches the page: it
renders the in-flight draft in-process with ``services.preview_page``. See
``docs/architecture/preview-links.md``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# The worker API's published port (docker-compose ``ports: "8002:8002"``). It
# is used only for the derived default; an install that publishes the worker
# somewhere else sets ``preview_base_url`` explicitly.
WORKER_API_PORT = 8002

# ``operator_service_host`` is a bare hostname. ``scripts/_grafana_service_host.py``
# refuses anything else for the same reason: a scheme, port or path in it would
# be pasted into the link and produce a silently broken URL.
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_DEFAULT_HOST = "localhost"


def _read(config: Any, key: str) -> str:
    """Read ``key`` from a SiteConfig-shaped object (``.get(key, default)``).

    Accepts the kernel ``SiteConfig`` or a module's ``platform.config``
    capability. ``None`` (tests, ad-hoc callers) reads as unset. Never raises:
    a notification that cannot build its link must still go out.
    """
    if config is None:
        return ""
    try:
        return str(config.get(key, "") or "").strip()
    except Exception as exc:  # noqa: BLE001 - logged; the derived default still yields a working local link
        logger.warning(
            "[preview_links] reading app_settings.%s failed (%s); "
            "deriving the preview base instead",
            key, exc,
        )
        return ""


def operator_preview_base_url(config: Any) -> str:
    """Base URL the operator's device opens previews on, without a trailing slash.

    ``preview_base_url`` when set; otherwise ``http://{operator_service_host}:8002``,
    with ``localhost`` when that is unset or not a bare hostname.
    """
    explicit = _read(config, "preview_base_url").rstrip("/")
    if explicit:
        return explicit
    host = _read(config, "operator_service_host")
    if host and not _HOST_RE.match(host):
        logger.warning(
            "[preview_links] operator_service_host=%r is not a bare hostname "
            "(no scheme, port or path); deriving the preview base from %r",
            host, _DEFAULT_HOST,
        )
        host = ""
    return f"http://{host or _DEFAULT_HOST}:{WORKER_API_PORT}"


def operator_preview_url(config: Any, preview_token: str | None) -> str:
    """The operator's link to ``/preview/{preview_token}``; ``""`` when there is no token."""
    preview_token = (preview_token or "").strip()
    if not preview_token:
        return ""
    return f"{operator_preview_base_url(config)}/preview/{preview_token}"


__all__ = [
    "WORKER_API_PORT",
    "operator_preview_base_url",
    "operator_preview_url",
]
