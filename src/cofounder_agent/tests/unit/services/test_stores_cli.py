"""Unit tests for ``poindexter stores`` CLI (GH-113).

These tests exercise the click command surface without hitting a real
Postgres — the asyncpg ``connect()`` boundary is mocked. The goal is to
confirm round-trip behavior of every subcommand:

  - list    → SELECT and pretty-print
  - show    → SELECT one + masked credentials
  - enable  → UPDATE enabled = TRUE
  - disable → UPDATE enabled = FALSE
  - set-secret → resolve credentials_ref → encrypt → write

The ``plugins`` package is intentionally stubbed at the sys.modules level
so we don't drag in the full plugin runtime (apscheduler, etc.) for a
CLI surface test.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Stub plugins.secrets so the CLI's late import works without dragging
# in the full plugins/__init__.py side-effects (apscheduler, etc.).
# Tests that need to assert call args grab the AsyncMocks back via
# ``sys.modules['plugins.secrets'].<attr>`` from within the test.
# ---------------------------------------------------------------------------


def _install_secrets_stub(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    """Install a fake ``plugins.secrets`` module with two AsyncMock funcs.

    Returns ``(set_secret_mock, ensure_pgcrypto_mock)`` so each test can
    assert against them directly. Uses ``monkeypatch.setitem`` so the
    real ``plugins`` and ``plugins.secrets`` modules are restored at
    test teardown — without this, the fake ``plugins`` module (which
    has no ``registry`` attribute) leaks into later tests and breaks
    ``monkeypatch.setattr("plugins.registry.entry_points", ...)``.
    """
    set_secret_mock = AsyncMock(return_value=None)
    ensure_pgcrypto_mock = AsyncMock(return_value=None)
    fake_plugins = types.ModuleType("plugins")
    fake_secrets = types.ModuleType("plugins.secrets")
    fake_secrets.set_secret = set_secret_mock
    fake_secrets.ensure_pgcrypto = ensure_pgcrypto_mock
    monkeypatch.setitem(sys.modules, "plugins", fake_plugins)
    monkeypatch.setitem(sys.modules, "plugins.secrets", fake_secrets)
    return set_secret_mock, ensure_pgcrypto_mock


# ---------------------------------------------------------------------------
# Shared mock pool — asyncpg.connect returns one of these
# ---------------------------------------------------------------------------


def _mock_conn(
    *,
    fetch_rows: list[dict] | None = None,
    fetchrow_value: dict | None = None,
    fetchval_value: object = None,
    execute_status: str = "UPDATE 1",
) -> MagicMock:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_rows or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow_value)
    conn.fetchval = AsyncMock(return_value=fetchval_value)
    conn.execute = AsyncMock(return_value=execute_status)
    conn.close = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _ensure_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _patch_connect(conn: MagicMock):
    """Patch asyncpg.connect() inside the stores module to return ``conn``."""
    return patch(
        "poindexter.cli.stores._connect",
        new=AsyncMock(return_value=conn),
    )


# ---------------------------------------------------------------------------
# list / show / enable / disable
# ---------------------------------------------------------------------------


class TestStoresList:
    def test_empty(self, cli_runner):
        from poindexter.cli.stores import stores_group
        conn = _mock_conn(fetch_rows=[])
        with _patch_connect(conn):
            result = cli_runner.invoke(stores_group, ["list"])
        assert result.exit_code == 0
        assert "no object stores" in result.output.lower()

    def test_with_rows(self, cli_runner):
        from poindexter.cli.stores import stores_group
        conn = _mock_conn(fetch_rows=[
            {
                "name": "primary",
                "provider": "cloudflare_r2",
                "bucket": "media",
                "public_url": "https://pub.r2.dev",
                "enabled": True,
                "cache_busting_strategy": "none",
                "last_upload_at": None,
                "last_upload_status": None,
                "total_uploads": 0,
                "total_failures": 0,
                "last_error": None,
            },
        ])
        with _patch_connect(conn):
            result = cli_runner.invoke(stores_group, ["list"])
        assert result.exit_code == 0
        assert "primary" in result.output
        assert "cloudflare_r2" in result.output
        assert "media" in result.output


class TestStoresShow:
    def test_existing_store(self, cli_runner):
        from poindexter.cli.stores import stores_group
        # First fetchrow returns the store row, second returns the
        # credentials_ref lookup. asyncpg's AsyncMock side_effect is the
        # cleanest way to multi-return.
        store_row = {
            "name": "primary",
            "provider": "cloudflare_r2",
            "endpoint_url": "https://test.r2.dev",
            "bucket": "media",
            "public_url": "https://pub.r2.dev",
            "credentials_ref": "storage_credentials",
            "cache_busting_strategy": "none",
            "cache_busting_config": {},
            "enabled": True,
            "metadata": {},
            "last_upload_at": None,
            "last_upload_status": None,
            "last_error": None,
            "total_uploads": 0,
            "total_failures": 0,
            "total_bytes_uploaded": 0,
            "created_at": None,
            "updated_at": None,
        }
        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=[
            store_row,
            {"value": "enc:v1:abc"},  # credentials_ref pointer is set
        ])
        conn.close = AsyncMock(return_value=None)
        with _patch_connect(conn):
            result = cli_runner.invoke(stores_group, ["show", "primary"])
        assert result.exit_code == 0
        assert "primary" in result.output
        assert "cloudflare_r2" in result.output
        # Masked credentials line should appear, raw value should not.
        assert "encrypted" in result.output.lower()
        assert "enc:v1:abc" not in result.output

    def test_missing_store_exits_nonzero(self, cli_runner):
        from poindexter.cli.stores import stores_group
        conn = _mock_conn(fetchrow_value=None)
        with _patch_connect(conn):
            result = cli_runner.invoke(stores_group, ["show", "nonexistent"])
        assert result.exit_code != 0


class TestStoresEnableDisable:
    def test_enable_existing(self, cli_runner):
        from poindexter.cli.stores import stores_group
        conn = _mock_conn(execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(stores_group, ["enable", "primary"])
        assert result.exit_code == 0
        assert "enabled" in result.output

    def test_disable_existing(self, cli_runner):
        from poindexter.cli.stores import stores_group
        conn = _mock_conn(execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(stores_group, ["disable", "primary"])
        assert result.exit_code == 0
        assert "disabled" in result.output

    def test_enable_missing_exits_nonzero(self, cli_runner):
        from poindexter.cli.stores import stores_group
        conn = _mock_conn(execute_status="UPDATE 0")
        with _patch_connect(conn):
            result = cli_runner.invoke(stores_group, ["enable", "ghost"])
        assert result.exit_code != 0


class TestStoresSetSecret:
    def test_stdin_json_round_trip(self, cli_runner, monkeypatch):
        """``--from-stdin`` reads JSON, encrypts, writes to credentials_ref."""
        from poindexter.cli.stores import stores_group
        conn = MagicMock()
        # First fetchval returns the credentials_ref pointer
        conn.fetchval = AsyncMock(return_value="storage_credentials")
        conn.close = AsyncMock(return_value=None)

        async_set_secret, _ = _install_secrets_stub(monkeypatch)

        payload = json.dumps({
            "access_key": "AKIA...",
            "secret_key": "supersecret",
        })

        with _patch_connect(conn):
            result = cli_runner.invoke(
                stores_group,
                ["set-secret", "primary", "--from-stdin"],
                input=payload,
            )

        assert result.exit_code == 0, result.output
        assert "storage_credentials" in result.output
        # set_secret was called with the JSON blob — confirm by
        # inspecting the call args.
        async_set_secret.assert_awaited_once()
        call_args = async_set_secret.await_args
        # Positional: (conn, key, value_blob)
        assert call_args.args[1] == "storage_credentials"
        stored_blob = json.loads(call_args.args[2])
        assert stored_blob == {"access_key": "AKIA...", "secret_key": "supersecret"}

    def test_inline_args(self, cli_runner, monkeypatch):
        """``--access-key`` + ``--secret-key`` skip the prompts entirely."""
        from poindexter.cli.stores import stores_group
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value="storage_credentials")
        conn.close = AsyncMock(return_value=None)

        async_set_secret, _ = _install_secrets_stub(monkeypatch)

        with _patch_connect(conn):
            result = cli_runner.invoke(
                stores_group,
                [
                    "set-secret", "primary",
                    "--access-key", "k",
                    "--secret-key", "s",
                ],
            )

        assert result.exit_code == 0, result.output
        async_set_secret.assert_awaited_once()
        stored_blob = json.loads(async_set_secret.await_args.args[2])
        assert stored_blob == {"access_key": "k", "secret_key": "s"}

    def test_no_credentials_ref_refuses(self, cli_runner):
        """If the row has no credentials_ref, refuse rather than silently no-op."""
        from poindexter.cli.stores import stores_group
        conn = MagicMock()
        # credentials_ref is NULL on the row
        conn.fetchval = AsyncMock(return_value=None)
        conn.close = AsyncMock(return_value=None)

        with _patch_connect(conn):
            result = cli_runner.invoke(
                stores_group,
                ["set-secret", "primary", "--access-key", "k", "--secret-key", "s"],
            )

        assert result.exit_code != 0
        assert "credentials_ref" in result.output.lower() or "error" in result.output.lower()

    def test_stdin_invalid_json_errors(self, cli_runner):
        from poindexter.cli.stores import stores_group
        result = cli_runner.invoke(
            stores_group, ["set-secret", "primary", "--from-stdin"],
            input="not json",
        )
        assert result.exit_code != 0
        assert "json" in result.output.lower()
