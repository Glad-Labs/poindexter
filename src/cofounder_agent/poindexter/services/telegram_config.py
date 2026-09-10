"""
Telegram configuration — single source of truth.

All services that send Telegram messages reach this class through the
``AppContainer`` (``container.telegram_config``) rather than hardcoding
the chat ID or using inconsistent env-var names.

Both values come from site_config (DB → env var fallback). No hardcoded
defaults: a missing chat_id means no operator has configured Telegram
yet, and the caller must decide what to do (log-and-skip vs fail loud).

History: until 2026-05-01 this module exposed ``TELEGRAM_BOT_TOKEN`` as a
module-level constant captured at import time. After the secrets-flip
(GH-107) marked ``telegram_bot_token`` as ``is_secret=true``, sync
``site_config.get(...)`` started returning the encrypted ciphertext for
that key — and any HTTP call using the captured value silently 401'd
against the Telegram bot API. Same #325 bug class as the auth /
webhook / newsletter / redis fixes.

The module then exposed free ``get_telegram_chat_id`` (sync) and
``get_telegram_bot_token`` (async, decrypted) functions reading from a
module-level lifespan-bound ``site_config`` singleton.

2026-05-28 — DI migration PR 3 (design doc
``docs/architecture/2026-05-28-site-config-di-migration.md``) converts
this module to a ``TelegramConfig`` class with constructor DI. The
module-level ``site_config`` singleton + ``set_site_config`` setter are
gone; callers reach the class via ``container.telegram_config``. The
legacy ``TELEGRAM_CHAT_ID`` / ``TELEGRAM_BOT_TOKEN`` empty-string
back-compat shims are also gone — any remaining importer of those names
would hit ``AttributeError`` at import time, which is the right
failure mode per ``feedback_no_silent_defaults``.
"""

from services.site_config import SiteConfig


class TelegramConfig:
    """Resolves Telegram bot credentials from a SiteConfig instance.

    Constructed by ``AppContainer.telegram_config`` per the SiteConfig
    constructor-DI migration. Holds no state of its own beyond the
    injected ``SiteConfig`` — every call reads through to the current
    in-memory cache (chat_id) or hits the DB (bot_token, is_secret).
    """

    def __init__(self, *, site_config: SiteConfig) -> None:
        self._site_config = site_config

    def get_telegram_chat_id(self) -> str:
        """Plaintext chat_id from site_config (not is_secret)."""
        return self._site_config.get("telegram_chat_id", "")

    async def get_telegram_bot_token(self) -> str:
        """Decrypted bot token. is_secret=true so MUST be fetched via get_secret."""
        return await self._site_config.get_secret("telegram_bot_token", "")

    async def telegram_configured(self) -> bool:
        """True iff both chat_id and bot_token are set."""
        return bool(self.get_telegram_chat_id() and await self.get_telegram_bot_token())
