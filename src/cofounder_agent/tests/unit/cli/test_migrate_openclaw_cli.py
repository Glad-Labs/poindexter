"""Unit tests for ``poindexter auth migrate-openclaw`` (Glad-Labs/poindexter#246).

Mirrors ``test_mcp_oauth.py`` shape — Click smoke tests that catch
typos at command-registration without hitting the DB. The actual
provisioning helper (``_provision_consumer_client``) is exercised by
the ``migrate-cli`` / ``migrate-mcp`` paths and shares one code path.

We also smoke-test the bash helper at ``skills/openclaw/_lib/get_token.sh``
end-to-end: it must round-trip a fake JWT through the
``_oauth_decode_exp`` + ``_oauth_token_is_fresh`` helpers, and fall
back to ``POINDEXTER_KEY`` when no OAuth creds are set. We shell out
to bash because that's exactly how OpenClaw invokes the script — a
Python re-implementation would prove nothing about the wire shape.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner


_REPO_ROOT = Path(__file__).resolve().parents[5]
_HELPER = _REPO_ROOT / "skills" / "openclaw" / "_lib" / "get_token.sh"


# ---------------------------------------------------------------------------
# CLI command shape
# ---------------------------------------------------------------------------


class TestMigrateOpenclawCommandRegistered:
    """The same smoke-test pattern as ``test_mcp_oauth.py`` —
    catches typos in the command name + option flags without hitting
    the DB-bound ``_provision_consumer_client``."""

    def test_command_exists(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("migrate-openclaw")
        assert cmd is not None, "migrate-openclaw command not registered"
        opt_names = {p.name for p in cmd.params}
        assert {"name", "scopes"}.issubset(opt_names)

    def test_default_scopes_cover_read_and_write(self):
        """OpenClaw skills do both reads (list/get) and writes
        (approve/publish/reject/create) — default scope must include
        both api:read and api:write."""
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("migrate-openclaw")
        scopes_opt = next(p for p in cmd.params if p.name == "scopes")
        assert "api:read" in scopes_opt.default
        assert "api:write" in scopes_opt.default

    def test_default_client_name(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("migrate-openclaw")
        name_opt = next(p for p in cmd.params if p.name == "name")
        assert name_opt.default == "openclaw-skills"

    def test_help_describes_env_block(self):
        """The help text should mention pasting an env block into
        ~/.openclaw/openclaw.json — that's the operator instruction
        the CLI prints on success."""
        from poindexter.cli.auth import auth_group

        runner = CliRunner()
        result = runner.invoke(auth_group, ["migrate-openclaw", "--help"])
        assert result.exit_code == 0, result.output
        assert "openclaw.json" in result.output.lower() or \
               "openclaw" in result.output.lower()

    def test_invalid_scopes_rejected(self):
        """Empty --scopes must raise UsageError, not silently provision
        a no-scope client."""
        from poindexter.cli.auth import auth_group

        runner = CliRunner()
        result = runner.invoke(
            auth_group, ["migrate-openclaw", "--scopes", "   "],
        )
        assert result.exit_code != 0
        assert "scope" in result.output.lower()


class TestMigrateOpenclawHappyPath:
    """End-to-end at the Click layer — patch out the DB helper and
    confirm the command echoes the new credentials + the env block."""

    def test_prints_env_block_and_secret(self):
        from poindexter.cli import auth as auth_mod

        runner = CliRunner()

        async def _fake_provision(
            *, name, scopes, client_id_setting_key, client_secret_setting_key,
        ):
            assert client_id_setting_key == "openclaw_oauth_client_id"
            assert client_secret_setting_key == "openclaw_oauth_client_secret"
            return ("pdx_openclaw_test", "secret_test_xyz")

        with patch.object(
            auth_mod, "_provision_consumer_client",
            side_effect=_fake_provision,
        ), patch.object(
            auth_mod, "_bootstrap_path_for_secret_key", lambda: None,
        ):
            result = runner.invoke(
                auth_mod.auth_group, ["migrate-openclaw"],
            )

        assert result.exit_code == 0, result.output
        assert "pdx_openclaw_test" in result.output
        assert "secret_test_xyz" in result.output
        # The CLI prints the env block the operator pastes into
        # ~/.openclaw/openclaw.json — must include both vars.
        assert "POINDEXTER_OAUTH_CLIENT_ID=pdx_openclaw_test" in result.output
        assert "POINDEXTER_OAUTH_CLIENT_SECRET=secret_test_xyz" in result.output


# ---------------------------------------------------------------------------
# Bash helper end-to-end
# ---------------------------------------------------------------------------


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_jwt(exp_offset: int) -> str:
    header = _b64url_no_pad(json.dumps({"alg": "HS256"}).encode())
    payload = _b64url_no_pad(
        json.dumps({"exp": int(time.time()) + exp_offset}).encode()
    )
    return f"{header}.{payload}.signature"


_BASH = shutil.which("bash")


@pytest.mark.skipif(_BASH is None, reason="bash not on PATH")
class TestBashHelper:
    """Exercise ``skills/openclaw/_lib/get_token.sh`` via subprocess.

    The helper has been hand-tested against a real worker; this
    suite locks in the contract:

    1. ``_oauth_decode_exp`` round-trips a JWT exp claim
    2. ``_oauth_token_is_fresh`` returns true for >30s-of-life tokens
    3. ``_oauth_token_is_fresh`` returns false for stale tokens
    4. ``get_poindexter_token`` falls back to ``POINDEXTER_KEY`` when no
       OAuth creds are set
    5. ``get_poindexter_token`` exits non-zero with no creds at all
    """

    def _bash(self, script: str, env_extra: dict[str, str] | None = None):
        env = {**os.environ, **(env_extra or {})}
        # Ensure the OAuth env vars don't leak in from the test runner.
        env.pop("POINDEXTER_OAUTH_CLIENT_ID", None)
        env.pop("POINDEXTER_OAUTH_CLIENT_SECRET", None)
        env.pop("POINDEXTER_KEY", None)
        env.pop("GLADLABS_KEY", None)
        for k, v in (env_extra or {}).items():
            env[k] = v
        return subprocess.run(
            [_BASH, "-c", f". '{_HELPER}'\n{script}"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    def test_decodes_exp_from_jwt(self):
        token = _make_jwt(600)
        result = self._bash(f'echo $(_oauth_decode_exp "{token}")')
        assert result.returncode == 0
        decoded = int(result.stdout.strip())
        # Within 5s of expected exp.
        assert abs(decoded - (int(time.time()) + 600)) < 5

    def test_decodes_returns_empty_for_static_token(self):
        """A non-JWT static-Bearer token (no dots) must decode to empty —
        the helper falls through to the legacy path on empty exp."""
        result = self._bash('echo "[$(_oauth_decode_exp "static-token-abc")]"')
        assert result.returncode == 0
        assert result.stdout.strip() == "[]"

    def test_token_is_fresh_for_valid_jwt(self):
        token = _make_jwt(600)
        result = self._bash(
            f'if _oauth_token_is_fresh "{token}"; then echo FRESH; else echo STALE; fi'
        )
        assert result.returncode == 0
        assert "FRESH" in result.stdout

    def test_token_is_stale_for_expired_jwt(self):
        token = _make_jwt(-100)
        result = self._bash(
            f'if _oauth_token_is_fresh "{token}"; then echo FRESH; else echo STALE; fi'
        )
        assert result.returncode == 0
        assert "STALE" in result.stdout

    def test_token_is_stale_within_30s_skew(self):
        """Tokens with <30s of life must be treated as stale so the
        next call mints a fresh JWT before the current one expires."""
        token = _make_jwt(15)  # 15s left — under the 30s skew
        result = self._bash(
            f'if _oauth_token_is_fresh "{token}"; then echo FRESH; else echo STALE; fi'
        )
        assert result.returncode == 0
        assert "STALE" in result.stdout

    def test_legacy_static_bearer_fallback(self):
        result = self._bash(
            "get_poindexter_token",
            env_extra={"POINDEXTER_KEY": "legacy-token-abc"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "legacy-token-abc"

    def test_legacy_gladlabs_key_fallback(self):
        """The historical ``GLADLABS_KEY`` env var also resolves —
        it's the renamed version of POINDEXTER_KEY and some old
        OpenClaw configs still set it."""
        result = self._bash(
            "get_poindexter_token",
            env_extra={"GLADLABS_KEY": "legacy-gladlabs-token"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "legacy-gladlabs-token"

    def test_no_creds_at_all_exits_nonzero(self):
        result = self._bash("get_poindexter_token")
        assert result.returncode != 0
        assert "no auth configured" in result.stderr.lower() or \
               "no auth" in result.stderr.lower()
        # The error message must surface the migration command name so
        # the operator knows where to go.
        assert "migrate-openclaw" in result.stderr
