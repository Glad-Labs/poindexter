"""Unit tests for ``plugins.secrets`` SQL-shaping (no real DB).

The pgcrypto round-trip lives in
``tests/integration/test_plugin_secrets.py`` (needs the extension binary).
These pin the part that doesn't need a live DB: that :func:`set_secret`
threads the caller's ``category`` into the INSERT.

Why this matters: the historical ``set_secret`` hardcoded
``category='secrets'``. The generic ``poindexter settings set --secret``
path (added 2026-06-13) wants to honor the operator's ``--category`` so a
secret can land in, e.g., the ``finance`` category, while still defaulting
to ``secrets`` when none is given. These tests guard that contract with a
mocked asyncpg connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.secrets import set_secret


def _mock_conn() -> MagicMock:
    """A conn whose ``fetchval`` returns a fake ciphertext and whose
    ``execute`` records the upsert call."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="ZmFrZQ==")  # base64("fake")
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    return conn


def _insert_call(conn: MagicMock):
    """The ``conn.execute`` call whose SQL is the app_settings upsert."""
    for call in conn.execute.await_args_list:
        if call.args and "INSERT INTO app_settings" in call.args[0]:
            return call
    raise AssertionError("set_secret never issued the app_settings INSERT")


@pytest.mark.unit
class TestSetSecretCategory:
    """``set_secret`` parameterizes category (default ``secrets``)."""

    async def test_defaults_category_to_secrets(self, monkeypatch):
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        conn = _mock_conn()

        await set_secret(conn, "some_api_token", "s3cr3t")

        # INSERT positional args: (sql, key, stored_value, category, description)
        assert _insert_call(conn).args[3] == "secrets"

    async def test_honors_explicit_category(self, monkeypatch):
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        conn = _mock_conn()

        await set_secret(conn, "acme_api_token", "tok", category="integrations")

        assert _insert_call(conn).args[3] == "integrations"
