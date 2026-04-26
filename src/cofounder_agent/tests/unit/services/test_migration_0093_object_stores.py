"""Unit tests for migration 0093 (object_stores table) — GH-113.

We don't spin up a real Postgres for unit tests. Instead we exercise:

  - SQL_UP / SQL_DOWN are well-formed strings (no obvious typos).
  - ``_seed_primary_from_storage_settings`` reads the right keys from
    a mocked connection and inserts the right values.
  - The migration uses idempotent constructs (CREATE ... IF NOT EXISTS,
    INSERT ... ON CONFLICT DO NOTHING).

Schema-validation tests against a real Postgres live in the integration
suite (``tests/integration/test_object_stores_db.py``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _import_migration():
    """Late-import so the migration discovery glob doesn't double-run it."""
    import importlib
    return importlib.import_module(
        "services.migrations.0093_create_object_stores_table"
    )


class TestMigrationSQL:
    """Static checks on the SQL — idempotency, expected columns."""

    def test_creates_table_idempotently(self):
        m = _import_migration()
        assert "CREATE TABLE IF NOT EXISTS object_stores" in m.SQL_UP

    def test_unique_index_on_name(self):
        m = _import_migration()
        # Spec mandates a unique index on name (operator addressing).
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_object_stores_name" in m.SQL_UP

    def test_enabled_index(self):
        m = _import_migration()
        # Operators query "list enabled stores" — index helps.
        assert "idx_object_stores_enabled" in m.SQL_UP

    def test_required_columns_present(self):
        """Every column the ticket called out must appear in the DDL."""
        m = _import_migration()
        required = [
            "name", "provider", "endpoint_url", "bucket", "public_url",
            "credentials_ref", "cache_busting_strategy", "enabled",
        ]
        for col in required:
            assert col in m.SQL_UP, f"missing column: {col}"

    def test_observability_columns_present(self):
        """Counters used by the Grafana per-store dashboards."""
        m = _import_migration()
        for col in (
            "last_upload_at", "last_upload_status", "total_uploads",
            "total_failures", "total_bytes_uploaded", "last_error",
        ):
            assert col in m.SQL_UP, f"missing observability column: {col}"

    def test_down_drops_table(self):
        m = _import_migration()
        assert "DROP TABLE IF EXISTS object_stores" in m.SQL_DOWN

    def test_touch_updated_at_trigger(self):
        m = _import_migration()
        assert "object_stores_touch_updated_at" in m.SQL_UP


class TestSeedPrimary:
    """``_seed_primary_from_storage_settings`` — values from app_settings."""

    @pytest.mark.asyncio
    async def test_seeds_from_storage_keys(self):
        """When storage_* are set, primary row picks them up."""
        m = _import_migration()
        conn = MagicMock()

        # Map of which app_settings keys exist with which values.
        app_settings = {
            "storage_provider": "aws_s3",
            "storage_endpoint": "https://s3.amazonaws.com",
            "storage_bucket": "my-prod-bucket",
            "storage_public_url": "https://cdn.example.com",
        }

        async def _fetchrow(sql, key):
            if key in app_settings:
                return {"value": app_settings[key]}
            return None

        conn.fetchrow = AsyncMock(side_effect=_fetchrow)
        conn.execute = AsyncMock(return_value=None)

        await m._seed_primary_from_storage_settings(conn)

        # Inspect the INSERT call
        conn.execute.assert_awaited_once()
        args = conn.execute.await_args.args
        # args[0] is the SQL string; args[1:] are the bound params in order:
        # name, provider, endpoint_url, bucket, public_url, credentials_ref,
        # cache_busting_strategy, enabled, metadata
        assert args[1] == "primary"
        assert args[2] == "aws_s3"
        assert args[3] == "https://s3.amazonaws.com"
        assert args[4] == "my-prod-bucket"
        assert args[5] == "https://cdn.example.com"
        assert args[6] == "storage_credentials"
        assert args[7] == "none"
        assert args[8] is True  # bucket present → enabled

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_cloudflare_keys(self):
        """Pre-#198 deployments still have cloudflare_r2_* — pick those up."""
        m = _import_migration()
        conn = MagicMock()

        app_settings = {
            "cloudflare_r2_endpoint": "https://acct.r2.cloudflarestorage.com",
            "cloudflare_r2_bucket": "legacy-bucket",
            "r2_public_url": "https://pub.r2.dev",
        }

        async def _fetchrow(sql, key):
            if key in app_settings:
                return {"value": app_settings[key]}
            return None

        conn.fetchrow = AsyncMock(side_effect=_fetchrow)
        conn.execute = AsyncMock(return_value=None)

        await m._seed_primary_from_storage_settings(conn)
        args = conn.execute.await_args.args
        # provider defaults to cloudflare_r2 when no setting is present
        assert args[2] == "cloudflare_r2"
        assert args[3] == "https://acct.r2.cloudflarestorage.com"
        assert args[4] == "legacy-bucket"
        assert args[5] == "https://pub.r2.dev"
        assert args[8] is True  # bucket present → enabled

    @pytest.mark.asyncio
    async def test_seeds_disabled_when_bucket_missing(self):
        """No bucket configured anywhere → row is created disabled."""
        m = _import_migration()
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value=None)

        await m._seed_primary_from_storage_settings(conn)
        args = conn.execute.await_args.args
        assert args[1] == "primary"
        assert args[2] == "cloudflare_r2"  # default
        assert args[4] == ""  # empty bucket (NOT None — defensive)
        assert args[8] is False  # disabled (no bucket)

    @pytest.mark.asyncio
    async def test_idempotent_insert(self):
        """The INSERT uses ON CONFLICT DO NOTHING so repeated runs are safe."""
        m = _import_migration()
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value=None)
        await m._seed_primary_from_storage_settings(conn)
        sql = conn.execute.await_args.args[0]
        assert "ON CONFLICT (name) DO NOTHING" in sql
