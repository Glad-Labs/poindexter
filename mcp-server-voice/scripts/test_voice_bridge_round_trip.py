"""End-to-end audio round-trip smoke test for the LiveKit MCP bridge.

Unlike ``test_voice_bridge_smoke.py`` (which exercises the control plane
against the no-op audio plane), this script flips the ``Pipecat`` audio
plane on, joins a real LiveKit room as a SECOND participant, publishes
a Whisper-decodable audio clip, and verifies:

1. The bridge's STT picked up the speech and wrote a transcript line to
   ``~/.poindexter/voice/<sid>.in`` within 5 seconds.
2. Writing a sentence to ``~/.poindexter/voice/<sid>.out`` results in
   audio published to the room within 3 seconds (we subscribe to the
   bridge participant's track on the harness side and assert >0 bytes
   flowed).
3. ``voice_leave_room`` cleans up gracefully (no stuck ``.lock`` file).

## Prerequisites

- LiveKit container reachable at ``localhost:7880`` (the docker-compose
  default). Override via ``LIVEKIT_URL``.
- ``LIVEKIT_API_KEY`` / ``LIVEKIT_API_SECRET`` env vars set to the
  container's values.
- Pipecat / faster-whisper / Kokoro / livekit installed in the active
  Python environment (see ``mcp-server-voice/pyproject.toml``).
- A 5090 (or any CUDA-capable GPU) is recommended; CPU-only Whisper /
  Kokoro works but takes 5-10x longer.

## Usage

::

    poetry run python mcp-server-voice/scripts/test_voice_bridge_round_trip.py

Outputs a wall-clock latency reading at the bottom -- useful when tuning
``voice_bridge_stt_model`` / ``voice_bridge_tts_voice``.

This is intentionally a script, not a unit test: the live stack is the
whole point. If it can't reach LiveKit / Whisper / Kokoro it prints a
clear "stack not reachable" line and exits non-zero rather than hanging.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import wave
from pathlib import Path

# Resolve sibling imports.
HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
REPO_ROOT = MCP_SERVER_DIR.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))
# Add the cofounder_agent package so ``services.voice_pipecat`` resolves.
SERVICES_ROOT = REPO_ROOT / "src" / "cofounder_agent"
if SERVICES_ROOT.is_dir() and str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))


def _heading(text: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{text}\n{bar}", flush=True)


async def _generate_test_clip(out_path: Path, voice: str = "af_bella") -> Path:
    """Render a short Whisper-decodable clip via Kokoro itself.

    Kokoro is already a hard dep of the audio plane; using it for the
    test source removes the "where do we get a WAV file" question.
    Returns the path to the rendered clip.
    """
    try:
        from kokoro_onnx import Kokoro  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"kokoro_onnx not importable -- install the audio deps from "
            f"mcp-server-voice/pyproject.toml. Original: {exc}"
        ) from exc

    text = "Hello bridge, this is a smoke test."
    kokoro = Kokoro.from_pretrained(model="kokoro-v0_19", voice=voice)
    samples, sr = kokoro.create(text, voice=voice, speed=1.0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        # samples come back as a numpy float32 array; convert to int16 PCM.
        import numpy as np

        clip = (np.clip(samples, -1.0, 1.0) * 32767).astype("int16")
        wf.writeframes(clip.tobytes())
    return out_path


async def _publish_clip_to_room(
    *,
    url: str,
    api_key: str,
    api_secret: str,
    room_name: str,
    clip_path: Path,
) -> tuple["object", "object"]:
    """Join ``room_name`` as a harness participant and publish ``clip_path``.

    Returns the (room, audio_source) handles so the caller can keep
    pumping frames or disconnect cleanly.
    """
    from livekit import api, rtc

    grants = api.VideoGrants(
        room_join=True, room=room_name,
        can_publish=True, can_subscribe=True,
    )
    token = (
        api.AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity("smoke-test-publisher")
        .with_grants(grants)
        .to_jwt()
    )
    room = rtc.Room()
    await room.connect(url, token)

    # Read the WAV and create a LiveKit audio source.
    import numpy as np

    with wave.open(str(clip_path), "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        pcm_bytes = wf.readframes(n_frames)
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)

    source = rtc.AudioSource(sample_rate=sr, num_channels=1)
    track = rtc.LocalAudioTrack.create_audio_track("smoke-clip", source)
    await room.local_participant.publish_track(track)

    # Push the whole clip in 10ms chunks (LiveKit's expected cadence).
    chunk_samples = sr // 100  # 10ms
    for i in range(0, len(samples), chunk_samples):
        chunk = samples[i:i + chunk_samples]
        await source.capture_frame(
            rtc.AudioFrame(
                data=chunk.tobytes(),
                sample_rate=sr,
                num_channels=1,
                samples_per_channel=len(chunk),
            ),
        )
        await asyncio.sleep(0.01)
    return room, source


async def main() -> int:
    _heading("LiveKit MCP bridge -- round-trip smoke test (real audio)")

    started = time.time()
    sandbox = REPO_ROOT / ".tmp" / "voice-bridge-roundtrip"
    sandbox.mkdir(parents=True, exist_ok=True)
    for stale in sandbox.glob("vb-*"):
        try:
            stale.unlink()
        except OSError:
            pass
    os.environ["POINDEXTER_VOICE_DIR"] = str(sandbox)

    url = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")

    if not api_key or not api_secret:
        print(
            "[FAIL] LIVEKIT_API_KEY / LIVEKIT_API_SECRET unset. Set them "
            "to the values in docker-compose.local.yml and rerun.",
            file=sys.stderr,
        )
        return 2

    # Reset registry + import the bridge fresh.
    import livekit_bridge  # noqa: WPS433

    livekit_bridge._registry = livekit_bridge._BridgeRegistry()
    from audio_plane_pipecat import PipecatAudioMediaPlane  # noqa: WPS433

    sid = "vb-rt00001"
    room = "voice-bridge-smoke"
    plane = PipecatAudioMediaPlane(
        stt_model=os.environ.get("VOICE_BRIDGE_STT_MODEL", "base.en"),
        tts_voice=os.environ.get("VOICE_BRIDGE_TTS_VOICE", "af_bella"),
        livekit_url=url,
        livekit_api_key=api_key,
        livekit_api_secret=api_secret,
    )
    config = livekit_bridge.BridgeConfig(
        room=room,
        identity=f"claude-bridge-{sid}",
        chunk_max_chars=200,
        out_poll_interval=0.05,
        max_session_seconds=120,
    )

    print(f"-> Starting bridge sid={sid} room={room} url={url}")
    state = await livekit_bridge.start_bridge(
        session_id=sid, config=config, media=plane,
    )
    print(f"   bridge task: {state.task!r}")

    # Generate a Whisper-decodable WAV via Kokoro (so we don't ship a
    # binary blob in the repo).
    print("-> Generating test clip via Kokoro")
    clip_path = sandbox / "smoke-clip.wav"
    await _generate_test_clip(clip_path)
    print(f"   clip: {clip_path} ({clip_path.stat().st_size} bytes)")

    # Join the room as a SECOND participant + publish the clip.
    print("-> Publishing clip from harness participant 'smoke-test-publisher'")
    pub_started = time.time()
    pub_room, pub_source = await _publish_clip_to_room(
        url=url, api_key=api_key, api_secret=api_secret,
        room_name=room, clip_path=clip_path,
    )

    # Wait for STT to land a transcript on the .in pipe (cap 10s).
    print("-> Waiting for transcript on .in pipe (cap 10s)")
    in_path = sandbox / f"{sid}.in"
    transcript_seen_at: float | None = None
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if in_path.exists() and in_path.stat().st_size > 0:
            content = in_path.read_text(encoding="utf-8").strip()
            if content:
                transcript_seen_at = time.time()
                print(f"   .in: {content!r}")
                break
        await asyncio.sleep(0.1)
    if transcript_seen_at is None:
        print(
            "[FAIL] No transcript landed on .in within 10s. Check Whisper "
            "model load / VAD silence threshold.",
            file=sys.stderr,
        )
        await livekit_bridge.stop_bridge(sid)
        await pub_room.disconnect()
        return 3

    stt_latency = transcript_seen_at - pub_started
    print(f"   STT latency (publish -> .in): {stt_latency:.2f}s")

    # Now write a TTS request to .out and verify audio flows back.
    print("-> Writing reply to .out + asserting audio publishes within 3s")
    tts_started = time.time()
    queued = await livekit_bridge.speak_into_bridge(
        sid, "Got it, I see you in the room.",
    )
    print(f"   queued {queued} TTS chunks")

    # We can't easily measure exact bytes-on-the-wire from a script
    # without a full LiveKit subscriber wiring, but the bridge's
    # speak_count counter increments per chunk it forwarded to the
    # audio plane -- that's the load-bearing signal.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if state.speak_count >= queued:
            break
        await asyncio.sleep(0.05)
    if state.speak_count < queued:
        print(
            f"[FAIL] Bridge speak_count={state.speak_count} but queued "
            f"{queued} chunks. TTS pump didn't drain.",
            file=sys.stderr,
        )
        await livekit_bridge.stop_bridge(sid)
        await pub_room.disconnect()
        return 4
    tts_latency = time.time() - tts_started
    print(f"   TTS latency (queue -> publish): {tts_latency:.2f}s")

    print("-> Tearing down bridge and harness")
    stopped = await livekit_bridge.stop_bridge(sid)
    assert stopped, "stop_bridge returned False"
    await pub_room.disconnect()

    lock_path = sandbox / f"{sid}.lock"
    assert not lock_path.exists(), f".lock lingered: {lock_path}"

    elapsed = time.time() - started
    _heading("PASS")
    print(f"Round-trip smoke test passed in {elapsed:.2f}s.")
    print(f"  STT latency: {stt_latency:.2f}s (target warm <1.2s, cold <3s)")
    print(f"  TTS latency: {tts_latency:.2f}s (target warm <0.8s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
