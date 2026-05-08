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
    pip install py-cord[voice] faster-whisper kokoro-onnx soundfile asyncpg webrtcvad-wheels

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
import soundfile as sf
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro

# OAuth helper for the Poindexter worker API (Glad-Labs/poindexter#248).
# Picks OAuth client_credentials when configured, else legacy Bearer.
from _oauth_helper import (  # noqa: E402
    oauth_client_from_pool,
    read_app_setting,
)
# Slice 3 (poindexter#390): semantic recall helpers — embed-on-save +
# pgvector cosine search over voice_messages. Best-effort by design;
# any failure logs WARNING and the conversation continues.
from _voice_memory import (  # noqa: E402
    format_recalled_context,
    recall_similar_turns,
    save_message_with_embedding,
)

try:
    import webrtcvad
    HAS_VAD = True
except ImportError:
    HAS_VAD = False
    print("[WARN] webrtcvad not installed — falling back to /listen + /stop mode")


async def _load_config_from_db(pool) -> dict:
    """Pull Discord/voice/Ollama config from app_settings.

    discord_bot_token + discord_voice_bot_token are is_secret=true rows
    (encrypted at rest with `enc:v1:` prefix). A naive SELECT returns
    the ciphertext, which discord.py then rejects with the cryptic
    "Newline, carriage return, or null byte detected in headers" error.
    Route those keys through the helper that does pgcrypto decryption.

    OAuth creds are intentionally NOT in this list — the OAuth helper
    handles its own resolution chain (bootstrap.toml + app_settings).
    """
    secret_keys = ("discord_bot_token", "discord_voice_bot_token")
    plain_keys = (
        "discord_voice_channel_id",
        "discord_guild_id",
        "whisper_model",
        "tts_voice",
        "ollama_base_url",
        # Slice 3 (poindexter#390): semantic recall tunables. Operators
        # nudge these at runtime via `poindexter settings set` — bot
        # picks them up on next restart.
        "voice_agent_recall_k",
        "voice_agent_recall_min_similarity",
    )
    cfg: dict = {}
    for k in secret_keys:
        cfg[k] = await read_app_setting(pool, k, "")
    rows = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
        list(plain_keys),
    )
    for r in rows:
        cfg[r["key"]] = r["value"]
    return cfg


def _resolve_db_url() -> str:
    from brain.bootstrap import resolve_database_url
    db_url = os.getenv("DATABASE_URL") or resolve_database_url()
    if not db_url:
        print("ERROR: No database URL found. Run `poindexter setup` first.")
        sys.exit(1)
    return db_url


print("[INIT] Loading config + OAuth client...")
_DB_URL = _resolve_db_url()
API_URL = os.getenv("POINDEXTER_API_URL", "http://localhost:8002")


# Single asyncio.run() for ALL initialization — pool, config read, and
# OAuth client mint must all happen in the SAME event loop. asyncpg
# pools are bound to the loop they were created on; reusing a pool
# across multiple asyncio.run() calls (each spinning up a fresh loop)
# triggers `another operation is in progress` on the second call.
async def _bootstrap_init():
    pool = await asyncpg.create_pool(_DB_URL, min_size=1, max_size=2)
    cfg = await _load_config_from_db(pool)
    oauth = await oauth_client_from_pool(pool, base_url=API_URL)
    return pool, cfg, oauth


_pool, _cfg, _oauth_client = asyncio.run(_bootstrap_init())

DISCORD_TOKEN = _cfg.get("discord_voice_bot_token", "") or _cfg.get("discord_bot_token", "")
VOICE_CHANNEL_ID = int(_cfg.get("discord_voice_channel_id", "0"))
WHISPER_MODEL = _cfg.get("whisper_model", "base.en")
# Kokoro voice slug. The default `af_heart` is the warmest of the
# bundled voices; `am_michael` is the masculine equivalent. Full list:
# https://github.com/thewh1teagle/kokoro-onnx#voices
TTS_VOICE = _cfg.get("tts_voice", "af_heart")

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

# discord.py / py-cord don't auto-load libopus; the voice gateway hop
# completes without it (so /join superficially works) but actual audio
# encode/decode silently no-ops. Force-load early so any failure is
# visible in the boot log instead of as "bot is in the channel but
# never responds".
try:
    if not discord.opus.is_loaded():
        # py-cord ships libopus DLLs alongside the discord package on
        # Windows installs (discord/bin/libopus-0.x64.dll). Try those
        # first; fall back to system-level Linux paths for the docker /
        # Linux-host case.
        _opus_candidates = []
        try:
            _discord_pkg_dir = Path(discord.__file__).resolve().parent
            _opus_candidates.extend([
                str(_discord_pkg_dir / "bin" / "libopus-0.x64.dll"),
                str(_discord_pkg_dir / "bin" / "libopus-0.x86.dll"),
            ])
        except Exception:  # noqa: BLE001
            pass
        _opus_candidates.extend([
            "/usr/lib/x86_64-linux-gnu/libopus.so.0",
            "/usr/lib/libopus.so.0",
            "libopus.so.0",
            "opus",
        ])
        for opus_path in _opus_candidates:
            try:
                discord.opus.load_opus(opus_path)
                if discord.opus.is_loaded():
                    print(f"[INIT] libopus loaded from {opus_path}")
                    break
            except OSError:
                continue
        if not discord.opus.is_loaded():
            print("[INIT] WARN: libopus not found — voice frames will silently drop")
except Exception as exc:  # noqa: BLE001
    print(f"[INIT] WARN: opus init crashed: {exc}")

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


# ---------------------------------------------------------------------------
# Slice 2: persistent conversation memory (poindexter#391).
#
# The in-memory conversation_history above is per-process (lost on restart).
# Slice 2 backs it with a `voice_messages` table so the bot can pick up "where
# we left off" across restarts. Initialised lazily on first save (CREATE TABLE
# IF NOT EXISTS), so no migration coupling — the bot is self-bootstrapping.
#
# Schema is deliberately small: one row per turn, scoped to discord_user_id
# (so multi-user voice channels don't blend transcripts). For now everyone
# sees a shared conversation since the bot has one mic; per-user-only memory
# is a future refinement.
# ---------------------------------------------------------------------------

_VOICE_MEMORY_LIMIT = int(os.environ.get("VOICE_MEMORY_LIMIT", "20"))

# Slice 3 (poindexter#390): semantic recall config. Defaults match the
# migration 20260506_051355 seeds + settings_defaults.py registry.
def _get_recall_int(key: str, default: int) -> int:
    raw = _cfg.get(key, "")
    try:
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _get_recall_float(key: str, default: float) -> float:
    raw = _cfg.get(key, "")
    try:
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


VOICE_RECALL_K = _get_recall_int("voice_agent_recall_k", 3)
VOICE_RECALL_MIN_SIM = _get_recall_float("voice_agent_recall_min_similarity", 0.5)

# The embedder URL is the same Ollama base used for the LLM call. We
# resolve it here (early in the module) because _save_message + recall
# fire long before the OLLAMA_URL constant further down would bind.
OLLAMA_URL_FOR_EMBED = (
    os.getenv("OLLAMA_URL")
    or _cfg.get("ollama_base_url", "")
    or "http://host.docker.internal:11434"
)


async def _memory_conn():
    """Open a fresh single-shot asyncpg connection on the calling loop.

    The module-level ``_pool`` was created at import-time via
    ``asyncio.run(_bootstrap_init())``, binding it to a loop that's
    now closed; using it from the discord.Bot loop triggers
    ``cannot perform operation: another operation is in progress``.
    Memory ops are infrequent (one per voice turn), so per-call connect
    is cheap and avoids the loop-binding gotcha entirely.
    """
    return await asyncpg.connect(_DB_URL)


async def _ensure_voice_messages_table() -> None:
    """Lazy-bootstrap the table on first use.

    Mirrors migration 20260506_051355 (Slice 3, poindexter#390) so
    fresh installs that haven't yet run the migration get the same
    shape — embedding column + discord_channel_id + HNSW index. The
    pgvector extension is created defensively in case this is the
    first vector-typed column in the DB; a no-op on existing installs.
    """
    conn = await _memory_conn()
    try:
        # pgvector is part of the base schema for poindexter installs,
        # but defending against ad-hoc / stripped-down environments is
        # cheap (CREATE EXTENSION IF NOT EXISTS is a no-op when present).
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as exc:  # noqa: BLE001 — operator may lack perms
            print(f"[MEMORY] pgvector extension check failed (non-fatal): {exc}")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_messages (
                id BIGSERIAL PRIMARY KEY,
                discord_user_id TEXT,
                discord_channel_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding vector(768),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Pre-existing tables (Slice 2) need the new columns ALTERed in.
        await conn.execute(
            "ALTER TABLE voice_messages ADD COLUMN IF NOT EXISTS discord_channel_id TEXT"
        )
        await conn.execute(
            "ALTER TABLE voice_messages ADD COLUMN IF NOT EXISTS embedding vector(768)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_messages_recent ON voice_messages (created_at DESC)"
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_messages_embedding_hnsw
                ON voice_messages
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_messages_user_channel
                ON voice_messages (discord_user_id, discord_channel_id, created_at DESC)
            """
        )
    finally:
        await conn.close()


async def _load_recent_messages(limit: int = _VOICE_MEMORY_LIMIT) -> list[dict]:
    """Return the last `limit` voice turns oldest→newest for prompt context."""
    conn = await _memory_conn()
    try:
        rows = await conn.fetch(
            "SELECT role, content FROM voice_messages ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    finally:
        await conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def _save_message(
    role: str,
    content: str,
    discord_user_id: str | None = None,
    discord_channel_id: str | None = None,
) -> int | None:
    """Persist a turn + best-effort embed it for semantic recall.

    Returns the inserted row id (or None if even the INSERT failed).
    Embedding failure does NOT propagate — the row stays in the table
    with a NULL embedding, recall queries skip those rows, and linear
    last-N memory is unaffected.
    """
    try:
        conn = await _memory_conn()
        try:
            return await save_message_with_embedding(
                conn,
                role=role,
                content=content,
                discord_user_id=discord_user_id,
                discord_channel_id=discord_channel_id,
                ollama_url=OLLAMA_URL_FOR_EMBED,
            )
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 — memory is best-effort, never block the conversation
        print(f"[MEMORY] save failed (non-fatal): {exc}")
        return None


async def _recall_for(
    user_text: str,
    *,
    discord_user_id: str | None,
    discord_channel_id: str | None,
    exclude_ids: list[int] | None = None,
) -> list[dict]:
    """Return top-K prior voice_messages turns similar to ``user_text``.

    Always returns a list (empty on any failure) so the caller can
    splat it into the prompt unconditionally. The voice loop is
    latency-sensitive — a 10s embedder timeout caps the worst case.
    """
    if VOICE_RECALL_K <= 0:
        return []
    try:
        conn = await _memory_conn()
        try:
            return await recall_similar_turns(
                conn,
                query_text=user_text,
                ollama_url=OLLAMA_URL_FOR_EMBED,
                discord_user_id=discord_user_id,
                discord_channel_id=discord_channel_id,
                k=VOICE_RECALL_K,
                min_similarity=VOICE_RECALL_MIN_SIM,
                exclude_ids=exclude_ids or [],
            )
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 — recall is best-effort
        print(f"[MEMORY] recall failed (non-fatal): {exc}")
        return []


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


# Kokoro is loaded once at import-time. Same model the voice-agent
# containers use. Output is float32 mono at 24 kHz; we write a WAV that
# Discord's FFmpegPCMAudio will resample to its native 48 kHz on play.
#
# First-run: model + voices are downloaded from the official Kokoro
# Hugging Face mirror into /root/.cache/kokoro (mounted as a docker
# volume so re-builds don't re-pull ~370 MB).
_KOKORO_DIR = Path(os.environ.get("KOKORO_DIR", "/root/.cache/kokoro"))
_KOKORO_MODEL_URL = os.environ.get(
    "KOKORO_MODEL_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
)
_KOKORO_VOICES_URL = os.environ.get(
    "KOKORO_VOICES_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
)


def _ensure_kokoro_assets() -> tuple[str, str]:
    """Download Kokoro model + voices on first run, cache thereafter."""
    import urllib.request
    _KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    model_path = _KOKORO_DIR / "kokoro-v1.0.onnx"
    voices_path = _KOKORO_DIR / "voices-v1.0.bin"
    for url, target in ((_KOKORO_MODEL_URL, model_path), (_KOKORO_VOICES_URL, voices_path)):
        if target.is_file() and target.stat().st_size > 0:
            continue
        print(f"[INIT] Downloading {url} → {target} (one-time, ~325 MB for model)...")
        urllib.request.urlretrieve(url, str(target))
        print(f"[INIT]   wrote {target.stat().st_size} bytes")
    return str(model_path), str(voices_path)


_kokoro_model_path, _kokoro_voices_path = _ensure_kokoro_assets()
print(f"[INIT] Loading Kokoro TTS from {_kokoro_model_path}...")
_kokoro = Kokoro(_kokoro_model_path, _kokoro_voices_path)
print("[INIT] Kokoro ready.")


async def text_to_speech(text: str) -> str:
    out_path = str(DATA_DIR / f"reply_{int(time.time() * 1000)}.wav")
    # kokoro.create() is synchronous and CPU-bound (~1-3s for typical
    # response length); run it in the default executor so the bot's
    # event loop stays responsive to other Discord events.
    samples, sample_rate = await asyncio.get_running_loop().run_in_executor(
        None, lambda: _kokoro.create(text, voice=TTS_VOICE, speed=1.0, lang="en-us"),
    )
    sf.write(out_path, samples, sample_rate)
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


async def process_speech(audio_pcm: bytes, text_channel, guild, user_id=None):
    """Transcribe speech, get Claude response, play TTS.

    ``user_id`` + ``text_channel.id`` get threaded into the
    voice_messages rows so Slice 3 (poindexter#390) recall queries can
    scope to the current conversation.
    """
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

    discord_user_id = str(user_id) if user_id is not None else None
    discord_channel_id = str(getattr(text_channel, "id", "")) or None

    # Slice 3: pull semantic-recall context BEFORE saving the new user
    # turn so we don't recall the turn we're about to write. Result is
    # spliced into the LLM prompt as a separate "recalled context"
    # block (distinct from the linear last-N already in
    # conversation_history).
    recall_hits = await _recall_for(
        text,
        discord_user_id=discord_user_id,
        discord_channel_id=discord_channel_id,
    )
    if recall_hits:
        print(f"[RECALL] {len(recall_hits)} prior turn(s) injected")

    conversation_history.append({"role": "user", "content": text})
    await _save_message("user", text, discord_user_id, discord_channel_id)
    await text_channel.send(f"🎤 **You:** {text}")

    response_text = await get_claude_response(text, recall_hits=recall_hits)
    print(f"[CLAUDE] {response_text[:100]}...")
    conversation_history.append({"role": "assistant", "content": response_text})
    await _save_message("assistant", response_text, discord_user_id, discord_channel_id)
    await text_channel.send(f"🤖 **Poindexter:** {response_text[:1900]}")

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
                process_speech(audio_data, self.text_channel, self.guild, user_id),
                bot.loop,
            )

    def cleanup(self):
        for uid in list(self._buffers.keys()):
            if self._buffers[uid]:
                self._emit(uid)


OLLAMA_URL = _cfg.get("ollama_base_url", "http://host.docker.internal:11434")

# ---------------------------------------------------------------------------
# Skill catalog — load operator skills from skills/poindexter/*/SKILL.md
# and expose them to the LLM as JSON-action dispatch targets. The skill
# registry was originally namespaced as `skills/openclaw/` (after the
# OpenClaw orchestration platform); renamed 2026-05-05 to drop the
# external-platform branding (no openclaw service in the loop — each
# skill's run.sh hits the local worker via OAuth).
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(os.environ.get(
    "POINDEXTER_SKILLS_DIR", "/skills/poindexter",
))


def _load_skills_catalog() -> dict[str, dict]:
    """Scan SKILLS_DIR for SKILL.md frontmatter + return name → metadata.

    Each entry: {description, run_path, raw_md}. The LLM gets `name +
    description` only — full SKILL.md isn't needed for tool selection,
    keeping the prompt token count low matters for voice-loop latency.
    """
    catalog: dict[str, dict] = {}
    if not SKILLS_DIR.is_dir():
        print(f"[SKILLS] {SKILLS_DIR} not present — voice falls back to chat-only mode")
        return catalog
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        md_path = skill_dir / "SKILL.md"
        run_path = skill_dir / "scripts" / "run.sh"
        if not md_path.is_file() or not run_path.is_file():
            continue
        try:
            md = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        name = ""
        description = ""
        in_frontmatter = False
        for line in md.splitlines():
            line = line.strip()
            if line == "---":
                if not in_frontmatter:
                    in_frontmatter = True
                    continue
                break
            if in_frontmatter:
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
        if name and description:
            catalog[name] = {
                "description": description,
                "run_path": str(run_path),
            }
    print(f"[SKILLS] Loaded {len(catalog)} skills: {', '.join(sorted(catalog.keys()))}")
    return catalog


_SKILLS_CATALOG = _load_skills_catalog()


def _build_system_prompt() -> str:
    """Compose the qwen3:8b system prompt with the live skill catalog.

    Updated catalog requires container restart (not a hot-reload concern
    for a single-operator voice bot — restart cycle is seconds).
    """
    lines = [
        "You are Poindexter, an AI content pipeline assistant. You help the",
        "operator manage their content pipeline through voice. Keep replies",
        "concise — they'll be spoken aloud (3 sentences max).",
        "",
        "To take an action, output JSON on its own line in this shape:",
        '  {"skill": "<skill-name>", "args": ["arg1", "arg2"]}',
        "",
        "Available skills (pick the most fitting one based on what the user",
        "asks for; pass positional args matching the skill's run.sh):",
        "",
    ]
    for name in sorted(_SKILLS_CATALOG):
        lines.append(f"- {name}: {_SKILLS_CATALOG[name]['description']}")
    lines += [
        "",
        "If no skill fits, just chat — no JSON needed. Never make up a skill",
        "name not in the list above.",
    ]
    return "\n".join(lines)


SYSTEM_PROMPT = _build_system_prompt()


async def _execute_action(action: dict) -> str:
    """Dispatch to a SKILLS_DIR skill via its run.sh and return spoken result.

    Backwards-compat: if the LLM emits the legacy ``{"action": "..."}``
    shape from earlier prompts (in old conversation history), map it
    onto the equivalent skill name when one exists.
    """
    skill_name = action.get("skill") or action.get("action", "")
    args: list[str] = action.get("args") or []

    # Legacy keys (action=approve / reject / publish) → skill names.
    legacy_map = {
        "approve": ("approve-post", [str(action.get("task_id", ""))]),
        "reject": ("reject-post", [str(action.get("task_id", "")), str(action.get("reason", ""))]),
        "publish": ("create-post", [str(action.get("topic", ""))]),
        "tasks": ("list-tasks", []),
        "stats": ("list-tasks", []),
        "health": ("list-tasks", []),
    }
    if skill_name in legacy_map and skill_name not in _SKILLS_CATALOG:
        skill_name, args = legacy_map[skill_name]

    skill = _SKILLS_CATALOG.get(skill_name)
    if not skill:
        return f"I don't know the skill '{skill_name}'."

    cmd = ["bash", skill["run_path"], *[a for a in args if a != ""]]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "FASTAPI_URL": API_URL},
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = (stdout_b or b"").decode("utf-8", errors="replace").strip()
        err = (stderr_b or b"").decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        proc.kill()
        return f"Skill {skill_name} timed out."
    except Exception as exc:  # noqa: BLE001 — surface any subprocess error
        return f"Skill {skill_name} failed to launch: {exc}"

    if proc.returncode != 0:
        # First line of stderr is usually the most useful for spoken context.
        first_err = (err.splitlines() or ["unknown error"])[0]
        return f"Skill {skill_name} failed: {first_err}"

    # Strip JSON output to a spoken-friendly summary if obvious. The skills
    # mostly print "Action succeeded." then a JSON dump; speak the first
    # human-readable line.
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith("{") and not line.startswith("["):
            return line[:200]
    return f"Skill {skill_name} ran."


async def get_claude_response(
    user_text: str,
    *,
    recall_hits: list[dict] | None = None,
) -> str:
    """Send text to local Ollama with tool awareness, return spoken response.

    ``recall_hits`` is the Slice 3 (poindexter#390) semantic-recall
    payload — top-K prior voice_messages turns similar to ``user_text``.
    They get rendered as a separate "recalled context" block prepended
    to the system prompt so the LLM can distinguish them from the live
    last-N linear history.
    """
    import json as _json

    history_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in conversation_history[-6:]
    )
    prompt = f"{history_text}\nuser: {user_text}\nassistant:"

    system_prompt = SYSTEM_PROMPT
    if recall_hits:
        recall_block = format_recalled_context(recall_hits)
        if recall_block:
            system_prompt = f"{recall_block}\n\n---\n\n{SYSTEM_PROMPT}"

    try:
        import httpx
        ollama_url = os.getenv("OLLAMA_URL") or OLLAMA_URL
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": "qwen3:8b",
                    "prompt": prompt,
                    "system": system_prompt,
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
                            # `skill` is the new shape (OpenClaw catalog);
                            # `action` is the legacy shape from older
                            # SYSTEM_PROMPT versions. _execute_action handles
                            # both transparently.
                            if "skill" in action or "action" in action:
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
    # Slice 2: bring up persistent memory + warm in-memory history with the
    # last N turns from the previous session. Best-effort — table-create or
    # load failures don't block the bot from coming up.
    try:
        await _ensure_voice_messages_table()
        prior = await _load_recent_messages()
        if prior:
            conversation_history.extend(prior)
            print(f"[MEMORY] Loaded {len(prior)} prior turns from voice_messages")
    except Exception as exc:  # noqa: BLE001
        print(f"[MEMORY] init failed (non-fatal): {exc}")
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

    # Defer immediately — the slash-command interaction token expires
    # after 3s. Voice handshake + Whisper/Kokoro warmup can blow past
    # that, and the original /join would crash with "Unknown interaction"
    # while trying to send the success response.
    await ctx.defer()

    vc = ctx.guild.voice_client
    if vc and vc.is_connected():
        await vc.move_to(ctx.author.voice.channel)
    else:
        # py-cord's connect() returns when the gateway voice-state hop
        # completes, but the underlying voice-WS handshake (which is
        # what is_connected() actually checks) is still in flight.
        # Pass a generous timeout + reconnect=True so transient handshake
        # failures retry instead of crashing.
        vc = await ctx.author.voice.channel.connect(timeout=30.0, reconnect=True)

    # Poll is_connected() for up to 15s before start_recording. py-cord's
    # poll_voice_ws task can raise `_MissingSentinel.poll_event` if the
    # ws attribute is still MISSING when the task fires — manifests as
    # "Not connected to voice channel" from start_recording.
    for _ in range(30):
        if vc.is_connected() and getattr(vc, "ws", None) and not isinstance(vc.ws, type(discord.utils.MISSING)):
            break
        await asyncio.sleep(0.5)

    if not vc.is_connected():
        try:
            await vc.disconnect(force=True)
        except Exception:  # noqa: BLE001
            pass
        await ctx.followup.send(
            "Voice WS handshake didn't complete in 15s — Discord region issue?",
            ephemeral=True,
        )
        return

    if HAS_VAD:
        sink = VADSink(ctx.channel, ctx.guild)
        try:
            vc.start_recording(sink, lambda *a: None, ctx.channel)
        except Exception as exc:  # noqa: BLE001
            await ctx.followup.send(f"Couldn't start recording: {exc}", ephemeral=True)
            return
        await ctx.followup.send(
            f"Joined **{ctx.author.voice.channel.name}** — listening with VAD. "
            f"Just talk naturally. I'll respond when you pause."
        )
    else:
        await ctx.followup.send(
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
                await process_speech(audio_bytes, channel, ctx.guild, user_id)

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
    # Slice 2: also wipe the persistent table so the bot doesn't re-load
    # the cleared turns on next restart. Best-effort.
    try:
        async with _pool.acquire() as conn:
            await conn.execute("TRUNCATE voice_messages")
    except Exception as exc:  # noqa: BLE001
        print(f"[MEMORY] truncate failed (non-fatal): {exc}")
    await ctx.respond("Conversation history cleared (in-memory + persistent).")


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


@bot.slash_command(name="approve", description="Stage a post (does not publish)")
async def approve(ctx, task_id: str):
    """Stage only — does NOT publish.

    Per ``feedback_approve_does_not_mean_publish``: picking the best N from
    ``awaiting_approval`` is staging, not shipping. Follow up with
    ``/publish <task_id>`` to ship, or use ``/approve-publish`` for the
    explicit one-step path.
    """
    json_data = {"approved": True}
    data = await _api_call("POST", f"/api/tasks/{task_id}/approve", json_data)
    status = data.get("status", data.get("error", "?"))
    if status == "approved":
        await ctx.respond(f"**#{task_id}:** staged — `/publish {task_id}` to ship")
    else:
        await ctx.respond(f"**#{task_id}:** {status}")


@bot.slash_command(name="approve-publish", description="Stage and ship a post in one step")
async def approve_publish(ctx, task_id: str, schedule: str | None = None):
    """One-step approve + publish. Explicit opt-in to publishing — only use
    when you've already reviewed the post and want to skip the staging gate.
    """
    json_data: dict[str, object] = {"approved": True, "auto_publish": True}
    if schedule:
        json_data["publish_at"] = schedule
    data = await _api_call("POST", f"/api/tasks/{task_id}/approve", json_data)
    status = data.get("status", data.get("error", "?"))
    await ctx.respond(f"**#{task_id}:** {status}")


@bot.slash_command(name="publish", description="Publish an approved post")
async def publish(ctx, task_id: str):
    data = await _api_call("POST", f"/api/tasks/{task_id}/publish")
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
