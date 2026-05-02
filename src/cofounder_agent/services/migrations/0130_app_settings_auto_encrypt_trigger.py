"""Migration 0130: BEFORE-INSERT/UPDATE trigger that auto-encrypts is_secret rows.

Closes a footgun where editing an ``is_secret=true`` app_settings row
through pgAdmin (or any other DB tool, or a misbehaving service that
bypasses ``plugins.secrets.set_secret``) writes the value as plaintext.
The Grafana "Plaintext secrets (should be 0)" panel fires within
minutes; until someone notices, the secret sits in the clear.

This migration installs a ``BEFORE INSERT OR UPDATE`` trigger on
``app_settings`` that intercepts plaintext writes to ``is_secret=true``
rows and encrypts them in place using pgcrypto's ``pgp_sym_encrypt``,
producing the same ``enc:v1:<base64>`` shape ``plugins.secrets`` writes.

## Where the encryption key lives

pgcrypto needs a symmetric key. Application-side, that key is the
``POINDEXTER_SECRET_KEY`` env var (mirrored from
``~/.poindexter/bootstrap.toml``). Inside the database, the trigger
reads it from a Postgres GUC named ``poindexter.secret_key``.

The migration sets the GUC at the role level
(``ALTER ROLE poindexter SET poindexter.secret_key = '...'``) when
``POINDEXTER_SECRET_KEY`` is available in the migration runner's env.
That makes every session as the ``poindexter`` role inherit the key
automatically — including pgAdmin sessions, ``poindexter`` CLI calls,
and worker connections. Once set, the operator never needs to touch
the GUC again unless rotating the key.

## Behavior matrix

| Row state                                | Trigger action |
|------------------------------------------|----------------|
| is_secret=true, plaintext, GUC set       | encrypt → enc:v1:... |
| is_secret=true, plaintext, GUC unset     | leave plaintext (Grafana alert catches it) |
| is_secret=true, already enc:v1:          | pass through (no double-encrypt) |
| is_secret=true, empty string             | pass through (treated as "not set") |
| is_secret=false                          | pass through (not a secret) |
| Application-layer encryption (set_secret) | trigger sees enc:v1: → no-op |

## Why a trigger and not a CHECK constraint

A CHECK constraint can REJECT plaintext (raise an error), which would
break pgAdmin sessions that don't know to encrypt. A trigger is more
forgiving: it tries to fix the value, and only falls back to plaintext
when the GUC isn't available (in which case the existing alert path
still surfaces the problem). Errors during legitimate operator
workflows are a worse UX than a brief plaintext window.

## Idempotent

CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS + CREATE TRIGGER
means re-running this migration is safe.

## Down

down() drops the trigger and function — leaves rows untouched.
"""

from __future__ import annotations

import os

from services.logger_config import get_logger

logger = get_logger(__name__)


_TRIGGER_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION app_settings_auto_encrypt() RETURNS trigger AS $$
DECLARE
    enc_key TEXT;
BEGIN
    -- Only intervene on is_secret=true rows that are plaintext.
    IF NEW.is_secret IS NOT TRUE THEN
        RETURN NEW;
    END IF;
    IF NEW.value IS NULL OR NEW.value = '' THEN
        RETURN NEW;
    END IF;
    -- Already-encrypted values pass through unchanged. This keeps the
    -- application-layer path (plugins.secrets.set_secret) idempotent
    -- with the trigger — set_secret writes enc:v1:..., trigger sees it,
    -- skips re-encrypting.
    IF NEW.value LIKE 'enc:v1:%' THEN
        RETURN NEW;
    END IF;

    -- Try to read the encryption key from the session GUC. The fallback
    -- here covers two cases:
    --   1. GUC is unset entirely (current_setting raises) — second arg
    --      true returns NULL instead of raising.
    --   2. GUC is set to empty string — explicit IS NOT NULL AND <> ''
    --      check below.
    BEGIN
        enc_key := current_setting('poindexter.secret_key', true);
    EXCEPTION WHEN OTHERS THEN
        enc_key := NULL;
    END;

    IF enc_key IS NULL OR enc_key = '' THEN
        -- No key available — leave plaintext. The Grafana
        -- "Plaintext secrets" alert will catch it on next refresh.
        -- Operator can backfill via:
        --   ALTER ROLE poindexter SET poindexter.secret_key = '<key>';
        --   then re-update the row.
        RETURN NEW;
    END IF;

    NEW.value := 'enc:v1:' || encode(
        pgp_sym_encrypt(NEW.value, enc_key),
        'base64'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


_DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS app_settings_auto_encrypt_trigger ON app_settings;
"""


_CREATE_TRIGGER_SQL = """
CREATE TRIGGER app_settings_auto_encrypt_trigger
BEFORE INSERT OR UPDATE OF value ON app_settings
FOR EACH ROW
EXECUTE FUNCTION app_settings_auto_encrypt();
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # pgcrypto must be available — every existing migration that
        # uses pgp_sym_encrypt assumes this, but make it explicit here
        # so a fresh-DB smoke gets a clear error instead of a cryptic
        # "function pgp_sym_encrypt does not exist" mid-trigger.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

        await conn.execute(_TRIGGER_FUNCTION_SQL)
        await conn.execute(_DROP_TRIGGER_SQL)  # idempotent
        await conn.execute(_CREATE_TRIGGER_SQL)
        logger.info(
            "Migration 0130: created app_settings_auto_encrypt trigger"
        )

        # Opportunistically set the role-level GUC if the migration
        # runner has POINDEXTER_SECRET_KEY available. Skipped in CI
        # (where the env var is unset) — the trigger gracefully falls
        # back to leaving plaintext when the GUC isn't set.
        secret_key = os.getenv("POINDEXTER_SECRET_KEY")
        if secret_key:
            # ALTER ROLE doesn't accept parameter binding for the value
            # in plpgsql; we have to interpolate. Single-quote-escape
            # the key to be safe even though pgcrypto-format keys
            # shouldn't contain quotes.
            escaped = secret_key.replace("'", "''")
            # The role name is the connection role — the migration
            # runner runs as the same role the worker uses.
            role_row = await conn.fetchrow("SELECT current_user AS role")
            role_name = role_row["role"]
            await conn.execute(
                f"ALTER ROLE {role_name} SET poindexter.secret_key = '{escaped}'"
            )
            logger.info(
                "Migration 0130: set poindexter.secret_key GUC on role %s",
                role_name,
            )
        else:
            logger.info(
                "Migration 0130: POINDEXTER_SECRET_KEY env unset — trigger "
                "installed but GUC not seeded. Operator must run "
                "`ALTER ROLE poindexter SET poindexter.secret_key = '<key>'` "
                "manually for pgAdmin auto-encryption to work."
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DROP_TRIGGER_SQL)
        await conn.execute(
            "DROP FUNCTION IF EXISTS app_settings_auto_encrypt()"
        )
        # Don't unset the role GUC — the operator may have other things
        # depending on it, and the GUC alone is harmless.
        logger.info(
            "Migration 0130: dropped app_settings_auto_encrypt trigger + function"
        )
