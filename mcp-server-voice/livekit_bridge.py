"""LiveKit ↔ Claude Code session bridge — voice as a UI surface.

This module is the *additive* alternative to the
``voice_agent_brain_mode=claude-code`` subprocess-spawn path. Instead of
spawning a fresh ``claude -p`` subprocess per voice turn (which fights the
architecture — auth coupling, session-per-call, no context inheritance,
Dockerfile bloat), we let an *already-running* Claude Code session
hijack the LiveKit room:

- Voice in becomes the next user input to that session (via the
  ``<session_id>.in`` named pipe — the slash command's Monitor watcher
  wakes the session with the transcript).
- Session output becomes voice out (via the ``<session_id>.out`` pipe —
  the bridge polls it and pipes lines through Kokoro TTS into the room).

The always-on ``voice-agent-livekit`` container stays exactly as it is —
that's the always-on phone-tap-to-join interface using ollama as the
brain. The MCP bridge is *additive*: it lets a live Claude Code session
optionally hijack the same room when the operator wants the full toolchain
on the line instead of Emma.

## Deferred: actual LiveKit + Whisper + Kokoro integration

The first cut of this module ships the **session lifecycle, pipe
plumbing, chunking, and watchdog** — every part the slash commands and
unit tests touch. The audio media plane (LiveKit room subscribe →
Whisper STT → Silero VAD → Kokoro TTS publish) is wrapped behind a
single :class:`AudioMediaPlane` interface; the default implementation is
:class:`NoopAudioMediaPlane`, which logs "would have done X" so the
session-side wiring (slash commands, Monitor, voice_speak chunking) can
be smoke-tested without a GPU.

A ``PipecatAudioMediaPlane`` will land in PR #2 once the always-on
``voice-agent-livekit`` container's Pipecat pipeline gets factored into
``services/voice_pipecat.py`` — the bridge will instantiate one of those
and we get the audio for free, no duplicated Whisper / Silero / Kokoro
state in the MCP server process.

## Process model — subprocess-spawned worker (Glad-Labs/glad-labs-stack#1010)

The bridge worker runs as a **separate Python subprocess**, not as an
``asyncio.create_task`` inside the long-lived MCP server process. The MCP
server is a thin launcher: ``voice_join_room`` calls
:func:`spawn_bridge_subprocess`, which ``Popen``-launches
``bridge_worker.py`` (detached + hidden), and that child imports the
*fresh on-disk* :func:`start_bridge` / :func:`_bridge_main` code.

Why: the MCP server is a long-lived process. If the worker ran in-process
it would bind to whatever modules that process loaded at startup, so any
code change (e.g. the Pipecat 1.2 migration) leaves the running MCP server
using STALE cached modules until restarted — which a mobile operator
cannot do. Symptom: every MCP-spawned bridge's ``.in`` transcript pipe
stays empty (a deaf bridge) while a standalone subprocess running fresh
code works. Spawning a subprocess sidesteps module staleness entirely.

:func:`start_bridge` / :func:`_bridge_main` stay callable in-process so
unit tests and the ``VOICE_BRIDGE_INPROCESS=1`` escape hatch keep the old
behaviour. Default is subprocess.

## Pipe + status layout

For session_id ``ab12``:

- ``~/.poindexter/voice/ab12.in``  — bridge writes utterance transcripts here;
  the slash-command-side ``Monitor`` watches the file for new lines.
- ``~/.poindexter/voice/ab12.out`` — slash command (or anything else) writes
  text-to-speak lines here; bridge polls and forwards to TTS.
- ``~/.poindexter/voice/ab12.lock`` — sentinel file holding the worker PID
  (written by :func:`_bridge_main`, unlinked on exit). Cross-process PID
  file — :func:`terminate_bridge_process` reads it to leave-by-PID.
- ``~/.poindexter/voice/ab12.status`` — single-word readiness signal the
  launcher polls: ``connecting`` → ``ready`` (after the audio plane
  connects) → ``stopped``, or ``error: <repr>`` if connect raised. The
  lock is written BEFORE connect, so it can't signal "connected" — the
  status file fills that gap.
- ``~/.poindexter/voice/ab12.log`` — the launcher's capture of the child's
  stdout/stderr. The launcher reads its tail into error messages.

Pipes are plain files, not POSIX FIFOs. Plain files survive a worker
crash without leaving a half-open FD; new writes append; readers track
the byte offset themselves. Cross-platform — works on Matt's Windows
host without the Linux-FIFO quirks.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("livekit-bridge")


# ---------------------------------------------------------------------------
# Pipe layout
# ---------------------------------------------------------------------------


def voice_pipe_dir() -> Path:
    """Directory holding per-session pipe files. Created on first use.

    Honours ``$POINDEXTER_VOICE_DIR`` for tests + non-default home dirs,
    falls back to ``~/.poindexter/voice`` (matches the rest of the
    Poindexter on-disk layout — bootstrap.toml, claude session memory,
    voice memory all live under ``~/.poindexter/``).
    """
    override = os.environ.get("POINDEXTER_VOICE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".poindexter" / "voice"


def session_pipe_paths(session_id: str) -> dict[str, Path]:
    """Return the canonical .in / .out / .lock / .status / .log paths.

    Pure function so the slash command, the bridge worker, the subprocess
    launcher, and the unit tests all agree on where the files live.

    - ``in`` / ``out`` — the transcript pipes (see module docstring).
    - ``lock`` — worker PID file (written by :func:`_bridge_main`).
    - ``status`` — readiness signal the launcher polls. The lock is
      written BEFORE the audio plane connects, so it can't say "ready";
      the status file does (``connecting`` → ``ready`` → ``stopped`` /
      ``error: ...``).
    - ``log`` — the launcher's capture of the child's stdout/stderr.
    """
    base = voice_pipe_dir()
    return {
        "in": base / f"{session_id}.in",
        "out": base / f"{session_id}.out",
        "lock": base / f"{session_id}.lock",
        "status": base / f"{session_id}.status",
        "log": base / f"{session_id}.log",
    }


def ensure_session_pipes(session_id: str) -> dict[str, Path]:
    """Create the pipe directory + empty .in / .out files; clear .status.

    Idempotent — safe to call multiple times. Returns the same dict
    :func:`session_pipe_paths` would. The ``.in`` / ``.out`` files are
    created-if-absent (never truncated — a re-join must not drop final
    lines a Monitor hasn't read). The ``.status`` file is *cleared* to an
    empty string so a stale ``ready`` / ``error`` from a previous session
    can't fool the launcher's readiness poll into a false positive.
    """
    paths = session_pipe_paths(session_id)
    paths["in"].parent.mkdir(parents=True, exist_ok=True)
    for kind in ("in", "out"):
        if not paths[kind].exists():
            paths[kind].touch()
    # Clear the readiness signal — fresh session starts with no status.
    paths["status"].write_text("", encoding="utf-8")
    return paths


def _write_status(session_id: str, status: str) -> None:
    """Best-effort write of the ``.status`` readiness file.

    A status-write failure must never break the worker — the status file
    is a convenience signal for the launcher's readiness poll, not a
    correctness invariant (leave-by-PID via ``.lock`` is the primary
    teardown signal). So we swallow every error and log at debug level.
    """
    try:
        session_pipe_paths(session_id)["status"].write_text(
            status, encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover — defensive
        logger.debug(
            "status-write failed for session %s (status=%r): %r",
            session_id, status, exc,
        )


# ---------------------------------------------------------------------------
# Sentence chunking — keeps long replies interruptible
# ---------------------------------------------------------------------------


# Regex catches end-of-sentence punctuation (., !, ?) followed by a space
# or end-of-string. Used by :func:`chunk_text_for_tts` so a 2000-char
# reply gets sent to TTS in 4 separately-cancellable chunks instead of
# one monolithic blob the user can't interrupt.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def chunk_text_for_tts(text: str, *, max_chars: int = 500) -> list[str]:
    """Split text into TTS-friendly chunks bounded by sentence ends.

    Behaviour:

    1. Strip leading/trailing whitespace; collapse interior runs of
       whitespace to single spaces (TTS engines hate "    " — Kokoro
       inserts an audible pause per whitespace token).
    2. Split on sentence-end boundaries (``.``, ``!``, ``?``).
    3. Greedy-pack: append each sentence to the current chunk until
       adding one more would exceed ``max_chars``, then start a new
       chunk. A single sentence longer than ``max_chars`` gets its own
       chunk un-split — better an oversized chunk than a mid-sentence
       cut that confuses the TTS prosody model.
    4. Drop empty results — text that's all whitespace returns ``[]``,
       not ``[""]``.

    Returns a list because the caller (``voice_speak``) iterates it; an
    iterator would force the bridge to hold the function alive for the
    duration of a long reply. ``max_chars`` defaults to the documented
    500 (per ``voice_bridge_chunk_max_chars`` setting); callers pass
    the DB value.
    """
    text = (text or "").strip()
    if not text:
        return []
    # Collapse whitespace runs — TTS reads each whitespace token as
    # a pause cue; "X    Y" becomes a multi-second silence.
    text = re.sub(r"\s+", " ", text)

    sentences = _SENTENCE_END.split(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = (current + " " + sentence).strip() if current else sentence
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Audio media plane — abstract interface, no-op default
# ---------------------------------------------------------------------------


class AudioMediaPlane:
    """Interface for the actual LiveKit + STT + TTS path.

    The bridge worker owns the *control plane* (session lifecycle, pipe
    polling, watchdog timers). Audio media — joining the LiveKit room,
    subscribing to participant tracks, running Whisper, publishing TTS
    audio — lives behind this interface so the bridge can be unit-tested
    without dragging Pipecat / livekit / faster-whisper / kokoro onto the
    test path.

    Subclasses override the four methods the bridge actually calls
    (``connect``, ``disconnect``, ``speak``, ``set_utterance_callback``).
    Errors should raise — the bridge logs + propagates per
    ``feedback_no_silent_defaults``.
    """

    async def connect(self, *, room: str, identity: str) -> None:
        """Join the LiveKit room. Idempotent (re-connect is a no-op)."""
        raise NotImplementedError

    async def disconnect(self) -> None:
        """Leave the LiveKit room. Idempotent."""
        raise NotImplementedError

    async def speak(self, text: str) -> None:
        """TTS ``text`` into the room. May queue; should return promptly."""
        raise NotImplementedError

    def set_utterance_callback(
        self, callback: Callable[[str], asyncio.Future | None]
    ) -> None:
        """Register the callback the STT pipeline fires on each utterance end.

        Callback receives the transcript string and is awaited if it
        returns a coroutine (which is the case in the bridge — the
        callback writes to the .in pipe).
        """
        raise NotImplementedError


class NoopAudioMediaPlane(AudioMediaPlane):
    """Default audio plane that logs every call but moves no bytes.

    Picked when the real Pipecat plane isn't installed (CI, local
    smoke tests, the public Poindexter release before
    ``services/voice_pipecat.py`` lands). The control plane works
    end-to-end against this; the operator just hears silence and the
    .in pipe is silent until something else writes to it (the smoke
    script does this manually).
    """

    def __init__(self) -> None:
        self._connected = False
        self._on_utterance: Callable[[str], asyncio.Future | None] | None = None
        self._room: str | None = None
        self._identity: str | None = None

    async def connect(self, *, room: str, identity: str) -> None:
        if self._connected:
            return
        self._connected = True
        self._room = room
        self._identity = identity
        logger.info(
            "[noop-media] would join LiveKit room=%s as identity=%s "
            "(install Pipecat plane for real audio)",
            room, identity,
        )

    async def disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        logger.info("[noop-media] would disconnect from room=%s", self._room)

    async def speak(self, text: str) -> None:
        # Truncate so the log doesn't get spammed by long replies.
        preview = text[:80].replace("\n", " ")
        logger.info("[noop-media] would TTS-speak: %s%s",
                    preview, "..." if len(text) > 80 else "")

    def set_utterance_callback(
        self, callback: Callable[[str], asyncio.Future | None]
    ) -> None:
        self._on_utterance = callback

    async def fake_utterance(self, transcript: str) -> None:
        """Test hook — pretend Whisper just emitted an utterance.

        Used by the smoke script and the unit tests to drive the .in
        pipe without standing up a real audio pipeline.
        """
        if self._on_utterance is None:
            logger.warning(
                "[noop-media] fake_utterance called but no callback registered",
            )
            return
        result = self._on_utterance(transcript)
        if asyncio.iscoroutine(result):
            await result


# ---------------------------------------------------------------------------
# Bridge worker — the per-session asyncio task
# ---------------------------------------------------------------------------


@dataclass
class BridgeConfig:
    """Bridge worker configuration. Resolved from app_settings before construction."""

    room: str
    identity: str = "claude-bridge"
    chunk_max_chars: int = 500
    max_session_seconds: int = 1800
    out_poll_interval: float = 0.25
    # Auto-leave when the room becomes empty for more than this many seconds.
    # The audio plane is responsible for telling us "the room is empty"; for
    # the no-op plane this is effectively unused.
    empty_room_grace_seconds: int = 60
    # Audio-plane knobs piped through to PipecatAudioMediaPlane (PR #2).
    # Defaults match the migration seed in
    # services/migrations/20260507_022644_seed_voice_bridge_app_settings.py.
    stt_model: str = "base"
    tts_voice: str = "af_bella"
    # Turn-detection cadence (operator-tunable via app_settings). Defaults
    # raised from the bring-up's 0.2/0.8 — that chopped sentences at every
    # pause. See Glad-Labs/glad-labs-stack#1010 (voice hardening).
    vad_stop_secs: float = 0.5
    user_speech_timeout: float = 1.5


@dataclass
class BridgeState:
    """In-memory per-session worker state. Exposed to tests via the registry."""

    session_id: str
    config: BridgeConfig
    media: AudioMediaPlane
    started_at: float = field(default_factory=time.time)
    out_offset: int = 0  # byte offset already-forwarded from the .out pipe
    speak_count: int = 0
    utterance_count: int = 0
    leave_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class _BridgeRegistry:
    """In-process registry mapping session_id → BridgeState.

    The MCP server runs as a single Python process per Claude Code
    instance; in-process state is fine. If we ever shard the MCP server
    across processes, this graduates to ``brain_knowledge`` (which is
    what feedback_db_first_config would prescribe anyway).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, BridgeState] = {}

    def get(self, session_id: str) -> BridgeState | None:
        return self._sessions.get(session_id)

    def add(self, state: BridgeState) -> None:
        self._sessions[state.session_id] = state

    def remove(self, session_id: str) -> BridgeState | None:
        return self._sessions.pop(session_id, None)

    def all_session_ids(self) -> list[str]:
        return list(self._sessions.keys())


# Module-global registry so the MCP tool functions and the worker
# coroutines share the same view. Exposed to tests as
# ``livekit_bridge._registry``.
_registry = _BridgeRegistry()


def new_session_id() -> str:
    """Generate a short, URL-safe session id.

    8 hex chars is plenty — sessions are scoped per Claude Code instance
    and we cap concurrent count at "fits in head". Prefixing with ``vb-``
    so a session id grepped out of a log is immediately recognisable as
    a voice-bridge id, not a Claude session UUID.
    """
    return f"vb-{uuid.uuid4().hex[:8]}"


async def _on_utterance_end(state: BridgeState, transcript: str) -> None:
    """Append a transcript line to the .in pipe and bump the counter.

    Called by the audio plane every time Silero VAD fires an
    utterance-end event. We append a single line, newline-terminated, so
    the slash-command-side Monitor (which tails the file) wakes the
    Claude Code session with exactly one user input per utterance.
    """
    if not transcript or not transcript.strip():
        return
    paths = session_pipe_paths(state.session_id)
    line = transcript.strip().replace("\r", " ").replace("\n", " ") + "\n"
    # Best-effort write — if the .in file got deleted out from under us
    # (manual cleanup, restart) we recreate it. We don't want a missing
    # pipe to silently drop voice input.
    try:
        with open(paths["in"], "a", encoding="utf-8") as fh:
            fh.write(line)
    except FileNotFoundError:
        ensure_session_pipes(state.session_id)
        with open(paths["in"], "a", encoding="utf-8") as fh:
            fh.write(line)
    state.utterance_count += 1
    logger.info(
        "voice_utterance_end session=%s n=%d transcript=%r",
        state.session_id, state.utterance_count,
        transcript[:120] + ("..." if len(transcript) > 120 else ""),
    )


async def _drain_out_pipe(state: BridgeState) -> None:
    """Read newly-appended lines from the .out pipe and forward to TTS.

    Tracks ``state.out_offset`` so we don't replay the entire file every
    poll cycle. Each line is one logical text-to-speak request — the
    slash command (or whoever wrote it) is responsible for sentence
    boundaries; the bridge re-chunks via :func:`chunk_text_for_tts` so
    long replies stay interruptible.
    """
    paths = session_pipe_paths(state.session_id)
    if not paths["out"].exists():
        return
    try:
        size = paths["out"].stat().st_size
    except FileNotFoundError:
        return
    if size <= state.out_offset:
        return
    try:
        with open(paths["out"], "r", encoding="utf-8") as fh:
            fh.seek(state.out_offset)
            new_data = fh.read()
            state.out_offset = fh.tell()
    except FileNotFoundError:
        return

    # Each line is one TTS request; chunk each individually.
    for raw_line in new_data.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        chunks = chunk_text_for_tts(
            line, max_chars=state.config.chunk_max_chars,
        )
        for chunk in chunks:
            try:
                await state.media.speak(chunk)
                state.speak_count += 1
            except Exception:  # noqa: BLE001 — log + continue rather than die
                logger.exception(
                    "TTS chunk failed for session %s — continuing",
                    state.session_id,
                )


async def _bridge_main(state: BridgeState) -> None:
    """Per-session worker coroutine. Owns the audio plane lifecycle.

    Lifecycle:

    1. Connect the audio plane (joins LiveKit room).
    2. Register the utterance-end callback so transcripts hit ``.in``.
    3. Loop: drain .out → speak; check watchdogs; sleep.
    4. On leave_event or watchdog trip: disconnect, remove from registry,
       clean up .lock file. The .in / .out files are intentionally left
       in place so a slash command's Monitor can read final lines after
       the worker exits.
    """
    paths = ensure_session_pipes(state.session_id)
    paths["lock"].write_text(str(os.getpid()), encoding="utf-8")
    # The lock is written BEFORE connect, so it can't signal "ready".
    # The .status file carries the readiness handshake the launcher polls.
    _write_status(state.session_id, "connecting")

    state.media.set_utterance_callback(
        lambda t: _on_utterance_end(state, t),
    )

    try:
        await state.media.connect(
            room=state.config.room,
            identity=state.config.identity,
        )
    except Exception as exc:
        logger.exception(
            "Audio plane connect failed for session %s — bridge aborting",
            state.session_id,
        )
        # Signal the launcher BEFORE the existing raise so its readiness
        # poll surfaces the connect failure instead of timing out.
        _write_status(state.session_id, f"error: {exc!r}")
        # Per feedback_no_silent_defaults: tear down + re-raise so the
        # MCP tool sees the failure rather than thinking the bridge is up.
        try:
            paths["lock"].unlink(missing_ok=True)
        except OSError:
            pass
        _registry.remove(state.session_id)
        raise

    # Connect succeeded — the bridge is live and the .in pipe will fill.
    _write_status(state.session_id, "ready")

    deadline = state.started_at + state.config.max_session_seconds
    poll = state.config.out_poll_interval

    try:
        while not state.leave_event.is_set():
            now = time.time()
            if now >= deadline:
                logger.warning(
                    "Bridge session %s hit max_session_seconds=%d — auto-leaving",
                    state.session_id, state.config.max_session_seconds,
                )
                break
            await _drain_out_pipe(state)
            try:
                await asyncio.wait_for(state.leave_event.wait(), timeout=poll)
            except asyncio.TimeoutError:
                continue
    finally:
        try:
            await state.media.disconnect()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Audio plane disconnect failed for session %s — continuing",
                state.session_id,
            )
        # Mark stopped then drop the file — leave-by-PID via .lock is the
        # primary teardown signal, so the .status file is disposable here.
        _write_status(state.session_id, "stopped")
        for kind in ("lock", "status"):
            try:
                paths[kind].unlink(missing_ok=True)
            except OSError:
                pass
        _registry.remove(state.session_id)
        logger.info(
            "Bridge session %s closed — utterances=%d, tts_chunks=%d, "
            "elapsed=%.1fs",
            state.session_id, state.utterance_count, state.speak_count,
            time.time() - state.started_at,
        )


_AUDIO_DEP_MODULES: tuple[str, ...] = (
    "pipecat",
    "livekit",
    "faster_whisper",
    "kokoro",
)


def _resolve_default_audio_plane(config: BridgeConfig) -> AudioMediaPlane:
    """Pick the default audio plane for a new bridge session.

    With ``VOICE_BRIDGE_AUDIO_PLANE=noop`` the no-op stub is returned --
    explicit opt-in for tests, CI, and operators debugging audio-driver
    problems on a control-plane-only box.

    With ``VOICE_BRIDGE_AUDIO_PLANE`` unset or set to ``pipecat`` the
    Pipecat plane in ``audio_plane_pipecat`` is used. Required imports
    (``pipecat`` / ``livekit`` / ``faster_whisper`` / ``kokoro``) are
    probed up front: missing any of them raises ``RuntimeError`` with
    the install command so a fresh ``uv run server.py`` does not silently
    fall back to ``NoopAudioMediaPlane`` (which logs "would have done X"
    while every MCP tool reports success and the operator hears nothing).
    Honors ``feedback_no_silent_defaults``.
    """
    plane = (os.environ.get("VOICE_BRIDGE_AUDIO_PLANE", "") or "").strip().lower()
    if plane == "noop":
        logger.info(
            "[VOICE_BRIDGE] VOICE_BRIDGE_AUDIO_PLANE=noop -- using "
            "NoopAudioMediaPlane (silent stub)."
        )
        return NoopAudioMediaPlane()

    missing: list[str] = []
    for mod in _AUDIO_DEP_MODULES:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise RuntimeError(
            f"[VOICE_BRIDGE] PipecatAudioMediaPlane unavailable -- "
            f"missing audio dep(s): {', '.join(missing)}. Install the "
            f"optional audio extras with `uv --directory mcp-server-voice "
            f"sync --extra audio` (and `--extra gpu` on CUDA boxes) to "
            f"enable real audio. Set VOICE_BRIDGE_AUDIO_PLANE=noop to "
            f"explicitly opt in to the silent stub for CI / control-plane "
            f"testing."
        )
    try:
        from audio_plane_pipecat import (
            resolve_audio_plane,  # type: ignore[import-not-found]
        )
    except ImportError as exc:
        raise RuntimeError(
            f"[VOICE_BRIDGE] audio_plane_pipecat import failed even "
            f"though every probed audio dep imported cleanly: {exc}. "
            f"This is a packaging bug -- file an issue with the full "
            f"traceback."
        ) from exc
    stt_model = getattr(config, "stt_model", "base") or "base"
    tts_voice = getattr(config, "tts_voice", "af_bella") or "af_bella"
    vad_stop_secs = getattr(config, "vad_stop_secs", 0.5)
    user_speech_timeout = getattr(config, "user_speech_timeout", 1.5)
    return resolve_audio_plane(
        stt_model=stt_model,
        tts_voice=tts_voice,
        vad_stop_secs=vad_stop_secs,
        user_speech_timeout=user_speech_timeout,
    )


async def start_bridge(
    *,
    session_id: str | None = None,
    config: BridgeConfig,
    media: AudioMediaPlane | None = None,
) -> BridgeState:
    """Public constructor: spin up a per-session bridge worker task.

    The function returns AFTER the audio plane has connected (so the
    caller can rely on "the bridge is up" once it returns). The actual
    worker loop runs as a background asyncio task — it polls the .out
    pipe, manages the watchdog, and tears down on leave_event.

    Args:
        session_id: Optional explicit id; one is generated when None.
            Useful for tests (deterministic id) and for the slash
            command (lets the user override if they want a memorable
            id).
        config: BridgeConfig — caller resolves from app_settings, we
            don't read settings ourselves so the same module works in
            unit tests.
        media: Optional audio plane override. Defaults to
            NoopAudioMediaPlane (the safe choice when the Pipecat plane
            isn't built).

    Returns:
        BridgeState — registered in the module registry. The caller can
        flip ``state.leave_event.set()`` to terminate, or call
        :func:`stop_bridge`.
    """
    sid = session_id or new_session_id()
    if _registry.get(sid) is not None:
        # Re-using an active session id is a programmer error — the
        # registry is in-process so this only happens if the slash
        # command writes a duplicate id. Fail loud, no silent reuse.
        raise RuntimeError(
            f"voice bridge session {sid!r} is already running; "
            f"call stop_bridge first or pick a fresh id",
        )

    if media is None:
        media = _resolve_default_audio_plane(config)
    state = BridgeState(session_id=sid, config=config, media=media)
    _registry.add(state)

    # Kick off the worker; await the first iteration so the audio plane
    # connect failure surfaces here rather than as an orphaned task.
    state.task = asyncio.create_task(
        _bridge_main(state), name=f"voice-bridge:{sid}",
    )
    # Yield once so the task gets a chance to run through the connect
    # step before we return; if connect raises, the caller sees the
    # exception when they next await something on this state.
    await asyncio.sleep(0)
    return state


async def stop_bridge(session_id: str, *, timeout: float = 5.0) -> bool:
    """Stop the named bridge worker. Idempotent.

    Returns True if a worker was running and stopped, False if no such
    session was registered. Idempotent — calling twice with the same id
    returns True the first time, False the second.

    Args:
        session_id: Bridge session to terminate.
        timeout: Max seconds to wait for the worker to clean up before
            cancelling it outright. The audio plane disconnect should
            be much faster than this on the no-op plane; the cap is
            for the future Pipecat plane that may have a slow LiveKit
            disconnect.
    """
    state = _registry.get(session_id)
    if state is None:
        return False
    state.leave_event.set()
    if state.task is not None:
        try:
            await asyncio.wait_for(state.task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Bridge session %s did not exit within %.1fs — cancelling",
                session_id, timeout,
            )
            state.task.cancel()
            try:
                await state.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001 — already logged inside the task
            pass
    return True


# ---------------------------------------------------------------------------
# Subprocess launcher + leave-by-PID — the cross-process bridge lifecycle
# (Glad-Labs/glad-labs-stack#1010)
# ---------------------------------------------------------------------------


# Resolved once: absolute path to the subprocess entrypoint that imports
# fresh on-disk code and runs the worker. Lives next to this module.
_BRIDGE_WORKER_PATH = Path(__file__).resolve().parent / "bridge_worker.py"


def _read_log_tail(log_path: Path, *, lines: int = 20) -> str:
    """Return the last ``lines`` lines of the worker log, or a marker.

    Best-effort — a missing / unreadable log returns a short marker so
    the error message is still actionable rather than crashing the
    error path itself.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no worker log captured)"
    tail = text.splitlines()[-lines:]
    return "\n".join(tail) if tail else "(worker log empty)"


def _read_pid_from_lock(lock_path: Path) -> int | None:
    """Read the integer PID from a ``.lock`` file, or None if absent/garbage."""
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def spawn_bridge_subprocess(
    session_id: str,
    config: BridgeConfig,
    *,
    ready_timeout: float = 30.0,
) -> int:
    """Launch the bridge worker as a detached subprocess; return its PID.

    The MCP server is long-lived, so an in-process worker binds to the
    modules loaded at server startup — stale after any code change, and a
    mobile operator can't restart the server to pick up fresh code. This
    launcher spawns ``bridge_worker.py`` as a separate Python process that
    imports fresh on-disk code, so a Pipecat / STT migration takes effect
    on the next ``voice_join_room`` with no restart. See
    Glad-Labs/glad-labs-stack#1010.

    Blocking (``Popen`` + a readiness poll), so call it via
    ``asyncio.to_thread`` from async code.

    Lifecycle:

    1. :func:`ensure_session_pipes` (clears ``.status``).
    2. Build the child env: inherit the launcher's env (LiveKit creds +
       DATABASE_URL are already present on the MCP server), inject
       ``POINDEXTER_VOICE_BRIDGE_SESSION_ID`` +
       ``POINDEXTER_VOICE_BRIDGE_CONFIG`` (JSON of ``asdict(config)``),
       pass through ``POINDEXTER_VOICE_DIR`` if set.
    3. ``Popen`` the worker fully detached + hidden (no console window on
       Windows per feedback_no_popups; ``start_new_session`` on POSIX),
       capturing stdout+stderr to ``<sid>.log``.
    4. Poll ``<sid>.status`` until ``ready`` (return the PID from
       ``<sid>.lock``), ``error: ...`` (raise), the process dies (raise),
       or ``ready_timeout`` elapses (kill + raise). Every failure path
       raises ``RuntimeError`` with the worker log tail — fail loud,
       explicit (feedback_no_silent_defaults).

    The 30s default covers a cold faster-whisper ``base`` model load
    (~10s) with headroom.
    """
    paths = ensure_session_pipes(session_id)
    status_path = paths["status"]
    lock_path = paths["lock"]
    log_path = paths["log"]

    env = os.environ.copy()
    env["POINDEXTER_VOICE_BRIDGE_SESSION_ID"] = session_id
    env["POINDEXTER_VOICE_BRIDGE_CONFIG"] = json.dumps(
        dataclasses.asdict(config),
    )
    # Pass the voice-dir override through explicitly so the child resolves
    # the same pipe directory even if os.environ.copy() ever misses it.
    voice_dir_override = os.environ.get("POINDEXTER_VOICE_DIR", "").strip()
    if voice_dir_override:
        env["POINDEXTER_VOICE_DIR"] = voice_dir_override

    popen_kwargs: dict[str, object] = {
        "env": env,
        "cwd": str(_BRIDGE_WORKER_PATH.parent),
    }
    if os.name == "nt":
        # Hidden + detached: no console window pops up (feedback_no_popups
        # — background jobs run hidden), and the child outlives the parent.
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    else:
        # New session so the child isn't killed with the launcher's group.
        popen_kwargs["start_new_session"] = True

    # Append so a re-join under the same sid keeps prior diagnostics; the
    # child's first log line announces the new run.
    with open(log_path, "a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, our own entrypoint
            [sys.executable, str(_BRIDGE_WORKER_PATH)],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **popen_kwargs,  # type: ignore[arg-type]
        )

    deadline = time.monotonic() + ready_timeout
    poll_interval = 0.25
    while True:
        try:
            status = status_path.read_text(encoding="utf-8").strip()
        except OSError:
            status = ""

        if status == "ready":
            pid = _read_pid_from_lock(lock_path)
            if pid is None:
                # ready but no PID — the lock should always be written
                # before status flips to ready. Fail loud rather than
                # returning a bogus PID the operator can't leave-by.
                _kill_pid(proc.pid)
                raise RuntimeError(
                    f"[VOICE_BRIDGE] bridge worker for session "
                    f"{session_id!r} reported ready but wrote no PID to "
                    f"{lock_path}. Worker log tail:\n"
                    f"{_read_log_tail(log_path)}"
                )
            logger.info(
                "[VOICE_BRIDGE] bridge subprocess ready session=%s pid=%d",
                session_id, pid,
            )
            return pid

        if status.startswith("error:"):
            raise RuntimeError(
                f"[VOICE_BRIDGE] bridge worker for session {session_id!r} "
                f"failed to start: {status}. Worker log tail:\n"
                f"{_read_log_tail(log_path)}"
            )

        if proc.poll() is not None:
            # Process exited without writing a terminal status — crashed
            # before / during connect (import explosion, bad creds, etc.).
            raise RuntimeError(
                f"[VOICE_BRIDGE] bridge worker for session {session_id!r} "
                f"exited (code={proc.returncode}) before signalling ready. "
                f"Worker log tail:\n{_read_log_tail(log_path)}"
            )

        if time.monotonic() >= deadline:
            _kill_pid(proc.pid)
            raise RuntimeError(
                f"[VOICE_BRIDGE] bridge worker for session {session_id!r} "
                f"did not become ready within {ready_timeout:.0f}s "
                f"(last status={status!r}). Killed it. Worker log tail:\n"
                f"{_read_log_tail(log_path)}"
            )

        time.sleep(poll_interval)


def _kill_pid(pid: int) -> None:
    """Best-effort terminate a PID cross-platform. Swallows already-dead."""
    try:
        if os.name == "nt":
            # taskkill /T also reaps any children the worker spawned.
            subprocess.run(  # noqa: S603,S607 — fixed argv
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError) as exc:
        logger.debug("kill pid=%d swallowed: %r", pid, exc)


def terminate_bridge_process(session_id: str) -> bool:
    """Leave-by-PID: signal the subprocess worker for ``session_id``.

    Reads the PID from ``<sid>.lock`` and terminates it cross-platform.
    Returns True if a process was signalled, False if there was no lock /
    no PID (nothing to stop). Idempotent: a second call after the first
    cleaned up the lock returns False. Swallows ``ProcessLookupError`` /
    already-dead — the goal state (process gone) is reached either way.

    The complement to :func:`spawn_bridge_subprocess`. The in-process
    :func:`stop_bridge` only knows about workers in *this* process's
    registry; for a subprocess worker the ``.lock`` PID file is the only
    cross-process handle.
    """
    paths = session_pipe_paths(session_id)
    pid = _read_pid_from_lock(paths["lock"])
    if pid is None:
        return False

    signalled = False
    try:
        if os.name == "nt":
            # SIGTERM maps to TerminateProcess on Windows; /T reaps the
            # process tree. Try the graceful os.kill first, fall back to
            # taskkill /F /T to guarantee the tree is gone.
            try:
                os.kill(pid, signal.SIGTERM)
                signalled = True
            except (ProcessLookupError, OSError):
                pass
            result = subprocess.run(  # noqa: S603,S607 — fixed argv
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
            # taskkill returns 0 when it killed the tree; 128 == no such
            # process (already dead) — both count as "we reached goal".
            if result.returncode == 0:
                signalled = True
        else:
            # POSIX: SIGTERM, then escalate to SIGKILL after a short grace
            # if the process is still alive.
            os.kill(pid, signal.SIGTERM)
            signalled = True
            grace_deadline = time.monotonic() + 2.0
            while time.monotonic() < grace_deadline:
                try:
                    os.kill(pid, 0)  # liveness probe — raises if gone
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except ProcessLookupError:
        # Already dead — that's the goal state, treat as "we handled it".
        signalled = True
    except OSError as exc:
        logger.warning(
            "terminate_bridge_process(%s) os error for pid=%d: %r",
            session_id, pid, exc,
        )

    # Drop the cross-process handles so a re-join starts clean and a second
    # terminate call returns False (idempotent).
    for kind in ("lock", "status"):
        try:
            paths[kind].unlink(missing_ok=True)
        except OSError:
            pass
    return signalled


async def speak_into_bridge(
    session_id: str, text: str, *, max_chars: int | None = None,
) -> int:
    """TTS ``text`` into the named bridge session. Returns chunk count.

    Used by the ``voice_speak`` MCP tool. Chunks here (not in the worker
    loop) so the caller's "wrote to .out" path is the same as the
    smoke script's "wrote to .out manually" path — both feed the same
    polling loop and both get the same chunking treatment.
    """
    state = _registry.get(session_id)
    if state is None:
        raise RuntimeError(
            f"voice bridge session {session_id!r} is not running; "
            f"call voice_join_room first",
        )
    cap = max_chars or state.config.chunk_max_chars
    chunks = chunk_text_for_tts(text, max_chars=cap)
    if not chunks:
        return 0
    paths = session_pipe_paths(session_id)
    # Append each chunk as its own line so the .out poller sees them
    # one-at-a-time. The poller re-chunks but a chunked input is a
    # no-op pass-through — the cost is one regex run per chunk.
    with open(paths["out"], "a", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(chunk + "\n")
    return len(chunks)
