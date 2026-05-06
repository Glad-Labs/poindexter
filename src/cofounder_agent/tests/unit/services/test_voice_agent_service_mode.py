"""Unit tests for the always-on voice agent daemon entrypoints (#383).

Covers the enabled-flag handling, brain validation, and Pipecat-import
isolation that wraps the existing
``services.voice_agent_livekit.run_bot`` and
``services.voice_agent_webrtc._serve`` functions.

The Pipecat dependencies (pipecat, livekit, kokoro_onnx, sounddevice)
aren't installed in the unit-test environment — they live in the
voice-agent Docker image only. Each test stubs the relevant imports
before exercising the code path.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Lightweight Pipecat / LiveKit stubs so ``import services.voice_agent_*``
# resolves without the heavy dependencies actually being installed.
# ---------------------------------------------------------------------------


def _ensure_pipecat_stubs() -> None:
    """Inject just enough fake modules to satisfy import-time references.

    The functions under test never actually call into Pipecat — they
    short-circuit on the enabled flag or on a brain-name validation
    error. Stubs only need to exist; their attributes are exercised by
    the imported module's `from x import Y` statements.
    """
    if "pipecat" in sys.modules:
        return

    def _stub_module(name: str, **attrs: Any) -> types.ModuleType:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    # Top-level packages
    _stub_module("pipecat")
    _stub_module("pipecat.audio")
    _stub_module("pipecat.audio.vad")
    _stub_module(
        "pipecat.audio.vad.silero",
        SileroVADAnalyzer=type("SileroVADAnalyzer", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module(
        "pipecat.audio.vad.vad_analyzer",
        VADParams=type("VADParams", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module("pipecat.pipeline")
    _stub_module("pipecat.pipeline.pipeline", Pipeline=type("Pipeline", (), {"__init__": lambda self, *a, **kw: None}))
    _stub_module("pipecat.pipeline.runner", PipelineRunner=type("PipelineRunner", (), {"__init__": lambda self, **kw: None}))
    _stub_module(
        "pipecat.pipeline.task",
        PipelineParams=type("PipelineParams", (), {"__init__": lambda self, **kw: None}),
        PipelineTask=type("PipelineTask", (), {"__init__": lambda self, *a, **kw: None}),
    )
    _stub_module("pipecat.processors")
    _stub_module("pipecat.processors.aggregators")
    _stub_module(
        "pipecat.processors.aggregators.llm_context",
        LLMContext=type("LLMContext", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module(
        "pipecat.processors.aggregators.llm_response_universal",
        LLMContextAggregatorPair=type("LLMContextAggregatorPair", (), {"__init__": lambda self, **kw: None}),
        LLMUserAggregatorParams=type("LLMUserAggregatorParams", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module("pipecat.services")
    _stub_module("pipecat.services.kokoro")
    _kokoro_cls = type(
        "KokoroTTSService",
        (),
        {
            "__init__": lambda self, **kw: None,
            "Settings": type("Settings", (), {"__init__": lambda self, **kw: None}),
        },
    )
    _stub_module("pipecat.services.kokoro.tts", KokoroTTSService=_kokoro_cls)
    _stub_module("pipecat.services.ollama")
    _stub_module(
        "pipecat.services.ollama.llm",
        OLLamaLLMService=type("OLLamaLLMService", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module("pipecat.services.whisper")

    class _WhisperModel:
        BASE = "base"

        def __init__(self, value):
            self.value = value

    _stub_module(
        "pipecat.services.whisper.stt",
        Model=_WhisperModel,
        WhisperSTTService=type("WhisperSTTService", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module("pipecat.transports")
    _stub_module(
        "pipecat.transports.base_transport",
        BaseTransport=type("BaseTransport", (), {}),
        TransportParams=type("TransportParams", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module("pipecat.transports.livekit")
    _stub_module(
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
    _stub_module("pipecat.transports.smallwebrtc")
    _stub_module(
        "pipecat.transports.smallwebrtc.connection",
        SmallWebRTCConnection=type("SmallWebRTCConnection", (), {}),
    )
    _stub_module(
        "pipecat.transports.smallwebrtc.request_handler",
        IceCandidate=type("IceCandidate", (), {"__init__": lambda self, **kw: None}),
        SmallWebRTCPatchRequest=type("SmallWebRTCPatchRequest", (), {"__init__": lambda self, **kw: None}),
        SmallWebRTCRequest=type("SmallWebRTCRequest", (), {"__init__": lambda self, **kw: None}),
        SmallWebRTCRequestHandler=type("SmallWebRTCRequestHandler", (), {"__init__": lambda self, **kw: None}),
    )
    _stub_module(
        "pipecat.transports.smallwebrtc.transport",
        SmallWebRTCTransport=type(
            "SmallWebRTCTransport",
            (),
            {
                "__init__": lambda self, **kw: None,
                "event_handler": lambda self, name: (lambda fn: fn),
            },
        ),
    )
    _stub_module("pipecat_ai_small_webrtc_prebuilt")
    _stub_module(
        "pipecat_ai_small_webrtc_prebuilt.frontend",
        SmallWebRTCPrebuiltUI=type("SmallWebRTCPrebuiltUI", (), {}),
    )

    # livekit (stub the api submodule with the symbols voice_agent_livekit
    # imports at module load).
    _stub_module("livekit")

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

    _stub_module(
        "livekit.api",
        AccessToken=_AccessToken,
        VideoGrants=type("VideoGrants", (), {"__init__": lambda self, **kw: None}),
    )


_ensure_pipecat_stubs()


# Now that stubs are in place, importing the module is safe.
from services import voice_agent_livekit  # noqa: E402


class _FakeSiteConfig:
    """Minimal SiteConfig stand-in: just a get(key, default) method."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    async def load(self, *_a, **_kw) -> None:  # noqa: D401
        return None


class _FakePool:
    async def close(self) -> None:
        return None


@pytest.fixture
def fake_pool_and_config(monkeypatch):
    """Patch asyncpg.create_pool + SiteConfig + brain.bootstrap so
    ``run_service()`` can be exercised without touching Postgres.
    """
    fake_pool = _FakePool()

    async def _create_pool(*_a, **_kw):
        return fake_pool

    fake_asyncpg = types.ModuleType("asyncpg")
    fake_asyncpg.create_pool = _create_pool
    monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)

    fake_brain = types.ModuleType("brain")
    fake_brain_bootstrap = types.ModuleType("brain.bootstrap")
    fake_brain_bootstrap.require_database_url = lambda **_kw: "postgres://stub"
    monkeypatch.setitem(sys.modules, "brain", fake_brain)
    monkeypatch.setitem(sys.modules, "brain.bootstrap", fake_brain_bootstrap)

    # services.site_config is the canonical home of SiteConfig in the
    # real codebase; replace it with a swappable handle for the test.
    site_cfg_holder: dict[str, _FakeSiteConfig] = {"cfg": _FakeSiteConfig({})}

    fake_site_config_module = types.ModuleType("services.site_config")

    class _SwappableSiteConfig:
        def __init__(self):
            self._delegate = site_cfg_holder["cfg"]

        def get(self, key, default=None):
            return self._delegate.get(key, default)

        async def load(self, *_a, **_kw):
            return None

    fake_site_config_module.SiteConfig = _SwappableSiteConfig
    monkeypatch.setitem(sys.modules, "services.site_config", fake_site_config_module)

    return site_cfg_holder


@pytest.mark.asyncio
async def test_run_service_exits_zero_when_disabled(fake_pool_and_config, caplog):
    """Disabled toggle -> exit 0 + log message + run_bot never called."""
    fake_pool_and_config["cfg"] = _FakeSiteConfig(
        {"voice_agent_livekit_enabled": "false"},
    )

    called = {"run_bot": False}

    async def _fake_run_bot(*_a, **_kw):
        called["run_bot"] = True

    voice_agent_livekit.run_bot = _fake_run_bot  # type: ignore[assignment]

    rc = await voice_agent_livekit.run_service()

    assert rc == 0
    assert called["run_bot"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", ["false", "False", "0", "no", "off", "OFF"])
async def test_run_service_disabled_accepts_common_falsy_strings(
    fake_pool_and_config, flag_value,
):
    """All the common ways an operator might write 'off' should disable
    the surface — explicit list per feedback_no_silent_defaults (silent
    fallthrough on a typo would be a bug, so we whitelist the intent).
    """
    fake_pool_and_config["cfg"] = _FakeSiteConfig(
        {"voice_agent_livekit_enabled": flag_value},
    )

    called = {"run_bot": False}

    async def _fake_run_bot(*_a, **_kw):
        called["run_bot"] = True

    voice_agent_livekit.run_bot = _fake_run_bot  # type: ignore[assignment]

    rc = await voice_agent_livekit.run_service()
    assert rc == 0
    assert called["run_bot"] is False


@pytest.mark.asyncio
async def test_run_service_rejects_invalid_brain(fake_pool_and_config):
    """Unknown brain value -> SystemExit, no silent fallback to ollama."""
    fake_pool_and_config["cfg"] = _FakeSiteConfig(
        {
            "voice_agent_livekit_enabled": "true",
            "voice_agent_brain": "totally-not-a-brain",
        },
    )

    with pytest.raises(SystemExit) as excinfo:
        await voice_agent_livekit.run_service()
    msg = str(excinfo.value)
    assert "totally-not-a-brain" in msg
    assert "ollama" in msg and "claude-code" in msg


@pytest.mark.asyncio
async def test_run_service_passes_settings_to_run_bot(fake_pool_and_config):
    """When enabled + valid brain, run_service() invokes run_bot with the
    DB-sourced room / identity / brain.
    """
    fake_pool_and_config["cfg"] = _FakeSiteConfig(
        {
            "voice_agent_livekit_enabled": "true",
            "voice_agent_room_name": "ops-standup",
            "voice_agent_identity": "poindexter-on-call",
            "voice_agent_brain": "claude-code",
        },
    )

    captured: dict[str, Any] = {}

    async def _fake_run_bot(room, identity, *, brain, **_kw):
        captured["room"] = room
        captured["identity"] = identity
        captured["brain"] = brain

    voice_agent_livekit.run_bot = _fake_run_bot  # type: ignore[assignment]

    rc = await voice_agent_livekit.run_service()
    assert rc == 0
    assert captured == {
        "room": "ops-standup",
        "identity": "poindexter-on-call",
        "brain": "claude-code",
    }


@pytest.mark.asyncio
async def test_run_service_uses_defaults_when_settings_absent(fake_pool_and_config):
    """No app_settings rows -> documented defaults
    (poindexter / poindexter-bot / ollama).
    """
    fake_pool_and_config["cfg"] = _FakeSiteConfig({})

    captured: dict[str, Any] = {}

    async def _fake_run_bot(room, identity, *, brain, **_kw):
        captured["room"] = room
        captured["identity"] = identity
        captured["brain"] = brain

    voice_agent_livekit.run_bot = _fake_run_bot  # type: ignore[assignment]

    rc = await voice_agent_livekit.run_service()
    assert rc == 0
    assert captured == {
        "room": "poindexter",
        "identity": "poindexter-bot",
        "brain": "ollama",
    }


def test_resolve_livekit_creds_prefers_site_config_over_env(monkeypatch):
    """site_config.voice_agent_livekit_url overrides LIVEKIT_URL env."""
    monkeypatch.setenv("LIVEKIT_URL", "ws://wrong:9999")
    site_cfg = _FakeSiteConfig({"voice_agent_livekit_url": "ws://livekit:7880"})
    url, _key, _secret = voice_agent_livekit._resolve_livekit_creds(site_cfg)
    assert url == "ws://livekit:7880"


def test_resolve_livekit_creds_falls_back_to_env_when_setting_missing(monkeypatch):
    """Empty / missing site_config row -> use LIVEKIT_URL env value."""
    monkeypatch.setenv("LIVEKIT_URL", "ws://from-env:7880")
    site_cfg = _FakeSiteConfig({})
    url, _key, _secret = voice_agent_livekit._resolve_livekit_creds(site_cfg)
    assert url == "ws://from-env:7880"


def test_resolve_livekit_creds_dev_default(monkeypatch):
    """No site_config + no env -> hardcoded localhost dev fallback."""
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    url, _key, _secret = voice_agent_livekit._resolve_livekit_creds(None)
    assert url == "ws://localhost:7880"


# ---------------------------------------------------------------------------
# Regression tests for the IN_PROGRESS sentinel bug (2026-05-05).
#
# Pipecat's ``register_direct_function`` runner ignores the function's
# return value — results MUST flow back through
# ``params.result_callback(...)``. Prior to the fix, our three voice
# tools returned strings instead of calling the callback, so the literal
# "IN_PROGRESS" placeholder that Pipecat stamps into the LLM context on
# ``FunctionCallInProgressFrame`` never got replaced. The LLM saw
# "IN_PROGRESS" as the tool result, decided the call hadn't completed,
# and re-issued it on every turn.
#
# These tests construct a fake ``FunctionCallParams`` with a recording
# callback, invoke each tool, and assert (a) the callback fired exactly
# once, (b) with the actual tool output, (c) NOT with the "IN_PROGRESS"
# sentinel that would indicate a regression.
# ---------------------------------------------------------------------------


class _RecordingCallback:
    """Stand-in for Pipecat's ``FunctionCallResultCallback`` Protocol.

    Records every invocation so the test can assert exactly one final
    result was delivered, and that the payload isn't the IN_PROGRESS
    sentinel.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    async def __call__(self, result: Any, *, properties: Any = None) -> None:
        self.calls.append((result, properties))


def _fake_params(callback: _RecordingCallback) -> Any:
    """Build a minimal stand-in for ``FunctionCallParams``.

    The tool only touches ``params.result_callback`` — everything else
    (function_name, llm, context, tool_resources) is unused by our
    handlers, so a duck-typed object is enough.
    """
    return types.SimpleNamespace(result_callback=callback)


@pytest.fixture
def stub_worker_get(monkeypatch):
    """Patch ``_worker_get`` so the tools don't try a real HTTP call.

    Returns a ``dict[path, response]`` the test populates per scenario;
    a missing path raises ``RuntimeError`` so the assertion failure is
    obvious instead of a generic transport error.
    """
    responses: dict[str, Any] = {}

    async def _fake(path: str) -> Any:
        if path not in responses:
            raise RuntimeError(f"unexpected _worker_get path: {path}")
        return responses[path]

    monkeypatch.setattr(voice_agent_livekit, "_worker_get", _fake)
    return responses


@pytest.mark.asyncio
async def test_check_pipeline_health_delivers_result_via_callback(stub_worker_get):
    """check_pipeline_health calls params.result_callback with the real payload.

    Regression: prior to the fix this returned a str (silently dropped
    by Pipecat's runner), leaving the LLM to see only the IN_PROGRESS
    sentinel.
    """
    stub_worker_get["/api/health"] = {
        "status": "ok",
        "components": {
            "database": "connected",
            "task_executor": {
                "running": True,
                "total_processed": 42,
                "in_progress_count": 1,
                "pending_task_count": 3,
            },
            "gpu": {"busy": False, "owner": None},
        },
    }
    cb = _RecordingCallback()

    # Tools registered via register_direct_function have their RETURN
    # value discarded — only the callback flows back into the LLM
    # context. Don't assert on the awaited value; assert on the callback.
    await voice_agent_livekit.check_pipeline_health(_fake_params(cb))

    assert len(cb.calls) == 1, (
        "check_pipeline_health must call params.result_callback exactly "
        "once per Pipecat's contract — anything else leaves the IN_PROGRESS "
        "placeholder in the LLM context forever."
    )
    result, _props = cb.calls[0]
    assert result != "IN_PROGRESS", (
        "Tool delivered the IN_PROGRESS sentinel itself — this would round-"
        "trip into the context and confuse the LLM into re-calling the tool."
    )
    assert "system status is ok" in result
    assert "database is connected" in result
    assert "worker is running" in result
    assert "GPU is idle" in result


@pytest.mark.asyncio
async def test_get_published_post_count_delivers_result_via_callback(stub_worker_get):
    """get_published_post_count calls params.result_callback with the count."""
    stub_worker_get["/api/posts?limit=1"] = {"total": 51}
    cb = _RecordingCallback()

    await voice_agent_livekit.get_published_post_count(_fake_params(cb))

    assert len(cb.calls) == 1
    result, _props = cb.calls[0]
    assert result != "IN_PROGRESS"
    assert "51" in result
    assert "published posts" in result


@pytest.mark.asyncio
async def test_get_ai_spending_status_delivers_result_via_callback(stub_worker_get):
    """get_ai_spending_status calls params.result_callback with totals."""
    stub_worker_get["/api/costs/summary"] = {
        "today_usd": "1.23",
        "month_usd": "45.67",
    }
    cb = _RecordingCallback()

    await voice_agent_livekit.get_ai_spending_status(_fake_params(cb))

    assert len(cb.calls) == 1
    result, _props = cb.calls[0]
    assert result != "IN_PROGRESS"
    assert "1.23" in result
    assert "45.67" in result


@pytest.mark.asyncio
async def test_tool_failure_still_delivers_callback(stub_worker_get):
    """Even when the worker call fails, the tool MUST call result_callback.

    Otherwise the IN_PROGRESS placeholder leaks into the context and the
    LLM keeps re-issuing the call. The error message itself is fine —
    what matters is that *something* flows back.
    """
    cb = _RecordingCallback()

    # No stub registered for /api/health -> _worker_get raises -> tool
    # must still deliver via callback.
    await voice_agent_livekit.check_pipeline_health(_fake_params(cb))

    assert len(cb.calls) == 1, (
        "Tool must always close the FunctionCallInProgressFrame loop, "
        "even on failure — otherwise the IN_PROGRESS placeholder leaks."
    )
    result, _props = cb.calls[0]
    assert result != "IN_PROGRESS"
    assert "Could not reach the worker" in result


def test_default_tools_match_pipecat_direct_function_protocol():
    """All registered tools must be ``async def fn(params, ...)``.

    Pipecat's ``DirectFunctionWrapper.validate_function`` enforces this
    at register time — if a sync function or a wrong-named first param
    sneaks in, ``register_direct_function`` raises immediately and the
    tool surface vanishes silently (the bot just stops being able to
    answer those questions).

    We can't import DirectFunctionWrapper here without the real Pipecat
    install, so re-implement its checks: must be a coroutine function
    AND must have its first parameter named ``params``.
    """
    import asyncio
    import inspect

    for fn in voice_agent_livekit._DEFAULT_TOOLS:
        assert asyncio.iscoroutinefunction(fn), (
            f"voice tool {fn.__name__} must be `async def` per Pipecat's "
            "DirectFunctionWrapper contract; sync functions are silently "
            "rejected at register time."
        )
        params = list(inspect.signature(fn).parameters)
        assert params, f"voice tool {fn.__name__} must accept a `params` argument"
        assert params[0] == "params", (
            f"voice tool {fn.__name__} first parameter must be named "
            "'params' — Pipecat strips it from the LLM-facing schema by "
            "name match, and otherwise refuses to register the function."
        )
