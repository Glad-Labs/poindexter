"""
Telegram configuration — single source of truth.

All services that send Telegram messages import from here rather than
hardcoding the chat ID or using inconsistent env-var names.

site_config.get() checks the DB first, then falls back to env vars
automatically -- no need for explicit os.getenv() fallbacks.
"""

from services.site_config import site_config

# Resolved once at import time via site_config (DB -> env var -> default).
# Legacy OPENCLAW_TELEGRAM_* env vars are handled by site_config aliases.
TELEGRAM_CHAT_ID: str = site_config.get("telegram_chat_id", "5318613610")

TELEGRAM_BOT_TOKEN: str = site_config.get("telegram_bot_token", "")
