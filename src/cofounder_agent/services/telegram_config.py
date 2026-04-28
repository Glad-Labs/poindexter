"""
Telegram configuration — single source of truth.

All services that send Telegram messages import from here rather than
hardcoding the chat ID or using inconsistent env-var names.

Both values come from site_config (DB -> env var). No hardcoded
defaults: a missing chat_id means no operator has configured Telegram
yet, and the caller must decide what to do (log-and-skip vs fail loud).

Post-Phase-H (GH#95): the module-level ``site_config`` singleton import
was removed. Callers pass a ``SiteConfig`` instance into the helpers
below (typically sourced from ``app.state.site_config``).

GH-107 / poindexter#156: ``telegram_bot_token`` is ``is_secret=true``
in app_settings, so ``get_telegram_bot_token`` and ``telegram_configured``
must use the async ``site_config.get_secret(...)`` helper. A sync
``site_config.get(...)`` would return the ``enc:v1:<ciphertext>`` blob
and the bot-API URL ``https://api.telegram.org/bot{token}/...`` would
404/401 every time. Both helpers are now ``async def``.
"""

from typing import Any


def get_telegram_chat_id(site_config: Any) -> str:
    """Return the configured Telegram chat ID, or ``""`` if unset.

    Chat ID is **not** a secret (``is_secret=false`` / unflagged in
    app_settings) so the sync ``.get(...)`` is correct here.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return site_config.get("telegram_chat_id", "")


async def get_telegram_bot_token(site_config: Any) -> str:
    """Return the configured Telegram bot token, or ``""`` if unset.

    Async because ``telegram_bot_token`` is ``is_secret=true`` in
    app_settings — must be decrypted via ``site_config.get_secret(...)``.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return await site_config.get_secret("telegram_bot_token", "")


async def telegram_configured(site_config: Any) -> bool:
    """True iff both chat_id and bot_token are set.

    Async because reading the bot token is async (see
    ``get_telegram_bot_token``).

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return bool(
        get_telegram_chat_id(site_config) and await get_telegram_bot_token(site_config)
    )
