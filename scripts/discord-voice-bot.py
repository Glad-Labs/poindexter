"""
Discord Voice Bot — hands-free voice meetings with Claude Code.

Join a Discord voice channel, talk naturally. The bot uses VAD
(voice activity detection) to auto-detect when you speak and pause.
No /listen or /stop needed during conversation.

Flow:
1. /join — bot enters your voice channel, starts listening
2. You talk — VAD detects speech, buffers audio
3. You pause (~1.5s) — bot transcribes via Whisper on GPU
4. Claude responds — bot speaks via Edge TTS
5. Repeat — fully hands-free back-and-forth
6. /leave — done

Usage:
    python scripts/discord-voice-bot.py

Requires:
    pip install py-cord[voice] faster-whisper edge-tts asyncpg webrtcvad

Configuration (all from app_settings DB, no env vars):
    discord_bot_token          — Discord bot token
    discord_voice_channel_id   — auto-join channel (optional)
    whisper_model              — faster-whisper model (default: base.en)
    tts_voice                  — Edge TTS voice (default: en-US-GuyNeural)
"""

import asyncio
import io
import os
import struct
import sys
import tempfile
import time
import wave
from collections import deque
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

import discord
import edge_tts
from faster_whisper import WhisperModel

try:
    import webrtcvad
    HAS_VAD = True
except ImportError:
    HAS_VAD = False
    print("[WARN] webrtcvad not installed — falling back to /listen + /stop mode")


def _load_config_from_db() -> dict:
    import asyncpg
    from brain.bootstrap import resolve_database_url

    db_url = os.getenv("DATABASE_URL") or resolve_database_url()
    if not db_url:
        print("ERROR: No database URL found. Run `poindexter setup` first.")
        sys.exit(1)

    async def _fetch():
        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                "SELECT key, value FROM app_settings WHERE key IN "
                "('discord_bot_token', 'discord_voice_bot_token', "
                "'discord_voice_channel_id', "
                "'whisper_model', 'tts_voice', 'api_token')"
            )
            return {r["key"]: r["value"] for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_fetch())


print("[INIT] Loading config from app_settings...")
_cfg = _load_config_from_db()

DISCORD_TOKEN = _cfg.get("discord_bot_token", "")
VOICE_CHANNEL_ID = int(_cfg.get("discord_voice_channel_id", "0"))
WHISPER_MODEL = _cfg.get("whisper_model", "base.en")
TTS_VOICE = _cfg.get("tts_voice", "en-US-GuyNeural")
API_URL = os.getenv("POINDEXTER_API_URL", "http://localhost:8002")
API_TOKEN = _cfg.get("api_token", "")

SILENCE_THRESHOLD_S = 1.5
MIN_SPEECH_S = 0.5
MAX_SPEECH_S = 30.0
SAMPLE_RATE = 48000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

DATA_DIR = Path(tempfile.gettempdir()) / "discord-voice-bot"
DATA_DIR.mkdir(exist_ok=True)

print(f"[INIT] Loading Whisper model '{WHISPER_MODEL}' on GPU...")
whisper_model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
print("[INIT] Whisper ready.")

if HAS_VAD:
    vad = webrtcvad.Vad(2)
    print("[INIT] WebRTC VAD ready (aggressiveness=2)")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
bot = discord.Bot(intents=intents, loop=loop)

conversation_history = []
_vad_tasks = {}


async def transcribe_audio(audio_bytes: bytes) -> str:
    tmp_path = DATA_DIR / f"recording_{int(time.time() * 1000)}.wav"
    tmp_path.write_bytes(audio_bytes)
    try:
        segments, _ = whisper_model.transcribe(
            str(tmp_path), beam_size=5, language="en"
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()
    finally:
        tmp_path.unlink(missing_ok=True)


async def text_to_speech(text: str) -> str:
    out_path = str(DATA_DIR / f"reply_{int(time.time() * 1000)}.mp3")
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(out_path)
    return out_path


def _pcm_to_wav(pcm_data: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def _stereo_to_mono_16k(pcm_data: bytes, from_rate: int = 48000) -> bytes:
    samples = struct.unpack(f"<{len(pcm_data) // 2}h", pcm_data)
    mono = [samples[i] for i in range(0, len(samples), 2)]
    ratio = from_rate // 16000
    downsampled = [mono[i] for i in range(0, len(mono), ratio)]
    return struct.pack(f"<{len(downsampled)}h", *downsampled)


async def process_speech(audio_pcm: bytes, text_channel, guild):
    """Transcribe speech, get Claude response, play TTS."""
    wav_data = _pcm_to_wav(audio_pcm)
    duration = len(audio_pcm) / (SAMPLE_RATE * 2 * 2)

    if duration < MIN_SPEECH_S:
        return

    print(f"[VAD] Processing {duration:.1f}s of speech")

    text = await transcribe_audio(wav_data)
    if not text or len(text.strip()) < 3:
        print("[VAD] Transcription empty, skipping")
        return

    print(f"[VAD] Transcribed: {text}")
    conversation_history.append({"role": "user", "content": text})
    await text_channel.send(f"🎤 **You:** {text}")

    response_text = await get_claude_response(text)
    print(f"[CLAUDE] {response_text[:100]}...")
    conversation_history.append({"role": "assistant", "content": response_text})
    await text_channel.send(f"🤖 **Claude:** {response_text[:1900]}")

    vc = guild.voice_client
    if vc and vc.is_connected() and not vc.is_playing():
        tts_path = await text_to_speech(response_text[:500])
        vc.play(discord.FFmpegPCMAudio(tts_path))
        while vc.is_playing():
            await asyncio.sleep(0.5)
        try:
            os.unlink(tts_path)
        except OSError:
            pass


class VADSink(discord.sinks.Sink):
    """Custom sink with voice activity detection.

    Continuously receives audio. Uses WebRTC VAD to detect speech
    segments. When speech ends (silence > threshold), emits the
    buffered audio for transcription.
    """

    def __init__(self, text_channel, guild):
        super().__init__()
        self.text_channel = text_channel
        self.guild = guild
        self._buffers = {}
        self._speech_active = {}
        self._silence_start = {}
        self._frame_buf = {}

    def write(self, data: bytes, user_id):
        if not HAS_VAD:
            return

        if user_id not in self._buffers:
            self._buffers[user_id] = bytearray()
            self._speech_active[user_id] = False
            self._silence_start[user_id] = 0
            self._frame_buf[user_id] = bytearray()

        self._frame_buf[user_id].extend(data)

        frame_bytes = FRAME_SAMPLES * 2 * 2
        while len(self._frame_buf[user_id]) >= frame_bytes:
            frame = bytes(self._frame_buf[user_id][:frame_bytes])
            self._frame_buf[user_id] = self._frame_buf[user_id][frame_bytes:]

            try:
                mono_16k = _stereo_to_mono_16k(frame)
                vad_frame_size = int(16000 * FRAME_MS / 1000) * 2
                is_speech = vad.is_speech(mono_16k[:vad_frame_size], 16000)
            except Exception:
                is_speech = False

            if is_speech:
                self._speech_active[user_id] = True
                self._silence_start[user_id] = 0
                self._buffers[user_id].extend(frame)

                if len(self._buffers[user_id]) > MAX_SPEECH_S * SAMPLE_RATE * 4:
                    self._emit(user_id)
            else:
                if self._speech_active[user_id]:
                    self._buffers[user_id].extend(frame)
                    if self._silence_start[user_id] == 0:
                        self._silence_start[user_id] = time.monotonic()
                    elif time.monotonic() - self._silence_start[user_id] > SILENCE_THRESHOLD_S:
                        self._emit(user_id)

    def _emit(self, user_id):
        audio_data = bytes(self._buffers[user_id])
        self._buffers[user_id] = bytearray()
        self._speech_active[user_id] = False
        self._silence_start[user_id] = 0

        if len(audio_data) > MIN_SPEECH_S * SAMPLE_RATE * 4:
            asyncio.run_coroutine_threadsafe(
                process_speech(audio_data, self.text_channel, self.guild),
                bot.loop,
            )

    def cleanup(self):
        for uid in list(self._buffers.keys()):
            if self._buffers[uid]:
                self._emit(uid)


async def get_claude_response(user_text: str) -> str:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_URL}/api/compose/chat",
                headers={"Authorization": f"Bearer {API_TOKEN}"},
                json={"message": user_text, "history": conversation_history[-10:]},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("response", data.get("text", str(data)))
    except Exception as e:
        print(f"[API] Failed: {e}")

    return f"I heard: '{user_text}'. (Claude API not connected yet)"


@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user}")
    if VOICE_CHANNEL_ID:
        channel = bot.get_channel(VOICE_CHANNEL_ID)
        if channel and isinstance(channel, discord.VoiceChannel):
            vc = await channel.connect()
            print(f"[BOT] Auto-joined voice channel: {channel.name}")


@bot.slash_command(name="join", description="Join your voice channel and start listening")
async def join(ctx):
    if not ctx.author.voice:
        await ctx.respond("You're not in a voice channel.", ephemeral=True)
        return

    vc = ctx.guild.voice_client
    if vc and vc.is_connected():
        await vc.move_to(ctx.author.voice.channel)
    else:
        vc = await ctx.author.voice.channel.connect()

    if HAS_VAD:
        sink = VADSink(ctx.channel, ctx.guild)
        vc.start_recording(sink, lambda *a: None, ctx.channel)
        await ctx.respond(
            f"Joined **{ctx.author.voice.channel.name}** — listening with VAD. "
            f"Just talk naturally. I'll respond when you pause."
        )
    else:
        await ctx.respond(
            f"Joined **{ctx.author.voice.channel.name}**. "
            f"VAD not available — use `/listen` and `/stop`."
        )


@bot.slash_command(name="listen", description="Start recording (fallback for no-VAD mode)")
async def listen(ctx):
    vc = ctx.guild.voice_client
    if not vc or not vc.is_connected():
        await ctx.respond("Not in a voice channel. Use `/join` first.", ephemeral=True)
        return
    if vc.is_recording():
        await ctx.respond("Already listening.", ephemeral=True)
        return

    sink = discord.sinks.WaveSink()

    async def _on_done(sink, channel, *args):
        for user_id, audio in sink.audio_data.items():
            audio_bytes = audio.file.read()
            if len(audio_bytes) > 5000:
                await process_speech(audio_bytes, channel, ctx.guild)

    vc.start_recording(sink, _on_done, ctx.channel)
    await ctx.respond("Listening... Use `/stop` when done.")


@bot.slash_command(name="stop", description="Stop recording")
async def stop(ctx):
    vc = ctx.guild.voice_client
    if not vc or not vc.is_recording():
        await ctx.respond("Not recording.", ephemeral=True)
        return
    vc.stop_recording()
    await ctx.respond("Processing...")


@bot.slash_command(name="leave", description="Leave voice channel")
async def leave(ctx):
    vc = ctx.guild.voice_client
    if vc:
        if vc.is_recording():
            vc.stop_recording()
        await vc.disconnect()
        await ctx.respond("Left voice channel.")
    else:
        await ctx.respond("Not in a voice channel.", ephemeral=True)


@bot.slash_command(name="clear", description="Clear conversation history")
async def clear(ctx):
    conversation_history.clear()
    await ctx.respond("Conversation history cleared.")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: discord_bot_token not found in app_settings.")
        print("  Set it via the API:")
        print("  curl -X PUT localhost:8002/api/settings/discord_bot_token \\")
        print('    -H "Authorization: Bearer $TOKEN" \\')
        print("    -d '{\"value\": \"your-bot-token\"}'")
        raise SystemExit(1)

    print("[BOT] Starting Discord Voice Bot (VAD enabled)" if HAS_VAD else "[BOT] Starting (no VAD)")
    print(f"[BOT] Config from app_settings | Whisper: {WHISPER_MODEL} | TTS: {TTS_VOICE}")
    print(f"[BOT] VAD: silence={SILENCE_THRESHOLD_S}s, min_speech={MIN_SPEECH_S}s, max={MAX_SPEECH_S}s")
    bot.run(DISCORD_TOKEN)
