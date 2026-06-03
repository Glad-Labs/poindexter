"""Unit tests for the always-on LiveKit bot's proactive JWT refresh (#564).

The always-on ``voice-agent-livekit`` bot mints a fixed-TTL join JWT and
joins room ``poindexter`` indefinitely. Before #564 the TTL was a flat
6 hours: once the JWT expired, the LiveKit rtc Room's *native* reconnect
re-used the original (now-expired) token and got
``401 Unauthorized - no permissions to access the room`` — and the bot
could not self-recover until the container restart-policy bounced it.

Root cause (verified against the installed SDKs):
  * ``pipecat`` ``LiveKitTransportClient`` stores the token once at
    construction (``self._token``) and the rtc ``Room`` ships it to the
    native/rust FFI engine a single time in ``connect()``.
  * The engine's automatic reconnect re-uses that captured token; updating
    ``self._token`` afterward is *not* pushed back down to the engine.
  * Therefore the only way to present a fresh token is a controlled
    reconnect — a fresh ``room.connect(url, new_token, ...)`` handshake.

The fix is a proactive refresh loop that re-mints a fresh JWT *before* the
current one expires and drives a controlled reconnect so the bot stays
authenticated indefinitely. These tests pin that contract; they stub the
Pipecat / LiveKit SDK entirely (it lives only in the voice-agent Docker
image, never in the unit-test venv) and make no network calls.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Minimal Pipecat / LiveKit stubs so ``import services.voice_agent_livekit``
# resolves without the heavy deps. Mirrors the stub set in
# test_voice_agent_service_mode.py (kept independent so either test file can
# run in isolation).
# ---------------------------------------------------------------------------


def _ensure_pipecat_stubs() -> None:
    if "pipecat.transports.livekit.transport" in sys.modules:
        return

    def _stub(name: str, **attrs: Any) -> None:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules.setdefault(name, mod)

    for pkg in (
        "pipecat",
        "pipecat.pipeline",
        "pipecat.transports",
        "pipecat.transports.livekit",
    ):
        _stub(pkg)
    _stub(
        "pipecat.pipeline.runner",
        PipelineRunner=type("PipelineRunner", (), {"__init__": lambda self, **kw: None}),
    )
    _stub(
        "pipecat.transports.livekit.transport",
        LiveKitParams=type("LiveKitParams", (), {"__init__": lambda self, **kw: None}),
        LiveKitTransport=type(
            "LiveKitTransport",
            (),
            {
                "__init__": lambda self, **kw: None,
                "event_handler": lambda self, name: (lambda fn: fn),
            },
        ),
    )
    _stub("livekit")

    class _AccessToken:
        def __init__(self, **kw):
            pass

        def with_identity(self, *_a, **_kw):
            return self

        def with_name(self, *_a, **_kw):
            return self

        def with_grants(self, *_a, **_kw):
            return self

        def with_ttl(self, *_a, **_kw):
            return self

        def to_jwt(self):
            return "stub-jwt"

    _stub(
        "livekit.api",
        AccessToken=_AccessToken,
        VideoGrants=type("VideoGrants", (), {"__init__": lambda self, **kw: None}),
    )

    # services.voice_agent re-exports build_voice_pipeline_task; stub it so
    # the import in voice_agent_livekit resolves without pulling Pipecat.
    if "services.voice_agent" not in sys.modules:
        _stub(
            "services.voice_agent",
            build_voice_pipeline_task=lambda *a, **kw: None,
        )


_ensure_pipecat_stubs()

from services import voice_agent_livekit  # noqa: E402


class _FakeSiteConfig:
    def __init__(self, values: dict[str, str] | None = None):
        self._values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


# ---------------------------------------------------------------------------
# Fake Pipecat transport: just enough surface for the refresh helper.
#
# The real chain is transport._client._room. _client holds the token the
# native engine captured at connect() time; a controlled reconnect is
# disconnect() then connect() after _client._token is swapped for a fresh
# JWT. The fake records the sequence so the test can assert the order.
# ---------------------------------------------------------------------------


class _FakeRoom:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def disconnect(self, *_a, **_kw) -> None:
        self.events.append("disconnect")


class _FakeClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._room = _FakeRoom()
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        # Mirror real behaviour: the token the engine will use on the next
        # handshake is whatever _token holds at connect() time.
        self._room.events.append(f"connect:{self._token}")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._room.events.append("client-disconnect")


class _FakeTransport:
    def __init__(self, token: str) -> None:
        self._client = _FakeClient(token)


# ---------------------------------------------------------------------------
# Interval resolution
# ---------------------------------------------------------------------------


def test_refresh_interval_defaults_to_ttl_minus_margin():
    """With no overrides the refresh fires one safety-margin before expiry.

    A 360-minute TTL with the default 5-minute margin should schedule the
    re-mint at 355 minutes — comfortably before the JWT actually expires so
    the reconnect always presents a still-valid replacement.
    """
    cfg = _FakeSiteConfig({})
    interval = voice_agent_livekit._resolve_token_refresh_interval_s(cfg, ttl_minutes=360)
    assert interval == (360 - 5) * 60


def test_refresh_interval_is_db_configurable():
    """Operators can tune the safety margin via app_settings (config-in-DB)."""
    cfg = _FakeSiteConfig({"voice_agent_token_refresh_margin_minutes": "30"})
    interval = voice_agent_livekit._resolve_token_refresh_interval_s(cfg, ttl_minutes=360)
    assert interval == (360 - 30) * 60


def test_refresh_interval_clamped_to_minimum_when_margin_exceeds_ttl():
    """A margin >= TTL must not yield a zero/negative sleep.

    Otherwise a misconfigured margin would spin the refresh loop in a tight
    busy-reconnect cycle. Clamp to a sane positive floor instead.
    """
    cfg = _FakeSiteConfig({"voice_agent_token_refresh_margin_minutes": "9999"})
    interval = voice_agent_livekit._resolve_token_refresh_interval_s(cfg, ttl_minutes=10)
    assert interval > 0


# ---------------------------------------------------------------------------
# Controlled reconnect with a freshly-minted token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_transport_token_remints_and_reconnects(monkeypatch):
    """The refresh re-mints a NEW JWT and reconnects with it presented.

    Critical: the new token must be installed on the client BEFORE the
    reconnect handshake, because the native engine only reads the token at
    connect() time. We assert (a) a fresh mint happened, (b) the client's
    stored token was swapped, and (c) the reconnect ran disconnect THEN
    connect, with the connect carrying the fresh token.
    """
    minted: list[dict[str, Any]] = []

    def _fake_mint(api_key, api_secret, *, identity, room, **kw):
        minted.append({"identity": identity, "room": room, **kw})
        return f"fresh-jwt-{len(minted)}"

    monkeypatch.setattr(voice_agent_livekit, "_mint_token", _fake_mint)

    transport = _FakeTransport(token="stale-6h-jwt")

    new_token = await voice_agent_livekit._refresh_transport_token(
        transport,
        api_key="k",
        api_secret="s",
        identity="poindexter-bot",
        room="poindexter",
    )

    assert new_token == "fresh-jwt-1"
    assert len(minted) == 1
    assert minted[0]["identity"] == "poindexter-bot"
    assert minted[0]["room"] == "poindexter"

    # The client's stored token was replaced with the fresh one.
    assert transport._client._token == "fresh-jwt-1"

    # The reconnect ran disconnect -> connect, and the connect handshake
    # carried the FRESH token (not the stale one).
    assert transport._client.disconnect_calls == 1
    assert transport._client.connect_calls == 1
    assert transport._client._room.events == [
        "client-disconnect",
        "connect:fresh-jwt-1",
    ]


@pytest.mark.asyncio
async def test_refresh_transport_token_handles_missing_client_gracefully(monkeypatch):
    """A transport without a wired ``_client`` must not crash the loop.

    Pipecat builds the client lazily; if the refresh fires in a window
    before setup() completed we degrade to a best-effort re-mint rather
    than tearing the bot down.
    """
    monkeypatch.setattr(voice_agent_livekit, "_mint_token", lambda *a, **kw: "fresh")

    transport = types.SimpleNamespace()  # no _client attribute

    # Must not raise.
    result = await voice_agent_livekit._refresh_transport_token(
        transport,
        api_key="k",
        api_secret="s",
        identity="poindexter-bot",
        room="poindexter",
    )
    assert result == "fresh"


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_refresh_loop_refreshes_each_interval(monkeypatch):
    """The loop sleeps the resolved interval then refreshes, repeatedly.

    We patch sleep to a no-op that records the interval and stop the loop
    after two refreshes so the test is deterministic and instant.
    """
    sleeps: list[float] = []
    refreshes = {"n": 0}

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def _fake_refresh(*_a, **_kw):
        refreshes["n"] += 1
        if refreshes["n"] >= 2:
            raise asyncio.CancelledError
        return "fresh"

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(voice_agent_livekit, "_refresh_transport_token", _fake_refresh)

    transport = _FakeTransport(token="t0")
    cfg = _FakeSiteConfig({})

    with pytest.raises(asyncio.CancelledError):
        await voice_agent_livekit._token_refresh_loop(
            transport,
            site_config=cfg,
            api_key="k",
            api_secret="s",
            identity="poindexter-bot",
            room="poindexter",
            ttl_minutes=360,
        )

    # Slept the default (TTL - margin) interval before each refresh.
    assert sleeps == [(360 - 5) * 60, (360 - 5) * 60]
    assert refreshes["n"] == 2


@pytest.mark.asyncio
async def test_token_refresh_loop_survives_transient_refresh_error(monkeypatch):
    """A single failed refresh must NOT kill the loop.

    Self-recovery is the whole point of #564 — one transient reconnect
    error should be logged and retried on the next tick, not propagate out
    and take the bot offline (the exact failure mode we are fixing).
    """
    refreshes = {"n": 0}

    async def _fake_sleep(_seconds: float) -> None:
        return None

    async def _flaky_refresh(*_a, **_kw):
        refreshes["n"] += 1
        if refreshes["n"] == 1:
            raise RuntimeError("transient 401 on reconnect")
        if refreshes["n"] >= 2:
            raise asyncio.CancelledError
        return "fresh"

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(voice_agent_livekit, "_refresh_transport_token", _flaky_refresh)

    transport = _FakeTransport(token="t0")
    cfg = _FakeSiteConfig({})

    with pytest.raises(asyncio.CancelledError):
        await voice_agent_livekit._token_refresh_loop(
            transport,
            site_config=cfg,
            api_key="k",
            api_secret="s",
            identity="poindexter-bot",
            room="poindexter",
            ttl_minutes=360,
        )

    # The loop kept going past the transient error (reached the 2nd tick).
    assert refreshes["n"] == 2


# ---------------------------------------------------------------------------
# run_bot wiring — the loop must actually be started alongside the pipeline
# and cancelled cleanly when the runner returns.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_bot_starts_and_cancels_refresh_loop(monkeypatch):
    """run_bot launches the refresh loop concurrently and cancels it on exit.

    This pins the wiring: without it the helper would exist but never run,
    and the bot would still 401 after 6h. We stub the whole Pipecat / DB
    chain and assert the loop was both started (cancellation observed) and
    awaited to completion before run_bot returns.
    """
    # --- stub the DB + bootstrap + site_config chain run_bot imports ---
    fake_pool = types.SimpleNamespace()

    async def _create_pool(*_a, **_kw):
        async def _close() -> None:
            return None

        fake_pool.close = _close
        return fake_pool

    fake_asyncpg = types.ModuleType("asyncpg")
    fake_asyncpg.create_pool = _create_pool
    monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)

    fake_brain = types.ModuleType("brain")
    fake_brain_bootstrap = types.ModuleType("brain.bootstrap")
    fake_brain_bootstrap.require_database_url = lambda **_kw: "postgres://stub"
    monkeypatch.setitem(sys.modules, "brain", fake_brain)
    monkeypatch.setitem(sys.modules, "brain.bootstrap", fake_brain_bootstrap)

    class _SiteConfig:
        def get(self, key, default=None):
            return default

        async def load(self, *_a, **_kw):
            return None

    fake_site_config_module = types.ModuleType("services.site_config")
    fake_site_config_module.SiteConfig = _SiteConfig
    monkeypatch.setitem(sys.modules, "services.site_config", fake_site_config_module)

    # Avoid touching the real brain-path resolver / pyroscope / pipeline.
    monkeypatch.setattr(voice_agent_livekit, "_ensure_brain_on_path", lambda: None)
    monkeypatch.setattr(
        voice_agent_livekit,
        "_resolve_livekit_creds",
        lambda *_a, **_kw: ("ws://stub:7880", "k", "s"),
    )
    monkeypatch.setattr(voice_agent_livekit, "_mint_token", lambda *a, **kw: "join-jwt")
    monkeypatch.setattr(voice_agent_livekit, "_resolve_brain_mode", lambda *_a, **_kw: "ollama")
    monkeypatch.setattr(
        voice_agent_livekit,
        "build_voice_pipeline_task",
        lambda *a, **kw: object(),
    )

    # Observe the loop being scheduled + cancelled. The fake loop signals
    # when it has started so the runner can deterministically wait for it
    # (mirrors reality: the real runner blocks for the bot's whole lifetime,
    # giving the concurrent refresh task ample time to start).
    loop_state = {"started": False, "cancelled": False}
    started_evt = asyncio.Event()

    async def _fake_loop(_transport, **_kw):
        loop_state["started"] = True
        started_evt.set()
        try:
            await asyncio.Event().wait()  # block until cancelled
        except asyncio.CancelledError:
            loop_state["cancelled"] = True
            raise

    monkeypatch.setattr(voice_agent_livekit, "_token_refresh_loop", _fake_loop)

    # A runner that waits for the refresh loop to start, then returns so
    # run_bot proceeds to teardown (which must cancel the loop).
    class _Runner:
        def __init__(self, **_kw):
            pass

        async def run(self, _task):
            await started_evt.wait()
            return None

    monkeypatch.setattr(voice_agent_livekit, "PipelineRunner", _Runner)

    await voice_agent_livekit.run_bot("poindexter", "poindexter-bot", brain="ollama")

    assert loop_state["started"] is True, "refresh loop was never scheduled"
    assert loop_state["cancelled"] is True, (
        "refresh loop was not cancelled on shutdown — it would leak past " "run_bot's lifetime"
    )
