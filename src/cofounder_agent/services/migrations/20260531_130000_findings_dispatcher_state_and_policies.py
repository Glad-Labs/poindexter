"""Migration 20260531_130000_findings_dispatcher_state_and_policies

Stands up the brain findings dispatcher (Glad-Labs/poindexter#461 Phase 1):

1. ``findings_dispatch_state`` — sibling state table keyed on the
   ``audit_log`` row id. The dispatcher polls findings via a NOT EXISTS
   anti-join against this table, so ``audit_log`` stays append-only (no
   mutable ``processed_at`` column on the canonical record).

2. **Backfill** every pre-existing finding as already-dispatched
   (``dispatch_result='backfilled_preexisting'``, channel ``log_only``).
   This is the load-bearing safety step: without it, activating the
   dispatcher would route the entire accumulated backlog (263 rows as of
   2026-05-31, incl. 162 media_drift) at once — a notification storm. The
   dispatcher must only act on findings emitted AFTER it goes live.

3. Seed per-kind delivery policies + a ``findings.default`` catch-all
   (``log_only``) so an unlisted/new finding kind can never silently start
   paging. Phase 1 implements discord/telegram/log_only; policies naming
   the Phase-2 channels (auto_fix / github_issue) fall through to their
   ``fallback`` in the dispatcher.

All seeds are ``ON CONFLICT DO NOTHING`` — never clobber an operator-tuned
value; a replay on an up-to-date DB is a no-op.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Per-kind policies. Keys flatten to findings.<kind>.<field> app_settings
# rows. media_drift is the dominant emitted kind (162/263 on 2026-05-31) and
# NOT in the original issue list — pinned to log_only so it never spams.
_POLICIES: dict[str, dict[str, str]] = {
    "default": {"delivery": "log_only", "fallback": "log_only", "cooldown_minutes": "1440", "min_severity": "warn"},
    "anomaly": {"delivery": "telegram", "fallback": "discord", "cooldown_minutes": "60", "min_severity": "critical"},
    "quality_regression": {"delivery": "github_issue", "fallback": "discord", "cooldown_minutes": "1440", "min_severity": "warn"},
    "broken_link": {"delivery": "discord", "fallback": "log_only", "cooldown_minutes": "360", "min_severity": "warn"},
    "broken_external_link": {"delivery": "auto_fix", "fallback": "discord", "cooldown_minutes": "60", "min_severity": "warn"},
    "broken_internal_link": {"delivery": "auto_fix", "fallback": "discord", "cooldown_minutes": "60", "min_severity": "warn"},
    "broken_external_link_autofixed": {"delivery": "log_only"},
    "broken_internal_link_autofixed": {"delivery": "log_only"},
    "missing_seo": {"delivery": "auto_fix", "fallback": "github_issue", "cooldown_minutes": "1440", "min_severity": "warn"},
    "duplicate_post": {"delivery": "log_only"},
    "topic_gap": {"delivery": "discord", "fallback": "log_only", "cooldown_minutes": "1440", "min_severity": "info"},
    "media_drift": {"delivery": "log_only"},
    "r2_static_drift": {"delivery": "discord", "fallback": "log_only", "cooldown_minutes": "360", "min_severity": "warn"},
    "post_verification_failure": {"delivery": "discord", "fallback": "log_only", "cooldown_minutes": "360", "min_severity": "warn"},
    "stock_image_regenerated": {"delivery": "log_only"},
    "uncategorized_post_autofixed": {"delivery": "log_only"},
    "cloud_sync_returned_false": {"delivery": "discord", "fallback": "log_only", "cooldown_minutes": "360", "min_severity": "warn"},
}


def _setting_rows() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for kind, fields in _POLICIES.items():
        for field, value in fields.items():
            rows.append((f"findings.{kind}.{field}", value))
    return rows


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS findings_dispatch_state (
                finding_id      BIGINT PRIMARY KEY,
                kind            TEXT NOT NULL,
                dedup_key       TEXT,
                channel         TEXT NOT NULL,
                dispatch_result TEXT NOT NULL,
                dispatched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Cooldown lookup index: (kind, dedup_key) recent 'sent' rows.
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_findings_dispatch_cooldown
            ON findings_dispatch_state (kind, dedup_key, dispatched_at)
            WHERE dedup_key IS NOT NULL
            """
        )

        # Backfill pre-existing findings as already-dispatched so the
        # dispatcher never storms the operator with the accumulated backlog.
        backfilled = await conn.execute(
            """
            INSERT INTO findings_dispatch_state
                (finding_id, kind, dedup_key, channel, dispatch_result, dispatched_at)
            SELECT a.id,
                   COALESCE(a.details->>'kind', 'unknown'),
                   a.details->>'dedup_key',
                   'log_only',
                   'backfilled_preexisting',
                   NOW()
            FROM audit_log a
            WHERE a.event_type = 'finding'
              AND NOT EXISTS (
                    SELECT 1 FROM findings_dispatch_state s WHERE s.finding_id = a.id
              )
            """
        )
        logger.info("Migration findings_dispatcher: backfilled (%s)", backfilled)

        # Seed delivery policies.
        for key, value in _setting_rows():
            await conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO NOTHING",
                key, value,
            )
        logger.info(
            "Migration findings_dispatcher: seeded %d policy keys", len(_setting_rows())
        )


async def down(pool) -> None:
    """Drop the state table and remove seeded policy rows still at default."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS findings_dispatch_state")
        for key, value in _setting_rows():
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key, value,
            )
        logger.info("Migration findings_dispatcher down: dropped state table + default policy rows")
