"""
Discord Voice Bot — hands-free voice meetings with Claude Code.

Join a Discord voice channel, talk naturally. The bot:
1. Records your voice (voice activity detection)
2. Transcribes via faster-whisper on GPU
3. Sends text to the Poindexter worker API (or prints for piping to Claude)
4. Converts Claude's response to speech via Edge TTS
5. Plays the audio back in the voice channel

Usage:
    python scripts/discord-voice-bot.py

Requires:
    pip install py-cord[voice] faster-whisper edge-tts asyncpg

Configuration:
    All settings read from app_settings DB (via bootstrap.toml for
    the database URL). No .env files or environment variables needed.

    app_settings keys:
        discord_bot_token          — Discord bot token
        discord_voice_channel_id   — auto-join channel (optional)
        whisper_model              — faster-whisper model (default: base.en)
        tts_voice                  — Edge TTS voice (default: en-US-GuyNeural)
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

# Add project root so brain.bootstrap is importable
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

import discord
import edge_tts
from faster_whisper import WhisperModel


def _load_config_from_db() -> dict:
    """Read all voice bot settings from app_settings via bootstrap.toml DB URL."""
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
                "('discord_bot_token', 'discord_voice_channel_id', "
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
API_URL = "http://localhost:8002"
API_TOKEN = _cfg.get("api_token", "")

DATA_DIR = Path(tempfile.gettempdir()) / "discord-voice-bot"
DATA_DIR.mkdir(exist_ok=True)

print(f"[INIT] Loading Whisper model '{WHISPER_MODEL}' on GPU...")
whisper_model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
print("[INIT] Whisper ready.")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = discord.Bot(intents=intents)

active_sink = None
conversation_history = []


class VoiceSink(discord.sinks.WaveSink):
    """Collects audio from the voice channel."""
    pass


async def transcribe_audio(audio_bytes: bytes) -> str:
    """Transcribe audio bytes using faster-whisper on GPU."""
    tmp_path = DATA_DIR / f"recording_{int(time.time())}.wav"
    tmp_path.write_bytes(audio_bytes)
    try:
        segments, info = whisper_model.transcribe(
            str(tmp_path), beam_size=5, language="en"
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()
    finally:
        tmp_path.unlink(missing_ok=True)


async def text_to_speech(text: str) -> str:
    """Convert text to MP3 via Edge TTS. Returns file path."""
    out_path = str(DATA_DIR / f"reply_{int(time.time())}.mp3")
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(out_path)
    return out_path


async def on_recording_done(sink: discord.sinks.WaveSink, channel, *args):
    """Called when recording stops. Transcribe + respond."""
    for user_id, audio in sink.audio_data.items():
        audio_bytes = audio.file.read()
        if len(audio_bytes) < 5000:
            continue

        print(f"[VOICE] Received {len(audio_bytes)} bytes from user {user_id}")

        text = await transcribe_audio(audio_bytes)
        if not text or len(text) < 3:
            print("[VOICE] Transcription empty, skipping")
            continue

        print(f"[VOICE] Transcribed: {text}")

        conversation_history.append({"role": "user", "content": text})

        await channel.send(f"**You said:** {text}")

        response_text = await get_claude_response(text)
        print(f"[CLAUDE] Response: {response_text[:100]}...")

        conversation_history.append({"role": "assistant", "content": response_text})

        await channel.send(f"**Claude:** {response_text[:1900]}")

        vc = channel.guild.voice_client
        if vc and vc.is_connected():
            tts_path = await text_to_speech(response_text[:500])
            vc.play(discord.FFmpegPCMAudio(tts_path))
            while vc.is_playing():
                await asyncio.sleep(0.5)
            os.unlink(tts_path)


async def get_claude_response(user_text: str) -> str:
    """Get a response from the Poindexter API or fall back to echo."""
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

    return f"I heard you say: '{user_text}'. (API not connected — set POINDEXTER_API_URL and POINDEXTER_API_TOKEN)"


@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user}")
    if VOICE_CHANNEL_ID:
        channel = bot.get_channel(VOICE_CHANNEL_ID)
        if channel and isinstance(channel, discord.VoiceChannel):
            await channel.connect()
            print(f"[BOT] Auto-joined voice channel: {channel.name}")


@bot.slash_command(name="join", description="Join your voice channel")
async def join(ctx):
    if not ctx.author.voice:
        await ctx.respond("You're not in a voice channel.", ephemeral=True)
        return
    vc = await ctx.author.voice.channel.connect()
    await ctx.respond(f"Joined **{ctx.author.voice.channel.name}**. Use `/listen` to start.")


@bot.slash_command(name="listen", description="Start listening (records until /stop)")
async def listen(ctx):
    vc = ctx.guild.voice_client
    if not vc or not vc.is_connected():
        await ctx.respond("Not in a voice channel. Use `/join` first.", ephemeral=True)
        return

    global active_sink
    active_sink = VoiceSink()
    vc.start_recording(active_sink, on_recording_done, ctx.channel)
    await ctx.respond("Listening... Use `/stop` when you're done talking.")


@bot.slash_command(name="stop", description="Stop listening and get a response")
async def stop(ctx):
    vc = ctx.guild.voice_client
    if not vc or not vc.is_recording():
        await ctx.respond("Not recording.", ephemeral=True)
        return

    vc.stop_recording()
    await ctx.respond("Processing your message...")


@bot.slash_command(name="leave", description="Leave the voice channel")
async def leave(ctx):
    vc = ctx.guild.voice_client
    if vc:
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

    print("[BOT] Starting Discord Voice Bot...")
    print(f"[BOT] Config loaded from app_settings (no .env needed)")
    print(f"[BOT] Whisper model: {WHISPER_MODEL}")
    print(f"[BOT] TTS voice: {TTS_VOICE}")
    print(f"[BOT] API: {API_URL}")
    bot.run(DISCORD_TOKEN)
