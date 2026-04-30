"""Migration 0121: flip ``smtp_password`` to ``is_secret=true`` (encrypted at rest).

Closes Glad-Labs/poindexter#221 — the schema flip that
``newsletter_service.py`` is gated on. Until the row is marked
``is_secret=true``, ``site_config.load()`` happily caches the SMTP
password in the in-memory non-secret dict (see
``services/site_config.py:88`` — ``WHERE is_secret = false``), and the
sync ``site_config.get("smtp_password", "")`` call works. The moment
this migration lands the row drops out of the cache and callers MUST
go through ``await site_config.get_secret("smtp_password", "")``.
``newsletter_service`` is updated in the same commit.

## What this migration does

1. Set ``is_secret = true`` on the existing ``smtp_password`` row in
   ``app_settings`` (if a row exists). Categorises it under
   ``'secrets'`` for consistency with other ``set_secret``-managed rows.

2. If ``POINDEXTER_SECRET_KEY`` is set in the environment AND pgcrypto
   is available, encrypt the plaintext value in-place using
   ``plugins.secrets.set_secret`` so it lands in the canonical
   ``enc:v1:<base64>`` form. Otherwise, leave the value as plaintext
   and log a warning — ``plugins.secrets.migrate_plaintext_secrets``
   will pick it up on the next boot when the key IS available, and
   ``plugins.secrets.get_secret`` already handles the
   ``is_secret=true`` + plaintext interim state with a one-time
   warning per process.

This dual path is what makes the migration safe to run in CI's
``migrations_smoke`` (no ``POINDEXTER_SECRET_KEY``) and in production
(key present, immediate encryption).

## Idempotent

- The row may not exist (fresh install / SMTP never configured) — skip.
- The row may already be ``is_secret=true`` and already encrypted
  (re-run on a deployed system) — skip.
- The row may be ``is_secret=true`` but still plaintext (previous run
  flipped the flag without encrypting because the key was missing) —
  encrypt now if the key is available, otherwise no-op again.

Re-running the migration on any of those states is a no-op.

## Down

Down() is intentionally a no-op. Demoting an encrypted secret back to
a non-secret cached row is exactly the security regression this
migration exists to fix. If a roll-back is genuinely needed, an
operator can run ``poindexter settings demote-secret smtp_password``
manually — but the expected operational flow is roll-forward only.
"""

from __future__ import annotations

import os

from services.logger_config import get_logger

logger = get_logger(__name__)


_SMTP_KEY = "smtp_password"
_ENC_PREFIX = "enc:v1:"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = $1",
            _SMTP_KEY,
        )

        if row is None:
            logger.info(
                "0121: no '%s' row in app_settings (SMTP never configured) — nothing to flip",
                _SMTP_KEY,
            )
            return

        current_value = row["value"] or ""
        is_secret_now = bool(row["is_secret"])
        already_encrypted = current_value.startswith(_ENC_PREFIX)

        if is_secret_now and already_encrypted:
            logger.info(
                "0121: '%s' is already is_secret=true and encrypted — no-op",
                _SMTP_KEY,
            )
            return

        # Always flip the flag. We do this even when we can't encrypt
        # (no key) so the caching layer immediately stops handing out
        # the password via the sync get() path. plugins.secrets.get_secret
        # transparently handles the is_secret=true + plaintext interim.
        await conn.execute(
            """
            UPDATE app_settings
               SET is_secret = TRUE,
                   category = COALESCE(NULLIF(category, ''), 'secrets'),
                   updated_at = NOW()
             WHERE key = $1
            """,
            _SMTP_KEY,
        )
        logger.info(
            "0121: set is_secret=true on '%s' row (was is_secret=%s, encrypted=%s)",
            _SMTP_KEY, is_secret_now, already_encrypted,
        )

        # If the value is empty there is nothing to encrypt. The flag
        # flip is enough.
        if not current_value:
            logger.info(
                "0121: '%s' has empty value — flag flipped, no encryption needed",
                _SMTP_KEY,
            )
            return

        # If we already think it's encrypted (sentinel present) but the
        # is_secret flag was off, leave the ciphertext alone — flipping
        # the flag above is the right fix.
        if already_encrypted:
            logger.info(
                "0121: '%s' already had enc:v1: sentinel — flag flipped, ciphertext preserved",
                _SMTP_KEY,
            )
            return

        # We have plaintext. Try to encrypt it now if the symmetric key
        # is available; otherwise defer to plugins.secrets.migrate_plaintext_secrets
        # which the worker boot path runs.
        if not os.getenv("POINDEXTER_SECRET_KEY"):
            logger.warning(
                "0121: POINDEXTER_SECRET_KEY not set — '%s' left as plaintext "
                "with is_secret=true. plugins.secrets.migrate_plaintext_secrets "
                "will encrypt it on next worker boot when the key is available. "
                "site_config.get_secret() already handles this interim safely.",
                _SMTP_KEY,
            )
            return

        try:
            from plugins.secrets import ensure_pgcrypto, set_secret

            await ensure_pgcrypto(conn)
            await set_secret(
                conn,
                _SMTP_KEY,
                current_value,
                description="SMTP server password (encrypted at rest, see #221)",
            )
            logger.info(
                "0121: encrypted plaintext value of '%s' in-place (enc:v1:)",
                _SMTP_KEY,
            )
        except Exception as e:
            # Don't fail the whole migration if encryption hits a
            # transient issue — the flag flip is the load-bearing
            # change. plugins.secrets.migrate_plaintext_secrets will
            # retry on next boot.
            logger.warning(
                "0121: deferred encryption of '%s' to next boot "
                "(plugins.secrets.migrate_plaintext_secrets) — reason: %s",
                _SMTP_KEY, e,
            )


async def down(_pool) -> None:
    """No-op by design.

    Demoting ``smtp_password`` back to ``is_secret=false`` would put
    the cleartext password back into the in-memory ``site_config``
    cache and surface it via ``site_config.all()`` debug dumps —
    exactly the bug poindexter#221 closed. If a roll-back is genuinely
    required, the operator should run ``poindexter settings demote-secret
    smtp_password`` deliberately.
    """
    logger.info(
        "0121: down() is a no-op — refusing to demote '%s' back to non-secret",
        _SMTP_KEY,
    )
