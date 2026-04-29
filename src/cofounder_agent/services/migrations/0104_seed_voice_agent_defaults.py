"""Migration 0104: Seed defaults for the voice_agent service.

Pairs with ``services/voice_agent.py`` — every knob the agent reads is
an ``app_settings`` row so operators can tune at runtime without
redeploys (per the project's DB-first config standard).

Defaults (Matt's call, 2026-04-29):

- LLM: ``glm-4.7-5090:latest`` (the daily-driver writer model)
- Ollama URL: ``host.docker.internal:11434`` (worker-container default)
- TTS voice: ``bf_emma`` — the highest-graded British female in Kokoro's
  catalog (B- overall vs C / D for bf_isabella / bf_alice / bf_lily)
- TTS speed: ``1.0``
- Whisper model: ``base.en`` (decent accuracy + low latency tradeoff
  on the 5090; drop to ``tiny.en`` if latency hurts)
- System prompt: short Emma persona — TTS-aware (no markdown), terse,
  honest about not knowing things, ~80-word answers by default

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so a pre-set custom value
survives a re-run.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "voice_agent_llm_model",
        "glm-4.7-5090:latest",
        "Ollama model tag the voice agent uses for its LLM step. "
        "Same daily-driver as pipeline_writer_model by default.",
    ),
    (
        "voice_agent_ollama_url",
        "http://host.docker.internal:11434",
        "Ollama base URL. Default targets the host's Ollama from inside "
        "Docker; running voice_agent.py directly on the host can stay on "
        "this URL or switch to http://localhost:11434.",
    ),
    (
        "voice_agent_tts_voice",
        "bf_emma",
        "Kokoro voice id. bf_emma is the top-graded British female in "
        "the Kokoro-82M catalog (B-). Other UK female options: "
        "bf_isabella (C), bf_alice (D), bf_lily (D).",
    ),
    (
        "voice_agent_tts_speed",
        "1.0",
        "Kokoro playback speed multiplier. 1.0 = natural; 0.95 = slightly "
        "slower (helpful for technical content); 1.1 = brisker.",
    ),
    (
        "voice_agent_whisper_model",
        "base",
        "faster-whisper model size. Valid Pipecat 1.1 enum values: "
        "tiny, base, small, medium, large-v3, "
        "deepdml/faster-whisper-large-v3-turbo-ct2 (LARGE_V3_TURBO), "
        "Systran/faster-distil-whisper-large-v2 (DISTIL_LARGE_V2), "
        "Systran/faster-distil-whisper-medium.en (DISTIL_MEDIUM_EN). "
        "base balances accuracy + latency. Drop to tiny if latency hurts; "
        "bump to DISTIL_MEDIUM_EN for English-optimized accuracy on a 5090.",
    ),
    (
        "voice_agent_system_prompt",
        (
            "You are Emma, a concise voice assistant for Matt at Glad Labs. "
            "Speak naturally — your output goes through text-to-speech, so "
            "avoid markdown, bullet lists, and code blocks. Use short "
            "sentences. If Matt asks a factual question you don't know the "
            "answer to, say so plainly rather than guessing. Default to "
            "responses under 30 seconds of speech (~80 words) unless he "
            "explicitly asks for a longer one."
        ),
        "Voice agent personality / system prompt. Edit at runtime to "
        "swap personas (e.g. tactical-sim agent, customer-service tone).",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "0104: seeded %d/%d voice_agent settings "
            "(remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info("0104: removed %d voice_agent seeds", len(_SEEDS))
