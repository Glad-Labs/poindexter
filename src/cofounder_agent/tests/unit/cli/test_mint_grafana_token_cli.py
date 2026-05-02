"""Unit tests for ``poindexter auth mint-grafana-token`` (Glad-Labs/poindexter#247).

The command:

1. Provisions a ``grafana-alerts`` OAuth client on first call,
   persisting encrypted creds to ``app_settings.grafana_oauth_client_id``
   and ``grafana_oauth_client_secret``.
2. Reuses the existing client on subsequent calls.
3. Mints a long-TTL JWT (default 90d) bound to that client.
4. Prints the token + expiry for the operator to paste into Grafana.

Tests stub out:
- ``_provision_consumer_client`` — DB-bound, exercised separately by
  ``migrate-cli`` / ``migrate-mcp`` paths.
- ``services.auth.oauth_issuer.issue_token`` — returns a deterministic
  fake token + claims object so we can assert the CLI output shape
  without needing ``POINDEXTER_SECRET_KEY``.
- ``plugins.secrets.get_secret`` — controls the
  "first call" vs "reuse" code path.
- ``_pool`` — returns an asyncpg pool stub with ``acquire``/``close``
  context-manager surfaces.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


def _make_fake_pool(secret_returns: dict[str, str | None]):
    """asyncpg pool stub with the surfaces the CLI touches."""
    pool = MagicMock()
    pool.close = AsyncMock(return_value=None)

    fake_conn = MagicMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool._fake_conn = fake_conn  # for assertions
    pool._secret_returns = secret_returns
    return pool


def _make_fake_claims(client_id: str, scopes: list[str], ttl_seconds: int):
    """Build a fake TokenClaims-shaped object for issue_token mocks."""
    import time
    now = int(time.time())
    claims = MagicMock()
    claims.client_id = client_id
    claims.scopes = frozenset(scopes)
    claims.issued_at = now
    claims.expires_at = now + ttl_seconds
    claims.jti = "test-jti"
    return claims


# ---------------------------------------------------------------------------
# CLI command shape
# ---------------------------------------------------------------------------


class TestMintGrafanaTokenCommandRegistered:
    def test_command_exists(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("mint-grafana-token")
        assert cmd is not None, "mint-grafana-token command not registered"
        opt_names = {p.name for p in cmd.params}
        assert {"ttl_str", "scopes", "name"}.issubset(opt_names)

    def test_default_ttl_is_90d(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("mint-grafana-token")
        ttl_opt = next(p for p in cmd.params if p.name == "ttl_str")
        assert ttl_opt.default == "90d"

    def test_default_scopes_cover_read_write(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("mint-grafana-token")
        scopes_opt = next(p for p in cmd.params if p.name == "scopes")
        assert "api:read" in scopes_opt.default
        assert "api:write" in scopes_opt.default

    def test_default_name(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("mint-grafana-token")
        name_opt = next(p for p in cmd.params if p.name == "name")
        assert name_opt.default == "grafana-alerts"


# ---------------------------------------------------------------------------
# TTL parser
# ---------------------------------------------------------------------------


class TestParseTTL:
    def test_seconds_suffix(self):
        from poindexter.cli.auth import _parse_ttl
        assert _parse_ttl("60s") == 60

    def test_minutes_suffix(self):
        from poindexter.cli.auth import _parse_ttl
        assert _parse_ttl("5m") == 300

    def test_hours_suffix(self):
        from poindexter.cli.auth import _parse_ttl
        assert _parse_ttl("2h") == 7200

    def test_days_suffix(self):
        from poindexter.cli.auth import _parse_ttl
        assert _parse_ttl("90d") == 90 * 86400

    def test_bare_int_is_seconds(self):
        from poindexter.cli.auth import _parse_ttl
        assert _parse_ttl("3600") == 3600

    def test_case_insensitive_suffix(self):
        from poindexter.cli.auth import _parse_ttl
        assert _parse_ttl("90D") == 90 * 86400

    def test_garbage_raises(self):
        import click
        from poindexter.cli.auth import _parse_ttl
        with pytest.raises(click.UsageError):
            _parse_ttl("ninety days")


# ---------------------------------------------------------------------------
# Happy-path: first call provisions, second call reuses
# ---------------------------------------------------------------------------


class TestMintGrafanaHappyPath:
    def test_first_call_provisions_client_and_mints_token(self):
        from poindexter.cli import auth as auth_mod

        runner = CliRunner()

        async def _fake_provision(
            *, name, scopes, client_id_setting_key, client_secret_setting_key,
        ):
            assert name == "grafana-alerts"
            assert "api:read" in scopes
            assert client_id_setting_key == "grafana_oauth_client_id"
            assert client_secret_setting_key == "grafana_oauth_client_secret"
            return ("pdx_grafana_test", "secret_grafana_test")

        # First call → no existing client_id row → provision branch.
        async def _get_secret(_conn, _key, *args, **kwargs):
            return ""  # row missing

        async def _fake_pool_factory():
            return _make_fake_pool({"grafana_oauth_client_id": ""})

        with patch.object(
            auth_mod, "_provision_consumer_client",
            side_effect=_fake_provision,
        ), patch.object(
            auth_mod, "_bootstrap_path_for_secret_key", lambda: None,
        ), patch.object(
            auth_mod, "_pool", side_effect=_fake_pool_factory,
        ), patch(
            "plugins.secrets.get_secret", new=AsyncMock(side_effect=_get_secret),
        ), patch(
            "services.auth.oauth_issuer.issue_token",
            return_value=(
                "fake.jwt.token",
                _make_fake_claims(
                    "pdx_grafana_test", ["api:read", "api:write"],
                    90 * 86400,
                ),
            ),
        ):
            result = runner.invoke(
                auth_mod.auth_group, ["mint-grafana-token"],
            )

        assert result.exit_code == 0, result.output
        assert "pdx_grafana_test" in result.output
        assert "fake.jwt.token" in result.output
        # First-call branch wording.
        assert "provisioned" in result.output.lower()
        # Default TTL is 90d.
        assert "90d" in result.output

    def test_second_call_reuses_existing_client(self):
        from poindexter.cli import auth as auth_mod

        runner = CliRunner()

        provision_call_count = 0

        async def _fake_provision(**_kwargs):
            nonlocal provision_call_count
            provision_call_count += 1
            return ("should-not-be-called", "should-not-be-called")

        # Second call → client_id row already populated → reuse branch.
        async def _get_secret(_conn, key, *args, **kwargs):
            if key == "grafana_oauth_client_id":
                return "pdx_existing_grafana"
            return ""

        async def _fake_pool_factory():
            return _make_fake_pool({})

        with patch.object(
            auth_mod, "_provision_consumer_client",
            side_effect=_fake_provision,
        ), patch.object(
            auth_mod, "_bootstrap_path_for_secret_key", lambda: None,
        ), patch.object(
            auth_mod, "_pool", side_effect=_fake_pool_factory,
        ), patch(
            "plugins.secrets.get_secret", new=AsyncMock(side_effect=_get_secret),
        ), patch(
            "services.auth.oauth_issuer.issue_token",
            return_value=(
                "rotated.jwt.token",
                _make_fake_claims(
                    "pdx_existing_grafana", ["api:read", "api:write"],
                    30 * 86400,
                ),
            ),
        ):
            result = runner.invoke(
                auth_mod.auth_group,
                ["mint-grafana-token", "--ttl", "30d"],
            )

        assert result.exit_code == 0, result.output
        assert provision_call_count == 0, "second call must not re-provision"
        assert "pdx_existing_grafana" in result.output
        assert "rotated.jwt.token" in result.output
        # The reuse branch wording.
        assert "existing" in result.output.lower()
        assert "30d" in result.output

    def test_too_short_ttl_rejected(self):
        from poindexter.cli import auth as auth_mod

        runner = CliRunner()
        with patch.object(
            auth_mod, "_bootstrap_path_for_secret_key", lambda: None,
        ):
            result = runner.invoke(
                auth_mod.auth_group, ["mint-grafana-token", "--ttl", "30s"],
            )
        assert result.exit_code != 0
        assert "60 seconds" in result.output or "60" in result.output

    def test_too_long_ttl_rejected(self):
        from poindexter.cli import auth as auth_mod

        runner = CliRunner()
        with patch.object(
            auth_mod, "_bootstrap_path_for_secret_key", lambda: None,
        ):
            result = runner.invoke(
                auth_mod.auth_group, ["mint-grafana-token", "--ttl", "400d"],
            )
        assert result.exit_code != 0
        assert "365" in result.output

    def test_empty_scopes_rejected(self):
        from poindexter.cli import auth as auth_mod

        runner = CliRunner()
        with patch.object(
            auth_mod, "_bootstrap_path_for_secret_key", lambda: None,
        ):
            result = runner.invoke(
                auth_mod.auth_group,
                ["mint-grafana-token", "--scopes", "   "],
            )
        assert result.exit_code != 0
        assert "scope" in result.output.lower()
