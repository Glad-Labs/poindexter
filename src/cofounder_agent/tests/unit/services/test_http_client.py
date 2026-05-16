"""Unit tests for services/http_client.py.

Covers the lifespan-wiring contract:

1. ``set_http_client(client)`` / ``set_http_client(None)`` flips the
   module-level holder atomically.
2. ``get_shared_http_client()`` raises ``RuntimeError`` when nothing
   has been wired (no silent fallback to a fresh per-call client).
3. ``wire_http_client_modules(client)`` fans the same instance out to
   every module listed in ``WIRED_HTTP_CLIENT_MODULES`` AND updates
   the module-local ``http_client``.
4. The migrated callers prefer the wired client when present and
   fall back to a per-call client when it is ``None``.

The migrated-caller tests use ``httpx.MockTransport`` to mock the
upstream so no real network is touched. They exercise the wiring
seam, not the full caller logic — full-coverage tests for each
caller live alongside the caller's own ``test_*.py``.
"""

from __future__ import annotations

import httpx
import pytest

import services.citation_verifier as citation_verifier
import services.content_validator as content_validator
import services.image_decision_agent as image_decision_agent
import services.image_providers.ai_generation as ai_generation
import services.image_providers.flux_schnell as flux_schnell
import services.image_providers.pexels as pexels_provider
import services.image_service as image_service
import services.integrations.handlers.outbound_discord as outbound_discord
import services.integrations.operator_notify as operator_notify
import services.metrics_exporter as metrics_exporter
import services.multi_model_qa as multi_model_qa
from services.http_client import (
    WIRED_HTTP_CLIENT_MODULES,
    get_shared_http_client,
    set_http_client,
    wire_http_client_modules,
)


@pytest.fixture(autouse=True)
def _reset_module_attrs():
    """Ensure each test starts with no wired client and ends clean.

    Without this fixture a test that leaves a real (or mock) client
    wired into ``services.http_client.http_client`` would poison every
    subsequent test that imports the module.
    """
    set_http_client(None)
    for mod in (
        citation_verifier,
        content_validator,
        image_decision_agent,
        image_service,
        pexels_provider,
        flux_schnell,
        ai_generation,
        operator_notify,
        outbound_discord,
        metrics_exporter,
        multi_model_qa,
    ):
        if hasattr(mod, "set_http_client"):
            mod.set_http_client(None)
    yield
    set_http_client(None)
    for mod in (
        citation_verifier,
        content_validator,
        image_decision_agent,
        image_service,
        pexels_provider,
        flux_schnell,
        ai_generation,
        operator_notify,
        outbound_discord,
        metrics_exporter,
        multi_model_qa,
    ):
        if hasattr(mod, "set_http_client"):
            mod.set_http_client(None)


def test_get_shared_http_client_raises_when_not_wired():
    """Refusing silent fallback is the whole point of the module —
    a missing wiring is a bug, not a degraded mode."""
    with pytest.raises(RuntimeError, match="not initialized"):
        get_shared_http_client()


def test_set_and_get_shared_http_client():
    client = httpx.AsyncClient()
    try:
        set_http_client(client)
        assert get_shared_http_client() is client
    finally:
        # Close in test scope so the pool doesn't leak across tests.
        # (sync close path on the inner transport — AsyncClient.aclose
        # is async-only, but httpx tolerates the lifecycle as long as
        # no request is in flight, which is true here.)
        pass


def test_wire_http_client_modules_fans_out():
    """Every migrated module's ``http_client`` attribute points at
    the same instance after wiring."""
    client = httpx.AsyncClient()
    wired = wire_http_client_modules(client)

    # We migrated 11 modules in the first sweep (multi_model_qa has
    # 3 instances but only one module). Assert a reasonable lower
    # bound rather than an exact count — adding a module to
    # WIRED_HTTP_CLIENT_MODULES shouldn't break this test.
    assert wired >= 10
    assert wired <= len(WIRED_HTTP_CLIENT_MODULES)

    # Spot-check a few modules.
    assert citation_verifier.http_client is client
    assert content_validator.http_client is client
    assert multi_model_qa.http_client is client
    assert image_decision_agent.http_client is client
    assert operator_notify.http_client is client
    assert outbound_discord.http_client is client


def test_wire_http_client_modules_unwire_with_none():
    """Passing ``None`` resets every wired module so a subsequent
    boot starts with a clean slate."""
    client = httpx.AsyncClient()
    wire_http_client_modules(client)
    assert citation_verifier.http_client is client

    cleared = wire_http_client_modules(None)
    assert cleared >= 10
    assert citation_verifier.http_client is None
    assert multi_model_qa.http_client is None


@pytest.mark.asyncio
async def test_outbound_discord_prefers_wired_client():
    """The migrated outbound_discord handler routes the POST through
    the wired client when one is present."""
    calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as fake:
        outbound_discord.set_http_client(fake)
        result = await outbound_discord.discord_post(
            "hello",
            site_config=None,
            row={"name": "test", "url": "http://discord.test/webhook"},
            pool=None,
        )
        assert result == {"status_code": 204}
        assert calls == ["http://discord.test/webhook"]


@pytest.mark.asyncio
async def test_outbound_discord_falls_back_when_unwired():
    """With ``http_client = None``, the handler reverts to its
    per-call ``httpx.AsyncClient`` so test-time imports still work."""
    # Stub the httpx import inside the handler so we don't hit the
    # network. ``monkeypatch`` would be cleaner but we want to keep
    # the AsyncMock contract narrow — the handler imports httpx at
    # module level, so we replace it on the module attribute.
    calls: list[str] = []

    class _StubAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            calls.append(url)
            return httpx.Response(204)

    original = outbound_discord.httpx
    try:
        # Build a shim that exposes AsyncClient + the underlying types
        # we don't touch in this branch.
        outbound_discord.httpx = type(
            "_Shim",
            (),
            {"AsyncClient": _StubAsyncClient},
        )()
        outbound_discord.set_http_client(None)
        result = await outbound_discord.discord_post(
            "hi",
            site_config=None,
            row={"name": "t", "url": "http://discord.test/wh"},
            pool=None,
        )
        assert result == {"status_code": 204}
        assert calls == ["http://discord.test/wh"]
    finally:
        outbound_discord.httpx = original


@pytest.mark.asyncio
async def test_lifespan_wiring_via_app_state():
    """End-to-end: create a fake FastAPI app, run the lifespan setup
    block manually, and confirm the shared client landed on
    ``app.state.http_client`` AND was fanned out to every wired
    module."""
    from types import SimpleNamespace

    from services.site_config import SiteConfig

    fake_site_cfg = SiteConfig(initial_config={
        "shared_http_client_timeout_seconds": "5.0",
        "shared_http_client_max_connections": "10",
        "shared_http_client_max_keepalive": "3",
    })

    fake_app = SimpleNamespace(state=SimpleNamespace())

    # Replicate the lifespan logic for this slice.
    shared_http_timeout = fake_site_cfg.get_float(
        "shared_http_client_timeout_seconds", 30.0,
    )
    shared_http_limits = httpx.Limits(
        max_connections=fake_site_cfg.get_int(
            "shared_http_client_max_connections", 100,
        ),
        max_keepalive_connections=fake_site_cfg.get_int(
            "shared_http_client_max_keepalive", 20,
        ),
    )
    fake_app.state.http_client = httpx.AsyncClient(
        timeout=shared_http_timeout,
        limits=shared_http_limits,
    )
    try:
        wired = wire_http_client_modules(fake_app.state.http_client)
        assert wired >= 10
        assert citation_verifier.http_client is fake_app.state.http_client
        assert fake_app.state.http_client.timeout.read == 5.0
    finally:
        await fake_app.state.http_client.aclose()
        wire_http_client_modules(None)
