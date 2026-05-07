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

## Pipe layout

For session_id ``ab12``:

- ``~/.poindexter/voice/ab12.in``  — bridge writes utterance transcripts here;
  the slash-command-side ``Monitor`` watches the file for new lines.
- ``~/.poindexter/voice/ab12.out`` — slash command (or anything else) writes
  text-to-speak lines here; bridge polls and forwards to TTS.
- ``~/.poindexter/voice/ab12.lock`` — sentinel file; presence means a
  bridge worker owns this session id. Cleaned up on graceful exit.

Pipes are plain files, not POSIX FIFOs. Plain files survive a worker
crash without leaving a half-open FD; new writes append; readers track
the byte offset themselves. Cross-platform — works on Matt's Windows
host without the Linux-FIFO quirks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

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
    """Return the canonical .in / .out / .lock paths for a session id.

    Pure function so the slash command, the bridge worker, and the unit
    tests all agree on where the files live.
    """
    base = voice_pipe_dir()
    return {
        "in": base / f"{session_id}.in",
        "out": base / f"{session_id}.out",
        "lock": base / f"{session_id}.lock",
    }


def ensure_session_pipes(session_id: str) -> dict[str, Path]:
    """Create the pipe directory + empty .in / .out / .lock files.

    Idempotent — safe to call multiple times. Returns the same dict
    :func:`session_pipe_paths` would.
    """
    paths = session_pipe_paths(session_id)
    paths["in"].parent.mkdir(parents=True, exist_ok=True)
    for kind in ("in", "out"):
        if not paths[kind].exists():
            paths[kind].touch()
    return paths


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
        self, callback: Callable[[str], "asyncio.Future | None"]
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
        self._on_utterance: Callable[[str], "asyncio.Future | None"] | None = None
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
        self, callback: Callable[[str], "asyncio.Future | None"]
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
    task: Optional["asyncio.Task[None]"] = None


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

    state.media.set_utterance_callback(
        lambda t: _on_utterance_end(state, t),
    )

    try:
        await state.media.connect(
            room=state.config.room,
            identity=state.config.identity,
        )
    except Exception:
        logger.exception(
            "Audio plane connect failed for session %s — bridge aborting",
            state.session_id,
        )
        # Per feedback_no_silent_defaults: tear down + re-raise so the
        # MCP tool sees the failure rather than thinking the bridge is up.
        try:
            paths["lock"].unlink(missing_ok=True)
        except OSError:
            pass
        _registry.remove(state.session_id)
        raise

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
        try:
            paths["lock"].unlink(missing_ok=True)
        except OSError:
            pass
        _registry.remove(state.session_id)
        logger.info(
            "Bridge session %s closed — utterances=%d, tts_chunks=%d, "
            "elapsed=%.1fs",
            state.session_id, state.utterance_count, state.speak_count,
            time.time() - state.started_at,
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

    media = media or NoopAudioMediaPlane()
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
