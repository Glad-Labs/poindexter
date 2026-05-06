"""Migration 20260506_060539: seed ops_triage_cache_ttl_seconds for triage_routes idempotency.

ISSUE: Glad-Labs/poindexter#347 (firefighter ops LLM v1, step 3 — the
``POST /api/triage`` route).

The route is idempotent on ``alert_event_id`` via a process-local TTL
cache. The TTL is configurable so an operator can shorten the window
when iterating on the prompt (force a re-call by clearing or shrinking
the cache) without redeploying. v1 explicitly forbids new columns on
``alert_events``, so a process-local cache + this DB-tunable TTL is
the entire idempotency surface.

One row added:

- ``ops_triage_cache_ttl_seconds`` (``3600``) — default cache lifetime
  per alert_event_id. Brain retries within an hour return the cached
  diagnosis without re-burning LLM tokens.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so re-runs preserve any
operator-set value.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "ops_triage_cache_ttl_seconds",
        "3600",
        "TTL (seconds) for the POST /api/triage process-local idempotency "
        "cache (#347 step 3). Repeat calls for the same alert_event_id "
        "within this window return the cached diagnosis instead of "
        "re-invoking the LLM. Set lower while iterating on the system "
        "prompt to force re-calls; set higher to absorb brain retry "
        "storms without budget impact.",
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
    """Apply the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing -- skipping migration "
                "20260506_060539 (ops_triage_cache_ttl_seconds seed)"
            )
            return

        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, 'firefighter', $3, FALSE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 20260506_060539: seeded %d/%d ops_triage cache "
            "settings (remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "Migration 20260506_060539 rolled back: removed %d "
            "ops_triage cache settings",
            len(_SEEDS),
        )
