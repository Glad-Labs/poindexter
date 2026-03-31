"""
Discord Voice Bot — Push-to-talk communication with the system.

Sits in a Discord voice channel, listens when you speak (PTT),
transcribes via Whisper on the 5090, processes the request, and
responds via TTS in the voice channel + text in a paired channel.

Usage:
    python scripts/discord_voice_bot.py          # Run the bot
    pythonw scripts/discord_voice_bot.py         # Run windowless

Requires:
    pip install "discord.py[voice]" PyNaCl openai-whisper
    ffmpeg in PATH
"""

import asyncio
import io
import logging
import os
import struct
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone

# pythonw.exe compatibility
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import discord
from discord.ext import commands

LOG_FILE = os.path.join(os.path.expanduser("~"), ".gladlabs", "discord_voice.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("discord_voice")
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)
if sys.stdout is not None and getattr(sys.stdout, "name", "") != os.devnull:
    logger.addHandler(logging.StreamHandler(sys.stdout))

# Load config
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
if not DISCORD_TOKEN:
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("DISCORD_BOT_TOKEN="):
                DISCORD_TOKEN = _line.split("=", 1)[1].strip()

# Whisper model — "base" is fast on GPU, "medium" for better accuracy
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# TTS config
TTS_ENGINE = os.getenv("TTS_ENGINE", "sherpa")  # "sherpa" (local) or "elevenlabs" (cloud)
SHERPA_MODEL = os.path.join(
    os.path.expanduser("~"), ".openclaw", "tools", "sherpa-onnx-tts", "models"
)

# Voice channel to auto-join (set via command or env)
AUTO_JOIN_CHANNEL = os.getenv("DISCORD_VOICE_CHANNEL_ID", "")

# API for processing requests
API_URL = "https://cofounder-production.up.railway.app"
API_TOKEN = os.getenv("GLADLABS_KEY", "")
if not API_TOKEN:
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("GLADLABS_KEY="):
                API_TOKEN = _line.split("=", 1)[1].strip()


class AudioSink(discord.sinks.WaveSink):
    """Custom audio sink that captures voice data per user."""
    pass


class VoiceBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.whisper_model = None
        self.recording = False
        self.text_channel = None

    async def on_ready(self):
        logger.info("Discord voice bot online as %s", self.user)
        # Load Whisper model on startup
        await self._load_whisper()

    async def _load_whisper(self):
        """Load Whisper model (runs on GPU)."""
        logger.info("Loading Whisper model '%s'...", WHISPER_MODEL)
        try:
            import whisper
            self.whisper_model = whisper.load_model(WHISPER_MODEL)
            logger.info("Whisper model loaded (device: %s)", self.whisper_model.device)
        except Exception as e:
            logger.error("Failed to load Whisper: %s", e)

    async def transcribe_audio(self, audio_file_path: str) -> str:
        """Transcribe audio file using Whisper."""
        if not self.whisper_model:
            return "[Whisper not loaded]"
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: self.whisper_model.transcribe(audio_file_path)
            )
            text = result.get("text", "").strip()
            logger.info("Transcribed: %s", text[:100])
            return text
        except Exception as e:
            logger.error("Transcription failed: %s", e)
            return f"[Transcription error: {e}]"

    async def generate_tts(self, text: str) -> str | None:
        """Generate TTS audio file from text. Returns file path."""
        if TTS_ENGINE == "elevenlabs":
            return await self._tts_elevenlabs(text)
        return await self._tts_sherpa(text)

    async def _tts_sherpa(self, text: str) -> str | None:
        """Local TTS via Sherpa ONNX."""
        try:
            import subprocess
            out_path = os.path.join(tempfile.gettempdir(), f"tts_{int(time.time())}.wav")

            sherpa_bin = os.path.join(
                os.path.expanduser("~"), ".openclaw", "tools",
                "sherpa-onnx-tts", "runtime", "sherpa-onnx-v1.12.23-win-x64-shared",
                "sherpa-onnx-offline-tts.exe"
            )

            if not os.path.exists(sherpa_bin):
                logger.warning("Sherpa binary not found at %s", sherpa_bin)
                return None

            model_path = os.path.join(SHERPA_MODEL, "en_US-lessac-high.onnx")
            tokens_path = os.path.join(SHERPA_MODEL, "tokens.txt")
            data_dir = os.path.join(SHERPA_MODEL, "espeak-ng-data")

            result = subprocess.run(
                [sherpa_bin,
                 f"--vits-model={model_path}",
                 f"--vits-tokens={tokens_path}",
                 f"--vits-data-dir={data_dir}",
                 f"--output-filename={out_path}",
                 text[:500]],  # Limit text length
                capture_output=True, text=True, timeout=30
            )

            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("TTS generated: %s (%d bytes)", out_path, os.path.getsize(out_path))
                return out_path
            return None
        except Exception as e:
            logger.error("Sherpa TTS failed: %s", e)
            return None

    async def _tts_elevenlabs(self, text: str) -> str | None:
        """Cloud TTS via ElevenLabs."""
        try:
            import httpx
            api_key = os.getenv("ELEVENLABS_API_KEY", "")
            if not api_key:
                return None

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
                    headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                    json={"text": text[:500], "model_id": "eleven_monolingual_v1"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    out_path = os.path.join(tempfile.gettempdir(), f"tts_{int(time.time())}.mp3")
                    with open(out_path, "wb") as f:
                        f.write(resp.content)
                    return out_path
            return None
        except Exception as e:
            logger.error("ElevenLabs TTS failed: %s", e)
            return None


bot = VoiceBot()


@bot.command(name="join")
async def join(ctx):
    """Join the voice channel you're in."""
    if not ctx.author.voice:
        await ctx.send("You're not in a voice channel.")
        return

    channel = ctx.author.voice.channel
    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()

    bot.text_channel = ctx.channel
    await ctx.send(f"Joined **{channel.name}**. Use `!listen` to start, `!stop` to end.")
    logger.info("Joined voice channel: %s", channel.name)


@bot.command(name="leave")
async def leave(ctx):
    """Leave the voice channel."""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Left voice channel.")
        logger.info("Left voice channel")
    else:
        await ctx.send("Not in a voice channel.")


@bot.command(name="listen")
async def listen(ctx):
    """Start listening for voice input."""
    vc = ctx.voice_client
    if not vc or not vc.is_connected():
        await ctx.send("Not in a voice channel. Use `!join` first.")
        return

    if bot.recording:
        await ctx.send("Already listening.")
        return

    bot.recording = True
    bot.text_channel = ctx.channel

    sink = AudioSink()
    vc.start_recording(sink, _recording_finished, ctx)
    await ctx.send("Listening... Speak when ready. Use `!stop` when done.")
    logger.info("Started listening")


async def _recording_finished(sink: AudioSink, ctx):
    """Called when recording stops. Transcribe and respond."""
    logger.info("Recording finished, processing %d audio streams", len(sink.audio_data))

    for user_id, audio_data in sink.audio_data.items():
        # Save audio to temp WAV file
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name

        try:
            audio_data.file.seek(0)
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                wf.writeframes(audio_data.file.read())

            # Transcribe with Whisper
            text = await bot.transcribe_audio(tmp_path)

            if not text or text.strip() in ["", "[Whisper not loaded]"]:
                continue

            user = ctx.guild.get_member(user_id)
            user_name = user.display_name if user else f"User {user_id}"

            # Send transcription to text channel
            if bot.text_channel:
                await bot.text_channel.send(f"**{user_name}:** {text}")

            # Generate response (for now, echo + acknowledge)
            response = f"Heard: {text}"

            # Try TTS response
            tts_path = await bot.generate_tts(response)
            if tts_path and ctx.voice_client and ctx.voice_client.is_connected():
                source = discord.FFmpegPCMAudio(tts_path)
                ctx.voice_client.play(source)
                logger.info("Playing TTS response")

            if bot.text_channel:
                await bot.text_channel.send(f"**System:** {response}")

        except Exception as e:
            logger.error("Error processing audio from user %s: %s", user_id, e)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@bot.command(name="stop")
async def stop(ctx):
    """Stop listening."""
    vc = ctx.voice_client
    if not vc or not bot.recording:
        await ctx.send("Not currently listening.")
        return

    bot.recording = False
    vc.stop_recording()
    await ctx.send("Stopped listening.")
    logger.info("Stopped listening")


@bot.command(name="status")
async def status(ctx):
    """Show system status."""
    import urllib.request
    import json

    lines = ["**System Status:**"]

    # Check worker
    try:
        resp = json.loads(urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=3).read())
        lines.append(f"Worker: {resp.get('status', '?')}")
    except Exception:
        lines.append("Worker: offline")

    # Check GPU
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            util, temp, power = [v.strip() for v in result.stdout.strip().split(",")]
            lines.append(f"GPU: {util}% util, {temp}°C, {power}W")
    except Exception:
        pass

    # Whisper status
    lines.append(f"Whisper: {'loaded' if bot.whisper_model else 'not loaded'} ({WHISPER_MODEL})")
    lines.append(f"TTS: {TTS_ENGINE}")
    lines.append(f"Recording: {bot.recording}")

    await ctx.send("\n".join(lines))


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("No DISCORD_BOT_TOKEN found")
        sys.exit(1)

    logger.info("Starting Discord voice bot...")
    bot.run(DISCORD_TOKEN, log_handler=None)
