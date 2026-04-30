"""Migration 0120: seed ``uptime_kuma_api_key`` app_setting (closes Glad-Labs/poindexter#312).

The Uptime Kuma API key was previously hardcoded as a literal in
``infrastructure/prometheus/prometheus.yml`` (the ``uptime-kuma`` scrape
job's ``basic_auth.password`` field). That file is mirrored to the
public ``Glad-Labs/poindexter`` repo via the sync flow, so the key
leaked to a public git history. Severity is low (the key only authorises
the local docker-network Uptime Kuma instance) but non-zero — anyone who
cloned the public repo at any point still has a copy of the old key.

This migration introduces the runtime-resolved replacement:

- ``uptime_kuma_api_key`` (default ``""``, ``is_secret=true``,
  ``category='monitoring'``) — Uptime Kuma read-only API key used by
  Prometheus to scrape ``/metrics`` over basic auth (password-only,
  empty username). Operators rotate the key in Uptime Kuma's admin UI
  (Settings → API Keys), then ``poindexter set uptime_kuma_api_key
  <new-value>``. Empty value is the safe default — the prometheus.yml
  scrape job carries a ``__REPLACE_AT_RUNTIME__`` placeholder until a
  follow-up renders the file from this setting.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — re-running the migration
leaves any operator-set value alone, so this is safe for existing
operators who have already pasted their key in.

Cross-references:
- Issue: Glad-Labs/poindexter#312
- File rotated: infrastructure/prometheus/prometheus.yml (line ~49)
- Follow-up: render prometheus.yml from app_settings at startup so the
  placeholder gets substituted automatically (see PR body for the TODO).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_KEY = "uptime_kuma_api_key"
_VALUE = ""
_CATEGORY = "monitoring"
_DESCRIPTION = (
    "Uptime Kuma read-only API key used by Prometheus to scrape "
    "/metrics via password-only basic auth (empty username). Generate "
    "in Uptime Kuma admin UI: Settings -> API Keys. Rotate by revoking "
    "the existing key there and pasting the new one via "
    "'poindexter set uptime_kuma_api_key <new-value>'. Empty default "
    "leaves the uptime-kuma scrape job non-functional until set. "
    "is_secret=true so it never leaks into the in-memory config cache "
    "or logs. Closes Glad-Labs/poindexter#312 (rotation of leaked key)."
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
                "Table 'app_settings' missing — skipping migration 0120"
            )
            return

        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY, _VALUE, _CATEGORY, _DESCRIPTION,
        )
        if result == "INSERT 0 1":
            logger.info(
                "Migration 0120: seeded %s='' (operator must set via "
                "'poindexter set %s <value>' after rotating the Uptime "
                "Kuma API key)",
                _KEY, _KEY,
            )
        else:
            logger.info(
                "Migration 0120: %s already set, leaving operator value alone",
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
        logger.info("Migration 0120 rolled back: removed %s", _KEY)
