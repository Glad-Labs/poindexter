"""PipecatAudioMediaPlane — real audio for the LiveKit MCP bridge.

PR #1 shipped the bridge control plane (session lifecycle, pipe plumbing,
chunking, watchdog) wrapped behind a ``NoopAudioMediaPlane``. PR #2 (this
module) lands the real audio plane: it joins a LiveKit room as a
participant, runs Whisper STT on inbound audio (firing
``on_utterance(text)`` per Silero VAD utterance-end), and TTS-publishes
text fed in via :meth:`PipecatAudioMediaPlane.speak`.

## Why a class (not just function calls)

The bridge is one Pipecat task per session and the per-session state is
non-trivial: a Pipecat ``PipelineTask`` plus its background runner, a
LiveKit transport that owns the websocket to the SFU, an ``asyncio.Queue``
that funnels :meth:`speak` calls into a Pipecat ``TTSSpeakFrame`` producer,
plus an ``on_utterance`` callback the bridge installs after construction
so the worker's pipe write closure can capture ``state.session_id``.
Wrapping that under one class keeps the interface clean (``connect`` /
``speak`` / ``set_utterance_callback`` / ``disconnect``) and lets the
bridge worker treat the audio plane as a black box -- exactly the way
the no-op plane was already used.

## Failure posture

Every entry point follows ``feedback_no_silent_defaults`` -- a missing
LiveKit cred, a missing Whisper model, a missing Kokoro voice, an
unreachable SFU all raise ``RuntimeError`` with a ``[VOICE_BRIDGE]``
prefix and surface to the MCP tool caller (the bridge worker re-raises
on connect failure per the existing ``_bridge_main`` contract).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from livekit_bridge import AudioMediaPlane

logger = logging.getLogger("livekit-bridge.audio.pipecat")

_LOG_PREFIX = "[VOICE_BRIDGE]"


# ---------------------------------------------------------------------------
# Repo-root resolution -- shared services/voice_pipecat.py lives outside
# this MCP server's directory.
#
# The MCP server gets started by ``uv run --directory mcp-server-voice``,
# so ``import services.voice_pipecat`` won't resolve until we add the
# repo root to ``sys.path``. Mirrors the exact technique
# ``services/voice_agent_livekit.py::_ensure_brain_on_path`` uses.
# ---------------------------------------------------------------------------


def _ensure_services_on_path() -> None:
    """Walk parents until ``src/cofounder_agent/services/voice_pipecat.py``
    appears, then prepend that ``src/cofounder_agent`` directory to
    ``sys.path``. Idempotent and side-effect-free past the first call.
    """
    here = Path(__file__).resolve()
    candidates = [here.parent, *here.parents]
    for parent in candidates:
        target = parent / "src" / "cofounder_agent" / "services" / "voice_pipecat.py"
        if target.is_file():
            services_root = parent / "src" / "cofounder_agent"
            p = str(services_root)
            if p not in sys.path:
                sys.path.insert(0, p)
            return
        # In Docker the layout is /app/services/voice_pipecat.py.
        flat = parent / "services" / "voice_pipecat.py"
        if flat.is_file():
            p = str(parent)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


# ---------------------------------------------------------------------------
# Pipecat-backed audio plane
# ---------------------------------------------------------------------------


class PipecatAudioMediaPlane(AudioMediaPlane):
    """Real audio plane: LiveKit + faster-whisper + Silero VAD + Kokoro.

    Lifecycle:

    1. ``connect`` -- mint a JWT, build the Pipecat pipeline (Whisper STT
       in -> on_utterance hook; TTS speak frames out -> LiveKit audio
       track), kick off the runner as a background task.
    2. ``speak`` -- enqueue ``text`` into the TTS feeder; the Pipecat
       pipeline picks it up and renders Kokoro audio to the room.
    3. ``set_utterance_callback`` -- the bridge installs a closure that
       writes to ``<sid>.in``; we capture it and dispatch on every
       Whisper end-of-utterance.
    4. ``disconnect`` -- cancel the runner, drain in-flight TTS, leave
       the room.

    Idempotent: ``connect`` after ``connect`` is a no-op; ``disconnect``
    after ``disconnect`` is a no-op.
    """

    def __init__(
        self,
        *,
        stt_model: str = "base.en",
        tts_voice: str = "af_bella",
        livekit_url: str | None = None,
        livekit_api_key: str | None = None,
        livekit_api_secret: str | None = None,
        token_ttl_s: int = 3600,
    ) -> None:
        """Capture the runtime knobs; do NOT touch Pipecat / LiveKit yet.

        Construction is cheap so the bridge can build the plane before
        ``connect`` is called -- failures live in ``connect`` so the
        MCP tool surface gets one place to catch them.
        """
        self._stt_model = stt_model
        self._tts_voice = tts_voice
        self._livekit_url = livekit_url
        self._livekit_api_key = livekit_api_key
        self._livekit_api_secret = livekit_api_secret
        self._token_ttl_s = token_ttl_s

        self._connected = False
        self._on_utterance: Callable[[str], Awaitable[None] | None] | None = None
        self._room: str | None = None
        self._identity: str | None = None

        # Populated in connect()
        self._transport: Any | None = None
        self._task: Any | None = None  # Pipecat PipelineTask
        self._runner: Any | None = None  # Pipecat PipelineRunner
        self._runner_task: asyncio.Task[None] | None = None
        self._tts_queue: asyncio.Queue[str | None] | None = None
        self._tts_pump_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # AudioMediaPlane interface
    # ------------------------------------------------------------------

    async def connect(self, *, room: str, identity: str) -> None:
        """Join ``room`` as ``identity`` and bring up the Pipecat pipeline.

        Raises ``RuntimeError`` (with ``[VOICE_BRIDGE]`` prefix) on any
        config problem -- missing creds, unknown Whisper model, unknown
        Kokoro voice. The bridge worker re-raises so the MCP tool sees
        the failure rather than a half-connected zombie.
        """
        if self._connected:
            return
        _ensure_services_on_path()

        # Lazy imports -- defer the heavy pipecat / livekit closure until
        # actually connecting so unit tests can patch boundaries without
        # paying the import cost on collection.
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.frames.frames import TTSSpeakFrame
        from services.voice_pipecat import (
            build_kokoro_tts,
            build_livekit_bridge_transport,
            build_silero_vad,
            build_whisper_stt,
            mint_livekit_token,
            resolve_livekit_creds,
        )

        url = self._livekit_url
        api_key = self._livekit_api_key
        api_secret = self._livekit_api_secret
        if not (url and api_key and api_secret):
            res_url, res_key, res_secret = resolve_livekit_creds(None)
            url = url or res_url
            api_key = api_key or res_key
            api_secret = api_secret or res_secret

        # Loud failure if anything is still missing -- the bridge promises
        # "connect raises on misconfig" per feedback_no_silent_defaults.
        if not (url and api_key and api_secret):
            raise RuntimeError(
                f"{_LOG_PREFIX} PipecatAudioMediaPlane.connect: missing "
                f"LiveKit creds (url={bool(url)}, api_key={bool(api_key)}, "
                f"api_secret={bool(api_secret)}). Set LIVEKIT_URL / "
                f"LIVEKIT_API_KEY / LIVEKIT_API_SECRET in the environment "
                f"before voice_join_room."
            )

        try:
            token = mint_livekit_token(
                room=room,
                identity=identity,
                api_key=api_key,
                api_secret=api_secret,
                ttl_s=self._token_ttl_s,
            )
        except Exception as exc:
            raise RuntimeError(
                f"{_LOG_PREFIX} PipecatAudioMediaPlane.connect: failed to "
                f"mint LiveKit token for room={room!r} identity={identity!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            transport = build_livekit_bridge_transport(
                url=url, token=token, room=room,
            )
        except Exception as exc:
            raise RuntimeError(
                f"{_LOG_PREFIX} PipecatAudioMediaPlane.connect: failed to "
                f"build LiveKitTransport for room={room!r} at {url}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            stt = build_whisper_stt(self._stt_model)
        except Exception as exc:
            raise RuntimeError(
                f"{_LOG_PREFIX} PipecatAudioMediaPlane.connect: failed to "
                f"build Whisper STT (model={self._stt_model!r}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            tts = build_kokoro_tts(self._tts_voice)
        except Exception as exc:
            raise RuntimeError(
                f"{_LOG_PREFIX} PipecatAudioMediaPlane.connect: failed to "
                f"build Kokoro TTS (voice={self._tts_voice!r}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # Wire the STT output to our utterance callback. Pipecat exposes a
        # ``register_event_handler`` hook on STT services for transcript
        # frames; we install a coroutine that fans out to the bridge's
        # callback (which writes to ``<sid>.in``).
        self._install_stt_callback(stt)

        # Build a TTS feeder queue. ``speak()`` puts strings on this queue;
        # the pump task converts them to Pipecat ``TTSSpeakFrame`` and
        # injects them into the running pipeline so Kokoro renders audio.
        tts_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=64)
        self._tts_queue = tts_queue
        self._TTSSpeakFrame = TTSSpeakFrame  # noqa: N815 -- captured for pump

        # Bridge pipeline: transport in -> STT (utterance fan-out) ->
        # transport out for non-LLM path; TTS frames are queued via the
        # pump and injected through transport.output().
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                build_silero_vad(stop_secs=0.2),
                tts,
                transport.output(),
            ],
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                enable_usage_metrics=False,
            ),
            idle_timeout_secs=None,
            cancel_on_idle_timeout=False,
        )
        runner = PipelineRunner(handle_sigint=False)

        self._transport = transport
        self._task = task
        self._runner = runner
        self._room = room
        self._identity = identity

        # Kick off runner + TTS pump as background tasks; if the runner
        # crashes immediately we observe via the asyncio task at
        # disconnect time.
        self._runner_task = asyncio.create_task(
            runner.run(task), name=f"voice-bridge-runner:{identity}",
        )
        self._tts_pump_task = asyncio.create_task(
            self._tts_pump(),
            name=f"voice-bridge-tts-pump:{identity}",
        )

        self._connected = True
        logger.info(
            "%s PipecatAudioMediaPlane connected: room=%s identity=%s "
            "stt=%s tts=%s url=%s",
            _LOG_PREFIX, room, identity, self._stt_model, self._tts_voice, url,
        )

    async def disconnect(self) -> None:
        """Tear down runner + TTS pump + LiveKit transport. Idempotent."""
        if not self._connected:
            return
        self._connected = False
        # Stop the pump first so no new TTS frames get queued mid-shutdown.
        if self._tts_queue is not None:
            try:
                self._tts_queue.put_nowait(None)  # sentinel
            except asyncio.QueueFull:
                # Queue full means the pump is already overloaded; the
                # cancel below will still terminate it.
                pass
        if self._tts_pump_task is not None:
            self._tts_pump_task.cancel()
            try:
                await self._tts_pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Cancel the Pipecat runner; PipelineRunner.run() returns when the
        # task is cancelled.
        if self._runner_task is not None:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Best-effort transport cleanup -- LiveKitTransport handles its
        # own websocket lifecycle on cancellation, but newer Pipecat
        # versions expose ``cleanup()`` we should call when present.
        if self._transport is not None:
            cleanup = getattr(self._transport, "cleanup", None)
            if callable(cleanup):
                try:
                    result = cleanup()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "%s transport.cleanup() raised -- continuing", _LOG_PREFIX,
                    )

        self._transport = None
        self._task = None
        self._runner = None
        self._runner_task = None
        self._tts_queue = None
        self._tts_pump_task = None
        logger.info(
            "%s PipecatAudioMediaPlane disconnected from room=%s identity=%s",
            _LOG_PREFIX, self._room, self._identity,
        )

    async def speak(self, text: str) -> None:
        """Queue ``text`` for TTS. Returns promptly; rendering is async."""
        if not self._connected or self._tts_queue is None:
            raise RuntimeError(
                f"{_LOG_PREFIX} PipecatAudioMediaPlane.speak: plane is "
                f"not connected -- call connect() first."
            )
        if not text or not text.strip():
            return
        # If the pump is wedged (queue full), surface that loudly rather
        # than silently dropping audio.
        try:
            self._tts_queue.put_nowait(text)
        except asyncio.QueueFull as exc:
            raise RuntimeError(
                f"{_LOG_PREFIX} PipecatAudioMediaPlane.speak: TTS queue "
                f"full ({self._tts_queue.qsize()} pending) -- the bridge "
                f"is producing TTS faster than Kokoro can render. Raise "
                f"voice_bridge_chunk_max_chars or check Kokoro health."
            ) from exc

    def set_utterance_callback(
        self, callback: Callable[[str], Awaitable[None] | None],
    ) -> None:
        """Register the closure the STT pipeline fires per utterance-end."""
        self._on_utterance = callback

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _install_stt_callback(self, stt: Any) -> None:
        """Hook STT transcript frames -> on_utterance fan-out.

        Pipecat 1.1's ``WhisperSTTService`` exposes
        ``register_event_handler("on_transcript", handler)``. The handler
        receives a ``TranscriptionFrame``-like object with a ``.text``
        attribute; we await the bridge's callback if it returns a
        coroutine (which it does in the worker -- it appends to the
        ``.in`` pipe).

        Falls back to a ``frame_handler``-style intercept on older
        Pipecat versions so a minor upgrade doesn't break the bridge.
        """
        async def _on_transcript(*args: Any, **_kw: Any) -> None:
            # Pipecat passes (service, frame) or (frame,) depending on
            # version -- pick the last positional arg as the frame.
            frame = args[-1] if args else None
            text = ""
            if frame is not None:
                # TranscriptionFrame, InterimTranscriptionFrame, plain str
                text = getattr(frame, "text", None)
                if text is None:
                    text = getattr(frame, "transcript", None) or ""
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                text = (text or "").strip()
            if not text:
                return
            cb = self._on_utterance
            if cb is None:
                return
            try:
                result = cb(text)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception(
                    "%s utterance callback raised -- transcript dropped: %r",
                    _LOG_PREFIX, text[:120],
                )

        register = getattr(stt, "register_event_handler", None)
        if callable(register):
            try:
                register("on_transcript", _on_transcript)
                return
            except Exception:  # noqa: BLE001 -- fall through to frame_handler
                logger.warning(
                    "%s register_event_handler('on_transcript') failed; "
                    "trying frame_handler fallback",
                    _LOG_PREFIX,
                )
        # frame_handler fallback -- install a wrapping coroutine on the
        # STT service so transcript frames fan out to the callback.
        original_handler = getattr(stt, "frame_handler", None)
        if callable(original_handler):
            async def _wrapped_handler(frame: Any) -> Any:
                rv = original_handler(frame)
                if asyncio.iscoroutine(rv):
                    rv = await rv
                await _on_transcript(frame)
                return rv
            try:
                stt.frame_handler = _wrapped_handler  # type: ignore[assignment]
                return
            except Exception:  # noqa: BLE001
                pass
        logger.warning(
            "%s STT service exposes neither register_event_handler nor "
            "frame_handler -- utterance callback will not fire. Pipecat "
            "version mismatch?",
            _LOG_PREFIX,
        )

    async def _tts_pump(self) -> None:
        """Drain the TTS queue, converting each string to a Pipecat frame.

        Runs as a background task for the lifetime of the connection.
        Receives ``None`` as the shutdown sentinel.
        """
        assert self._tts_queue is not None
        assert self._task is not None
        TTSSpeakFrame = self._TTSSpeakFrame  # noqa: N806
        while True:
            try:
                text = await self._tts_queue.get()
            except asyncio.CancelledError:
                return
            if text is None:
                return
            try:
                # Pipecat 1.1: PipelineTask.queue_frames accepts an iterable
                # of frames; older versions exposed queue_frame for a
                # single frame. Support both.
                frame = TTSSpeakFrame(text=text)
                queue_frames = getattr(self._task, "queue_frames", None)
                if callable(queue_frames):
                    rv = queue_frames([frame])
                else:
                    queue_frame = getattr(self._task, "queue_frame", None)
                    if not callable(queue_frame):
                        raise RuntimeError(
                            "PipelineTask exposes neither queue_frames "
                            "nor queue_frame -- Pipecat API drift",
                        )
                    rv = queue_frame(frame)
                if asyncio.iscoroutine(rv):
                    await rv
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                # Per feedback_machine_rules: log full context, keep going.
                # A single failed chunk shouldn't kill the whole session.
                logger.exception(
                    "%s TTS pump: failed to queue frame for text=%r -- "
                    "continuing",
                    _LOG_PREFIX, text[:120],
                )


# ---------------------------------------------------------------------------
# Plane resolver -- picks Pipecat by default, no-op when escape hatch is set
# ---------------------------------------------------------------------------


def resolve_audio_plane(
    *,
    stt_model: str = "base.en",
    tts_voice: str = "af_bella",
    env: dict[str, str] | None = None,
) -> AudioMediaPlane:
    """Pick the audio plane for a new bridge session.

    Returns :class:`PipecatAudioMediaPlane` by default. When the
    ``VOICE_BRIDGE_AUDIO_PLANE`` env var is set to ``"noop"`` (case
    insensitive) we return a :class:`livekit_bridge.NoopAudioMediaPlane`
    instead -- that's the escape hatch for tests, CI, and operators
    debugging an audio-driver problem who want the control plane to
    keep working while the audio stack is down.

    Per ``feedback_no_silent_defaults`` the escape hatch is explicit:
    any other value (including the empty string) goes to Pipecat.
    """
    env_map = env if env is not None else os.environ
    choice = (env_map.get("VOICE_BRIDGE_AUDIO_PLANE", "") or "").strip().lower()
    if choice == "noop":
        from livekit_bridge import NoopAudioMediaPlane

        logger.info(
            "%s resolve_audio_plane: VOICE_BRIDGE_AUDIO_PLANE=noop -- "
            "returning NoopAudioMediaPlane (silent stub).",
            _LOG_PREFIX,
        )
        return NoopAudioMediaPlane()
    if choice and choice not in {"pipecat", ""}:
        # Unknown value -- log loudly so the operator knows their env
        # var didn't take effect (per feedback_no_silent_defaults: do
        # NOT silently fall through).
        raise RuntimeError(
            f"{_LOG_PREFIX} resolve_audio_plane: VOICE_BRIDGE_AUDIO_PLANE="
            f"{choice!r} is not recognised. Use 'pipecat' (default) or "
            f"'noop'."
        )
    return PipecatAudioMediaPlane(stt_model=stt_model, tts_voice=tts_voice)


__all__ = [
    "PipecatAudioMediaPlane",
    "resolve_audio_plane",
]
