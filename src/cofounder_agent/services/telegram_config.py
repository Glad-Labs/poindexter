"""
Telegram configuration — single source of truth.

All services that send Telegram messages import from here rather than
hardcoding the chat ID or using inconsistent env-var names.

The canonical env var is TELEGRAM_CHAT_ID; OPENCLAW_TELEGRAM_CHAT_ID is
accepted as an alias for backward compatibility.
"""

import os

# Resolved once at import time — check DB config first, then the preferred
# env var, then the legacy OpenClaw-prefixed variant, then fall back to default.
try:
    from services.site_config import site_config
    _db_chat_id = site_config.get("telegram_chat_id")
    _db_bot_token = site_config.get("telegram_bot_token")
except Exception:
    _db_chat_id = None
    _db_bot_token = None

TELEGRAM_CHAT_ID: str = (
    _db_chat_id
    or os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("OPENCLAW_TELEGRAM_CHAT_ID")
    or "5318613610"
)

TELEGRAM_BOT_TOKEN: str = (
    _db_bot_token
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("OPENCLAW_TELEGRAM_BOT_TOKEN")
    or ""
)
