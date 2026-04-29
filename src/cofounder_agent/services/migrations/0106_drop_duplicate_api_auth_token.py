"""Migration 0106: Drop the orphaned ``api_auth_token`` row from app_settings.

Fixes Glad-Labs/poindexter#231 — the worker middleware at
``middleware/api_token_auth.py:99`` validates Bearer tokens against
``site_config.get_secret("api_token", ...)``. Two near-identical rows
existed in ``app_settings``: the canonical ``api_token`` (which the
middleware reads) and a legacy/typo ``api_auth_token`` row that nothing
in the codebase consumed. Operators following the CLI's old error
message would copy the dead ``api_auth_token`` value into
``POINDEXTER_KEY`` and hit ``HTTP 401 Invalid token`` because the
middleware was comparing against the other row.

This migration removes the dead ``api_auth_token`` row so it can no
longer mislead anyone. The CLI error message at
``poindexter/cli/_api_client.py`` was updated in the same change to
point at the canonical ``api_token`` key.

Idempotent: ``DELETE WHERE key = 'api_auth_token'`` is a no-op if the
row was already deleted (or never existed on a fresh install where the
typo was never seeded).

Down migration: intentionally a no-op. The orphan was never load-bearing
— recreating it would just reintroduce the same trap. If someone is
genuinely rolling back through 0106 they should re-set ``api_token`` via
``poindexter settings set api_token <value>`` instead.

Note: 0105 is taken in parallel by the issue #216 sweep landing on the
same branch; this migration is numbered 0106 to avoid the collision.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_DEAD_KEY = "api_auth_token"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _DEAD_KEY,
        )
        # asyncpg returns a tag like "DELETE 1" or "DELETE 0".
        if result == "DELETE 1":
            logger.info(
                "0106: removed orphaned app_settings row '%s' "
                "(canonical key is 'api_token' — see poindexter#231)",
                _DEAD_KEY,
            )
        else:
            logger.info(
                "0106: no '%s' row to remove (already absent — fine on fresh installs)",
                _DEAD_KEY,
            )


async def down(_pool) -> None:
    """Intentionally a no-op.

    The dead row was never read by anything; reintroducing it would
    just recreate the trap that caused poindexter#231. Roll-forward
    only.
    """
    logger.info(
        "0106: down() is a no-op — '%s' is dead data we never want to recreate",
        _DEAD_KEY,
    )
