"""Unit tests for ``scripts/_grafana_webhook_token.py``.

The helper decrypts ``app_settings.grafana_webhook_oauth_jwt`` for
``scripts/start-stack.sh`` so it can be substituted into Grafana's
provisioning YAML via ``$GRAFANA_WEBHOOK_TOKEN`` (finding #2 from the
2026-05-19 jank-audit stress test).

Behavioral contract — these tests pin it so a future rewrite can't
silently degrade the fail-loud guarantees that ``feedback_no_silent_defaults``
requires:

1. Missing bootstrap.toml => stdout is empty, stderr warns, exit 0.
2. Missing ``database_url`` key => stdout empty, stderr warns, exit 0.
3. Missing ``poindexter_secret_key`` => stdout empty, stderr warns,
   exit 0.
4. DB connect failure => stdout empty, stderr warns, exit 0.
5. Row missing => stdout empty, stderr warns, exit 0.
6. Plaintext row (legacy) => printed verbatim.
7. ``enc:v1:...`` row => decrypted plaintext printed.

Every failure mode is exit 0 + empty stdout — start-stack.sh sources
the value into an env var and Grafana will boot with an empty Bearer
credential. Worker rejects with 401 and a clear log line — the
operator notices and runs ``poindexter auth mint-grafana-token --persist``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make ``scripts/`` importable like the rest of the script-test files
# in this dir do.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import _grafana_webhook_token as gwt  # noqa: E402

# ---------------------------------------------------------------------------
# _load_bootstrap
# ---------------------------------------------------------------------------


class TestLoadBootstrap:
    def test_returns_empty_when_file_missing(self, capsys, tmp_path):
        ghost = tmp_path / "nope.toml"
        with patch.object(gwt, "_BOOTSTRAP_PATH", ghost):
            data = gwt._load_bootstrap()
        assert data == {}
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_parses_toml_keys(self, capsys, tmp_path):
        bootstrap = tmp_path / "bootstrap.toml"
        bootstrap.write_text(
            'database_url = "postgresql://localhost/test"\n'
            'poindexter_secret_key = "fake-key"\n'
        )
        with patch.object(gwt, "_BOOTSTRAP_PATH", bootstrap):
            data = gwt._load_bootstrap()
        assert data["database_url"] == "postgresql://localhost/test"
        assert data["poindexter_secret_key"] == "fake-key"

    def test_strips_whitespace(self, capsys, tmp_path):
        bootstrap = tmp_path / "bootstrap.toml"
        bootstrap.write_text('database_url = "  postgresql://x  "\n')
        with patch.object(gwt, "_BOOTSTRAP_PATH", bootstrap):
            data = gwt._load_bootstrap()
        assert data["database_url"] == "postgresql://x"


# ---------------------------------------------------------------------------
# _decrypt — exercises every fail-soft path + the happy decrypt path
# ---------------------------------------------------------------------------


def _make_conn(*, row=None, fetchval_return: str = "", raise_on_query: bool = False):
    """asyncpg connection stub. ``row`` is what fetchrow returns;
    ``fetchval_return`` is what the pgp_sym_decrypt fetchval returns."""
    conn = MagicMock()
    if raise_on_query:
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.close = AsyncMock(return_value=None)
    return conn


class TestDecrypt:
    @pytest.mark.asyncio
    async def test_connect_failure_returns_empty(self, capsys):
        # asyncpg.connect raises — DB down or wrong DSN.
        fake_asyncpg = MagicMock()
        fake_asyncpg.connect = AsyncMock(side_effect=OSError("connection refused"))
        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            result = await gwt._decrypt("postgresql://nowhere", "key")
        assert result == ""
        captured = capsys.readouterr()
        assert "postgres connect failed" in captured.err

    @pytest.mark.asyncio
    async def test_row_missing_returns_empty(self, capsys):
        conn = _make_conn(row=None)
        fake_asyncpg = MagicMock()
        fake_asyncpg.connect = AsyncMock(return_value=conn)
        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            result = await gwt._decrypt("postgresql://x", "key")
        assert result == ""
        captured = capsys.readouterr()
        assert "not set" in captured.err
        assert "mint-grafana-token --persist" in captured.err
        conn.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_empty_value_returns_empty(self, capsys):
        conn = _make_conn(row={"value": "", "is_secret": True})
        fake_asyncpg = MagicMock()
        fake_asyncpg.connect = AsyncMock(return_value=conn)
        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            result = await gwt._decrypt("postgresql://x", "key")
        assert result == ""
        captured = capsys.readouterr()
        assert "is empty" in captured.err

    @pytest.mark.asyncio
    async def test_plaintext_row_returned_verbatim(self, capsys):
        # ``is_secret`` False or missing encryption prefix => plaintext path.
        conn = _make_conn(row={"value": "raw-jwt-value", "is_secret": False})
        fake_asyncpg = MagicMock()
        fake_asyncpg.connect = AsyncMock(return_value=conn)
        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            result = await gwt._decrypt("postgresql://x", "key")
        assert result == "raw-jwt-value"

    @pytest.mark.asyncio
    async def test_encrypted_row_decrypted(self, capsys):
        conn = _make_conn(
            row={"value": "enc:v1:base64payload", "is_secret": True},
            fetchval_return="decrypted-jwt-token",
        )
        fake_asyncpg = MagicMock()
        fake_asyncpg.connect = AsyncMock(return_value=conn)
        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            result = await gwt._decrypt("postgresql://x", "secret-key")
        assert result == "decrypted-jwt-token"
        # pgp_sym_decrypt was called with the stripped payload + key.
        args, _ = conn.fetchval.call_args
        # asyncpg.fetchval(query, *args) — we pass payload + key.
        assert "pgp_sym_decrypt" in args[0]
        assert args[1] == "base64payload"
        assert args[2] == "secret-key"

    @pytest.mark.asyncio
    async def test_decrypt_failure_returns_empty(self, capsys):
        conn = _make_conn(row={"value": "enc:v1:bad", "is_secret": True})
        conn.fetchval = AsyncMock(side_effect=RuntimeError("bad ciphertext"))
        fake_asyncpg = MagicMock()
        fake_asyncpg.connect = AsyncMock(return_value=conn)
        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            result = await gwt._decrypt("postgresql://x", "wrong-key")
        assert result == ""
        captured = capsys.readouterr()
        assert "pgcrypto decrypt failed" in captured.err


# ---------------------------------------------------------------------------
# main() — assembled behavior
# ---------------------------------------------------------------------------


class TestMain:
    def test_missing_bootstrap_emits_empty_stdout(self, capsys, tmp_path):
        ghost = tmp_path / "nope.toml"
        with patch.object(gwt, "_BOOTSTRAP_PATH", ghost):
            gwt.main()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "not found" in captured.err

    def test_missing_database_url_emits_empty_stdout(self, capsys, tmp_path):
        bootstrap = tmp_path / "bootstrap.toml"
        bootstrap.write_text('poindexter_secret_key = "k"\n')
        # Make sure DATABASE_URL env fallback is also absent.
        orig = os.environ.pop("DATABASE_URL", None)
        try:
            with patch.object(gwt, "_BOOTSTRAP_PATH", bootstrap):
                gwt.main()
        finally:
            if orig is not None:
                os.environ["DATABASE_URL"] = orig
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "database_url missing" in captured.err

    def test_missing_secret_key_emits_empty_stdout(self, capsys, tmp_path):
        bootstrap = tmp_path / "bootstrap.toml"
        bootstrap.write_text('database_url = "postgresql://x"\n')
        orig = os.environ.pop("POINDEXTER_SECRET_KEY", None)
        try:
            with patch.object(gwt, "_BOOTSTRAP_PATH", bootstrap):
                gwt.main()
        finally:
            if orig is not None:
                os.environ["POINDEXTER_SECRET_KEY"] = orig
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "poindexter_secret_key missing" in captured.err

    def test_full_happy_path_prints_decrypted_jwt(self, capsys, tmp_path):
        bootstrap = tmp_path / "bootstrap.toml"
        bootstrap.write_text(
            'database_url = "postgresql://localhost/test"\n'
            'poindexter_secret_key = "the-key"\n'
        )

        async def _fake_decrypt(dsn, key):
            assert dsn == "postgresql://localhost/test"
            assert key == "the-key"
            return "decrypted.jwt.contents"

        with patch.object(gwt, "_BOOTSTRAP_PATH", bootstrap), \
             patch.object(gwt, "_decrypt", side_effect=_fake_decrypt):
            gwt.main()
        captured = capsys.readouterr()
        # Exact stdout match — start-stack.sh captures with $() and we
        # must NOT emit a trailing newline.
        assert captured.out == "decrypted.jwt.contents"
