"""Migration 0116: seed 5 content-agent env-vars into app_settings (GH-175).

GH-175 — DB-first configuration migration. Five settings still read by
``agents/content_agent/config.py`` via ``os.getenv`` are moved into
``app_settings`` so operators can tune them at runtime without restarting
the worker or editing docker-compose.

Seeded keys (snake_case, mirroring the existing app_settings convention):

* ``serper_api_key`` — Serper search API key. ``is_secret=true`` so it's
  decrypted via ``site_config.get_secret(...)`` rather than cached in the
  in-memory config dict. Note: at the time of writing,
  ``agents/content_agent/config.py`` reads this synchronously at import
  time and so still uses the env fallback path inside ``site_config.get``.
  The schema is set up correctly here so a future migration to async
  access is a code-only change.
* ``local_llm_api_url`` — Ollama URL fallback. Empty default → "Ollama
  not configured", per the no-silent-defaults rule. Operators set this
  via ``poindexter settings set local_llm_api_url <url>`` or env.
* ``local_llm_model_name`` — Ollama model fallback. Default ``"auto"``
  matches the existing env-var fallback (OllamaClient picks the first
  available pulled model).
* ``max_log_size_mb`` — Log rotation file size cap (MB). Default ``5``
  matches the existing hardcoded fallback.
* ``max_log_backup_count`` — Log rotation backup count. Default ``3``
  matches the existing hardcoded fallback.

Each row reads the existing env-var at migration-apply time as its seed
value so that operators who already have these vars exported don't lose
their tuned values when the row is created. After this migration runs,
the env-var path becomes a backward-compat fallback inside
``site_config.get`` rather than the primary source.

Idempotent — ``INSERT ... ON CONFLICT DO NOTHING`` leaves any
operator-set value alone.
"""

import os

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


def _seed_settings() -> list[tuple[str, str, str, str, bool]]:
    """Build the seed rows. Reads env at apply-time so existing operator
    values are preserved on first install rather than being clobbered by
    a hardcoded default.

    Returns list of (key, value, category, description, is_secret) tuples.
    """
    return [
        (
            "serper_api_key",
            os.getenv("SERPER_API_KEY", ""),
            "external_apis",
            (
                "Serper search API key for real-time web-search capability "
                "in the content agent. Empty value disables web search "
                "without crashing. Get a key from https://serper.dev. "
                "Marked is_secret=true so it lives encrypted in the DB and "
                "is fetched via site_config.get_secret(...). Ref: GH-175."
            ),
            True,
        ),
        (
            "local_llm_api_url",
            os.getenv("LOCAL_LLM_API_URL", ""),
            "content",
            (
                "Ollama API base URL for local LLM calls (e.g. "
                "http://localhost:11434). Empty value means 'Ollama not "
                "configured' — callers must handle that explicitly per "
                "the no-silent-defaults rule. Ref: GH-175."
            ),
            False,
        ),
        (
            "local_llm_model_name",
            os.getenv("LOCAL_LLM_MODEL_NAME", "auto"),
            "content",
            (
                "Ollama model fallback used by agents/content_agent/config "
                "when no per-task model is configured. 'auto' lets "
                "OllamaClient pick the first available pulled model. "
                "Override with a specific tag (e.g. 'gemma3:27b'). "
                "Ref: GH-175."
            ),
            False,
        ),
        (
            "max_log_size_mb",
            os.getenv("MAX_LOG_SIZE_MB", "5"),
            "logging",
            (
                "Maximum size in MB of a rotating log file before it's "
                "rolled over. Default 5 MB matches the historical env-var "
                "fallback. Ref: GH-175."
            ),
            False,
        ),
        (
            "max_log_backup_count",
            os.getenv("MAX_LOG_BACKUP_COUNT", "3"),
            "logging",
            (
                "Number of rotated log backups to retain. Default 3 "
                "matches the historical env-var fallback. Ref: GH-175."
            ),
            False,
        ),
    ]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration 0116"
            )
            return

        rows = _seed_settings()
        for key, value, category, description, is_secret in rows:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description, is_secret,
            )
        logger.info(
            "Migration 0116: seeded %d content-agent settings (GH-175)",
            len(rows),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        keys = [k for k, *_ in _seed_settings()]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            keys,
        )
        logger.info("Migration 0116 rolled back: removed %d settings", len(keys))
