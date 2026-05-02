"""Migration 0107: Seed voice_agent_vad_stop_secs.

End-of-speech silence window the VAD waits before deciding the user
finished talking. Lower = snappier turn-taking; higher = more tolerance
for natural pauses mid-sentence.

Default 0.2 matches Pipecat's ``VAD_STOP_SECS`` constant — keeping it
explicit so operators can tune turn-taking pace from app_settings without
patching code. Drop to 0.15 for very brisk back-and-forth; bump to 0.4
for users who pause to think.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so a pre-set custom value
survives a re-run.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_KEY = "voice_agent_vad_stop_secs"
_VALUE = "0.2"
_DESCRIPTION = (
    "Silero VAD end-of-speech silence window in seconds. Lower = snappier "
    "turn-taking but more risk of cutting the user off mid-sentence; "
    "higher = more tolerance for natural pauses. 0.2 matches Pipecat's "
    "default. 0.15 for brisk dialog; 0.4 for thoughtful pauses."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, is_active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY, _VALUE, _DESCRIPTION,
        )
        if result == "INSERT 0 1":
            logger.info("0107: seeded %s = %s", _KEY, _VALUE)
        else:
            logger.info("0107: %s already set, left as-is", _KEY)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _KEY,
        )
        logger.info("0107: removed %s", _KEY)
