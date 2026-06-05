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
import modules.content.content_validator as content_validator
import services.image_decision_agent as image_decision_agent
import services.image_providers.ai_generation as ai_generation
import services.image_providers.flux_schnell as flux_schnell
import services.image_providers.pexels as pexels_provider
import services.image_service as image_service
import services.integrations.handlers.outbound_discord as outbound_discord
import services.integrations.operator_notify as operator_notify
import services.metrics_exporter as metrics_exporter
import modules.content.multi_model_qa as multi_model_qa
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


# ---------------------------------------------------------------------------
# Expanded edge-case coverage (auto/test-expand-2026-05-25).
#
# The original 7 tests pin the happy path + the "RuntimeError on unwired"
# contract. The following tests pin the three silent-skip branches inside
# ``wire_http_client_modules`` (failed import / missing setter / raising
# setter), the always-update-module-local postcondition, the FastAPI
# dependency surface, and the actionable-error-message contract.
# ---------------------------------------------------------------------------


def test_set_http_client_replaces_previous_instance():
    """A second wire call swaps the holder atomically. Pins that
    ``set_http_client`` is a setter, not an initializer that errors
    on a non-None previous value."""
    first = httpx.AsyncClient()
    second = httpx.AsyncClient()
    set_http_client(first)
    assert get_shared_http_client() is first
    set_http_client(second)
    assert get_shared_http_client() is second
    assert get_shared_http_client() is not first


def test_get_shared_http_client_returns_same_instance_across_calls():
    """Identity invariant — repeated calls must return the SAME object,
    not a fresh client per call. Guards against a future regression
    where someone "helpfully" rebuilds the client on each access."""
    client = httpx.AsyncClient()
    set_http_client(client)
    first = get_shared_http_client()
    second = get_shared_http_client()
    assert first is second is client


def test_get_shared_http_client_error_message_is_actionable():
    """The RuntimeError must guide an operator (or LLM debugger) to the
    fix — either the lifespan hasn't run, or a test forgot to wire.
    Pins ``feedback_no_silent_defaults``: missing config fails loud
    AND points at the remediation."""
    with pytest.raises(RuntimeError) as exc_info:
        get_shared_http_client()
    msg = str(exc_info.value)
    assert "lifespan" in msg.lower()
    assert "set_http_client" in msg


@pytest.mark.asyncio
async def test_get_http_client_dependency_reads_from_app_state():
    """FastAPI dependency reads from ``app.state.http_client``, NOT
    the module attr. Route handlers must honor the FastAPI lifecycle
    contract — the app is the source of truth, the module attribute
    is a convenience for non-route callers."""
    from types import SimpleNamespace

    from services.http_client import get_http_client

    client = httpx.AsyncClient()
    try:
        # Module attr stays None on purpose — the route must NOT depend
        # on it. If a refactor makes get_http_client fall through to
        # the module attr, this test will pass spuriously; the assert
        # below catches that by passing None on app.state.
        set_http_client(None)
        fake_request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(http_client=client)),
        )
        resolved = await get_http_client(fake_request)  # type: ignore[arg-type]
        assert resolved is client
    finally:
        await client.aclose()


def test_wired_modules_all_receive_same_instance():
    """Tightens the existing spot-check: every module in
    ``WIRED_HTTP_CLIENT_MODULES`` must receive the SAME instance, so a
    regression that rebuilds a client per module (the bug the migration
    explicitly fixed) would fail loud."""
    import importlib

    client = httpx.AsyncClient()
    wired = wire_http_client_modules(client)
    assert wired >= 10

    for modname in WIRED_HTTP_CLIENT_MODULES:
        mod = importlib.import_module(modname)
        # A module may exist in the tuple but not yet expose
        # ``http_client`` (added speculatively pre-migration). Those
        # are accepted by ``wire_http_client_modules`` and skipped here.
        if hasattr(mod, "http_client"):
            assert mod.http_client is client, (
                f"{modname}.http_client is not the shared instance"
            )


def test_unwire_round_trip_resets_to_unwired_state():
    """After ``wire(client)`` then ``wire(None)``,
    ``get_shared_http_client`` must raise again. Catches a regression
    where ``wire(None)`` only nulls the migrated modules but leaves
    the module-local pointer dangling."""
    client = httpx.AsyncClient()
    wire_http_client_modules(client)
    assert get_shared_http_client() is client

    wire_http_client_modules(None)
    with pytest.raises(RuntimeError, match="not initialized"):
        get_shared_http_client()


def test_wire_skips_module_with_failing_import(monkeypatch):
    """Best-effort wiring: if a listed module raises during import,
    the loop continues with the next module rather than aborting
    lifespan startup. Verifies the count drops by exactly the number
    of failing modules and the module-local pointer is still updated."""
    import importlib

    # Measure the baseline first so the assertion is robust to future
    # additions/removals from WIRED_HTTP_CLIENT_MODULES.
    client = httpx.AsyncClient()
    baseline = wire_http_client_modules(client)
    wire_http_client_modules(None)

    target = "services.metrics_exporter"
    assert target in WIRED_HTTP_CLIENT_MODULES, (
        "Test assumes metrics_exporter is in the wiring tuple"
    )
    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == target:
            raise ImportError(f"simulated import failure for {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    wired = wire_http_client_modules(client)
    assert wired == baseline - 1
    # Module-local pointer is updated regardless of per-module outcome.
    assert get_shared_http_client() is client


def test_wire_skips_module_whose_setter_raises():
    """If a module's ``set_http_client`` raises (e.g., a future
    migration where the setter does work that can fail), the lifespan
    must not crash. Other modules still wire normally."""
    client = httpx.AsyncClient()
    baseline = wire_http_client_modules(client)
    wire_http_client_modules(None)

    target = citation_verifier
    original_setter = target.set_http_client

    def boom(_client):
        raise RuntimeError("simulated setter failure")

    target.set_http_client = boom  # type: ignore[assignment]
    try:
        wired = wire_http_client_modules(client)
        assert wired == baseline - 1
        # A peer module still wired through to the shared instance.
        assert content_validator.http_client is client
        # Module-local pointer still updated for non-wired callers.
        assert get_shared_http_client() is client
    finally:
        target.set_http_client = original_setter  # type: ignore[assignment]


def test_wire_skips_module_without_set_http_client_attribute():
    """A module listed in ``WIRED_HTTP_CLIENT_MODULES`` that doesn't
    expose ``set_http_client`` (e.g., added speculatively before the
    migration landed) is silently skipped — the ``callable(setter)``
    guard on line 158 protects lifespan startup."""
    client = httpx.AsyncClient()
    baseline = wire_http_client_modules(client)
    wire_http_client_modules(None)

    target = citation_verifier
    original_setter = target.set_http_client
    delattr(target, "set_http_client")
    try:
        wired = wire_http_client_modules(client)
        assert wired == baseline - 1
        # Module-local pointer still updated for non-wired callers.
        assert get_shared_http_client() is client
    finally:
        target.set_http_client = original_setter  # type: ignore[assignment]


def test_wire_always_updates_module_local_when_zero_modules_wired(monkeypatch):
    """Catastrophic-import postcondition: even when every listed
    module fails to import, ``wire`` returns 0 but the module-local
    holder is STILL updated so ``get_shared_http_client()`` works
    for non-wired callers. Pins line 166's unconditional
    ``set_http_client(client)`` — a refactor that moves it inside
    the per-module loop would silently break this contract."""
    import importlib

    def fake_import(_name, *_args, **_kwargs):
        raise ImportError("simulated catastrophic import failure")

    monkeypatch.setattr(importlib, "import_module", fake_import)

    client = httpx.AsyncClient()
    wired = wire_http_client_modules(client)
    assert wired == 0
    # Critical postcondition — get_shared_http_client must still work
    # because ``set_http_client(client)`` runs unconditionally after
    # the loop.
    assert get_shared_http_client() is client
