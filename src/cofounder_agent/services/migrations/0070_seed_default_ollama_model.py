"""Migration 0070: seed ``default_ollama_model`` app_setting.

GH-93 — part of the DB-first env-var eviction. ``DEFAULT_OLLAMA_MODEL``
was exposed as a docker env var but the only code path that consumed it
(``services.ollama_client._default_model``) already reads
``site_config.get("default_ollama_model", "auto")``. Removing the env
var from ``docker-compose.local.yml`` requires the setting exists in
the DB so operators can tune it without ever editing compose.

Default: ``"auto"`` (OllamaClient picks the first available local model
from the pulled set). No env fallback — tunable at runtime via
``poindexter settings set default_ollama_model <model>``.

Idempotent — ``INSERT ... ON CONFLICT DO NOTHING`` leaves an operator-set
value alone.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration 0070"
            )
            return
        await conn.execute(
            """
            INSERT INTO app_settings (
                key, value, category, description, is_secret
            )
            VALUES (
                'default_ollama_model',
                'auto',
                'content',
                'Default Ollama model for LLM calls. "auto" → OllamaClient '
                'picks the first available pulled model. Override with a '
                'specific model tag (e.g. "gemma3:27b").',
                false
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 0070: seeded default_ollama_model='auto' (if not set)"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key='default_ollama_model'"
        )
        logger.info("Migration 0070 rolled back: removed default_ollama_model")
