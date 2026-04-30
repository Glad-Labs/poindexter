"""Migration 0118: seed ``topic_discovery_auto_enabled`` kill-switch.

Pairs with the legacy auto-discovery loop in ``services/idle_worker.py``.
After the niche-pivot work (#278, merged 2026-04-30) added an
operator-driven flow — ``topic_batches`` table, ``TopicBatchService``,
``poindexter topics rank-batch / resolve-batch`` CLI — the legacy
signal-driven discovery loop in ``IdleWorker`` (queue-low,
stale-content, rejection-streak, 24h safety net) still fires on its
own and bypasses the operator gate entirely. Result: pending /
awaiting_approval queues fill up with topics the operator never
explicitly approved as part of a sweep.

This migration introduces a single boolean knob that gates the
auto-firing branches inside ``IdleWorker._should_trigger_discovery``
and the 24h safety-net fallback in ``IdleWorker.run_cycle``:

- ``topic_discovery_auto_enabled`` (default ``"true"``) — when
  ``"false"``, the auto-firing branches bail out early with an INFO
  log and no work is queued. Manual operator triggers
  (``topic_discovery_manual_trigger=true``) still work, so an
  operator can fire one-shot discoveries even with auto disabled.

Default is ``"true"`` to preserve backward-compatible behaviour for
existing OSS users who haven't configured niches — they keep the
current auto-discovery semantics. Operators with niches set should
set this to ``"false"`` and drive discovery via
``poindexter topics rank-batch / resolve-batch`` instead. Matt flips
the value via the CLI/MCP after this PR merges; the migration does
NOT mutate his running DB.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — re-running the
migration leaves any operator-set value alone.

Cross-references:
- Niche-pivot design: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
- Niche-aware tables: migration 0113
- Operator CLI: ``poindexter topics rank-batch / resolve-batch``
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_KEY = "topic_discovery_auto_enabled"
_VALUE = "true"
_CATEGORY = "topic_discovery"
_DESCRIPTION = (
    "Master kill-switch for the LEGACY auto-firing topic discovery loop "
    "in services/idle_worker.py. When 'true' (default, backward "
    "compatible), the loop fires on signals (queue_low, stale_content, "
    "rejection_streak) and the 24h safety-net fallback. When 'false', "
    "all auto-firing branches bail out early with an INFO log and no "
    "work is queued — operators drive discovery exclusively through the "
    "niche-aware operator flow ('poindexter topics rank-batch' followed "
    "by 'poindexter topics resolve-batch'). Manual triggers via "
    "topic_discovery_manual_trigger=true still work in either mode so "
    "operators can fire one-shot discoveries even with auto disabled. "
    "Trade-off: 'true' keeps the pipeline self-feeding for OSS users "
    "with no niches configured, but bypasses the operator approval "
    "gate; 'false' enforces the gate but requires explicit operator "
    "action to keep the queue full. See niche-pivot work (#278) and "
    "migration 0113 for the operator-driven alternative."
)


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
                "Table 'app_settings' missing — skipping migration 0118"
            )
            return

        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES ($1, $2, $3, $4, FALSE)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY, _VALUE, _CATEGORY, _DESCRIPTION,
        )
        if result == "INSERT 0 1":
            logger.info(
                "Migration 0118: seeded %s=%s (legacy auto-discovery "
                "kill-switch, default backward-compatible)",
                _KEY, _VALUE,
            )
        else:
            logger.info(
                "Migration 0118: %s already set, leaving operator value alone",
                _KEY,
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _KEY,
        )
        logger.info("Migration 0118 rolled back: removed %s", _KEY)
