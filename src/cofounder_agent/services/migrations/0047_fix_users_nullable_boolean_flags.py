"""
Migration 0047: Fix nullable boolean flags on the users table.

users.is_active, users.is_locked, and users.totp_enabled are currently
nullable with no default value.  A NULL boolean is treated as falsy by
Python but is neither TRUE nor FALSE in SQL, causing:

- Users with NULL is_active treated as inactive (never explicitly deactivated)
- WHERE NOT is_locked excludes NULL-locked users incorrectly
- failed_login_attempts + 1 produces NULL, silently preventing account lockout

Fix:
1. Backfill existing NULLs with safe defaults (is_active→TRUE, others→FALSE/0)
2. Add NOT NULL + DEFAULT constraints to all four columns
"""

SQL_UP = """
-- Backfill existing NULLs with safe defaults before adding NOT NULL
UPDATE users SET is_active             = TRUE  WHERE is_active             IS NULL;
UPDATE users SET is_locked             = FALSE WHERE is_locked             IS NULL;
UPDATE users SET totp_enabled          = FALSE WHERE totp_enabled          IS NULL;
UPDATE users SET failed_login_attempts = 0     WHERE failed_login_attempts IS NULL;

-- Apply NOT NULL and DEFAULT constraints
ALTER TABLE users
    ALTER COLUMN is_active             SET NOT NULL,
    ALTER COLUMN is_active             SET DEFAULT TRUE,
    ALTER COLUMN is_locked             SET NOT NULL,
    ALTER COLUMN is_locked             SET DEFAULT FALSE,
    ALTER COLUMN totp_enabled          SET NOT NULL,
    ALTER COLUMN totp_enabled          SET DEFAULT FALSE,
    ALTER COLUMN failed_login_attempts SET NOT NULL,
    ALTER COLUMN failed_login_attempts SET DEFAULT 0;
"""

SQL_DOWN = """
-- Remove constraints — columns revert to nullable without defaults
ALTER TABLE users
    ALTER COLUMN is_active             DROP NOT NULL,
    ALTER COLUMN is_active             DROP DEFAULT,
    ALTER COLUMN is_locked             DROP NOT NULL,
    ALTER COLUMN is_locked             DROP DEFAULT,
    ALTER COLUMN totp_enabled          DROP NOT NULL,
    ALTER COLUMN totp_enabled          DROP DEFAULT,
    ALTER COLUMN failed_login_attempts DROP NOT NULL,
    ALTER COLUMN failed_login_attempts DROP DEFAULT;
"""


async def run_migration(conn) -> None:
    """Apply migration 0047."""
    await conn.execute(SQL_UP)


async def rollback_migration(conn) -> None:
    """Roll back migration 0047."""
    await conn.execute(SQL_DOWN)
