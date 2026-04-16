"""
Telegram configuration — single source of truth.

All services that send Telegram messages import from here rather than
hardcoding the chat ID or using inconsistent env-var names.

Both values come from site_config (DB -> env var). No hardcoded
defaults: a missing chat_id means no operator has configured Telegram
yet, and the caller must decide what to do (log-and-skip vs fail loud).
"""

from services.site_config import site_config

TELEGRAM_CHAT_ID: str = site_config.get("telegram_chat_id", "")
TELEGRAM_BOT_TOKEN: str = site_config.get("telegram_bot_token", "")


def telegram_configured() -> bool:
    """True iff both chat_id and bot_token are set."""
    return bool(TELEGRAM_CHAT_ID and TELEGRAM_BOT_TOKEN)
