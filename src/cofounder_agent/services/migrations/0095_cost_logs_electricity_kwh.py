"""Migration 0095: add ``electricity_kwh`` column to ``cost_logs``.

Every LLM call now records both:

- ``cost_usd`` — billed dollar cost (cloud API spend, or for local
  Ollama: power × duration × electricity rate)
- ``electricity_kwh`` — energy consumed *processing* the call.
  Local providers measure it directly (GPU watts × duration).
  Cloud providers estimate it from per-model Wh/1K-token figures
  in ``app_settings`` so we can compare data-center inference
  energy against on-prem inference energy on the same dashboard.

The column is nullable so existing rows stay valid; only new
``record_usage`` calls populate it. Rolling back drops the column
without disturbing the existing ``cost_usd`` data.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE cost_logs
            ADD COLUMN IF NOT EXISTS electricity_kwh NUMERIC(12, 8)
            """
        )
        # Index supports the dashboard query "kWh per provider, last 24h".
        # Partial index keeps it small — only rows that actually carry a
        # measurement get into it.
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cost_logs_electricity_kwh
            ON cost_logs (created_at, provider)
            WHERE electricity_kwh IS NOT NULL
            """
        )
        logger.info("0095: added cost_logs.electricity_kwh + index")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_cost_logs_electricity_kwh")
        await conn.execute(
            "ALTER TABLE cost_logs DROP COLUMN IF EXISTS electricity_kwh"
        )
        logger.info("0095: dropped cost_logs.electricity_kwh + index")
