"""Unit tests for ``poindexter settings`` CLI subcommands.

Pins the 2026-05-28 phantom-key behaviour change. The original
2026-05-27 bandaid (reject any key with ``/``, suggest the canonical
key) was replaced with proper UX:

  - ``settings set`` auto-strips the ``category/`` prefix when the
    canonical row exists. Warns on category mismatch (informational).
  - ``settings set`` with ``category/key`` where no canonical row
    exists fails loud and points the operator at ``--allow-new`` +
    bare-key + ``--category``.
  - ``settings set`` with bare key + ``--allow-new`` creates a new row.
  - ``settings set`` with bare key + no canonical row + no
    ``--allow-new`` also fails loud (typo guard).
  - ``settings list`` renders ``key [category] = value`` so the
    leftmost copyable token is the canonical key.

Related regression: Glad-Labs/poindexter#253 (phantom row UPSERT) and
the dev-diary publishing throttle that hid behind the silent failure.

Tests use mocked asyncpg connections — no real DB.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.settings import (
    _split_category_prefix,
    settings_group,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_asyncpg_connect(*, existing_category: str | None):
    """Patch ``asyncpg.connect`` to return a conn whose ``fetchrow`` either
    returns a canonical row (with the given category) or ``None``.

    Used by the ``settings set`` tests to simulate the canonical row
    being present or absent without spinning up a real DB.
    """
    conn = MagicMock()
    if existing_category is None:
        conn.fetchrow = AsyncMock(return_value=None)
    else:
        # asyncpg returns Record-like objects; a dict with __getitem__
        # is good enough for our access pattern.
        conn.fetchrow = AsyncMock(
            return_value={"key": "__set_by_test__", "category": existing_category},
        )
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.close = AsyncMock()
    return patch(
        "asyncpg.connect", new=AsyncMock(return_value=conn),
    ), conn


def _patch_resolve_dsn():
    return patch(
        "poindexter.cli._bootstrap.resolve_dsn",
        return_value="postgresql://fake/test",
    )


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# _split_category_prefix — the helper itself
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplitCategoryPrefix:

    def test_bare_key_returns_unchanged_no_prefix(self):
        canonical, prefix = _split_category_prefix("daily_post_limit")
        assert canonical == "daily_post_limit"
        assert prefix is None

    def test_single_slash_splits_into_prefix_and_canonical(self):
        canonical, prefix = _split_category_prefix("pipeline/daily_post_limit")
        assert canonical == "daily_post_limit"
        assert prefix == "pipeline"

    def test_rsplit_uses_rightmost_slash(self):
        """If a key has multiple slashes, the canonical part is the
        right-most segment per spec."""
        canonical, prefix = _split_category_prefix("plugin/llm/foo")
        assert canonical == "foo"
        assert prefix == "plugin/llm"


# ---------------------------------------------------------------------------
# settings set — slash-prefix handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsSetSlashHandling:
    """Cover the 7 cases from the 2026-05-28 spec."""

    def test_bare_key_existing_row_updates_canonical(self, runner):
        """Regular case: bare key, canonical exists → silent update."""
        ctx, conn = _patch_asyncpg_connect(existing_category="pipeline")
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "daily_post_limit", "4"],
            )

        assert result.exit_code == 0, result.output
        assert "Updated: daily_post_limit = 4" in result.output
        # The UPSERT was called with the bare key, not a phantom slash form.
        assert conn.execute.await_count == 1
        sql_args = conn.execute.await_args.args
        assert sql_args[1] == "daily_post_limit"
        assert sql_args[2] == "4"

    def test_category_slash_key_existing_matching_category_silently_strips(
        self, runner,
    ):
        """``settings set pipeline/daily_post_limit 4`` where the canonical
        row's category IS ``pipeline`` → silent strip, no warning."""
        ctx, conn = _patch_asyncpg_connect(existing_category="pipeline")
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "pipeline/daily_post_limit", "4"],
            )

        assert result.exit_code == 0, result.output
        assert "Updated: daily_post_limit = 4" in result.output
        # No warning about category mismatch — they matched.
        assert "warning:" not in result.output.lower()
        # UPSERT went to the bare canonical key.
        assert conn.execute.await_args.args[1] == "daily_post_limit"

    def test_category_slash_key_existing_mismatched_category_warns_and_updates(
        self, runner,
    ):
        """Supplied prefix disagrees with row's actual category → warn but
        proceed with canonical key (matches MCP-tool behaviour)."""
        # Canonical row exists, but its category is "quality" not "pipeline".
        ctx, conn = _patch_asyncpg_connect(existing_category="quality")
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "pipeline/daily_post_limit", "4"],
            )

        assert result.exit_code == 0, result.output
        assert "warning" in result.output.lower()
        assert "'pipeline'" in result.output and "'quality'" in result.output
        assert "Updated: daily_post_limit = 4" in result.output
        # UPSERT still went to the bare canonical key (informational warn only).
        assert conn.execute.await_args.args[1] == "daily_post_limit"

    def test_category_slash_key_canonical_missing_fails_loud(self, runner):
        """``settings set pipeline/daily_post_limit 4`` with no canonical
        row → exit 2 with actionable error message."""
        ctx, conn = _patch_asyncpg_connect(existing_category=None)
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "pipeline/daily_post_limit", "4"],
            )

        assert result.exit_code == 2, result.output
        assert "no setting named" in result.output
        assert "daily_post_limit" in result.output
        assert "--allow-new" in result.output
        # Crucially: --category pipeline is suggested (echo back the
        # supplied prefix).
        assert "--category pipeline" in result.output
        # And we did NOT upsert anything.
        assert conn.execute.await_count == 0

    def test_bare_key_missing_no_allow_new_fails_loud(self, runner):
        """Typo guard: ``settings set typo_key 4`` with no canonical row
        AND no ``--allow-new`` → exit 2."""
        ctx, conn = _patch_asyncpg_connect(existing_category=None)
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "typo_key", "4"],
            )

        assert result.exit_code == 2, result.output
        assert "no setting named" in result.output
        assert "typo_key" in result.output
        assert "--allow-new" in result.output
        assert conn.execute.await_count == 0

    def test_bare_key_missing_with_allow_new_creates(self, runner):
        """``settings set new_key 4 --allow-new`` → upsert succeeds."""
        ctx, conn = _patch_asyncpg_connect(existing_category=None)
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group,
                ["set", "new_key", "4", "--allow-new", "--category", "pipeline"],
            )

        assert result.exit_code == 0, result.output
        assert "Updated: new_key = 4" in result.output
        # The UPSERT was called once with the new bare key.
        assert conn.execute.await_count == 1
        sql_args = conn.execute.await_args.args
        assert sql_args[1] == "new_key"
        assert sql_args[3] == "pipeline"  # category positional arg


# ---------------------------------------------------------------------------
# settings list — leftmost-token-is-canonical format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsListFormat:
    """Pin the new ``key [category] = value`` rendering."""

    def test_active_only_list_renders_bare_key_first(self, runner):
        """Active-only path (HTTP-backed): leftmost token = bare key.

        Regression: previously rendered as ``category/key = value``,
        which trained operators to copy ``category/key`` back into
        ``settings set`` (phantom-key trap).
        """
        # Stub the HTTP layer's response shape.
        fake_data = {
            "items": [
                {
                    "key": "daily_post_limit",
                    "value_preview": "4",
                    "category": "pipeline",
                    "is_encrypted": False,
                },
                {
                    "key": "auto_publish_threshold",
                    "value_preview": "85",
                    "category": "quality",
                    "is_encrypted": False,
                },
            ],
            "total": 2,
        }

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *a, **kw):
                return MagicMock()

            async def json_or_raise(self, resp):
                return fake_data

        with patch(
            "poindexter.cli.settings.WorkerClient",
            return_value=_FakeClient(),
        ):
            result = runner.invoke(settings_group, ["list"])

        assert result.exit_code == 0, result.output
        # The bare key appears at the start of each row (after the
        # leading whitespace).
        for line in result.output.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(("daily_post_limit", "auto_publish_threshold")):
                # Confirm the format: bare key, then [category], then = value.
                # No "category/key" form anywhere.
                assert "pipeline/daily_post_limit" not in line
                assert "quality/auto_publish_threshold" not in line
                assert "[" in stripped and "]" in stripped
                break
        else:
            pytest.fail(
                f"No row started with the bare key — output was:\n{result.output}"
            )


# ---------------------------------------------------------------------------
# settings set --secret — encrypted generic-secret path
# ---------------------------------------------------------------------------


def _patch_asyncpg_connect_secret():
    """Patch ``asyncpg.connect`` for the ``--secret`` path.

    The secret path routes through ``plugins.secrets.set_secret``, which
    calls ``conn.fetchval`` (pgcrypto encrypt) then ``conn.execute`` (the
    upsert), preceded by ``ensure_pgcrypto``'s own ``conn.execute``. The
    mock returns a fake base64 ciphertext so we never need a real DB.
    """
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="ZmFrZQ==")  # base64("fake")
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.close = AsyncMock()
    return patch("asyncpg.connect", new=AsyncMock(return_value=conn)), conn


def _find_insert_call(conn):
    """The ``conn.execute`` call carrying the app_settings upsert SQL."""
    for call in conn.execute.await_args_list:
        if call.args and "INSERT INTO app_settings" in call.args[0]:
            return call
    return None


@pytest.mark.unit
class TestSettingsSetSecret:
    """`settings set --secret` encrypts via the pgcrypto helper."""

    def test_secret_flag_encrypts_and_hides_value(self, runner, monkeypatch):
        """The plaintext is encrypted (enc:v1: sentinel) and never echoed."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        ctx, conn = _patch_asyncpg_connect_secret()
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group,
                ["set", "acme_api_token", "super-secret-token", "--secret"],
            )

        assert result.exit_code == 0, result.output
        # The plaintext must never appear on the terminal.
        assert "super-secret-token" not in result.output
        # The stored value is ciphertext, not the plaintext.
        insert = _find_insert_call(conn)
        assert insert is not None, "set_secret never issued the upsert"
        assert insert.args[2].startswith("enc:v1:")
        assert "super-secret-token" not in insert.args[2]

    def test_secret_flag_defaults_category_to_secrets(self, runner, monkeypatch):
        """No --category given → the secret lands in the 'secrets' category."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        ctx, conn = _patch_asyncpg_connect_secret()
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "some_token", "v", "--secret"],
            )

        assert result.exit_code == 0, result.output
        assert _find_insert_call(conn).args[3] == "secrets"

    def test_secret_flag_honors_explicit_category(self, runner, monkeypatch):
        """An explicit --category is threaded through to set_secret."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        ctx, conn = _patch_asyncpg_connect_secret()
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group,
                ["set", "acme_api_token", "v", "--secret", "--category", "integrations"],
            )

        assert result.exit_code == 0, result.output
        assert _find_insert_call(conn).args[3] == "integrations"

    def test_secret_flag_creates_new_key_without_allow_new(self, runner, monkeypatch):
        """Operator secrets aren't phantom-key-guarded.

        Every other set_secret caller upserts unconditionally, so a
        brand-new secret key must NOT require --allow-new (the docs don't
        show it).
        """
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        ctx, conn = _patch_asyncpg_connect_secret()
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group,
                ["set", "brand_new_secret_key", "v", "--secret"],
            )

        assert result.exit_code == 0, result.output
        assert _find_insert_call(conn) is not None


# ---------------------------------------------------------------------------
# settings get --reveal — value-only decrypt for test-fires
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsGetReveal:
    """`settings get --reveal` decrypts and prints the bare plaintext."""

    @staticmethod
    def _patch_reveal_conn(*, row, decrypted):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=row)
        conn.fetchval = AsyncMock(return_value=decrypted)
        conn.close = AsyncMock()
        return patch("asyncpg.connect", new=AsyncMock(return_value=conn)), conn

    def test_reveal_prints_value_only_to_stdout(self, monkeypatch):
        """stdout is the bare plaintext (scriptable); warning goes to stderr."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        runner = CliRunner()
        ctx, _conn = self._patch_reveal_conn(
            row={"value": "enc:v1:abc", "is_secret": True},
            decrypted="the-decrypted-secret",
        )
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group,
                ["get", "lemon_squeezy_webhook_secret", "--reveal"],
            )

        assert result.exit_code == 0, result.stderr
        # Exactly the value + newline — nothing else — so SECRET=$(…) works.
        assert result.stdout == "the-decrypted-secret\n"
        # The exposure warning is on stderr, keeping stdout clean.
        assert "reveal" in result.stderr.lower()

    def test_reveal_json_includes_plaintext(self, monkeypatch):
        """--reveal --json emits structured output with the plaintext value."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        runner = CliRunner()
        ctx, _conn = self._patch_reveal_conn(
            row={"value": "enc:v1:abc", "is_secret": True},
            decrypted="plain",
        )
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["get", "k", "--reveal", "--json"],
            )

        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["value"] == "plain"

    def test_reveal_missing_key_fails_loud(self, monkeypatch):
        """An absent key exits non-zero rather than printing an empty secret."""
        monkeypatch.setenv("POINDEXTER_SECRET_KEY", "unit-test-key")
        runner = CliRunner()
        ctx, _conn = self._patch_reveal_conn(row=None, decrypted=None)
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(settings_group, ["get", "nope", "--reveal"])

        assert result.exit_code != 0
        assert "nope" in (result.stdout + result.stderr)
