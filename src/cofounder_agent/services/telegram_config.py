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
"""

from typing import Any


def get_telegram_chat_id(site_config: Any) -> str:
    """Return the configured Telegram chat ID, or ``""`` if unset.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return site_config.get("telegram_chat_id", "")


def get_telegram_bot_token(site_config: Any) -> str:
    """Return the configured Telegram bot token, or ``""`` if unset.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return site_config.get("telegram_bot_token", "")


def telegram_configured(site_config: Any) -> bool:
    """True iff both chat_id and bot_token are set.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return bool(
        get_telegram_chat_id(site_config) and get_telegram_bot_token(site_config)
    )
