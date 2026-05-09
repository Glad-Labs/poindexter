"""
Telegram configuration — single source of truth.

All services that send Telegram messages import from here rather than
hardcoding the chat ID or using inconsistent env-var names.

Both values come from site_config (DB → env var fallback). No hardcoded
defaults: a missing chat_id means no operator has configured Telegram
yet, and the caller must decide what to do (log-and-skip vs fail loud).

History: until 2026-05-01 this module exposed `TELEGRAM_BOT_TOKEN` as a
module-level constant captured at import time. After the secrets-flip
(GH-107) marked `telegram_bot_token` as `is_secret=true`, sync
`site_config.get(...)` started returning the encrypted ciphertext for
that key — and any HTTP call using the captured value silently 401'd
against the Telegram bot API. Same #325 bug class as the auth /
webhook / newsletter / redis fixes.

Callers should now use `get_telegram_bot_token()` (async, decrypted)
and `get_telegram_chat_id()` (sync, plaintext key). The legacy
`TELEGRAM_BOT_TOKEN` module attribute is kept as an empty string for
back-compat with imports that haven't migrated yet — it will fail
loud at HTTP send time rather than silently posting against a bad
URL, which is the right failure mode.
"""

from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# set_site_config() for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


# Legacy back-compat shims. Empty so any caller still using these will
# notice (HTTP error) instead of silently posting against ciphertext.
# TELEGRAM_CHAT_ID is intentionally not pre-populated at import time —
# the singleton was empty during the old import path anyway, so keeping
# it empty preserves prior behaviour.
TELEGRAM_CHAT_ID: str = ""
TELEGRAM_BOT_TOKEN: str = ""  # encrypted; use get_telegram_bot_token() instead


def get_telegram_chat_id() -> str:
    """Plaintext chat_id from site_config (not is_secret)."""
    return site_config.get("telegram_chat_id", "")


async def get_telegram_bot_token() -> str:
    """Decrypted bot token. is_secret=true so MUST be fetched via get_secret."""
    return await site_config.get_secret("telegram_bot_token", "")


async def telegram_configured() -> bool:
    """True iff both chat_id and bot_token are set."""
    return bool(get_telegram_chat_id() and await get_telegram_bot_token())
