"""Transient-network classification + connect-retry transport (stack#3161).

The worker container's resolver intermittently times out (glibc
``EAI_AGAIN`` — the DHCP-supplied upstream flaking, not NXDOMAIN), and a
single blip used to fail a whole scheduled job on its first request:
``sync_cloudflare_analytics`` and ``sync_affiliate_clicks`` each paged a
``job_failure`` for the same one-second resolver fault, eight times over
in the 08-01→08-07 window, and the alert-triage filer raised them as
separate issues because it had no way to see they were one fault.

Three exports:

* :func:`transient_retry_transport` — an ``httpx.AsyncHTTPTransport`` with
  connect-level retries. httpx retries exactly the right class natively
  (``ConnectError`` — which DNS failures surface as — and
  ``ConnectTimeout``), with built-in backoff, so a resolver blip becomes
  latency instead of a failed job. No hand-rolled retry loop. Safe for
  non-idempotent POSTs: only connection establishment is retried, request
  bodies are never replayed.
* :func:`is_transient_network_error` — the after-retries classifier: a
  request that STILL failed with this shape is a network fault, not a job
  fault. Callers treat it as a deferral (the tap-runner posture: declined,
  not broken — ``ok`` stays True, the next cycle retries) and emit ONE
  shared ``network_unreachable`` finding so a sustained outage pages once,
  not once per consumer.
* :func:`sidecar_connect_retries` — how many connect retries a request to a
  GPU sidecar gets (``gpu_sidecar_connect_retries``). RIFE, chatterbox,
  stable-audio and wan exit to give their CUDA context back, and a request
  that lands while Docker restarts one sees exactly the connect-phase
  failures the transport above retries.

``ReadTimeout`` is deliberately NOT transient here: the connection was
established, so the resolver is fine and the peer is slow — that is the
remote service's problem to page about, not a network deferral.

Implementation note: the classifier matches exception TYPE NAMES rather
than ``isinstance`` against ``httpx`` classes, and the transport builder
imports ``httpx`` at call time. Both on purpose: the job tests fake the
entire ``httpx`` module via ``patch.dict(sys.modules)``, where a
module-level import would trap the fake in this module's globals forever
(the known sys.modules-restore leak) and ``isinstance`` against a
MagicMock "class" raises ``TypeError`` inside the caller's except block.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: app_settings key for the sidecar connect retries, and its default (the
#: value settings_defaults.py seeds).
SIDECAR_CONNECT_RETRIES_KEY = "gpu_sidecar_connect_retries"
DEFAULT_SIDECAR_CONNECT_RETRIES = 5

# httpx's connect-phase failure classes, by name. DNS resolution failures
# surface as ConnectError; connect-phase deadline misses as ConnectTimeout.
_CONNECT_PHASE_EXCEPTION_NAMES: tuple[str, ...] = (
    "ConnectError",
    "ConnectTimeout",
)

# Substrings that identify a resolver fault when it arrives wrapped in a
# generic exception (asyncio wraps getaddrinfo failures inconsistently
# across httpx versions).
_RESOLVER_FAULT_MARKERS: tuple[str, ...] = (
    "Temporary failure in name resolution",
    "EAI_AGAIN",
    "Name or service not known",
    "getaddrinfo failed",
)


def transient_retry_transport(retries: int) -> Any:
    """Connect-retrying transport for one-shot outbound job clients."""
    import httpx

    return httpx.AsyncHTTPTransport(retries=max(0, int(retries)))


def is_transient_network_error(exc: BaseException) -> bool:
    """True when ``exc`` is the couldn't-even-connect class of failure."""
    if type(exc).__name__ in _CONNECT_PHASE_EXCEPTION_NAMES:
        return True
    text = str(exc)
    return any(marker in text for marker in _RESOLVER_FAULT_MARKERS)


def sidecar_connect_retries(site_config: Any) -> int:
    """Connect retries for a request to a GPU sidecar that may be restarting.

    RIFE, chatterbox, stable-audio and wan exit once idle to give their CUDA
    context back, which only a process exit returns, and Docker's restart
    policy brings them straight back. Measured 2026-09-25 from a container on
    the compose network, polling every 50 ms across such a restart: "Connection
    refused" from +0.13 s, "Temporary failure in name resolution" while the
    container was down, refused again, then HTTP 200 at +1.97 s. All
    ``ConnectError``, which :func:`transient_retry_transport` retries with
    0 / 0.5 / 1 / 2 / 4 s backoff, so the default 5 rides out ~7.5 s. Only the
    connection is retried; a request body is never sent twice. A sidecar that
    is really down fails that much later.

    Read with ``get`` (not ``get_int``) so any settings double works; an
    unparseable value warns and uses the default rather than disabling the
    retries.
    """
    if site_config is None:
        return DEFAULT_SIDECAR_CONNECT_RETRIES
    raw = site_config.get(SIDECAR_CONNECT_RETRIES_KEY, DEFAULT_SIDECAR_CONNECT_RETRIES)
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        logger.warning(
            "[net_transient] %s=%r is not an integer; using %d",
            SIDECAR_CONNECT_RETRIES_KEY, raw, DEFAULT_SIDECAR_CONNECT_RETRIES,
        )
        return DEFAULT_SIDECAR_CONNECT_RETRIES


__all__ = [
    "DEFAULT_SIDECAR_CONNECT_RETRIES",
    "SIDECAR_CONNECT_RETRIES_KEY",
    "is_transient_network_error",
    "sidecar_connect_retries",
    "transient_retry_transport",
]
