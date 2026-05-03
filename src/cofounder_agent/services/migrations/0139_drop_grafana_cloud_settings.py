"""Migration 0139: drop the Grafana Cloud signup-referral setting.

Grafana Cloud was retired 2026-05-03. The local Docker container
(``poindexter-grafana``) is the only Grafana now; ``grafana_url``
already points at ``http://localhost:3000``.

Originally this migration was scoped wider — five Cloud-era keys —
but on review only ``grafana_referral_url`` is genuinely Cloud-specific.
The others stay:

- ``grafana_oauth_client_id`` / ``grafana_oauth_client_secret`` — these
  are credentials for OUR OAuth issuer (``services/auth/oauth_issuer.py``)
  that local Grafana uses to validate JWTs when posting alerts to
  ``/api/alerts``. Cloud-Grafana used the same flow; self-hosted does
  too. Minted by the ``poindexter auth mint-grafana-token`` CLI.
- ``grafana_api_token`` / ``grafana_api_key`` — service-account tokens
  for the Grafana HTTP API used by ``brain/alert_sync.py`` to push
  alert rules from ``alert_rules`` rows. Empty today because nobody
  set up a service account on the local Grafana yet — when the
  operator does, the same key holds it. No reason to drop a key
  whose name and shape are still correct just because it's empty.
- ``grafana_referral_url`` — Cloud signup referral link. Marketing
  cruft from when Matt set the system up against Cloud's free tier.
  Self-hosted has no signup. **Dropped here.**

Idempotent — safe to re-run; ``DELETE WHERE key = $1`` is a no-op
when the row is already gone.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_KEYS_TO_DROP = [
    "grafana_referral_url",
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
                "Table 'app_settings' missing — skipping migration 0139"
            )
            return

        dropped = 0
        for key in _KEYS_TO_DROP:
            result = await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", key,
            )
            if result == "DELETE 1":
                dropped += 1
                logger.info("Migration 0139: dropped %s", key)
            else:
                logger.info(
                    "Migration 0139: %s not present (already cleaned up)",
                    key,
                )

        logger.info(
            "Migration 0139: dropped %d Cloud-era app_settings",
            dropped,
        )


async def down(pool) -> None:
    # The referral URL value is just a marketing link — re-creating it
    # by hand if needed is trivial. No restore-from-backup needed.
    del pool  # unused; required by migration runner signature
    logger.info(
        "Migration 0139 down is a no-op — re-add grafana_referral_url "
        "manually via app_settings if you reactivate Grafana Cloud."
    )
