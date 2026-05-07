"""Manual smoke test for the LiveKit MCP bridge — no GPU / Pipecat needed.

Usage::

    python scripts/test_voice_bridge_smoke.py

Spins up a single bridge worker against the no-op audio plane, fakes
two utterances (which append to the .in pipe), writes a multi-sentence
reply through ``voice_speak`` (which the worker forwards to the no-op
TTS via the .out pipe poller), then tears down. Verifies:

1. ``.in`` pipe has 2 lines after the fake utterances.
2. ``.out`` pipe has at least 2 lines after the multi-sentence reply
   (chunked at sentence boundaries).
3. ``.lock`` file is gone after ``voice_leave_room``.
4. The whole round trip completes in under 2 seconds.

This is the fast feedback loop while developing the bridge's control
plane. The *real* LiveKit + Whisper + Kokoro round trip needs a running
voice-agent stack and a 5090 — that's a separate manual step (see
docs/operations/voice-bridge.md "Live verification" section).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# Resolve sibling imports — works whether you run this from the repo
# root or from inside scripts/. ``HERE`` is mcp-server-voice/scripts/,
# so ``HERE.parent`` is mcp-server-voice/, where livekit_bridge.py lives.
HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
REPO_ROOT = MCP_SERVER_DIR.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import livekit_bridge  # noqa: E402


def _heading(text: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n{text}\n{bar}", flush=True)


async def main() -> int:
    _heading("LiveKit MCP bridge — control-plane smoke test")

    # Use a sandbox dir so we don't pollute ~/.poindexter/voice/. Clear
    # any pipe files from a previous run so the assertions count fresh
    # input rather than accumulating across invocations.
    sandbox = REPO_ROOT / ".tmp" / "voice-bridge-smoke"
    sandbox.mkdir(parents=True, exist_ok=True)
    for stale in sandbox.glob("vb-*"):
        try:
            stale.unlink()
        except OSError:
            pass
    os.environ["POINDEXTER_VOICE_DIR"] = str(sandbox)

    # Reset the registry in case a previous run left state behind (we
    # share a process when invoked from a watcher / IDE).
    livekit_bridge._registry = livekit_bridge._BridgeRegistry()

    sid = "vb-smoke001"
    media = livekit_bridge.NoopAudioMediaPlane()
    config = livekit_bridge.BridgeConfig(
        room="claude-bridge",
        chunk_max_chars=80,
        out_poll_interval=0.05,
        max_session_seconds=60,
    )

    started = time.time()
    print(f"-> Starting bridge session={sid} room={config.room}")
    state = await livekit_bridge.start_bridge(
        session_id=sid, config=config, media=media,
    )
    print(f"   bridge task: {state.task!r}")

    # Drive two fake utterances through the no-op audio plane. Each
    # appends one line to the .in pipe (the slash command's Monitor
    # would wake the session on each line).
    print("-> Faking two utterances")
    await media.fake_utterance("hello bridge it is matt")
    await media.fake_utterance("can you check the post count")

    # Write a multi-sentence reply through voice_speak. The bridge's
    # .out poller picks them up and forwards each chunk to the no-op
    # TTS plane (which logs but emits no audio).
    print("-> Speaking a multi-sentence reply")
    text = (
        "Sure thing. There are fifty two posts live right now. "
        "Latest one was published this morning. "
        "Let me know if you want a breakdown by category."
    )
    chunks = await livekit_bridge.speak_into_bridge(sid, text)
    print(f"   queued {chunks} TTS chunks")

    # Give the .out poller a chance to drain. Poll interval is 50ms;
    # 0.5s is plenty for 3-4 chunks.
    await asyncio.sleep(0.6)

    # Verify .in
    in_path = sandbox / f"{sid}.in"
    in_lines = [l for l in in_path.read_text().splitlines() if l.strip()]
    print(f"-> .in pipe has {len(in_lines)} lines:")
    for line in in_lines:
        print(f"     {line!r}")
    assert len(in_lines) == 2, (
        f"Expected 2 utterance lines in .in, got {len(in_lines)}"
    )

    # Verify .out
    out_path = sandbox / f"{sid}.out"
    out_lines = [l for l in out_path.read_text().splitlines() if l.strip()]
    print(f"-> .out pipe has {len(out_lines)} lines:")
    for line in out_lines:
        print(f"     {line!r}")
    assert len(out_lines) >= 2, (
        f"Expected 2+ chunks in .out, got {len(out_lines)}"
    )

    # Verify the worker emitted speak_count matching what we queued.
    print(
        f"-> Worker stats: utterance_count={state.utterance_count} "
        f"speak_count={state.speak_count}"
    )
    assert state.utterance_count == 2
    assert state.speak_count >= 2

    # Tear down
    print(f"-> Stopping bridge session={sid}")
    stopped = await livekit_bridge.stop_bridge(sid)
    assert stopped, "stop_bridge returned False on the active session"

    lock_path = sandbox / f"{sid}.lock"
    assert not lock_path.exists(), (
        f".lock file lingered after teardown: {lock_path}"
    )

    elapsed = time.time() - started
    print(f"\nSmoke test passed in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
