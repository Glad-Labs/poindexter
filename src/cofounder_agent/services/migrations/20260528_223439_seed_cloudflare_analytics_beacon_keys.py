"""Migration 20260528_223439: seed Cloudflare Analytics Engine beacon keys.

Provisions the three ``app_settings`` keys consumed by the
``sync_cloudflare_analytics`` job + the public-site beacon wiring:

- ``cloudflare_analytics_api_token`` (secret) — CF API token scoped to
  ``Account → Account Analytics → Read``. Operator fills in via
  ``poindexter set`` after the Worker is deployed (see
  ``infrastructure/cloudflare/page-views-beacon/README.md``).
- ``cloudflare_beacon_url`` (non-secret) — public URL of the deployed
  Worker. Read by the operator setup runbook only; the public site reads
  ``NEXT_PUBLIC_BEACON_URL`` from Vercel env vars, not from
  ``app_settings``.
- ``cloudflare_analytics_last_sync`` (non-secret) — high-water mark
  (ISO-8601 UTC) for the sync job. Seeded to
  ``1970-01-01T00:00:00Z`` so the first run pulls the configured lookback
  window (default 24h).

``cloudflare_account_id`` is intentionally **not** re-seeded — it already
exists in ``0000_baseline.seeds.sql`` per the 2026-05-27 operator-leak
audit. Re-seeding it here would be a no-op (``ON CONFLICT DO NOTHING``)
but adding a duplicate row is noise.

All three keys are seeded with empty placeholders per
``feedback_no_secrets_prompts`` — the operator fills them in via
``poindexter set`` once the Worker is live. No prompts, no manual env
var edits.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS = [
    {
        "key": "cloudflare_analytics_api_token",
        "value": "",
        "description": (
            "Cloudflare API token scoped to Account → Account Analytics → "
            "Read. Consumed by the sync_cloudflare_analytics job to pull "
            "page-view rows from the page_views_ae dataset via the CF "
            "SQL HTTP API. Operator fills in via `poindexter set` after "
            "deploying the Worker."
        ),
        "is_secret": True,
    },
    {
        "key": "cloudflare_beacon_url",
        "value": "",
        "description": (
            "Public URL of the deployed page-views-beacon Cloudflare "
            "Worker (e.g. https://beacon.example.com or "
            "https://page-views-beacon.<sub>.workers.dev). Reference "
            "value for the operator setup runbook — the public site "
            "reads NEXT_PUBLIC_BEACON_URL from Vercel env vars."
        ),
        "is_secret": False,
    },
    {
        "key": "cloudflare_analytics_last_sync",
        "value": "1970-01-01T00:00:00Z",
        "description": (
            "High-water mark (ISO-8601 UTC) for the "
            "sync_cloudflare_analytics job. Advanced atomically after "
            "each successful batch insert. Seeded to the epoch so the "
            "first run pulls the configured lookback window (default "
            "24h)."
        ),
        "is_secret": False,
    },
]


async def up(pool) -> None:
    """Seed the three rows idempotently.

    Idempotent via ``ON CONFLICT (key) DO NOTHING`` — re-runs against
    a DB that already has the rows leave the operator's chosen values
    intact.
    """
    async with pool.acquire() as conn:
        for row in _SEEDS:
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_active, is_secret)
                VALUES ($1, $2, 'cloudflare', $3, true, $4)
                ON CONFLICT (key) DO NOTHING
                """,
                row["key"],
                row["value"],
                row["description"],
                row["is_secret"],
            )
        logger.info(
            "Migration seed_cloudflare_analytics_beacon_keys: applied (%d rows)",
            len(_SEEDS),
        )


async def down(pool) -> None:
    """Remove the three seeded rows.

    Operator-tuned values that were not the original placeholder are
    lost on down — same as every other ``app_settings`` seed migration
    in this tree.
    """
    keys = [row["key"] for row in _SEEDS]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            keys,
        )
        logger.info(
            "Migration seed_cloudflare_analytics_beacon_keys down: reverted"
        )
