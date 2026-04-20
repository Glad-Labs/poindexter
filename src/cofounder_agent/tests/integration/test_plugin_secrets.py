"""Integration tests for plugins.secrets (pgcrypto).

Requires pgcrypto extension on the test Postgres. The harness tries to
install it via ``CREATE EXTENSION IF NOT EXISTS pgcrypto`` in the
fixture; if the extension binary isn't available, tests are skipped.
"""

from __future__ import annotations

import asyncpg
import pytest

from plugins.secrets import (
    SecretsError,
    demote_secret,
    ensure_pgcrypto,
    get_secret,
    is_encrypted,
    migrate_plaintext_secrets,
    rotate_key,
    set_secret,
)
from tests.integration.conftest import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


# Test-only encryption key — never used in real installs.
_TEST_KEY = "test-key-not-for-production-use-00000000"


@pytest.fixture
def secret_key_env(monkeypatch):
    """Set POINDEXTER_SECRET_KEY for the duration of a test."""
    monkeypatch.setenv("POINDEXTER_SECRET_KEY", _TEST_KEY)


@pytest.fixture
async def pgcrypto_ready(migrations_applied, clean_test_tables: asyncpg.Pool):
    """Install pgcrypto in the test DB; skip if extension binary missing."""
    async with clean_test_tables.acquire() as conn:
        try:
            await ensure_pgcrypto(conn)
        except SecretsError as e:
            pytest.skip(f"pgcrypto unavailable on this Postgres: {e}")
    return clean_test_tables


async def test_set_then_get_round_trip(pgcrypto_ready: asyncpg.Pool, secret_key_env):
    """set_secret + get_secret returns the original plaintext."""
    async with pgcrypto_ready.acquire() as conn:
        await set_secret(conn, "pexels_api_key", "sk-proj-abc123")
        val = await get_secret(conn, "pexels_api_key")

    assert val == "sk-proj-abc123"


async def test_stored_value_is_not_plaintext(
    pgcrypto_ready: asyncpg.Pool, secret_key_env
):
    """The raw DB value is ciphertext with the enc:v1: sentinel."""
    async with pgcrypto_ready.acquire() as conn:
        await set_secret(conn, "my_secret", "plaintext-value")
        row = await conn.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = 'my_secret'"
        )

    assert row["is_secret"] is True
    assert row["value"].startswith("enc:v1:")
    assert "plaintext-value" not in row["value"]
    assert is_encrypted(row["value"])


async def test_get_secret_missing_returns_none(
    pgcrypto_ready: asyncpg.Pool, secret_key_env
):
    """Absent key returns None, not an exception."""
    async with pgcrypto_ready.acquire() as conn:
        val = await get_secret(conn, "does_not_exist")
    assert val is None


async def test_get_secret_passes_plain_settings_through(
    pgcrypto_ready: asyncpg.Pool, secret_key_env
):
    """Rows with is_secret=false are returned verbatim, no decrypt attempt."""
    async with pgcrypto_ready.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, is_secret)
            VALUES ('site_name', 'My Site', 'identity', FALSE)
            """
        )
        val = await get_secret(conn, "site_name")

    assert val == "My Site"


async def test_set_secret_upserts(pgcrypto_ready: asyncpg.Pool, secret_key_env):
    """Two set_secret calls for the same key result in one row with the latest value."""
    async with pgcrypto_ready.acquire() as conn:
        await set_secret(conn, "api_key", "original-value")
        await set_secret(conn, "api_key", "rotated-value")

        count = await conn.fetchval(
            "SELECT COUNT(*) FROM app_settings WHERE key = 'api_key'"
        )
        val = await get_secret(conn, "api_key")

    assert count == 1
    assert val == "rotated-value"


async def test_migrate_plaintext_secrets(
    pgcrypto_ready: asyncpg.Pool, secret_key_env
):
    """Pre-existing is_secret=true plaintext rows get encrypted by migration.

    Idempotent: a second call is a no-op.
    """
    async with pgcrypto_ready.acquire() as conn:
        # Simulate pre-encryption state: is_secret=true but plaintext value.
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, is_secret)
            VALUES
              ('legacy_key_1', 'plaintext-1', 'secrets', TRUE),
              ('legacy_key_2', 'plaintext-2', 'secrets', TRUE)
            """
        )

        migrated = await migrate_plaintext_secrets(conn)
        assert migrated == 2

        # Values are now encrypted but decrypt to the originals.
        val1 = await get_secret(conn, "legacy_key_1")
        val2 = await get_secret(conn, "legacy_key_2")
        assert val1 == "plaintext-1"
        assert val2 == "plaintext-2"

        # Idempotent: second call finds nothing to migrate.
        migrated_again = await migrate_plaintext_secrets(conn)
        assert migrated_again == 0


async def test_demote_secret_returns_plaintext(
    pgcrypto_ready: asyncpg.Pool, secret_key_env
):
    """demote_secret stores plaintext and sets is_secret=false."""
    async with pgcrypto_ready.acquire() as conn:
        await set_secret(conn, "not_really_secret", "some-value")
        await demote_secret(conn, "not_really_secret")

        row = await conn.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = 'not_really_secret'"
        )

    assert row["is_secret"] is False
    assert row["value"] == "some-value"
    assert not is_encrypted(row["value"])


async def test_rotate_key_preserves_plaintext(
    pgcrypto_ready: asyncpg.Pool, secret_key_env, monkeypatch
):
    """After rotate_key, secrets decrypt correctly under the new key."""
    new_key = "new-test-key-1111111111111111"

    async with pgcrypto_ready.acquire() as conn:
        await set_secret(conn, "rotating_key_1", "value-1")
        await set_secret(conn, "rotating_key_2", "value-2")

        rotated = await rotate_key(conn, old_key=_TEST_KEY, new_key=new_key)
        assert rotated == 2

        # After rotation, env var must be the new key for get_secret to work.
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", new_key)

        val1 = await get_secret(conn, "rotating_key_1")
        val2 = await get_secret(conn, "rotating_key_2")
        assert val1 == "value-1"
        assert val2 == "value-2"


async def test_missing_key_env_var_raises(
    pgcrypto_ready: asyncpg.Pool, monkeypatch
):
    """Reading/writing secrets without POINDEXTER_SECRET_KEY set raises clearly."""
    monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)

    async with pgcrypto_ready.acquire() as conn:
        with pytest.raises(SecretsError, match="POINDEXTER_SECRET_KEY"):
            await set_secret(conn, "any_key", "any-value")


async def test_corrupted_ciphertext_raises(
    pgcrypto_ready: asyncpg.Pool, secret_key_env
):
    """Tampered ciphertext surfaces as SecretsError, not a silent wrong value."""
    async with pgcrypto_ready.acquire() as conn:
        await set_secret(conn, "will_be_corrupted", "original")
        # Corrupt the stored value.
        await conn.execute(
            "UPDATE app_settings SET value = 'enc:v1:garbage-base64-xxxxx' "
            "WHERE key = 'will_be_corrupted'"
        )

        with pytest.raises(SecretsError, match="corrupted"):
            await get_secret(conn, "will_be_corrupted")
