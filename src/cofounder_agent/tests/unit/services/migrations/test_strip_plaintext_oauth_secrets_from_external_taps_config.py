"""Regression guard for the gsc_main / ga4_main plaintext-secret strip.

``external_taps.config`` is a plain (unencrypted) jsonb column. The
``gsc_main`` / ``ga4_main`` Singer-tap rows stored their Google OAuth
``client_secret`` (``oauth_client_secret`` on ga4_main) and ``refresh_token``
inline in ``config.tap_config`` — readable by any SQL session, pgAdmin
browse, or DB dump — even though the operator setup runbook
(``docs/integrations/setup-gsc-and-ga4.md``) already asks for the same
values to be stored properly encrypted in ``app_settings`` first
(Glad-Labs/poindexter#857).

This migration strips those plaintext fields out of ``tap_config`` and
replaces them with a ``config.secret_fields`` map that
``tap.singer_subprocess`` (``services/integrations/handlers/tap_singer_subprocess.py``)
resolves via ``site_config.get_secret()`` immediately before spawning the
tap. It reuses ``plugins.secrets.get_secret`` / ``set_secret`` — the
canonical encrypted-storage path — rather than hand-rolling pgcrypto calls,
and salvages a plaintext value into ``app_settings`` only if that key isn't
already set there (the expected case is that it already is, per the
runbook's Step 3-before-Step-4 ordering).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "services" / "migrations"
_MIGRATION_FILE = (
    _MIGRATIONS_DIR
    / "20260714_064046_strip_plaintext_oauth_secrets_from_external_taps_config.py"
)


def _load_migration():
    """Import the timestamp-prefixed migration file the same way the real
    runner does (``services/migrations/__init__.py``) — its name isn't a
    valid Python identifier, so a plain ``import`` can't reach it."""
    spec = importlib.util.spec_from_file_location(_MIGRATION_FILE.stem, _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _row(row_id: int, name: str, config: dict[str, Any] | str) -> dict[str, Any]:
    return {"id": row_id, "name": name, "config": config}


class _FakeAcquire:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakePool:
    """``pool.acquire()`` hands back the single fake conn every time —
    matches how ``pool.acquire()`` is used as a short-lived context
    manager per query in the real migration."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def _mock_conn(taps_rows: list[dict[str, Any]], existing_secrets: dict[str, str] | None = None) -> MagicMock:
    """Fake conn wired for both the migration's own SQL and the
    ``plugins.secrets`` calls it makes internally.

    ``existing_secrets`` seeds ``app_settings`` rows that already hold a
    (fake-encrypted) value — ``get_secret`` should return them without the
    migration ever calling ``set_secret`` for that key.
    """
    existing_secrets = existing_secrets or {}
    conn = MagicMock()

    async def fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM external_taps" in sql:
            return list(taps_rows)
        raise AssertionError(f"unexpected fetch: {sql!r}")

    async def fetchrow(sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM app_settings" in sql:
            key = args[0]
            if key in existing_secrets:
                return {"value": f"enc:v1:{existing_secrets[key]}", "is_secret": True}
            return None
        raise AssertionError(f"unexpected fetchrow: {sql!r}")

    async def fetchval(sql: str, *args: Any) -> str:
        # Fake "encryption": pgp_sym_encrypt/decrypt just pass the value
        # through unchanged (args[0] is the plaintext or the stripped
        # ciphertext respectively) — the real pgcrypto round-trip is
        # covered by tests/integration/test_plugin_secrets.py.
        if "pgp_sym_encrypt" in sql or "pgp_sym_decrypt" in sql:
            return args[0]
        raise AssertionError(f"unexpected fetchval: {sql!r}")

    conn.fetch = AsyncMock(side_effect=fetch)
    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    conn.fetchval = AsyncMock(side_effect=fetchval)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    return conn


def _external_taps_update(conn: MagicMock, row_id: int) -> dict[str, Any]:
    """The decoded ``config`` argument of the ``UPDATE external_taps``
    call for ``row_id``, or raise if none was issued."""
    for call in conn.execute.await_args_list:
        sql = call.args[0] if call.args else ""
        if "UPDATE external_taps" in sql and call.args[1] == row_id:
            return json.loads(call.args[2])
    raise AssertionError(f"no UPDATE external_taps issued for row id={row_id}")


def _app_settings_upserts(conn: MagicMock) -> list[str]:
    """Keys that ``set_secret`` wrote via the ``INSERT INTO app_settings``
    upsert (i.e. plaintext secrets that got salvaged)."""
    keys = []
    for call in conn.execute.await_args_list:
        sql = call.args[0] if call.args else ""
        if "INSERT INTO app_settings" in sql:
            keys.append(call.args[1])
    return keys


@pytest.mark.unit
class TestStripPlaintextOauthSecrets:
    async def test_gsc_row_strips_secrets_and_sets_secret_fields_map(self, monkeypatch):
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        migration = _load_migration()
        rows = [
            _row(1, "gsc_main", {
                "command": "tap-google-search-console",
                "tap_config": {
                    "client_id": "not-secret-id",
                    "client_secret": "plaintext-secret",
                    "refresh_token": "plaintext-refresh",
                    "site_urls": ["https://example.com"],
                },
            }),
        ]
        conn = _mock_conn(rows, existing_secrets={
            "google_oauth_client_secret": "plaintext-secret",
            "google_oauth_refresh_token": "plaintext-refresh",
        })
        pool = _FakePool(conn)

        await migration.up(pool)

        updated = _external_taps_update(conn, 1)
        assert "client_secret" not in updated["tap_config"]
        assert "refresh_token" not in updated["tap_config"]
        assert updated["tap_config"]["client_id"] == "not-secret-id"
        assert updated["tap_config"]["site_urls"] == ["https://example.com"]
        assert updated["secret_fields"] == {
            "client_secret": "google_oauth_client_secret",
            "refresh_token": "google_oauth_refresh_token",
        }

    async def test_ga4_row_strips_secrets_and_sets_secret_fields_map(self, monkeypatch):
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        migration = _load_migration()
        rows = [
            _row(2, "ga4_main", {
                "command": "tap-ga4",
                "tap_config": {
                    "oauth_client_id": "not-secret-id",
                    "oauth_client_secret": "plaintext-secret",
                    "refresh_token": "plaintext-refresh",
                    "property_id": "123456789",
                },
            }),
        ]
        conn = _mock_conn(rows, existing_secrets={
            "google_oauth_client_secret": "plaintext-secret",
            "google_oauth_refresh_token": "plaintext-refresh",
        })
        pool = _FakePool(conn)

        await migration.up(pool)

        updated = _external_taps_update(conn, 2)
        assert "oauth_client_secret" not in updated["tap_config"]
        assert "refresh_token" not in updated["tap_config"]
        assert updated["tap_config"]["oauth_client_id"] == "not-secret-id"
        assert updated["tap_config"]["property_id"] == "123456789"
        assert updated["secret_fields"] == {
            "oauth_client_secret": "google_oauth_client_secret",
            "refresh_token": "google_oauth_refresh_token",
        }

    async def test_existing_app_settings_secret_is_not_overwritten(self, monkeypatch):
        """The expected live-prod case: Step 3 of the runbook already set
        the app_settings secret before Step 4 duplicated it into
        tap_config. The migration must not re-write it."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        migration = _load_migration()
        rows = [
            _row(1, "gsc_main", {
                "tap_config": {"client_secret": "stale-duplicate", "refresh_token": "stale-refresh"},
            }),
        ]
        conn = _mock_conn(rows, existing_secrets={
            "google_oauth_client_secret": "already-set",
            "google_oauth_refresh_token": "already-set-too",
        })
        pool = _FakePool(conn)

        await migration.up(pool)

        assert _app_settings_upserts(conn) == []

    async def test_missing_app_settings_secret_is_salvaged_from_plaintext(self, monkeypatch):
        """Defensive path: if app_settings doesn't have the secret yet,
        salvage it from the plaintext tap_config value before stripping —
        never silently lose the only copy of a live credential."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        migration = _load_migration()
        rows = [
            _row(1, "gsc_main", {
                "tap_config": {"client_secret": "salvage-me", "refresh_token": "salvage-me-too"},
            }),
        ]
        conn = _mock_conn(rows, existing_secrets={})
        pool = _FakePool(conn)

        await migration.up(pool)

        salvaged = _app_settings_upserts(conn)
        assert "google_oauth_client_secret" in salvaged
        assert "google_oauth_refresh_token" in salvaged

    async def test_rows_without_plaintext_secrets_are_untouched(self, monkeypatch):
        """Idempotency: a row already migrated (secret_fields set, plaintext
        fields already gone) triggers no UPDATE at all on a re-run."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        migration = _load_migration()
        rows = [
            _row(1, "gsc_main", {
                "tap_config": {"client_id": "not-secret-id"},
                "secret_fields": {
                    "client_secret": "google_oauth_client_secret",
                    "refresh_token": "google_oauth_refresh_token",
                },
            }),
        ]
        conn = _mock_conn(rows, existing_secrets={
            "google_oauth_client_secret": "already-set",
            "google_oauth_refresh_token": "already-set-too",
        })
        pool = _FakePool(conn)

        await migration.up(pool)

        update_calls = [
            call for call in conn.execute.await_args_list
            if call.args and "UPDATE external_taps" in call.args[0]
        ]
        assert update_calls == []

    async def test_handles_string_encoded_config_column(self, monkeypatch):
        """asyncpg returns jsonb as a raw string unless a codec is
        registered on the pool — the migration must parse it either way,
        mirroring the same guard in tap_singer_subprocess.py."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        migration = _load_migration()
        rows = [
            _row(1, "gsc_main", json.dumps({
                "tap_config": {"client_id": "id", "client_secret": "plaintext-secret", "refresh_token": "plaintext-refresh"},
            })),
        ]
        conn = _mock_conn(rows, existing_secrets={
            "google_oauth_client_secret": "plaintext-secret",
            "google_oauth_refresh_token": "plaintext-refresh",
        })
        pool = _FakePool(conn)

        await migration.up(pool)

        updated = _external_taps_update(conn, 1)
        assert "client_secret" not in updated["tap_config"]
        assert updated["secret_fields"]["client_secret"] == "google_oauth_client_secret"
