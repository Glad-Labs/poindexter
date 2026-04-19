"""plugins.secrets — transparent encryption-at-rest for app_settings secrets.

Per Matt's 2026-04-19 decision, rows in ``app_settings`` with
``is_secret=true`` must be encrypted at rest. We use pgcrypto (Postgres
extension) with symmetric encryption keyed by a secret in
``bootstrap.toml``. Plugins don't see the cipher — they call
:func:`get_secret` / :func:`set_secret` and get plaintext.

## Encryption scheme

- **Algorithm:** pgcrypto's ``pgp_sym_encrypt`` / ``pgp_sym_decrypt``
  (PGP symmetric; AES-256 under the hood with randomized IV).
- **Storage format:** the ciphertext is base64-encoded and prefixed
  with ``enc:v1:``. That prefix is the sentinel that lets us tell
  whether a row is already encrypted. Preserves the ``value TEXT``
  column type (no schema change needed for existing app_settings).
- **Key:** read from env var ``POINDEXTER_SECRET_KEY``. Bootstrap
  generates it on first run and writes it to ``~/.poindexter/bootstrap.toml``
  alongside ``DATABASE_URL``. Both are the chicken-and-egg secrets —
  not in DB.
- **Rotation:** call :func:`rotate_key` with old_key + new_key. Decrypts
  every secret with old, re-encrypts with new. Matt-operated, not
  runtime-automatic.

## What's secret vs. plain

The existing ``is_secret`` boolean column on ``app_settings`` is
authoritative. When :func:`set_secret` is called, it writes encrypted
and sets ``is_secret=true``. When :func:`get_secret` reads, it
decrypts if ``is_secret=true``, returns verbatim otherwise. Existing
non-secret rows are unaffected.

## Migration from plaintext

:func:`migrate_plaintext_secrets` runs once on boot: for every
``is_secret=true`` row whose value does NOT start with the ``enc:v1:``
sentinel, re-store it encrypted. Idempotent; safe to call on every
worker boot.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# Sentinel prefix on encrypted values so we can tell them apart from
# plaintext without a schema change. Any new cipher version (e.g. we
# want to swap to a different algorithm) bumps the prefix: enc:v2:.
_ENC_PREFIX = "enc:v1:"

# Env var the helper reads for the symmetric key. Bootstrap.toml writes
# this alongside DATABASE_URL. Both are chicken-and-egg: you need them
# to reach the DB in the first place, so they live outside the DB.
_KEY_ENV = "POINDEXTER_SECRET_KEY"


class SecretsError(Exception):
    """Raised when the helper can't encrypt/decrypt — bad key, missing
    extension, corrupted ciphertext."""


def _key() -> str:
    """Read the symmetric key from env. Raises if unset."""
    k = os.getenv(_KEY_ENV)
    if not k:
        raise SecretsError(
            f"{_KEY_ENV} env var is required for encrypted secrets. "
            "Bootstrap.toml should set it; regenerate with `poindexter setup --rotate-secrets` "
            "if missing."
        )
    return k


async def ensure_pgcrypto(conn: Any) -> None:
    """Install pgcrypto extension if not already. Idempotent."""
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    except Exception as e:
        raise SecretsError(
            f"Could not install pgcrypto extension: {e}. "
            "Postgres superuser required; or install the extension manually and re-run."
        ) from e


def is_encrypted(value: str | None) -> bool:
    """True if ``value`` has already been through :func:`set_secret`."""
    return bool(value) and value.startswith(_ENC_PREFIX)


async def get_secret(conn: Any, key: str) -> str | None:
    """Read an app_settings row, decrypting if ``is_secret=true``.

    Returns:
        The plaintext value, or ``None`` if the row doesn't exist.

        For ``is_secret=false`` rows, returns the value verbatim
        (backward-compat — plain settings keep working without changes).

        For ``is_secret=true`` rows that happen to NOT be encrypted yet
        (pre-migration state), returns the plaintext. Log a warning.
    """
    row = await conn.fetchrow(
        "SELECT value, is_secret FROM app_settings WHERE key = $1", key
    )
    if not row:
        return None

    if not row["is_secret"]:
        return row["value"]

    value = row["value"]
    if not value:
        return ""

    if not is_encrypted(value):
        logger.warning(
            "get_secret: key=%r has is_secret=true but no encryption sentinel; "
            "returning plaintext. Run migrate_plaintext_secrets to fix.",
            key,
        )
        return value

    stripped = value[len(_ENC_PREFIX):]
    try:
        plaintext = await conn.fetchval(
            "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
            stripped,
            _key(),
        )
    except Exception as e:
        raise SecretsError(
            f"Could not decrypt {key!r}. "
            f"Key mismatch or corrupted ciphertext: {e}"
        ) from e
    return plaintext


async def set_secret(conn: Any, key: str, value: str, description: str = "") -> None:
    """Write an encrypted secret into ``app_settings``.

    Upserts with ``is_secret=true``. Always encrypts, regardless of
    whether the row already existed as a plain setting — that's the
    safe default; demotion from secret → plain is explicit via
    :func:`demote_secret`.
    """
    ciphertext = await conn.fetchval(
        "SELECT encode(pgp_sym_encrypt($1, $2), 'base64')",
        value,
        _key(),
    )
    stored = f"{_ENC_PREFIX}{ciphertext}"
    await conn.execute(
        """
        INSERT INTO app_settings (key, value, category, description, is_secret)
        VALUES ($1, $2, 'secrets', $3, TRUE)
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                is_secret = TRUE,
                description = COALESCE(NULLIF(EXCLUDED.description, ''), app_settings.description),
                updated_at = NOW()
        """,
        key,
        stored,
        description,
    )


async def demote_secret(conn: Any, key: str) -> None:
    """Convert a secret row back to a plaintext setting.

    Rare — use when a value was mis-classified as secret (e.g. a URL
    someone accidentally flagged). Decrypts, stores plaintext, sets
    ``is_secret=false``.
    """
    plain = await get_secret(conn, key)
    if plain is None:
        return
    await conn.execute(
        """
        UPDATE app_settings
           SET value = $2,
               is_secret = FALSE,
               updated_at = NOW()
         WHERE key = $1
        """,
        key,
        plain,
    )


async def migrate_plaintext_secrets(conn: Any) -> int:
    """For every ``is_secret=true`` row whose value isn't encrypted yet,
    re-store it encrypted. Idempotent. Returns the number of rows
    migrated.

    Intended as a one-shot on first boot after pgcrypto lands. Safe to
    call on every subsequent boot — it's a no-op once all rows have
    the sentinel.
    """
    rows = await conn.fetch(
        "SELECT key, value FROM app_settings WHERE is_secret = TRUE"
    )
    migrated = 0
    for row in rows:
        val = row["value"]
        if not val or is_encrypted(val):
            continue
        await set_secret(conn, row["key"], val)
        migrated += 1
    if migrated:
        logger.info(
            "migrate_plaintext_secrets: encrypted %d previously-plaintext secret(s)",
            migrated,
        )
    return migrated


async def rotate_key(conn: Any, old_key: str, new_key: str) -> int:
    """Re-encrypt every secret with ``new_key`` instead of ``old_key``.

    Matt-operated rotation path. The env var is NOT updated by this
    function — caller must swap ``POINDEXTER_SECRET_KEY`` in
    bootstrap.toml before or after (depending on order) and handle the
    transition. Typical flow:

    1. Set ``POINDEXTER_SECRET_KEY=<old>`` (current state)
    2. Call ``rotate_key(conn, old_key=<old>, new_key=<new>)``
    3. Decrypts every secret with ``<old>``, re-encrypts with ``<new>``,
       stores result
    4. Write ``<new>`` to bootstrap.toml
    5. Restart worker; next ``get_secret`` reads the new env var and
       decrypts successfully

    Returns the number of rows rotated.
    """
    rows = await conn.fetch(
        "SELECT key, value FROM app_settings WHERE is_secret = TRUE"
    )
    rotated = 0
    for row in rows:
        val = row["value"]
        if not val or not is_encrypted(val):
            continue
        stripped = val[len(_ENC_PREFIX):]
        plaintext = await conn.fetchval(
            "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
            stripped, old_key,
        )
        new_ct = await conn.fetchval(
            "SELECT encode(pgp_sym_encrypt($1, $2), 'base64')",
            plaintext, new_key,
        )
        stored = f"{_ENC_PREFIX}{new_ct}"
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = NOW() WHERE key = $1",
            row["key"], stored,
        )
        rotated += 1
    if rotated:
        logger.info("rotate_key: re-encrypted %d secret(s) under new key", rotated)
    return rotated
