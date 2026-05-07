"""Pure-mock tests for ``audio_plane_pipecat.PipecatAudioMediaPlane``.

The real plane talks to LiveKit + faster-whisper + Silero + Kokoro --
none of those belong in the unit suite. We mock pipecat / livekit at the
boundary and verify the seams that matter:

1. ``connect()`` mints a token with the right room / identity / TTL and
   builds a transport against that token.
2. The STT callback registration fans transcript text to the bridge's
   ``on_utterance`` closure.
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
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def register_event_handler(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler


class _FakeTts:
    pass


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
        "vads": [],
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
        t = _FakeLiveKitTransport(**kwargs)
        return t

    def _build_whisper_stt(model: str) -> Any:
        spy["stts"].append(model)
        return _FakeStt()

    def _build_kokoro_tts(voice: str) -> Any:
        spy["ttses"].append(voice)
        return _FakeTts()

    def _build_silero_vad(stop_secs: float = 0.2) -> Any:
        spy["vads"].append(stop_secs)
        return object()

    def _mint_token(**kwargs: Any) -> str:
        spy["tokens_minted"].append(kwargs)
        return f"jwt-for-{kwargs['identity']}"

    def _resolve_creds(_site_config: Any) -> tuple[str, str, str]:
        return ("ws://livekit:7880", "key", "secret")

    voice_pipecat.build_livekit_bridge_transport = _build_transport
    voice_pipecat.build_whisper_stt = _build_whisper_stt
    voice_pipecat.build_kokoro_tts = _build_kokoro_tts
    voice_pipecat.build_silero_vad = _build_silero_vad
    voice_pipecat.mint_livekit_token = _mint_token
    voice_pipecat.resolve_livekit_creds = _resolve_creds

    monkeypatch.setitem(sys.modules, "services", services_pkg)
    monkeypatch.setitem(sys.modules, "services.voice_pipecat", voice_pipecat)

    # pipecat.pipeline.* -- only the symbols the plane imports.
    pipecat_pkg = types.ModuleType("pipecat")
    pipecat_pkg.__path__ = []  # type: ignore[attr-defined]
    pipeline_pkg = types.ModuleType("pipecat.pipeline")
    pipeline_pkg.__path__ = []  # type: ignore[attr-defined]
    pipeline_mod = types.ModuleType("pipecat.pipeline.pipeline")

    def _make_pipeline(stages: list[Any]) -> Any:
        spy["pipelines"].append(list(stages))
        return ("pipeline", tuple(stages))

    pipeline_mod.Pipeline = _make_pipeline

    runner_mod = types.ModuleType("pipecat.pipeline.runner")
    runner_mod.PipelineRunner = _FakePipelineRunner

    task_mod = types.ModuleType("pipecat.pipeline.task")

    def _make_task(*args: Any, **kwargs: Any) -> Any:
        t = _FakePipelineTask(*args, **kwargs)
        spy["tasks"].append(t)
        return t

    task_mod.PipelineTask = _make_task

    class _Params:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    task_mod.PipelineParams = _Params

    frames_pkg = types.ModuleType("pipecat.frames")
    frames_pkg.__path__ = []  # type: ignore[attr-defined]
    frames_mod = types.ModuleType("pipecat.frames.frames")
    frames_mod.TTSSpeakFrame = _FakeTTSSpeakFrame

    monkeypatch.setitem(sys.modules, "pipecat", pipecat_pkg)
    monkeypatch.setitem(sys.modules, "pipecat.pipeline", pipeline_pkg)
    monkeypatch.setitem(sys.modules, "pipecat.pipeline.pipeline", pipeline_mod)
    monkeypatch.setitem(sys.modules, "pipecat.pipeline.runner", runner_mod)
    monkeypatch.setitem(sys.modules, "pipecat.pipeline.task", task_mod)
    monkeypatch.setitem(sys.modules, "pipecat.frames", frames_pkg)
    monkeypatch.setitem(sys.modules, "pipecat.frames.frames", frames_mod)

    return spy


# Force a fresh import after fakes are in place so the module's
# ``_ensure_services_on_path`` doesn't grab the real services package.
@pytest.fixture
def plane_module(fake_pipecat: dict[str, Any]):
    sys.modules.pop("audio_plane_pipecat", None)
    import audio_plane_pipecat  # noqa: WPS433 -- reimport after patching
    return audio_plane_pipecat


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
            stt_model="base.en", tts_voice="af_bella",
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
        # Wipe both the constructor-level creds AND env so the fallback
        # path also runs dry. The fixture's resolve_livekit_creds is
        # plumbed in, but we patch services.voice_pipecat to return
        # blanks for this test.
        from services import voice_pipecat as vp  # type: ignore[import-not-found]

        def _empty(_cfg: Any) -> tuple[str, str, str]:
            return ("", "", "")

        monkeypatch.setattr(vp, "resolve_livekit_creds", _empty)
        plane = plane_module.PipecatAudioMediaPlane()
        with pytest.raises(RuntimeError, match=r"missing LiveKit creds"):
            await plane.connect(room="r", identity="me")


# ===========================================================================
# Utterance fan-out -- transcripts -> on_utterance callback
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
            stt = plane._transport  # for typing only -- next line uses stt directly
            # The fake STT instance was returned by build_whisper_stt; pull
            # it back via the spy.
            stt_obj = None
            for mod in fake_pipecat["pipelines"][0]:
                if isinstance(mod, _FakeStt):
                    stt_obj = mod
                    break
            assert stt_obj is not None, "STT fake not present in pipeline"
            handler = stt_obj.handlers.get("on_transcript")
            assert handler is not None, "register_event_handler('on_transcript') was not called"

            # Simulate Pipecat firing a transcript frame.
            class _Frame:
                text = "hello bridge"

            await handler(_Frame())
            assert seen == ["hello bridge"]
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
            stt_obj = next(
                m for m in fake_pipecat["pipelines"][0] if isinstance(m, _FakeStt)
            )
            handler = stt_obj.handlers["on_transcript"]

            class _Frame:
                text = "   "

            await handler(_Frame())
            assert seen == []
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
            stt_obj = next(
                m for m in fake_pipecat["pipelines"][0] if isinstance(m, _FakeStt)
            )
            handler = stt_obj.handlers["on_transcript"]

            class _Frame:
                text = "tricky"

            # Must NOT raise -- a bad bridge writer shouldn't poison
            # the pipeline.
            await handler(_Frame())
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
            # Give the pump task a tick to drain.
            for _ in range(20):
                await asyncio.sleep(0.01)
                task_obj = fake_pipecat["tasks"][0]
                if task_obj.queued:
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
        # Bridge's voice_speak chunks at sentence boundaries before
        # writing to .out; the audio plane gets one speak() call per
        # chunk and must produce one TTS frame per call (preserving
        # interruptibility -- if we batched them, the operator could
        # only interrupt at the batch boundary).
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
        # All bg tasks were either cancelled or completed.
        assert plane._runner_task is None
        assert plane._tts_pump_task is None
        # Speaking after disconnect must raise (per the no_silent_defaults
        # contract -- caller should know the plane is dead).
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
        # Second call MUST NOT raise.
        await plane.disconnect()


# ===========================================================================
# resolve_audio_plane() -- escape hatch + default behaviour
# ===========================================================================


class TestResolveAudioPlane:

    def test_default_returns_pipecat(self, plane_module: Any) -> None:
        plane = plane_module.resolve_audio_plane(env={})
        assert isinstance(plane, plane_module.PipecatAudioMediaPlane)

    def test_noop_escape_hatch(self, plane_module: Any) -> None:
        plane = plane_module.resolve_audio_plane(env={"VOICE_BRIDGE_AUDIO_PLANE": "noop"})
        # NoopAudioMediaPlane lives in livekit_bridge -- plane.__class__
        # name is the cheapest way to verify without a circular import.
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
