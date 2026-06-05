"""Migration 20260604_143000: seed warm Speaches STT/TTS sidecar keys.

ISSUE: Glad-Labs/poindexter#1088 (move STT/TTS to a warm sidecar to
kill the ~12s Whisper cold-start; part of the #1006 always-on workstream).

Seeds six keys that let ``build_voice_pipeline_task`` build STT/TTS as
thin HTTP clients of the warm ``speaches`` container instead of loading
Whisper + Kokoro in-process. Both ``*_mode`` keys default to ``inprocess``
so this migration is a **no-op for behavior** — the voice path is
unchanged until an operator flips a mode to ``sidecar`` and restarts the
voice containers (backcompat-now-required: merge changes nothing live).

The STT model value differs by mode: in-process Pipecat wants the Whisper
enum (``medium``, via the reused ``voice_agent_whisper_model``); Speaches
wants an HF id (``Systran/faster-whisper-medium``), so sidecar STT gets its
own ``voice_agent_stt_model``. The TTS voice id (``bf_emma`` /
``bf_isabella``) is identical in both modes, so only the TTS *model* id is
new (``voice_agent_tts_model``).

``ON CONFLICT DO NOTHING`` so a live value is never clobbered by a re-apply.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_KEYS = [
    (
        "voice_agent_stt_mode",
        "inprocess",
        "STT backend for the voice pipeline (#1088): 'sidecar' = thin "
        "client of the warm Speaches container; 'inprocess' = load "
        "faster-whisper in-process (legacy). No silent default — an "
        "invalid value fails loud at pipeline build.",
    ),
    (
        "voice_agent_stt_base_url",
        "http://speaches:8000/v1",
        "Speaches STT endpoint (OpenAI-compatible) used when "
        "voice_agent_stt_mode=sidecar. Compose service name on the "
        "shared poindexter network.",
    ),
    (
        "voice_agent_stt_model",
        "Systran/faster-whisper-medium",
        "faster-whisper model id passed to Speaches when "
        "voice_agent_stt_mode=sidecar. NOTE: an HF id, not the Pipecat "
        "Whisper enum used by the in-process voice_agent_whisper_model.",
    ),
    (
        "voice_agent_tts_mode",
        "inprocess",
        "TTS backend for the voice pipeline (#1088): 'sidecar' = thin "
        "client of the warm Speaches container; 'inprocess' = run Kokoro "
        "in-process (legacy). No silent default.",
    ),
    (
        "voice_agent_tts_base_url",
        "http://speaches:8000/v1",
        "Speaches TTS endpoint used when voice_agent_tts_mode=sidecar. "
        "Same Speaches service as STT by default; separate key so STT and "
        "TTS can be split onto different hosts later without a migration.",
    ),
    (
        "voice_agent_tts_model",
        "speaches-ai/Kokoro-82M-v1.0-ONNX",
        "Kokoro model id passed to Speaches when voice_agent_tts_mode="
        "sidecar. The voice id (bf_emma / bf_isabella) still comes from "
        "voice_agent_tts_voice / voice_agent_claude_code_tts_voice — those "
        "carry over unchanged because the voice pack is identical.",
    ),
]


async def up(pool) -> None:
    """Seed the Speaches sidecar keys, idempotently."""
    async with pool.acquire() as conn:
        for key, value, description in _KEYS:
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_active, is_secret)
                VALUES ($1, $2, 'voice', $3, true, false)
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
                description,
            )
        logger.info(
            "Migration seed_voice_speaches_sidecar_keys: applied (%d keys)",
            len(_KEYS),
        )


async def down(pool) -> None:
    """Drop the Speaches sidecar keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key = ANY($1::text[])
            """,
            [k for k, _, _ in _KEYS],
        )
        logger.info(
            "Migration seed_voice_speaches_sidecar_keys down: reverted"
        )
