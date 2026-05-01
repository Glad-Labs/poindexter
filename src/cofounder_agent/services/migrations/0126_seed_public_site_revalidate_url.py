"""Migration 0126: seed ``public_site_revalidate_url`` app_setting.

Pairs with the Glad-Labs/poindexter#327 fix to
``services/revalidation_service.py``. The Next.js public site exposes
its ISR cache-busting endpoint at ``/api/revalidate``; before this
migration, the revalidation service derived that URL by appending
``/api/revalidate`` to whichever of ``public_site_url`` /
``site_url`` / ``next_public_public_site_url`` /
``next_public_api_base_url`` happened to be set first.

That implicit chain bit us at least once already (#327): the
``/go-live`` admin endpoint and the scheduled publisher promoted
posts to ``status='published'`` without any revalidation at all,
and the canonical publish_service path silently fell back to a
``localhost:3000`` URL when the chain misresolved on the cloud
worker. Making the URL its own DB-managed setting:

* gives operators one knob to point revalidation at a different
  Vercel project / staging URL without touching ``site_url``;
* lets us validate "is the system configured to revalidate prod"
  with a single ``SELECT value FROM app_settings WHERE key = ...``;
* keeps the legacy resolution chain as a fallback (still picked up
  by ``_resolve_revalidate_url``) so existing installs that haven't
  run this migration yet keep working.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — re-running the
migration leaves any operator-set value alone.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_KEY = "public_site_revalidate_url"
_VALUE = "https://www.gladlabs.io/api/revalidate"
_CATEGORY = "site"
_DESCRIPTION = (
    "Full URL of the Next.js public site's /api/revalidate endpoint. "
    "POSTed by services/revalidation_service.py to bust the ISR cache "
    "after every publish. Override per-environment (e.g. point at a "
    "Vercel preview URL for staging). Leave blank to fall back to the "
    "legacy resolution chain (public_site_url/site_url + "
    "/api/revalidate). Glad-Labs/poindexter#327."
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
                "Table 'app_settings' missing — skipping migration 0126"
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
            logger.info("Migration 0126: seeded %s=%s", _KEY, _VALUE)
        else:
            logger.info(
                "Migration 0126: %s already set, leaving operator "
                "value alone",
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
        logger.info(
            "Migration 0126 rolled back: removed %s",
            _KEY,
        )
