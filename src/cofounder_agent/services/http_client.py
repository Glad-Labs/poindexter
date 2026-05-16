"""Shared httpx.AsyncClient — lifespan-bound process-wide connection pool.

Why this exists
---------------
Pre-2026-05-16 the codebase had 102 per-call ``httpx.AsyncClient(...)``
instantiations across 58 files in ``services/``. Each ``async with``
block opens + tears down its own connection pool (TCP + TLS handshake
amortised over a single request). Under concurrent pipeline runs
this is wasteful — a single content task could spin up 10+ short-lived
pools against the same handful of hosts (Ollama / SDXL / Pexels /
Discord / Vercel) when a single shared pool keeps the TLS sessions
warm across the whole run.

Architecture
------------
* ``main.py``'s lifespan constructs ONE ``httpx.AsyncClient`` with
  generous default ``timeout`` + ``Limits``, attaches it to
  ``app.state.http_client``, and fans it out to every module that
  exposes ``set_http_client()`` via ``wire_http_client_modules``.
* Route handlers depend on it via ``Depends(get_http_client)``.
* Services that aren't FastAPI handlers (jobs, stages, atoms,
  pipeline-stage helpers) read the per-module ``http_client``
  attribute populated by ``set_http_client()`` at lifespan startup.
* Tests construct their own ``httpx.AsyncClient`` (or use
  ``httpx.MockTransport``) and call ``set_http_client(client)`` on
  the module under test.

Per-request timeouts still work: passing ``timeout=`` to
``client.get()`` / ``.post()`` overrides the client default for that
single call, so per-caller policies (3s for health checks, 600s for
LLM generation) are preserved without each caller needing its own
pool.

The lifespan is responsible for ``await client.aclose()`` at
shutdown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from fastapi import Request


# Module-level holder. ``main.py`` lifespan calls ``set_http_client()``
# with the shared client; downstream modules import this module and
# read the attribute lazily, so a fresh worker boot points every
# caller at the SAME pool.
http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Wire the lifespan-bound shared ``httpx.AsyncClient``.

    Called once during ``main.py`` lifespan startup, and once with
    ``None`` at shutdown so unit tests that re-import these modules
    see a clean slate.

    Args:
        client: The shared client, or ``None`` to clear.
    """
    global http_client
    http_client = client


def get_shared_http_client() -> httpx.AsyncClient:
    """Return the lifespan-bound shared client.

    Raises:
        RuntimeError: If called before the lifespan has wired the
            client. This is intentional — silent fallback to a new
            ``httpx.AsyncClient()`` would defeat the whole point of
            this module (the leaked pool would never be reused or
            closed). Per ``feedback_no_silent_defaults``, fail loud.
    """
    if http_client is None:
        raise RuntimeError(
            "Shared httpx.AsyncClient is not initialized. Either the "
            "lifespan has not run yet (bug in caller — they're running "
            "before lifespan startup completes) or a test forgot to "
            "wire one via services.http_client.set_http_client(client)."
        )
    return http_client


async def get_http_client(request: "Request") -> httpx.AsyncClient:
    """FastAPI dependency exposing the lifespan-bound shared client.

    Usage in a route handler::

        from fastapi import Depends
        from services.http_client import get_http_client

        @router.get("/foo")
        async def foo(client: httpx.AsyncClient = Depends(get_http_client)):
            r = await client.get("https://example.com", timeout=5.0)
            return r.json()
    """
    # ``request.app.state.http_client`` is the canonical handle owned
    # by the lifespan. We read it through the app state (not the
    # module attribute) so route handlers respect FastAPI's lifecycle
    # contract — the app is the source of truth, the module attribute
    # is a convenience for non-route callers.
    return request.app.state.http_client


# ---------------------------------------------------------------------------
# Module wiring — replicates the SiteConfig set_site_config() fan-out so a
# single lifespan call points every migrated module at the SAME shared
# client. Mirrors ``services.di_wiring.WIRED_MODULES``; kept here so the
# http-client wiring concern lives next to the http-client module attribute.
# ---------------------------------------------------------------------------

# Module dotted-paths that expose a ``set_http_client(client)`` setter.
# Update this tuple when adding new migrated callers — the lifespan
# loops over the list at startup and at shutdown.
WIRED_HTTP_CLIENT_MODULES: tuple[str, ...] = (
    "services.citation_verifier",
    "services.content_validator",
    "services.image_decision_agent",
    "services.image_service",
    "services.image_providers.pexels",
    "services.image_providers.flux_schnell",
    "services.image_providers.ai_generation",
    "services.integrations.operator_notify",
    "services.integrations.handlers.outbound_discord",
    "services.metrics_exporter",
    "services.multi_model_qa",
)


def wire_http_client_modules(client: httpx.AsyncClient | None) -> int:
    """Point every migrated module's ``http_client`` attribute at ``client``.

    Mirrors the SiteConfig wiring pattern (``set_site_config``) so a
    single lifespan call propagates the shared client. Best-effort:
    a module that fails to import (optional dependency missing) is
    skipped silently rather than aborting startup.

    Args:
        client: The shared client to wire, or ``None`` at shutdown.

    Returns:
        Count of modules successfully wired.
    """
    import importlib

    wired = 0
    for modname in WIRED_HTTP_CLIENT_MODULES:
        try:
            mod = importlib.import_module(modname)
        except Exception:  # noqa: BLE001 — optional / lazy module
            continue
        setter = getattr(mod, "set_http_client", None)
        if callable(setter):
            try:
                setter(client)
                wired += 1
            except Exception:  # noqa: BLE001 — defensive: never crash lifespan
                continue
    # Always update the module-local ``http_client`` so
    # ``get_shared_http_client()`` works for non-wired callers too.
    set_http_client(client)
    return wired
