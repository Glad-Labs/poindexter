"""Migration 0124: seed Ollama retry / concurrency app_settings.

Pairs with the ``services/ollama_client.py`` refactor in
Glad-Labs/poindexter#200, which replaced a hand-rolled exponential-
backoff loop with ``tenacity`` decorators and added an
``aiolimiter.AsyncLimiter`` to cap concurrent in-flight requests
against the local Ollama server.

This migration seeds the four knobs the new code reads at runtime:

- ``ollama_max_retries`` (default ``"3"``) — passed to
  ``tenacity.stop_after_attempt``. Includes the first attempt, so
  ``"3"`` means 1 try + 2 retries.
- ``ollama_retry_initial_seconds`` (default ``"1"``) — initial backoff
  delay passed to ``tenacity.wait_exponential_jitter(initial=...)``.
- ``ollama_retry_max_seconds`` (default ``"30"``) — upper bound on
  the backoff wait passed to ``wait_exponential_jitter(max=...)``.
- ``ollama_concurrency_limit`` (default ``"10"``) — passed to
  ``aiolimiter.AsyncLimiter(max_rate=...)``. Caps in-flight Ollama
  requests so a burst of pipeline tasks can't VRAM-thrash the GPU
  and silently fall back to a smaller model.

Trade-off: the tenacity ``stop`` / ``wait`` config is read on each
``generate_with_retry`` call (cheap), so changes to the three retry
keys take effect on the next call without a worker restart. The
concurrency limiter is module-level state — operators who want to
change ``ollama_concurrency_limit`` live can call
``services.ollama_client.rebuild_concurrency_limiter()`` to apply
the new cap without a restart, or simply restart the worker.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — re-running the
migration leaves any operator-set value alone.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SETTINGS = [
    (
        "ollama_max_retries",
        "3",
        "performance",
        (
            "Maximum number of attempts (initial + retries) for "
            "Ollama generate_with_retry. Passed to tenacity's "
            "stop_after_attempt. Default 3 = 1 try + 2 retries. "
            "Lower this if you'd rather fail-fast to the cloud "
            "fallback chain; raise it on a flaky local Ollama."
        ),
    ),
    (
        "ollama_retry_initial_seconds",
        "1",
        "performance",
        (
            "Initial backoff delay (seconds) for Ollama retries — "
            "passed to tenacity's wait_exponential_jitter(initial). "
            "Doubles on each subsequent retry up to "
            "ollama_retry_max_seconds, with up to 2s jitter."
        ),
    ),
    (
        "ollama_retry_max_seconds",
        "30",
        "performance",
        (
            "Upper bound (seconds) on Ollama retry backoff — passed "
            "to tenacity's wait_exponential_jitter(max). Caps the "
            "exponential growth so a long retry chain can't stretch "
            "into multi-minute waits."
        ),
    ),
    (
        "ollama_concurrency_limit",
        "10",
        "performance",
        (
            "Maximum concurrent in-flight Ollama requests this worker "
            "process will issue — passed to aiolimiter.AsyncLimiter. "
            "Prevents bursts of pipeline tasks from VRAM-thrashing "
            "the GPU and silently falling back to a smaller model. "
            "Tune down on shared GPUs, up on a dedicated rig. Live "
            "changes apply via "
            "services.ollama_client.rebuild_concurrency_limiter() "
            "or a worker restart."
        ),
    ),
]


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
                "Table 'app_settings' missing — skipping migration 0123"
            )
            return

        seeded = 0
        for key, value, category, description in _SETTINGS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            if result == "INSERT 0 1":
                seeded += 1
                logger.info(
                    "Migration 0123: seeded %s=%s", key, value
                )
            else:
                logger.info(
                    "Migration 0123: %s already set, leaving operator "
                    "value alone", key,
                )
        logger.info(
            "Migration 0123 complete — seeded %d/%d Ollama resilience "
            "settings (others already present)",
            seeded, len(_SETTINGS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _category, _description in _SETTINGS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "Migration 0123 rolled back: removed %d Ollama "
            "resilience settings",
            len(_SETTINGS),
        )
