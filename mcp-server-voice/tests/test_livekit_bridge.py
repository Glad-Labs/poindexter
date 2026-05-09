"""Tests for the LiveKit MCP bridge — voice as a session-agnostic UI surface.

The bridge is an additive surface: an already-running Claude Code session
calls ``voice_join_room`` to claim voice in/out for itself, talks via
``voice_speak`` + the per-session ``.in`` pipe, and tears down via
``voice_leave_room``. The always-on voice-agent-livekit container is
unaffected.

Tests focus on the boundaries we control — pipe layout, chunking,
session lifecycle, watchdog timeouts. The actual LiveKit + Whisper +
Kokoro round-trip is exercised via the smoke script
(``scripts/test_voice_bridge_smoke.py``), not here.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Make ``import server`` and ``import livekit_bridge`` resolve regardless
# of where pytest is invoked.
HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import livekit_bridge  # noqa: E402
import server  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: temp pipe dir, fake pool, tool callable resolution
# ---------------------------------------------------------------------------


@pytest.fixture
def voice_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the bridge's pipe directory at a tmp path.

    Sets ``$POINDEXTER_VOICE_DIR`` so any code path that resolves the
    pipe location (the bridge worker, the speak helper, the
    smoke script) all see the same tmp dir. Resets the in-process
    registry so cross-test bridges don't leak.
    """
    monkeypatch.setenv("POINDEXTER_VOICE_DIR", str(tmp_path))
    # Reset the in-process registry — pytest fixtures share the module,
    # so a leaked bridge from a previous test would poison a fresh
    # voice_join_room call.
    livekit_bridge._registry = livekit_bridge._BridgeRegistry()
    return tmp_path


class _FakePool:
    """Minimal asyncpg.Pool fake — returns canned bridge settings rows.

    The bridge tools call ``pool.fetch(...)`` to read app_settings. We
    return ``[{key, value}, ...]`` so the tool's ``_bridge_settings``
    helper can build the dict without touching Postgres.
    """

    def __init__(self, settings: dict[str, str]) -> None:
        self.settings = settings

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        return [{"key": k, "value": v} for k, v in self.settings.items()]


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    """Patch ``server._get_pool`` to return a recording fake pool."""
    pool = _FakePool({
        "voice_bridge_enabled": "true",
        "voice_default_room": "claude-bridge",
        "voice_bridge_stt_model": "base.en",
        "voice_bridge_tts_voice": "af_bella",
        "voice_bridge_max_session_seconds": "1800",
        "voice_bridge_chunk_max_chars": "500",
    })

    async def _get_pool() -> _FakePool:
        return pool

    monkeypatch.setattr(server, "_get_pool", _get_pool)
    return pool


def _resolve_tool(name: str):
    """FastMCP wraps tools — pull the underlying coroutine out for direct call."""
    obj = getattr(server, name, None)
    for attr in ("fn", "func", "callable"):
        impl = getattr(obj, attr, None)
        if callable(impl):
            return impl
    if callable(obj):
        return obj
    raise AssertionError(f"Could not resolve callable for {name!r}")


_voice_join_room = _resolve_tool("voice_join_room")
_voice_speak = _resolve_tool("voice_speak")
_voice_leave_room = _resolve_tool("voice_leave_room")


# ===========================================================================
# Pure helpers — chunking, pipe layout
# ===========================================================================


class TestChunking:
    """``chunk_text_for_tts`` is the function that keeps long replies
    interruptible. Bugs here directly degrade the user-facing experience."""

    def test_empty_input_returns_empty_list(self) -> None:
        assert livekit_bridge.chunk_text_for_tts("") == []
        assert livekit_bridge.chunk_text_for_tts("   ") == []

    def test_short_input_is_single_chunk(self) -> None:
        out = livekit_bridge.chunk_text_for_tts("Hello world.")
        assert out == ["Hello world."]

    def test_splits_at_sentence_boundaries(self) -> None:
        text = (
            "First sentence here. "
            "Second sentence here! "
            "Third one with a question? "
            "And the fourth."
        )
        # max_chars=40 forces multiple chunks
        chunks = livekit_bridge.chunk_text_for_tts(text, max_chars=40)
        # Every chunk should END at a sentence boundary (., !, ?) — never
        # mid-sentence — except possibly the last one in degenerate input.
        for c in chunks[:-1]:
            assert c.rstrip()[-1] in ".!?", (
                f"Chunk {c!r} ends mid-sentence — interruptibility broken"
            )

    def test_oversized_single_sentence_kept_whole(self) -> None:
        # A sentence longer than max_chars gets its own chunk un-split —
        # we'd rather emit one oversized chunk than cut the prosody.
        long = "Lorem ipsum dolor sit amet consectetur adipiscing elit." * 5
        chunks = livekit_bridge.chunk_text_for_tts(long, max_chars=50)
        assert len(chunks) == 1
        assert chunks[0] == long.replace("  ", " ")

    def test_collapses_whitespace_runs(self) -> None:
        # Multiple spaces -> single space (Kokoro reads each whitespace
        # token as a pause cue).
        text = "Hello\n\n\n   world.   Goodbye."
        chunks = livekit_bridge.chunk_text_for_tts(text, max_chars=200)
        assert "  " not in chunks[0]
        assert "\n" not in chunks[0]


class TestPipeLayout:
    """The pipe paths are a public-ish contract — the slash command's
    Monitor reads ``.in``, the smoke script writes ``.out``. Tests here
    pin the layout so a refactor doesn't break wire compatibility.
    """

    def test_session_pipe_paths_returns_in_out_lock(self, voice_dir: Path) -> None:
        paths = livekit_bridge.session_pipe_paths("vb-deadbeef")
        assert paths["in"] == voice_dir / "vb-deadbeef.in"
        assert paths["out"] == voice_dir / "vb-deadbeef.out"
        assert paths["lock"] == voice_dir / "vb-deadbeef.lock"

    def test_ensure_session_pipes_creates_files(self, voice_dir: Path) -> None:
        paths = livekit_bridge.ensure_session_pipes("vb-test1234")
        assert paths["in"].exists()
        assert paths["out"].exists()
        # .lock NOT created here — it's the worker's job (its presence
        # marks an active worker).
        assert not paths["lock"].exists()

    def test_ensure_session_pipes_is_idempotent(self, voice_dir: Path) -> None:
        livekit_bridge.ensure_session_pipes("vb-idem9999")
        # Write some content; second ensure call must NOT truncate.
        path = voice_dir / "vb-idem9999.in"
        path.write_text("existing content\n")
        livekit_bridge.ensure_session_pipes("vb-idem9999")
        assert path.read_text() == "existing content\n"


# ===========================================================================
# voice_join_room MCP tool
# ===========================================================================


@pytest.mark.asyncio
class TestVoiceJoinRoom:

    async def test_returns_session_id_and_creates_pipes(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        raw = await _voice_join_room()
        payload = json.loads(raw)

        assert payload["status"] == "started"
        assert payload["session_id"].startswith("vb-")
        assert payload["room"] == "claude-bridge"
        # Pipe files now exist on disk
        assert Path(payload["in_pipe"]).exists()
        assert Path(payload["out_pipe"]).exists()
        # Cleanup so the next test doesn't see this session
        await _voice_leave_room(payload["session_id"])

    async def test_respects_explicit_session_id(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        raw = await _voice_join_room(session_id="vb-mine0001")
        payload = json.loads(raw)
        assert payload["session_id"] == "vb-mine0001"
        await _voice_leave_room("vb-mine0001")

    async def test_respects_explicit_channel_id(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        raw = await _voice_join_room(channel_id="ops")
        payload = json.loads(raw)
        assert payload["room"] == "ops"
        await _voice_leave_room(payload["session_id"])

    async def test_fails_loud_when_disabled(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        # feedback_no_silent_defaults: master switch off must explicitly
        # error, not silently no-op or return ok=True.
        fake_pool.settings["voice_bridge_enabled"] = "false"
        raw = await _voice_join_room()
        payload = json.loads(raw)
        assert "error" in payload
        assert payload.get("disabled") is True
        assert "voice_bridge_enabled" in payload["error"]

    async def test_fails_loud_when_room_unset(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        fake_pool.settings["voice_default_room"] = ""
        raw = await _voice_join_room()
        payload = json.loads(raw)
        assert "error" in payload
        assert payload["missing_setting"] == "voice_default_room"


# ===========================================================================
# voice_leave_room MCP tool
# ===========================================================================


@pytest.mark.asyncio
class TestVoiceLeaveRoom:

    async def test_idempotent(self, voice_dir: Path, fake_pool: _FakePool) -> None:
        # Spin one up
        raw = await _voice_join_room(session_id="vb-leave0001")
        sid = json.loads(raw)["session_id"]

        # First leave — stops the worker
        first = json.loads(await _voice_leave_room(sid))
        assert first["status"] == "stopped"
        assert first["session_id"] == sid

        # Second leave — nothing to stop, but does NOT raise
        second = json.loads(await _voice_leave_room(sid))
        assert second["status"] == "not_running"
        assert second["session_id"] == sid

    async def test_unknown_session_returns_not_running(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        # Calling leave on an id that was never joined is a benign no-op,
        # not an error — the slash command may call leave on shutdown
        # without knowing if join ever succeeded.
        raw = await _voice_leave_room("vb-neverjoined")
        payload = json.loads(raw)
        assert payload["status"] == "not_running"


# ===========================================================================
# voice_speak MCP tool — chunking propagates correctly to .out pipe
# ===========================================================================


@pytest.mark.asyncio
class TestVoiceSpeak:

    async def test_chunks_long_text_at_sentence_boundaries(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        # Tighten the chunk cap so the test text actually splits.
        fake_pool.settings["voice_bridge_chunk_max_chars"] = "60"
        raw = await _voice_join_room(session_id="vb-speak0001")
        sid = json.loads(raw)["session_id"]

        text = (
            "First reply chunk should be self-contained. "
            "Second reply chunk continues the thought. "
            "Third chunk wraps it up."
        )
        speak_raw = await _voice_speak(text=text, session_id=sid)
        speak = json.loads(speak_raw)

        assert speak["status"] == "queued"
        assert speak["chunks"] >= 2, (
            "Three sentences over 60-char cap must produce 2+ chunks; "
            "got "
            f"{speak['chunks']}"
        )
        assert speak["session_id"] == sid

        # Each line in .out is a chunk — verify the file got the right
        # number of lines.
        out_path = voice_dir / f"{sid}.out"
        lines = [l for l in out_path.read_text().splitlines() if l.strip()]
        assert len(lines) == speak["chunks"]
        # Every chunk ends in sentence-end punctuation.
        for line in lines:
            assert line.rstrip()[-1] in ".!?"

        await _voice_leave_room(sid)

    async def test_empty_text_is_noop(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        raw = await _voice_join_room(session_id="vb-empty0001")
        sid = json.loads(raw)["session_id"]
        speak = json.loads(await _voice_speak(text="   ", session_id=sid))
        assert speak["chunks"] == 0
        await _voice_leave_room(sid)

    async def test_speak_without_join_errors_loud(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        speak = json.loads(
            await _voice_speak(text="anyone there?", session_id="vb-ghost"),
        )
        # The bridge returns a structured error rather than silently
        # accepting and dropping the audio (per feedback_no_silent_defaults).
        assert speak["status"] == "not_running"
        assert speak["session_id"] == "vb-ghost"
        assert "not running" in speak["error"]


# ===========================================================================
# STT pipe-write — faked utterance triggers .in append
# ===========================================================================


@pytest.mark.asyncio
class TestUtteranceFlow:

    async def test_fake_utterance_appends_to_in_pipe(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        # Build a state directly so we can grab the no-op media plane and
        # fire fake_utterance — joining via the MCP tool wraps the media
        # plane in a coroutine task we can't easily reach into.
        sid = "vb-utter0001"
        livekit_bridge.ensure_session_pipes(sid)
        media = livekit_bridge.NoopAudioMediaPlane()
        config = livekit_bridge.BridgeConfig(room="claude-bridge")
        state = await livekit_bridge.start_bridge(
            session_id=sid, config=config, media=media,
        )
        try:
            # Drive an utterance through the no-op plane's test hook.
            await media.fake_utterance("hello bridge how are you")
            # The .in pipe should now contain exactly that line.
            in_path = voice_dir / f"{sid}.in"
            content = in_path.read_text()
            assert "hello bridge how are you" in content
            assert content.endswith("\n")
            assert state.utterance_count == 1
        finally:
            await livekit_bridge.stop_bridge(sid)

    async def test_blank_utterance_is_dropped(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        # Whisper occasionally emits ""/whitespace for non-speech
        # noise — we drop those before they wake the session.
        sid = "vb-utter0002"
        livekit_bridge.ensure_session_pipes(sid)
        media = livekit_bridge.NoopAudioMediaPlane()
        config = livekit_bridge.BridgeConfig(room="claude-bridge")
        state = await livekit_bridge.start_bridge(
            session_id=sid, config=config, media=media,
        )
        try:
            await media.fake_utterance("   ")
            assert state.utterance_count == 0
            # .in is empty
            in_path = voice_dir / f"{sid}.in"
            assert in_path.read_text() == ""
        finally:
            await livekit_bridge.stop_bridge(sid)


# ===========================================================================
# Watchdog — auto-leave after max_session_seconds
# ===========================================================================


@pytest.mark.asyncio
class TestWatchdog:

    async def test_auto_leaves_after_max_session_seconds(
        self, voice_dir: Path, fake_pool: _FakePool,
    ) -> None:
        # Use a very tight watchdog and a fast poll interval so the test
        # finishes in well under a second.
        sid = "vb-watch0001"
        livekit_bridge.ensure_session_pipes(sid)
        media = livekit_bridge.NoopAudioMediaPlane()
        config = livekit_bridge.BridgeConfig(
            room="claude-bridge",
            max_session_seconds=0,  # immediate timeout next iteration
            out_poll_interval=0.05,
        )
        state = await livekit_bridge.start_bridge(
            session_id=sid, config=config, media=media,
        )
        # Worker should self-terminate via the deadline check; wait for
        # it to finish but cap at a generous 2s so a hung worker fails
        # the test rather than hanging the whole suite.
        assert state.task is not None
        await asyncio.wait_for(state.task, timeout=2.0)
        # After auto-leave, the registry no longer has this session and
        # the lock file is gone.
        assert livekit_bridge._registry.get(sid) is None
        assert not (voice_dir / f"{sid}.lock").exists()


# ===========================================================================
# new_session_id — recognisable + collision-resistant
# ===========================================================================


def test_new_session_id_is_url_safe_and_prefixed() -> None:
    sid = livekit_bridge.new_session_id()
    assert sid.startswith("vb-")
    # 8 hex chars after the prefix
    assert len(sid) == len("vb-") + 8
    assert all(c in "0123456789abcdef-v" for c in sid.replace("vb-", ""))


def test_new_session_id_collision_rate_is_acceptable() -> None:
    # Sanity check: in 1000 generations, we shouldn't see duplicates.
    # (8 hex chars = 2**32 space, so collision probability is tiny.)
    ids = {livekit_bridge.new_session_id() for _ in range(1000)}
    assert len(ids) == 1000, "session id collision in a 1000-id sample"


# ===========================================================================
# _resolve_default_audio_plane — fail-loud when audio extras absent
# ===========================================================================
#
# Regression guard for poindexter#426: a fresh `uv run server.py` installs
# only the core deps, so probing pipecat / livekit / faster_whisper / kokoro
# returns ImportError. The pre-fix code swallowed that and handed back a
# NoopAudioMediaPlane, so voice_join_room reported "started" while the
# operator heard nothing. The fixed resolver raises with the install hint;
# the noop opt-in stays available for CI / control-plane testing.


class TestResolveDefaultAudioPlane:

    def test_explicit_noop_opt_in_returns_noop_stub(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("VOICE_BRIDGE_AUDIO_PLANE", "noop")
        config = livekit_bridge.BridgeConfig(room="claude-bridge")
        plane = livekit_bridge._resolve_default_audio_plane(config)
        assert isinstance(plane, livekit_bridge.NoopAudioMediaPlane)

    def test_missing_audio_extras_raises_with_install_hint(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Default test env has no audio extras installed -- the plane
        # selector must fail loudly per feedback_no_silent_defaults
        # rather than silently routing real audio to the no-op stub.
        monkeypatch.delenv("VOICE_BRIDGE_AUDIO_PLANE", raising=False)
        config = livekit_bridge.BridgeConfig(room="claude-bridge")
        with pytest.raises(RuntimeError) as excinfo:
            livekit_bridge._resolve_default_audio_plane(config)
        msg = str(excinfo.value)
        assert "[VOICE_BRIDGE]" in msg
        assert "uv --directory mcp-server-voice sync --extra audio" in msg
        # Names every missing module so the operator sees exactly which
        # extras did not install (rather than first-failure-only).
        assert "pipecat" in msg

    def test_explicit_pipecat_value_also_raises_when_deps_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # `pipecat` is the implicit default; setting it explicitly should
        # behave identically (the only special-cased value is `noop`).
        monkeypatch.setenv("VOICE_BRIDGE_AUDIO_PLANE", "pipecat")
        config = livekit_bridge.BridgeConfig(room="claude-bridge")
        with pytest.raises(RuntimeError):
            livekit_bridge._resolve_default_audio_plane(config)
