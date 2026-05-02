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

Worker-API authentication (Glad-Labs/poindexter#248):
    Prefers OAuth 2.1 client credentials when ``scripts_oauth_client_id``
    + ``scripts_oauth_client_secret`` are present in app_settings or
    bootstrap.toml. Falls back to legacy static Bearer (``api_token``)
    otherwise. Run ``poindexter auth migrate-scripts`` to provision.
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
sys.path.insert(0, str(_project_root / "scripts"))

import asyncpg
import discord
import edge_tts
from faster_whisper import WhisperModel

# OAuth helper for the Poindexter worker API (Glad-Labs/poindexter#248).
# Picks OAuth client_credentials when configured, else legacy Bearer.
from _oauth_helper import oauth_client_from_pool  # noqa: E402

try:
    import webrtcvad
    HAS_VAD = True
except ImportError:
    HAS_VAD = False
    print("[WARN] webrtcvad not installed — falling back to /listen + /stop mode")


def _load_config_from_db(pool) -> dict:
    """Pull Discord/voice/Ollama config from app_settings.

    OAuth creds are intentionally NOT in this list — the OAuth helper
    handles its own resolution chain (bootstrap.toml + app_settings).
    """

    async def _fetch():
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE key IN "
            "('discord_bot_token', 'discord_voice_bot_token', "
            "'discord_voice_channel_id', 'discord_guild_id', "
            "'whisper_model', 'tts_voice', 'ollama_base_url')"
        )
        return {r["key"]: r["value"] for r in rows}

    return asyncio.run(_fetch())


def _resolve_db_url() -> str:
    from brain.bootstrap import resolve_database_url
    db_url = os.getenv("DATABASE_URL") or resolve_database_url()
    if not db_url:
        print("ERROR: No database URL found. Run `poindexter setup` first.")
        sys.exit(1)
    return db_url


print("[INIT] Loading config + OAuth client...")
_DB_URL = _resolve_db_url()
_pool: asyncpg.Pool = asyncio.run(asyncpg.create_pool(_DB_URL, min_size=1, max_size=2))
_cfg = _load_config_from_db(_pool)

API_URL = os.getenv("POINDEXTER_API_URL", "http://localhost:8002")

# Single shared OAuth client for the bot's lifetime — the cached JWT is
# reused across slash-command invocations + LLM-driven action handlers.
_oauth_client = asyncio.run(
    oauth_client_from_pool(_pool, base_url=API_URL),
)

DISCORD_TOKEN = _cfg.get("discord_voice_bot_token", "") or _cfg.get("discord_bot_token", "")
VOICE_CHANNEL_ID = int(_cfg.get("discord_voice_channel_id", "0"))
WHISPER_MODEL = _cfg.get("whisper_model", "base.en")
TTS_VOICE = _cfg.get("tts_voice", "en-US-GuyNeural")

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

GUILD_ID = int(_cfg.get("discord_guild_id", "0") or "0")
_debug_guilds = [GUILD_ID] if GUILD_ID else None
bot = discord.Bot(intents=intents, loop=loop, debug_guilds=_debug_guilds)

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


OLLAMA_URL = _cfg.get("ollama_base_url", "http://host.docker.internal:11434")

SYSTEM_PROMPT = """You are Poindexter, an AI content pipeline assistant. You help the operator manage their content pipeline through voice conversation. Keep responses concise — they'll be spoken aloud.

You can take pipeline actions by including JSON on its own line:
  {"action": "health"}
  {"action": "tasks"}
  {"action": "approve", "task_id": "123"}
  {"action": "reject", "task_id": "123", "reason": "off-topic"}
  {"action": "stats"}
  {"action": "publish", "topic": "Why Docker matters"}

Include action JSON when the user wants something done. Otherwise just chat naturally."""


async def _execute_action(action: dict) -> str:
    """Execute a pipeline action and return result as spoken text.

    All worker-API calls go through the shared OAuth client so the
    cached JWT is reused across actions. The DB stats path uses the
    shared asyncpg pool directly because that's a non-API query.
    """
    act = action.get("action", "")

    if act == "health":
        resp = await _oauth_client.get("/api/health")
        d = resp.json()
        te = d.get("components", {}).get("task_executor", {})
        return f"System is {d.get('status', '?')}. {te.get('pending_task_count', 0)} pending, {te.get('in_progress_count', 0)} in progress."

    elif act == "tasks":
        resp = await _oauth_client.get("/api/tasks?status=awaiting_approval")
        body = resp.json()
        tasks = body if isinstance(body, list) else body.get("tasks", [])
        if not tasks:
            return "No posts awaiting approval."
        lines = [f"{str(t.get('id','?'))[:6]}: {t.get('title', t.get('topic','?'))[:40]}, score {t.get('quality_score','?')}" for t in tasks[:5]]
        return f"{len(tasks)} posts waiting. " + ". ".join(lines)

    elif act == "approve":
        tid = action.get("task_id", "")
        resp = await _oauth_client.post(
            f"/api/tasks/{tid}/approve",
            json={"approved": True, "auto_publish": True},
        )
        return f"Task {tid} {resp.json().get('status', 'done')}."

    elif act == "reject":
        tid = action.get("task_id", "")
        reason = action.get("reason", "Rejected via voice")
        resp = await _oauth_client.post(
            f"/api/tasks/{tid}/reject",
            json={"feedback": reason, "reason": "operator"},
        )
        return f"Task {tid} rejected. {reason}."

    elif act == "stats":
        # Direct DB read — not a worker API call, no auth needed.
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) as c FROM pipeline_tasks_view "
                "WHERE created_at > NOW() - INTERVAL '24 hours' GROUP BY status ORDER BY c DESC"
            )
            total = await conn.fetchval("SELECT COUNT(*) FROM posts WHERE status = 'published'")
        parts = [f"{r['c']} {r['status']}" for r in rows]
        return f"Last 24 hours: {', '.join(parts)}. Total published: {total}."

    elif act == "publish":
        topic = action.get("topic", "")
        resp = await _oauth_client.post(
            "/api/tasks",
            json={"topic": topic, "category": "technology"},
        )
        return f"Queued new topic: {topic}."

    return "I didn't understand that action."


async def get_claude_response(user_text: str) -> str:
    """Send text to local Ollama with tool awareness, return spoken response."""
    import json as _json

    history_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in conversation_history[-6:]
    )
    prompt = f"{history_text}\nuser: {user_text}\nassistant:"

    try:
        import httpx
        ollama_url = os.getenv("OLLAMA_URL") or OLLAMA_URL
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": "qwen3:8b",
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 300},
                },
            )
            if resp.status_code == 200:
                response_text = resp.json().get("response", "").strip()

                for line in response_text.split("\n"):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            action = _json.loads(line)
                            if "action" in action:
                                result = await _execute_action(action)
                                clean = response_text.replace(line, "").strip()
                                return f"{clean} {result}".strip() if clean else result
                        except _json.JSONDecodeError:
                            pass

                return response_text or "I'm not sure how to respond to that."
    except Exception as e:
        print(f"[LLM] Ollama error: {e}")

    lower = user_text.lower()
    if any(w in lower for w in ["health", "status", "system"]):
        return await _execute_action({"action": "health"})
    elif any(w in lower for w in ["tasks", "pending", "approval", "queue"]):
        return await _execute_action({"action": "tasks"})
    elif any(w in lower for w in ["stats", "statistics", "numbers"]):
        return await _execute_action({"action": "stats"})

    return f"I heard: {user_text}. Ollama isn't responding — try a slash command."


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


# =========================================================================
# Pipeline Management Commands — replaces OpenClaw for common operations
# =========================================================================

async def _api_call(method: str, path: str, json_data: dict | None = None) -> dict:
    """Make an authenticated API call to the Poindexter worker.

    Routes through the shared OAuth client so the cached JWT is reused
    across slash commands. The 401-retry dance is handled inside the
    helper.
    """
    if method == "GET":
        resp = await _oauth_client.get(path)
    elif method == "POST":
        resp = await _oauth_client.post(path, json=json_data or {})
    elif method == "PUT":
        resp = await _oauth_client.put(path, json=json_data or {})
    else:
        return {"error": f"Unknown method: {method}"}
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text[:200]}


@bot.slash_command(name="health", description="Check system health")
async def health(ctx):
    data = await _api_call("GET", "/api/health")
    status = data.get("status", "unknown")
    te = data.get("components", {}).get("task_executor", {})
    pending = te.get("pending_task_count", "?")
    in_prog = te.get("in_progress_count", "?")
    await ctx.respond(
        f"**System:** {status}\n"
        f"**Pending:** {pending} | **In-progress:** {in_prog}"
    )


@bot.slash_command(name="tasks", description="List posts awaiting approval")
async def tasks(ctx):
    data = await _api_call("GET", "/api/tasks?status=awaiting_approval")
    task_list = data if isinstance(data, list) else data.get("tasks", [])
    if not task_list:
        await ctx.respond("No posts awaiting approval.")
        return
    lines = []
    for t in task_list[:10]:
        tid = str(t.get("id", "?"))[:8]
        title = t.get("title", t.get("topic", "untitled"))[:50]
        score = t.get("quality_score", "?")
        lines.append(f"`{tid}` | Q:{score} | {title}")
    await ctx.respond(f"**Awaiting Approval ({len(task_list)}):**\n" + "\n".join(lines))


@bot.slash_command(name="approve", description="Approve a post for publishing")
async def approve(ctx, task_id: str, schedule: str = None):
    json_data = {"approved": True, "auto_publish": True}
    if schedule:
        json_data["publish_at"] = schedule
    data = await _api_call("POST", f"/api/tasks/{task_id}/approve", json_data)
    status = data.get("status", data.get("error", "?"))
    await ctx.respond(f"**#{task_id}:** {status}")


@bot.slash_command(name="reject", description="Reject a post")
async def reject(ctx, task_id: str, reason: str = "Rejected via Discord"):
    data = await _api_call("POST", f"/api/tasks/{task_id}/reject", {
        "feedback": reason, "reason": "operator"
    })
    status = data.get("status", data.get("error", "?"))
    await ctx.respond(f"**#{task_id}:** {status} — {reason}")


@bot.slash_command(name="stats", description="Pipeline stats for the last 24h")
async def stats(ctx):
    """Stats are read directly from the shared asyncpg pool — these are
    DB queries, not worker-API calls, so no auth needed."""
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) as c FROM pipeline_tasks_view "
                "WHERE created_at > NOW() - INTERVAL '24 hours' GROUP BY status ORDER BY c DESC"
            )
            total_published = await conn.fetchval(
                "SELECT COUNT(*) FROM posts WHERE status = 'published'"
            )
        lines = [f"**{r['status']}:** {r['c']}" for r in rows]
        await ctx.respond(
            f"**Pipeline (24h):**\n" + "\n".join(lines) +
            f"\n\n**Total published:** {total_published}"
        )
    except Exception as e:
        await ctx.respond(f"Stats error: {e}")


@bot.slash_command(name="publish_now", description="Create and auto-queue a post on a topic")
async def publish_now(ctx, topic: str, category: str = "technology"):
    data = await _api_call("POST", "/api/tasks", {
        "topic": topic, "category": category
    })
    task_id = data.get("id", data.get("task_id", "?"))
    await ctx.respond(f"Queued: **{topic}**\nTask: `{task_id}`")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: discord_bot_token not found in app_settings.")
        print("  Set it via the CLI:")
        print("    poindexter settings set discord_bot_token <token>")
        print("  Or via the API (after `poindexter auth migrate-scripts`):")
        print("    poindexter settings set discord_bot_token <token>")
        raise SystemExit(1)

    print("[BOT] Starting Discord Voice Bot (VAD enabled)" if HAS_VAD else "[BOT] Starting (no VAD)")
    print(f"[BOT] Config from app_settings | Whisper: {WHISPER_MODEL} | TTS: {TTS_VOICE}")
    print(f"[BOT] VAD: silence={SILENCE_THRESHOLD_S}s, min_speech={MIN_SPEECH_S}s, max={MAX_SPEECH_S}s")
    bot.run(DISCORD_TOKEN)
