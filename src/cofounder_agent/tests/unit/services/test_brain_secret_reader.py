"""Unit tests for brain/secret_reader.py.

Covers the contract from Glad-Labs/poindexter#342 — the brain daemon
needs to decrypt ``is_secret=true`` ``app_settings`` rows before using
them in URLs/headers, otherwise pgcrypto's ``enc:v1:<base64>`` envelope
gets shoved verbatim into ``https://api.telegram.org/bot{token}/...``
and Python's http.client throws ``URL can't contain control characters``.

Required behaviours:

1. Plaintext rows (``is_secret=false``) come back verbatim — no decrypt
   round-trip.
2. Plaintext-but-marked-secret rows (legacy is_secret=true rows that
   were never re-encrypted) come back verbatim — the rotation script
   may not have processed them yet.
3. ``enc:v1:<base64>`` rows trigger ``pgp_sym_decrypt`` with the
   ``POINDEXTER_SECRET_KEY`` env var.
4. Missing rows return ``default``.
5. Empty values return ``default``.
6. Missing ``POINDEXTER_SECRET_KEY`` for an encrypted row returns
   ``default`` and logs a warning (no exception).
7. ``pgp_sym_decrypt`` raising returns ``default`` (no exception).
8. The SELECT itself failing returns ``default`` (no exception).

All DB I/O is mocked via AsyncMock — same pattern as
test_brain_alert_dispatcher.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Path-prelude: brain/ is a standalone package outside cofounder_agent.
# parents[5] = repo root that contains brain/.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import secret_reader as sr  # noqa: E402


def _row(value, is_secret):
    """Build the minimal asyncpg-row-shaped dict secret_reader reads."""
    return {"value": value, "is_secret": is_secret}


def _pool(*, fetchrow_return=None, fetchrow_raises=None,
          fetchval_return=None, fetchval_raises=None):
    """Build a pool with AsyncMock fetchrow + fetchval."""
    p = MagicMock()
    if fetchrow_raises is not None:
        p.fetchrow = AsyncMock(side_effect=fetchrow_raises)
    else:
        p.fetchrow = AsyncMock(return_value=fetchrow_return)
    if fetchval_raises is not None:
        p.fetchval = AsyncMock(side_effect=fetchval_raises)
    else:
        p.fetchval = AsyncMock(return_value=fetchval_return)
    return p


@pytest.mark.unit
@pytest.mark.asyncio
class TestReadAppSetting:
    """Pure unit coverage of read_app_setting — no real DB."""

    async def test_plaintext_row_returns_value_verbatim(self):
        """is_secret=false → value comes back unchanged, no fetchval call."""
        pool = _pool(fetchrow_return=_row("hello-world", False))
        result = await sr.read_app_setting(pool, "site_url")
        assert result == "hello-world"
        pool.fetchval.assert_not_awaited()

    async def test_legacy_secret_without_envelope_returns_verbatim(self):
        """is_secret=true but no ``enc:v1:`` prefix → return verbatim.

        Pre-migration rotation rows fall in this bucket. Returning the
        raw value matches the old behaviour callers expect.
        """
        pool = _pool(fetchrow_return=_row("plain-still", True))
        result = await sr.read_app_setting(pool, "telegram_bot_token")
        assert result == "plain-still"
        pool.fetchval.assert_not_awaited()

    async def test_encrypted_row_decrypts_via_pgcrypto(self, monkeypatch):
        """The headline fix — encrypted row + key set → decrypt round-trip."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "test-master-key")
        pool = _pool(
            fetchrow_return=_row("enc:v1:ZmFrZS1jaXBoZXItYmxvYg==", True),
            fetchval_return="real-bot-token-12345",
        )
        result = await sr.read_app_setting(pool, "telegram_bot_token")
        assert result == "real-bot-token-12345"

        # Verify the decrypt SQL was called with the right shape.
        assert pool.fetchval.await_count == 1
        sql, *args = pool.fetchval.await_args.args
        assert "pgp_sym_decrypt" in sql
        assert "decode($1, 'base64')" in sql
        # The "enc:v1:" prefix is stripped before decode.
        assert args[0] == "ZmFrZS1jaXBoZXItYmxvYg=="
        assert args[1] == "test-master-key"

    async def test_missing_row_returns_default(self):
        """Row not present → caller's default."""
        pool = _pool(fetchrow_return=None)
        result = await sr.read_app_setting(pool, "nope", default="fallback")
        assert result == "fallback"

    async def test_empty_value_returns_default(self):
        """Row present but value='' → default (avoid blanking working env)."""
        pool = _pool(fetchrow_return=_row("", False))
        result = await sr.read_app_setting(pool, "key", default="from-env")
        assert result == "from-env"

    async def test_encrypted_row_without_key_env_returns_default(
        self, monkeypatch, caplog,
    ):
        """No POINDEXTER_SECRET_KEY → can't decrypt → default + warning.

        This is the operator-misconfig case: the brain container shipped
        without the master key in env. We log loudly so it shows up in
        the brain log instead of a silent fallback.
        """
        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)
        pool = _pool(fetchrow_return=_row("enc:v1:ABC=", True))
        with caplog.at_level("WARNING"):
            result = await sr.read_app_setting(
                pool, "telegram_bot_token", default="bootstrap-fallback",
            )
        assert result == "bootstrap-fallback"
        # No decrypt attempted.
        pool.fetchval.assert_not_awaited()
        assert any(
            "POINDEXTER_SECRET_KEY" in rec.message for rec in caplog.records
        ), "expected a warning naming POINDEXTER_SECRET_KEY"

    async def test_decrypt_failure_returns_default_without_raising(
        self, monkeypatch, caplog,
    ):
        """pgp_sym_decrypt raising → default + warning, never exception."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "wrong-key")
        pool = _pool(
            fetchrow_return=_row("enc:v1:bad-cipher", True),
            fetchval_raises=Exception("Wrong key or corrupt data"),
        )
        with caplog.at_level("WARNING"):
            result = await sr.read_app_setting(pool, "key", default="x")
        assert result == "x"
        assert any(
            "decrypt" in rec.message.lower() for rec in caplog.records
        )

    async def test_select_failure_returns_default_without_raising(
        self, caplog,
    ):
        """SELECT raising (pool down, table missing, etc.) → default."""
        pool = _pool(fetchrow_raises=Exception("connection lost"))
        with caplog.at_level("WARNING"):
            result = await sr.read_app_setting(
                pool, "key", default="defaulted",
            )
        assert result == "defaulted"

    async def test_decrypt_returning_none_returns_default(self, monkeypatch):
        """If pgp_sym_decrypt returns NULL (nullable col, etc.), default."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "k")
        pool = _pool(
            fetchrow_return=_row("enc:v1:abc", True),
            fetchval_return=None,
        )
        result = await sr.read_app_setting(pool, "k", default="def")
        assert result == "def"


@pytest.mark.unit
@pytest.mark.asyncio
class TestEndToEndAlertDispatch:
    """End-to-end coverage of the #342 + #344 fixes: an alert_events row
    is polled, the brain notify path reads the encrypted
    telegram_bot_token from app_settings on every call (#344, no module
    globals), decrypts it (#342), and POSTs to the real Telegram URL —
    not the ``enc:v1:<base64>`` ciphertext.

    Mocks the HTTP layer (``urllib.request.urlopen``) and the DB
    (``asyncpg`` pool) but exercises the real ``brain_daemon.notify``
    plus the real ``brain.secret_reader.read_app_setting`` together.
    """

    async def test_full_path_decrypts_token_and_posts_to_real_telegram_url(
        self, monkeypatch,
    ):
        from brain import brain_daemon as bd

        # Master key for pgcrypto round-trip — the brain container env.
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "test-master-key")

        # Simulate the DB: send_telegram + send_discord each call
        # read_app_setting → fetchrow + fetchval. The encrypted-token
        # branch fires for telegram_bot_token; chat_id + webhook URLs
        # are plaintext.
        encrypted_row = {"value": "enc:v1:dGVzdC1ibG9i", "is_secret": True}
        chat_id_row = {"value": "123456789", "is_secret": False}
        empty_row = {"value": "", "is_secret": False}

        pool = MagicMock()
        # Lookup order inside notify():
        #   send_telegram → telegram_bot_token (enc), telegram_chat_id (plain)
        #   discord_ops_webhook_url (plain, empty here so we fall to lab-logs)
        #   send_discord → discord_lab_logs_webhook_url (plain, empty)
        pool.fetchrow = AsyncMock(side_effect=[
            encrypted_row,   # telegram_bot_token
            chat_id_row,     # telegram_chat_id
            empty_row,       # discord_ops_webhook_url
            empty_row,       # discord_lab_logs_webhook_url
        ])
        pool.fetchval = AsyncMock(return_value="DECRYPTED_BOT_TOKEN_42")

        # Capture the URL the brain actually hits.
        captured_requests = []

        class _FakeResp:
            status = 200

        def _fake_urlopen(req, timeout=10):
            captured_requests.append(req)
            return _FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        # Send an alert through the same code path the dispatcher uses.
        # #344: notify is async and accepts pool=.
        result = await bd.notify(
            "PoindexterPostgresDown — pg unreachable", pool=pool,
        )

        assert result is True, "notify should return True when telegram POST succeeded"
        # At least one POST went out; the first one is to Telegram.
        assert captured_requests, "notify did not hit the network"
        telegram_req = captured_requests[0]
        # The decrypted plaintext token is in the URL — NOT the ``enc:v1:`` ciphertext.
        assert "DECRYPTED_BOT_TOKEN_42" in telegram_req.full_url
        assert "enc:v1:" not in telegram_req.full_url
        assert telegram_req.full_url.startswith(
            "https://api.telegram.org/botDECRYPTED_BOT_TOKEN_42/sendMessage"
        )

    async def test_full_path_returns_false_when_token_decrypt_fails(
        self, monkeypatch,
    ):
        """No master key + encrypted token → notify returns False, no POST.

        This is the exact failure mode #342 fixes: before the fix the
        brain logged "No Telegram bot token" but the alert_dispatcher
        recorded ``dispatch_result = 'sent'`` anyway. After the fix,
        the bool return propagates through the alert_dispatcher
        adapter into a NotifyFailed exception, which the dispatcher
        catches and records as ``'error: ...'``.

        #344: The brain no longer caches the token at module level —
        secrets are lazy-fetched on every call. So this test feeds the
        DB an encrypted row + an empty key env and confirms the lazy
        path returns False and skips the network.
        """
        from brain import brain_daemon as bd

        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)
        # Make sure the env-var bootstrap fallbacks don't bleed in.
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("DISCORD_OPS_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("DISCORD_LAB_LOGS_WEBHOOK_URL", raising=False)

        # Token row is encrypted but key env is unset → read_app_setting
        # logs a warning and returns the empty default. chat_id +
        # webhooks return empty too so neither send path can fire.
        encrypted_row = {"value": "enc:v1:abc", "is_secret": True}
        empty_row = {"value": "", "is_secret": False}

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=[
            encrypted_row,   # telegram_bot_token (decrypt skipped → "")
            empty_row,       # telegram_chat_id
            empty_row,       # discord_ops_webhook_url
            empty_row,       # discord_lab_logs_webhook_url
        ])
        pool.fetchval = AsyncMock(return_value=None)

        captured = []

        def _fake_urlopen(req, timeout=10):
            captured.append(req)

            class _R:
                status = 200

            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        # No discord either — both channels should fail.
        result = await bd.notify("test alert", pool=pool)
        assert result is False, "notify must return False when no channel reached the operator"
        # And nothing should have been POSTed.
        assert captured == []
