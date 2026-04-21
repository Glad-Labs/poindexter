"""Migration 0076: seed direct-social-API settings (GH-36).

Retires the dlvr.it RSS bridge and replaces it with direct AT Protocol
(Bluesky) and Mastodon API publishing. This migration seeds the four
``app_settings`` rows the adapters read:

* ``bluesky_identifier`` — Bluesky handle or DID, e.g.
  ``gladlabs.bsky.social``. ``is_secret=true`` so it never leaks into
  the in-memory config dict or logs.
* ``bluesky_app_password`` — app password from
  https://bsky.app/settings/app-passwords. ``is_secret=true`` — NEVER
  the account password.
* ``mastodon_instance_url`` — full instance URL, e.g.
  ``https://mastodon.social``. Plain config row (not a secret — it's
  public info, and storing URLs as secrets complicates debugging).
* ``mastodon_access_token`` — token with ``write:statuses`` scope from
  Preferences > Development > New Application. ``is_secret=true``.

The seeded values are empty strings. Operators fill them in via
``mcp__poindexter__set_setting`` or the admin UI. The adapters
short-circuit with a "not configured" log when either credential is
missing — no crash, no fallback, just a clean skip (feedback_no_silent_defaults
still honored: the adapter fails LOUDLY only when the operator has
flagged the platform as enabled in ``social_distribution_platforms``).

LinkedIn / Reddit / YouTube are NOT seeded here — those adapters are
stubs awaiting OAuth setup (see GH-40).

Idempotent: ``ON CONFLICT DO NOTHING`` leaves any operator-tuned value
alone. Safe to re-run. Down migration deletes only the seed rows this
migration inserted.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


# Columns: key, value, category, description, is_secret.
# Keep descriptions short and actionable — operators see them in the
# admin UI when picking values.
_SEED_ROWS = (
    (
        "bluesky_identifier",
        "",
        "social",
        "GH-36: Bluesky handle or DID used for direct AT Protocol posting "
        "(e.g. 'gladlabs.bsky.social'). Empty = Bluesky distribution skipped. "
        "See bsky.app/settings.",
        True,
    ),
    (
        "bluesky_app_password",
        "",
        "social",
        "GH-36: Bluesky APP password (NOT the account password). Generate at "
        "https://bsky.app/settings/app-passwords. Store as a secret — rotate "
        "by revoking the old app password and pasting a new one.",
        True,
    ),
    (
        "mastodon_instance_url",
        "",
        "social",
        "GH-36: Full Mastodon instance URL, e.g. 'https://mastodon.social'. "
        "Empty = Mastodon distribution skipped.",
        False,
    ),
    (
        "mastodon_access_token",
        "",
        "social",
        "GH-36: Mastodon access token with 'write:statuses' scope. Create via "
        "Preferences > Development > New Application on your instance. Rotate "
        "by revoking the application there and re-seeding this row.",
        True,
    ),
    (
        "social_distribution_platforms",
        "",
        "social",
        "GH-36: Comma-separated list of platforms social_poster should push "
        "to after a successful publish. Valid values: 'bluesky,mastodon'. "
        "Leave empty to disable direct publishing entirely (Telegram/Discord "
        "notifications still fire). 'linkedin', 'reddit', 'youtube' are "
        "stubs awaiting OAuth — see GH-40.",
        False,
    ),
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
                "Table 'app_settings' missing — skipping migration 0076"
            )
            return
        for key, value, category, description, is_secret in _SEED_ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings (
                    key, value, category, description, is_secret
                )
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description, is_secret,
            )
        logger.info(
            "Migration 0076: seeded %d direct-social-API settings "
            "(if not already set)",
            len(_SEED_ROWS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        keys = [r[0] for r in _SEED_ROWS]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1)", keys,
        )
        logger.info(
            "Migration 0076 rolled back: removed %d direct-social-API settings",
            len(keys),
        )
