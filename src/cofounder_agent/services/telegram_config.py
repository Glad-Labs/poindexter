"""
Telegram configuration — single source of truth.

All services that send Telegram messages import from here rather than
hardcoding the chat ID or using inconsistent env-var names.

The canonical env var is TELEGRAM_CHAT_ID; OPENCLAW_TELEGRAM_CHAT_ID is
accepted as an alias for backward compatibility.
"""

import os

# Resolved once at import time — check the preferred env var first, then
# the legacy OpenClaw-prefixed variant, then fall back to the default.
TELEGRAM_CHAT_ID: str = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("OPENCLAW_TELEGRAM_CHAT_ID")
    or "5318613610"
)

TELEGRAM_BOT_TOKEN: str = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("OPENCLAW_TELEGRAM_BOT_TOKEN")
    or ""
)
