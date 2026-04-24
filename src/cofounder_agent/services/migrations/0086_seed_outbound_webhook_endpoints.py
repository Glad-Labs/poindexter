"""Migration 0086: Seed outbound ``webhook_endpoints`` rows.

Phase 1b of the Declarative Data Plane RFC. One row per outbound
destination currently wired by hand in the codebase:

- ``discord_ops``   → ``outbound.discord_post``
- ``telegram_ops``  → ``outbound.telegram_post``
- ``vercel_isr``    → ``outbound.vercel_isr``

All rows ``enabled=false``. The URLs and secret refs are seeded
optimistically — if the corresponding app_settings rows aren't set,
the handler will raise at dispatch time with a clear message.
Existing direct-call sites in task_executor / revalidation_service
continue working unchanged until they're migrated to ``deliver()`` in
a follow-up.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_DISCORD_URL_PLACEHOLDER = "https://discord.com/api/webhooks/REPLACE/REPLACE"
_TELEGRAM_API_BASE = "https://api.telegram.org"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # Discord — URL comes from app_settings.discord_ops_webhook_url
        # rather than the row (because it's already operator-provided there).
        # We stash the app_settings key in metadata so a follow-up migration
        # can hoist it into row.url if/when we centralize on the row.
        discord_url = (
            await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'discord_ops_webhook_url'"
            )
            or _DISCORD_URL_PLACEHOLDER
        )

        await conn.execute(
            """
            INSERT INTO webhook_endpoints
                (name, direction, handler_name, url, signing_algorithm,
                 enabled, metadata)
            VALUES ($1, 'outbound', 'discord_post', $2, 'none', FALSE, $3::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            "discord_ops",
            discord_url,
            (
                '{"description": "Discord #ops channel webhook — operator notifications",'
                ' "source_setting": "discord_ops_webhook_url"}'
            ),
        )

        # Telegram — Bot API base. chat_id lives in config JSONB so
        # operators with multiple chats (one per env, per niche, etc.)
        # can add additional rows pointing at the same base URL.
        chat_id = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'telegram_chat_id'"
        )
        await conn.execute(
            """
            INSERT INTO webhook_endpoints
                (name, direction, handler_name, url, signing_algorithm,
                 secret_key_ref, enabled, config, metadata)
            VALUES ($1, 'outbound', 'telegram_post', $2, 'bearer',
                    'telegram_bot_token', FALSE, $3::jsonb, $4::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            "telegram_ops",
            _TELEGRAM_API_BASE,
            f'{{"chat_id": {repr(chat_id) if chat_id else "null"}}}',
            '{"description": "Telegram Bot API sendMessage to the operator chat"}',
        )

        # Vercel ISR — url should point at the site base (handler appends
        # /api/revalidate). Pull from existing site URL settings if present.
        site_url = (
            await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'public_site_url'"
            )
            or await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'site_url'"
            )
            or ""
        )
        await conn.execute(
            """
            INSERT INTO webhook_endpoints
                (name, direction, handler_name, url, signing_algorithm,
                 secret_key_ref, enabled, config, metadata)
            VALUES ($1, 'outbound', 'vercel_isr', $2, 'none',
                    'revalidate_secret', FALSE, $3::jsonb, $4::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            "vercel_isr",
            site_url,
            (
                '{"default_paths": ["/", "/archive"],'
                ' "default_tags": ["posts", "post-index"],'
                ' "timeout_seconds": 10}'
            ),
            '{"description": "Next.js ISR cache revalidation on publish"}',
        )

        logger.info("0086: seeded 3 outbound webhook endpoints (all disabled)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM webhook_endpoints "
            "WHERE name IN ('discord_ops', 'telegram_ops', 'vercel_isr')"
        )
        logger.info("0086: removed outbound webhook endpoints")
