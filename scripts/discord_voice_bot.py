"""
Discord Voice Bot — Full voice conversation with the system.

Sits in a Discord voice channel, records when you speak,
transcribes via Whisper on the 5090, routes through Ollama,
and responds via Sherpa TTS in the voice channel.

Flow: Voice → Whisper STT → Ollama → Sherpa TTS → Voice

Usage:
    python scripts/discord_voice_bot.py          # Run the bot
    pythonw scripts/discord_voice_bot.py         # Run windowless

Requires:
    pip install py-cord[voice] PyNaCl openai-whisper httpx
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
DISCORD_TOKEN = os.getenv("DISCORD_VOICE_BOT_TOKEN", "")
if not DISCORD_TOKEN:
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("DISCORD_VOICE_BOT_TOKEN="):
                DISCORD_TOKEN = _line.split("=", 1)[1].strip()
            elif _line.startswith("DISCORD_BOT_TOKEN=") and not DISCORD_TOKEN:
                DISCORD_TOKEN = _line.split("=", 1)[1].strip()

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
TTS_ENGINE = os.getenv("TTS_ENGINE", "sherpa")
SHERPA_MODEL = os.path.join(
    os.path.expanduser("~"), ".openclaw", "tools", "sherpa-onnx-tts", "models"
)
OLLAMA_MODEL = os.getenv("VOICE_OLLAMA_MODEL", "qwen3.5:latest")


# ---------- Voice Recording Sink (py-cord) ----------

class WhisperSink(discord.sinks.WaveSink):
    """Records voice from Discord, saves per-user WAV files."""
    pass


def _merge_audio_data(sink: WhisperSink) -> str | None:
    """Merge all recorded user audio into a single WAV file for Whisper."""
    out_path = os.path.join(tempfile.gettempdir(), f"voice_{int(time.time())}.wav")
    try:
        all_frames = b""
        for user_id, audio in sink.audio_data.items():
            audio.file.seek(0)
            all_frames += audio.file.read()

        if not all_frames or len(all_frames) < 1000:
            return None

        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(all_frames)

        logger.info("Merged audio: %d bytes -> %s", len(all_frames), out_path)
        return out_path
    except Exception as e:
        logger.error("Audio merge failed: %s", e)
        return None


# ---------- Ollama Client ----------

async def _ask_ollama(question: str, model: str = None) -> str:
    """Send a question to local Ollama and return the response."""
    model = model or OLLAMA_MODEL
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "http://127.0.0.1:11434/api/generate",
                json={
                    "model": model,
                    "prompt": question,
                    "system": "You are Poindexter, a helpful voice assistant for Glad Labs. Keep responses concise (2-3 sentences) since they will be spoken aloud.",
                    "stream": False,
                    "options": {"num_predict": 1000, "temperature": 0.7},
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("response", "").strip() or "[Empty response]"
            return f"[Ollama returned HTTP {resp.status_code}]"
    except Exception as e:
        logger.error("Ollama request failed: %s", e)
        return f"Sorry, I couldn't process that. Ollama error."


# ---------- Bot ----------

class VoiceBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="?", intents=intents)
        self.whisper_model = None
        self.text_channel = None
        self.listening = False
        self._connections = {}

    async def on_ready(self):
        logger.info("Discord voice bot online as %s", self.user)
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
            return ""

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
                "bin", "sherpa-onnx-offline-tts.exe"
            )

            if not os.path.exists(sherpa_bin):
                logger.warning("Sherpa binary not found at %s, trying pyttsx3", sherpa_bin)
                return await self._tts_pyttsx3(text)

            model_path = os.path.join(SHERPA_MODEL, "en_US-lessac-high.onnx")
            tokens_path = os.path.join(SHERPA_MODEL, "tokens.txt")
            data_dir = os.path.join(SHERPA_MODEL, "espeak-ng-data")

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: subprocess.run(
                [sherpa_bin,
                 f"--vits-model={model_path}",
                 f"--vits-tokens={tokens_path}",
                 f"--vits-data-dir={data_dir}",
                 f"--output-filename={out_path}",
                 text[:500]],
                capture_output=True, text=True, timeout=30
            ))

            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info("TTS generated: %s (%d bytes)", out_path, os.path.getsize(out_path))
                return out_path
            return None
        except Exception as e:
            logger.error("Sherpa TTS failed: %s", e)
            return None

    async def _tts_pyttsx3(self, text: str) -> str | None:
        """Fallback TTS via pyttsx3 (system voices)."""
        try:
            import pyttsx3
            out_path = os.path.join(tempfile.gettempdir(), f"tts_{int(time.time())}.wav")
            loop = asyncio.get_event_loop()

            def _generate():
                engine = pyttsx3.init()
                engine.save_to_file(text[:500], out_path)
                engine.runAndWait()

            await loop.run_in_executor(None, _generate)

            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
            return None
        except Exception as e:
            logger.error("pyttsx3 TTS failed: %s", e)
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


# ---------- Recording callback ----------

async def _on_recording_done(sink: WhisperSink, channel: discord.TextChannel, vc):
    """Called when recording stops. Processes audio through the full pipeline."""
    audio_path = _merge_audio_data(sink)
    if not audio_path:
        await channel.send("*No audio captured.*")
        return

    await channel.send("*Transcribing...*")
    text = await bot.transcribe_audio(audio_path)

    if not text or text.startswith("["):
        await channel.send(f"*Could not transcribe audio.* {text}")
        # Clean up
        try:
            os.remove(audio_path)
        except OSError:
            pass
        return

    await channel.send(f"**You said:** {text}")
    logger.info("Voice input: %s", text[:100])

    # Route through Ollama
    await channel.send("*Thinking...*")
    response = await _ask_ollama(text)
    await channel.send(f"**Poindexter:** {response}")
    logger.info("Response: %s", response[:100])

    # TTS response in voice channel
    if vc and vc.is_connected():
        tts_path = await bot.generate_tts(response[:300])
        if tts_path:
            source = discord.FFmpegPCMAudio(tts_path)
            if vc.is_playing():
                vc.stop()
            vc.play(source, after=lambda e: logger.info("TTS playback done"))

    # Clean up temp files
    for path in [audio_path]:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------- Commands ----------

@bot.command(name="join")
async def join(ctx):
    """Join the voice channel you're in."""
    if not ctx.author.voice:
        await ctx.send("You're not in a voice channel.")
        return

    channel = ctx.author.voice.channel
    if ctx.guild.voice_client:
        await ctx.guild.voice_client.move_to(channel)
    else:
        await channel.connect()

    bot.text_channel = ctx.channel
    await ctx.send(f"Joined **{channel.name}**. Commands: `?listen` / `?stop` / `?ask` / `?say` / `?leave`")
    logger.info("Joined voice channel: %s", channel.name)


@bot.command(name="leave")
async def leave(ctx):
    """Leave the voice channel."""
    if ctx.guild.voice_client:
        if bot.listening:
            ctx.guild.voice_client.stop_recording()
            bot.listening = False
        await ctx.guild.voice_client.disconnect()
        await ctx.send("Left voice channel.")
        logger.info("Left voice channel")
    else:
        await ctx.send("Not in a voice channel.")


@bot.command(name="listen")
async def listen(ctx, duration: int = 10):
    """Start listening for voice input. Stops after duration (default 10s)."""
    vc = ctx.guild.voice_client if ctx.guild else ctx.guild.voice_client
    if not vc or not vc.is_connected():
        await ctx.send("Not in a voice channel. Use `?join` first.")
        return

    if bot.listening:
        await ctx.send("Already listening. Use `?stop` to finish.")
        return

    bot.listening = True
    bot.text_channel = ctx.channel
    sink = WhisperSink()

    vc.start_recording(
        sink,
        lambda s, *args: asyncio.ensure_future(_on_recording_done(s, ctx.channel, vc)),
        ctx.channel,
    )

    await ctx.send(f"🎙️ Listening for {duration}s... Speak now! (or `?stop` to finish early)")
    logger.info("Recording started (%ds)", duration)

    # Auto-stop after duration
    await asyncio.sleep(duration)
    if bot.listening:
        vc.stop_recording()
        bot.listening = False


@bot.command(name="stop")
async def stop(ctx):
    """Stop listening and process the recorded audio."""
    vc = ctx.guild.voice_client
    if not vc or not bot.listening:
        await ctx.send("Not currently listening.")
        return

    bot.listening = False
    vc.stop_recording()
    await ctx.send("⏹️ Recording stopped. Processing...")
    logger.info("Recording stopped manually")


@bot.command(name="say")
async def say(ctx, *, text: str):
    """Text-to-speech: type a message, bot speaks it in voice channel."""
    vc = ctx.guild.voice_client
    if not vc or not vc.is_connected():
        await ctx.send("Not in a voice channel. Use `?join` first.")
        return

    tts_path = await bot.generate_tts(text)
    if tts_path:
        source = discord.FFmpegPCMAudio(tts_path)
        if vc.is_playing():
            vc.stop()
        vc.play(source)
        await ctx.send(f"🔊 Speaking: {text[:100]}")
        logger.info("TTS playing: %s", text[:60])
    else:
        await ctx.send("TTS generation failed.")


@bot.command(name="ask")
async def ask(ctx, *, question: str):
    """Ask the system a question. Response in text + voice."""
    bot.text_channel = ctx.channel
    await ctx.send(f"Processing: *{question[:100]}*...")
    logger.info("Question: %s", question[:60])

    response = await _ask_ollama(question)
    await ctx.send(f"**Poindexter:** {response}")

    # TTS if in voice channel
    vc = ctx.guild.voice_client
    if vc and vc.is_connected():
        tts_path = await bot.generate_tts(response[:300])
        if tts_path:
            source = discord.FFmpegPCMAudio(tts_path)
            if vc.is_playing():
                vc.stop()
            vc.play(source)


@bot.command(name="status")
async def status(ctx):
    """Show system status."""
    import json
    import urllib.request

    lines = ["**System Status:**"]

    # Check Ollama
    try:
        resp = json.loads(urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3).read())
        model_names = [m["name"] for m in resp.get("models", [])]
        lines.append(f"Ollama: {len(model_names)} models loaded")
    except Exception:
        lines.append("Ollama: offline")

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

    lines.append(f"Whisper: {'loaded' if bot.whisper_model else 'not loaded'} ({WHISPER_MODEL})")
    lines.append(f"TTS: {TTS_ENGINE}")
    lines.append(f"Listening: {bot.listening}")
    lines.append(f"Voice model: {OLLAMA_MODEL}")

    await ctx.send("\n".join(lines))


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("No DISCORD_BOT_TOKEN found")
        sys.exit(1)

    logger.info("Starting Discord voice bot (py-cord + Whisper + Ollama + Sherpa TTS)...")
    bot.run(DISCORD_TOKEN)
