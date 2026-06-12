"""Pure-mock tests for ``audio_plane_pipecat.PipecatAudioMediaPlane``.

The real plane talks to LiveKit + faster-whisper + Silero + Kokoro --
none of those belong in the unit suite. We mock pipecat / livekit at the
boundary and verify the seams that matter:

1. ``connect()`` mints a token with the right room / identity / TTL and
   builds a transport against that token.
2. The in-pipeline ``_TranscriptCapture`` processor fans finalized
   ``TranscriptionFrame`` text to the bridge's ``on_utterance`` closure
   (Pipecat 1.2 dropped the ``register_event_handler('on_transcript')``
   hook PR#2 used).
3. ``speak()`` enqueues into the TTS pump (which converts to
   ``TTSSpeakFrame`` and feeds the Pipecat task).
4. ``disconnect()`` is idempotent and cleans up the runner / pump tasks.
5. ``resolve_audio_plane()`` honours the ``VOICE_BRIDGE_AUDIO_PLANE=noop``
   escape hatch and rejects unknown values loudly.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))


# ---------------------------------------------------------------------------
# Pipecat / LiveKit boundary fakes
# ---------------------------------------------------------------------------


class _FakeTTSSpeakFrame:
    """Stand-in for ``pipecat.frames.frames.TTSSpeakFrame``."""

    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeTranscriptionFrame:
    """Stand-in for ``pipecat.frames.frames.TranscriptionFrame``."""

    def __init__(self, text: str = "", *_a: Any, **_kw: Any) -> None:
        self.text = text


class _FakeFrameDirection:
    DOWNSTREAM = "downstream"
    UPSTREAM = "upstream"


class _FakeFrameProcessor:
    """Base class the plane's local ``_TranscriptCapture`` subclasses.

    Provides the two coroutines the capture relies on: ``process_frame``
    (the ``super()`` call) and ``push_frame`` (forwarding the frame on).
    """

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        self.pushed: list[tuple[Any, Any]] = []

    async def process_frame(self, frame: Any, direction: Any) -> None:
        return None

    async def push_frame(self, frame: Any, direction: Any = None) -> None:
        self.pushed.append((frame, direction))


class _FakePipelineTask:
    """Captures the frames the plane tries to inject via ``queue_frames``."""

    def __init__(self, *_args: Any, **_kw: Any) -> None:
        self.queued: list[list[Any]] = []

    async def queue_frames(self, frames: list[Any]) -> None:
        self.queued.append(list(frames))


class _FakePipelineRunner:
    """Async ``run`` that just blocks until cancelled."""

    def __init__(self, *_args: Any, **_kw: Any) -> None:
        self.runs: list[Any] = []

    async def run(self, task: Any) -> None:
        self.runs.append(task)
        try:
            await asyncio.Event().wait()  # blocks until cancelled
        except asyncio.CancelledError:
            return


class _FakeLiveKitTransport:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def input(self) -> str:
        return "in"

    def output(self) -> str:
        return "out"

    async def cleanup(self) -> None:  # exercised by disconnect()
        return None


class _FakeStt:
    """Minimal STT stand-in. The plane no longer registers callbacks on
    it (Pipecat 1.2) — transcripts are captured by an in-pipeline
    processor — so this is just an identity marker in the pipeline."""


class _FakeTts:
    pass


class _FakeAggregatorPair:
    """Stand-in for ``LLMContextAggregatorPair``. The plane keeps it only
    to drive VAD-based turn detection; we just need ``.user()`` /
    ``.assistant()`` to return identifiable pipeline stages."""

    def __init__(self, *, context: Any = None, user_params: Any = None) -> None:
        self.context = context
        self.user_params = user_params

    def user(self) -> tuple[str]:
        return ("user-agg",)

    def assistant(self) -> tuple[str]:
        return ("assistant-agg",)


@pytest.fixture
def fake_pipecat(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install fake pipecat / services modules into ``sys.modules``.

    The plane lazy-imports its heavy deps inside ``connect()`` -- we
    pre-populate ``sys.modules`` with the namespaces it imports so the
    actual pipecat / faster-whisper closure is never touched.
    """
    spy: dict[str, Any] = {
        "transports": [],
        "stts": [],
        "ttses": [],
        "pipelines": [],
        "tasks": [],
        "runners": [],
        "tokens_minted": [],
    }

    # services.voice_pipecat -- replace with a stub that returns our fakes.
    services_pkg = types.ModuleType("services")
    services_pkg.__path__ = []  # type: ignore[attr-defined]  # mark as package
    voice_pipecat = types.ModuleType("services.voice_pipecat")

    def _build_transport(**kwargs: Any) -> Any:
        spy["transports"].append(kwargs)
        return _FakeLiveKitTransport(**kwargs)

    def _build_whisper_stt(model: str) -> Any:
        spy["stts"].append(model)
        return _FakeStt()

    def _build_kokoro_tts(voice: str) -> Any:
        spy["ttses"].append(voice)
        return _FakeTts()

    def _mint_token(**kwargs: Any) -> str:
        spy["tokens_minted"].append(kwargs)
        return f"jwt-for-{kwargs['identity']}"

    def _resolve_creds(_site_config: Any) -> tuple[str, str, str]:
        return ("ws://livekit:7880", "key", "secret")

    async def _resolve_creds_async(_site_config: Any) -> tuple[str, str, str]:
        return ("ws://livekit:7880", "key", "secret")

    voice_pipecat.build_livekit_bridge_transport = _build_transport
    voice_pipecat.build_whisper_stt = _build_whisper_stt
    voice_pipecat.build_kokoro_tts = _build_kokoro_tts
    voice_pipecat.mint_livekit_token = _mint_token
    voice_pipecat.resolve_livekit_creds = _resolve_creds
    voice_pipecat.resolve_livekit_creds_async = _resolve_creds_async

    monkeypatch.setitem(sys.modules, "services", services_pkg)
    monkeypatch.setitem(sys.modules, "services.voice_pipecat", voice_pipecat)

    # --- pipecat package tree (only the symbols the plane imports) ---
    def _pkg(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        m.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, name, m)
        return m

    def _mod(name: str, **attrs: Any) -> types.ModuleType:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)
        return m

    _pkg("pipecat")
    _pkg("pipecat.pipeline")
    _pkg("pipecat.frames")
    _pkg("pipecat.processors")
    _pkg("pipecat.processors.aggregators")
    _pkg("pipecat.turns")
    _pkg("pipecat.turns.user_start")
    _pkg("pipecat.turns.user_stop")
    _pkg("pipecat.audio")
    _pkg("pipecat.audio.vad")

    def _make_pipeline(stages: list[Any]) -> Any:
        spy["pipelines"].append(list(stages))
        return ("pipeline", tuple(stages))

    _mod("pipecat.pipeline.pipeline", Pipeline=_make_pipeline)
    _mod("pipecat.pipeline.runner", PipelineRunner=_FakePipelineRunner)

    def _make_task(*args: Any, **kwargs: Any) -> Any:
        t = _FakePipelineTask(*args, **kwargs)
        spy["tasks"].append(t)
        return t

    _mod(
        "pipecat.pipeline.task",
        PipelineTask=_make_task,
        PipelineParams=type("PipelineParams", (), {"__init__": lambda _self, **_kw: None}),
    )
    _mod(
        "pipecat.frames.frames",
        TTSSpeakFrame=_FakeTTSSpeakFrame,
        TranscriptionFrame=_FakeTranscriptionFrame,
    )
    _mod(
        "pipecat.processors.frame_processor",
        FrameProcessor=_FakeFrameProcessor,
        FrameDirection=_FakeFrameDirection,
    )
    _mod(
        "pipecat.processors.aggregators.llm_context",
        LLMContext=type("LLMContext", (), {"__init__": lambda _self, **_kw: None}),
    )
    _mod(
        "pipecat.processors.aggregators.llm_response_universal",
        LLMContextAggregatorPair=_FakeAggregatorPair,
        LLMUserAggregatorParams=type(
            "LLMUserAggregatorParams", (), {"__init__": lambda _self, **_kw: None}
        ),
    )
    _mod(
        "pipecat.turns.user_start.vad_user_turn_start_strategy",
        VADUserTurnStartStrategy=type(
            "VADUserTurnStartStrategy", (), {"__init__": lambda _self, **_kw: None}
        ),
    )
    _mod(
        "pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy",
        SpeechTimeoutUserTurnStopStrategy=type(
            "SpeechTimeoutUserTurnStopStrategy", (), {"__init__": lambda _self, **_kw: None}
        ),
    )
    _mod(
        "pipecat.turns.user_turn_strategies",
        UserTurnStrategies=type("UserTurnStrategies", (), {"__init__": lambda _self, **_kw: None}),
    )
    _mod(
        "pipecat.audio.vad.silero",
        SileroVADAnalyzer=type("SileroVADAnalyzer", (), {"__init__": lambda _self, **_kw: None}),
    )
    _mod(
        "pipecat.audio.vad.vad_analyzer",
        VADParams=type("VADParams", (), {"__init__": lambda _self, **_kw: None}),
    )

    return spy


# Force a fresh import after fakes are in place so the module's
# ``_ensure_services_on_path`` doesn't grab the real services package.
@pytest.fixture
def plane_module(fake_pipecat: dict[str, Any]):
    sys.modules.pop("audio_plane_pipecat", None)
    import audio_plane_pipecat  # noqa: WPS433 -- reimport after patching
    return audio_plane_pipecat


def _find_capture(stages: list[Any]) -> Any:
    """Pull the local ``_TranscriptCapture`` instance out of the pipeline."""
    for stage in stages:
        if type(stage).__name__ == "_TranscriptCapture":
            return stage
    return None


# ===========================================================================
# connect()
# ===========================================================================


@pytest.mark.asyncio
class TestConnect:
    """``connect()`` is the most failure-prone path -- pin the seams."""

    async def test_mints_token_with_room_identity_and_ttl(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            stt_model="base", tts_voice="af_bella",
            livekit_url="ws://livekit:7880",
            livekit_api_key="k", livekit_api_secret="s",
            token_ttl_s=900,
        )
        try:
            await plane.connect(room="claude-bridge", identity="claude-bridge-vb-1")
            minted = fake_pipecat["tokens_minted"]
            assert len(minted) == 1
            assert minted[0]["room"] == "claude-bridge"
            assert minted[0]["identity"] == "claude-bridge-vb-1"
            assert minted[0]["ttl_s"] == 900
            # Transport got built with the same room and the minted JWT.
            assert fake_pipecat["transports"][0]["room"] == "claude-bridge"
            assert fake_pipecat["transports"][0]["token"] == "jwt-for-claude-bridge-vb-1"
        finally:
            await plane.disconnect()

    async def test_passes_stt_and_tts_settings_through(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            stt_model="large-v3", tts_voice="bf_emma",
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        try:
            await plane.connect(room="r", identity="me")
            assert fake_pipecat["stts"] == ["large-v3"]
            assert fake_pipecat["ttses"] == ["bf_emma"]
        finally:
            await plane.disconnect()

    async def test_pipeline_has_capture_between_stt_and_aggregator(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        try:
            await plane.connect(room="r", identity="me")
            stages = fake_pipecat["pipelines"][0]
            # transport.input() -> stt -> capture -> user-agg -> tts ->
            # transport.output() -> assistant-agg
            assert stages[0] == "in"
            assert isinstance(stages[1], _FakeStt)
            assert type(stages[2]).__name__ == "_TranscriptCapture"
            assert stages[3] == ("user-agg",)
            assert isinstance(stages[4], _FakeTts)
            assert stages[5] == "out"
            assert stages[6] == ("assistant-agg",)
        finally:
            await plane.disconnect()

    async def test_idempotent_connect_does_not_remint(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        try:
            await plane.connect(room="r", identity="me")
            await plane.connect(room="r", identity="me")  # no-op
            assert len(fake_pipecat["tokens_minted"]) == 1
        finally:
            await plane.disconnect()

    async def test_missing_creds_raise_loud(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from services import voice_pipecat as vp  # type: ignore[import-not-found]

        def _empty(_cfg: Any) -> tuple[str, str, str]:
            return ("", "", "")

        monkeypatch.setattr(vp, "resolve_livekit_creds", _empty)
        # The DB-first helper (#1000) must fall back to the env-only resolver
        # here: blank the bootstrap read + DATABASE_URL so it can't reach a
        # real DB and instead returns the empty creds above -> loud failure.
        monkeypatch.setattr(plane_module, "_bootstrap_value", lambda _k: "")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        plane = plane_module.PipecatAudioMediaPlane()
        with pytest.raises(RuntimeError, match=r"missing LiveKit creds"):
            await plane.connect(room="r", identity="me")


# ===========================================================================
# Utterance fan-out -- TranscriptionFrame -> _TranscriptCapture -> callback
# ===========================================================================


@pytest.mark.asyncio
class TestUtteranceCallback:

    async def test_transcript_frame_fires_callback(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        seen: list[str] = []

        async def _cb(text: str) -> None:
            seen.append(text)

        plane.set_utterance_callback(_cb)
        try:
            await plane.connect(room="r", identity="me")
            capture = _find_capture(fake_pipecat["pipelines"][0])
            assert capture is not None, "_TranscriptCapture not present in pipeline"

            await capture.process_frame(
                _FakeTranscriptionFrame("hello bridge"),
                _FakeFrameDirection.DOWNSTREAM,
            )
            assert seen == ["hello bridge"]
            # The frame is also forwarded downstream (pass-through).
            assert capture.pushed and capture.pushed[-1][0].text == "hello bridge"
        finally:
            await plane.disconnect()

    async def test_blank_transcript_is_dropped(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        seen: list[str] = []
        plane.set_utterance_callback(lambda t: seen.append(t) or None)
        try:
            await plane.connect(room="r", identity="me")
            capture = _find_capture(fake_pipecat["pipelines"][0])
            await capture.process_frame(
                _FakeTranscriptionFrame("   "), _FakeFrameDirection.DOWNSTREAM,
            )
            assert seen == []
        finally:
            await plane.disconnect()

    async def test_non_transcript_frame_passes_through_without_callback(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        seen: list[str] = []
        plane.set_utterance_callback(lambda t: seen.append(t) or None)
        try:
            await plane.connect(room="r", identity="me")
            capture = _find_capture(fake_pipecat["pipelines"][0])
            other = _FakeTTSSpeakFrame("not a transcript")
            await capture.process_frame(other, _FakeFrameDirection.DOWNSTREAM)
            assert seen == []  # not a TranscriptionFrame -> no callback
            assert capture.pushed[-1][0] is other  # still forwarded
        finally:
            await plane.disconnect()

    async def test_callback_exception_is_swallowed_not_fatal(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )

        async def _bad(_text: str) -> None:
            raise RuntimeError("bridge writer is broken")

        plane.set_utterance_callback(_bad)
        try:
            await plane.connect(room="r", identity="me")
            capture = _find_capture(fake_pipecat["pipelines"][0])
            # Must NOT raise -- a bad bridge writer shouldn't poison the pipeline.
            await capture.process_frame(
                _FakeTranscriptionFrame("tricky"), _FakeFrameDirection.DOWNSTREAM,
            )
        finally:
            await plane.disconnect()


# ===========================================================================
# speak() -> TTS pump -> queue_frames
# ===========================================================================


@pytest.mark.asyncio
class TestSpeak:

    async def test_speak_queues_a_tts_speak_frame(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        try:
            await plane.connect(room="r", identity="me")
            await plane.speak("hello world")
            for _ in range(20):
                await asyncio.sleep(0.01)
                if fake_pipecat["tasks"][0].queued:
                    break
            task_obj = fake_pipecat["tasks"][0]
            assert task_obj.queued, "TTS pump never enqueued a frame"
            frame_batch = task_obj.queued[0]
            assert len(frame_batch) == 1
            assert isinstance(frame_batch[0], _FakeTTSSpeakFrame)
            assert frame_batch[0].text == "hello world"
        finally:
            await plane.disconnect()

    async def test_speak_chunked_input_one_call_per_chunk(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        try:
            await plane.connect(room="r", identity="me")
            for chunk in ("First.", "Second.", "Third."):
                await plane.speak(chunk)
            for _ in range(50):
                await asyncio.sleep(0.01)
                if len(fake_pipecat["tasks"][0].queued) >= 3:
                    break
            queued = fake_pipecat["tasks"][0].queued
            assert len(queued) == 3, (
                f"expected 3 separate queue_frames calls, got {len(queued)}"
            )
            texts = [batch[0].text for batch in queued]
            assert texts == ["First.", "Second.", "Third."]
        finally:
            await plane.disconnect()

    async def test_speak_empty_text_is_noop(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        try:
            await plane.connect(room="r", identity="me")
            await plane.speak("   ")
            await plane.speak("")
            await asyncio.sleep(0.05)
            assert fake_pipecat["tasks"][0].queued == []
        finally:
            await plane.disconnect()

    async def test_speak_before_connect_raises(self, plane_module: Any) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        with pytest.raises(RuntimeError, match=r"not connected"):
            await plane.speak("hello")


# ===========================================================================
# disconnect() idempotency + cleanup
# ===========================================================================


@pytest.mark.asyncio
class TestDisconnect:

    async def test_disconnect_cleans_up_runner_and_pump(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        await plane.connect(room="r", identity="me")
        assert plane._runner_task is not None
        assert plane._tts_pump_task is not None

        await plane.disconnect()
        assert plane._runner_task is None
        assert plane._tts_pump_task is None
        with pytest.raises(RuntimeError, match=r"not connected"):
            await plane.speak("oops")

    async def test_disconnect_is_idempotent(
        self, plane_module: Any, fake_pipecat: dict[str, Any],
    ) -> None:
        plane = plane_module.PipecatAudioMediaPlane(
            livekit_url="ws://x", livekit_api_key="k", livekit_api_secret="s",
        )
        await plane.connect(room="r", identity="me")
        await plane.disconnect()
        await plane.disconnect()  # second call MUST NOT raise


# ===========================================================================
# resolve_audio_plane() -- escape hatch + default behaviour
# ===========================================================================


class TestResolveAudioPlane:

    def test_default_returns_pipecat(self, plane_module: Any) -> None:
        plane = plane_module.resolve_audio_plane(env={})
        assert isinstance(plane, plane_module.PipecatAudioMediaPlane)

    def test_noop_escape_hatch(self, plane_module: Any) -> None:
        plane = plane_module.resolve_audio_plane(env={"VOICE_BRIDGE_AUDIO_PLANE": "noop"})
        assert plane.__class__.__name__ == "NoopAudioMediaPlane"

    def test_noop_is_case_insensitive(self, plane_module: Any) -> None:
        plane = plane_module.resolve_audio_plane(env={"VOICE_BRIDGE_AUDIO_PLANE": "NoOp"})
        assert plane.__class__.__name__ == "NoopAudioMediaPlane"

    def test_unknown_value_raises_loud(self, plane_module: Any) -> None:
        with pytest.raises(RuntimeError, match=r"VOICE_BRIDGE_AUDIO_PLANE="):
            plane_module.resolve_audio_plane(
                env={"VOICE_BRIDGE_AUDIO_PLANE": "trust-me"},
            )

    def test_pipecat_explicit_value_returns_pipecat(self, plane_module: Any) -> None:
        plane = plane_module.resolve_audio_plane(
            env={"VOICE_BRIDGE_AUDIO_PLANE": "pipecat"},
        )
        assert isinstance(plane, plane_module.PipecatAudioMediaPlane)


# ===========================================================================
# _bootstrap_value() -- genesis-file read for DB-first creds (#1000)
# ===========================================================================


class TestBootstrapValue:
    """The MCP-spawned bridge resolves POINDEXTER_SECRET_KEY / database_url
    from bootstrap.toml when its env is bare, so it no longer needs a
    LIVEKIT_API_* copy in ~/.claude.json. Pins the genesis-file read.
    """

    @staticmethod
    def _point_home(tmp_path: Any, monkeypatch: Any) -> Any:
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
        monkeypatch.setenv("HOME", str(tmp_path))  # POSIX
        cfg = tmp_path / ".poindexter"
        cfg.mkdir(parents=True, exist_ok=True)
        return cfg

    def test_reads_top_level_string_key(
        self, plane_module: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        cfg = self._point_home(tmp_path, monkeypatch)
        (cfg / "bootstrap.toml").write_text(
            'poindexter_secret_key = "sk-abc-123"\n', encoding="utf-8"
        )
        assert plane_module._bootstrap_value("poindexter_secret_key") == "sk-abc-123"

    def test_missing_key_returns_empty(
        self, plane_module: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        cfg = self._point_home(tmp_path, monkeypatch)
        (cfg / "bootstrap.toml").write_text("other = 1\n", encoding="utf-8")
        assert plane_module._bootstrap_value("poindexter_secret_key") == ""

    def test_missing_file_returns_empty(
        self, plane_module: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        self._point_home(tmp_path, monkeypatch)
        assert plane_module._bootstrap_value("poindexter_secret_key") == ""
