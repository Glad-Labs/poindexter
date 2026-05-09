"""Unit tests for the MCP-server OAuth helpers (Glad-Labs/poindexter#243, #244, #249).

Each MCP server ships its own local mirror of
``services.auth.oauth_client`` (see ``mcp-server/oauth_client.py`` and
``mcp-server-gladlabs/oauth_client.py`` for the rationale — the MCP
servers run in their own ``uv`` venvs that don't have the worker
package installed). These tests exercise both mirrors against the
mock-transport pattern the worker-side suite uses, so any silent drift
from the worker contract gets caught locally.

Coverage per consumer:

- ``_decode_jwt_exp`` round-trip on a hand-rolled JWT.
- Mint then cache-hit on a fresh token (one /token round trip for two
  ``get_token()`` calls).
- 401 invalidates the cache and retries exactly once with a fresh JWT.
- Hard failure when OAuth credentials aren't configured. The legacy
  static-Bearer fallback was removed in Phase 3 (#249).
- Pool-backed constructor reads creds from app_settings.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Add ``mcp-server`` and ``mcp-server-gladlabs`` to sys.path so we can
# import their local ``oauth_client`` modules. The repo layout is:
#   <repo>/mcp-server/oauth_client.py
#   <repo>/mcp-server-gladlabs/oauth_client.py
#   <repo>/src/cofounder_agent/tests/unit/cli/test_mcp_oauth.py
# Six parents up gets us to <repo>.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MCP_DIR = _REPO_ROOT / "mcp-server"
_MCP_GLADLABS_DIR = _REPO_ROOT / "mcp-server-gladlabs"


def _make_jwt(exp_offset: int = 3600, sub: str = "pdx_mcp") -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "poindexter",
        "sub": sub,
        "scope": "api:read api:write",
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
        "jti": "test-mcp",
    }

    def _b64(d):
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_b64(header)}.{_b64(payload)}.signature"


def _import_mcp_oauth():
    """Import ``mcp-server/oauth_client.py`` as a fresh module each call.

    Adding/removing the directory from sys.path affects every test in
    the process, so we make it idempotent and return the imported
    module reference.
    """
    if str(_MCP_DIR) not in sys.path:
        sys.path.insert(0, str(_MCP_DIR))
    # Use a private alias to avoid colliding with the gladlabs mirror's
    # module name (both files are literally ``oauth_client.py``).
    import importlib

    mod_name = "mcp_server_oauth_client_under_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, _MCP_DIR / "oauth_client.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _import_gladlabs_oauth():
    """Same pattern for the gladlabs operator MCP mirror."""
    if str(_MCP_GLADLABS_DIR) not in sys.path:
        sys.path.insert(0, str(_MCP_GLADLABS_DIR))
    import importlib

    mod_name = "gladlabs_mcp_oauth_client_under_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, _MCP_GLADLABS_DIR / "oauth_client.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# mcp-server (public) helper
# ---------------------------------------------------------------------------


class TestMcpDecodeJWTExp:
    def test_decodes_exp(self):
        oac = _import_mcp_oauth()
        token = _make_jwt(600)
        exp = oac._decode_jwt_exp(token)  # noqa: SLF001
        assert exp is not None and exp > int(time.time())

    def test_returns_none_for_static_bearer(self):
        oac = _import_mcp_oauth()
        assert oac._decode_jwt_exp("plaintext-static-token") is None  # noqa: SLF001


class TestMcpOAuthClient:
    @pytest.mark.asyncio
    async def test_mint_then_cache(self):
        oac = _import_mcp_oauth()
        mints = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mints
            assert request.url.path == "/token"
            mints += 1
            return httpx.Response(
                200,
                json={"access_token": _make_jwt(3600), "expires_in": 3600},
            )

        c = oac.McpOAuthClient(
            base_url="http://test",
            client_id="pdx_mcp", client_secret="msecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        await c.get_token()
        await c.get_token()
        assert mints == 1
        await c.aclose()

    @pytest.mark.asyncio
    async def test_expiry_triggers_refresh(self):
        oac = _import_mcp_oauth()
        mints = 0

        def handler(_):
            nonlocal mints
            mints += 1
            return httpx.Response(
                200,
                json={"access_token": _make_jwt(3600), "expires_in": 3600},
            )

        c = oac.McpOAuthClient(
            base_url="http://test",
            client_id="pdx_mcp", client_secret="msecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        await c.get_token()
        # Force the cache to look stale and re-mint.
        c._cached.refresh_at = time.time() - 1  # noqa: SLF001
        await c.get_token()
        assert mints == 2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_401_invalidates_cache_and_retries(self):
        oac = _import_mcp_oauth()
        downstream_count = 0
        mints = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal downstream_count, mints
            if request.url.path == "/token":
                mints += 1
                return httpx.Response(
                    200,
                    json={"access_token": _make_jwt(), "expires_in": 3600},
                )
            downstream_count += 1
            if downstream_count == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"ok": True})

        c = oac.McpOAuthClient(
            base_url="http://test",
            client_id="pdx_mcp", client_secret="msecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        resp = await c.get("/api/health")
        assert resp.status_code == 200
        # Two downstream calls (initial 401 + retry) and two mints
        # (first plus the post-invalidate refresh).
        assert downstream_count == 2
        assert mints == 2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        """Phase 3 (#249): no static-Bearer fallback. Fail loud."""
        oac = _import_mcp_oauth()
        c = oac.McpOAuthClient(base_url="http://test")
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await c.get_token()


class TestMcpPoolConstructor:
    @pytest.mark.asyncio
    async def test_loads_credentials_from_app_settings(self):
        oac = _import_mcp_oauth()
        rows_by_key = {
            "mcp_oauth_client_id": {"value": "pdx_mcp123", "is_secret": True},
            "mcp_oauth_client_secret": {"value": "msecret", "is_secret": True},
        }

        async def _fetchrow(_sql, key):
            return rows_by_key.get(key)

        async def _fetchval(*args, **_kw):
            raise AssertionError(f"unexpected fetchval call: {args}")

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock(side_effect=_fetchval)

        client = await oac.oauth_client_from_pool(pool, base_url="http://test")
        assert client.using_oauth is True
        assert client._client_id == "pdx_mcp123"  # noqa: SLF001
        assert client._client_secret == "msecret"  # noqa: SLF001
        await client.aclose()

    @pytest.mark.asyncio
    async def test_raises_when_oauth_keys_missing(self):
        """Phase 3 (#249): no api_token fallback in pool constructor."""
        oac = _import_mcp_oauth()

        async def _fetchrow(_sql, key):
            return None

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock()

        client = await oac.oauth_client_from_pool(pool, base_url="http://test")
        assert client.using_oauth is False
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await client.get_token()
        await client.aclose()


# ---------------------------------------------------------------------------
# mcp-server-gladlabs (operator-only) helper
#
# These tests cover the private operator MCP mirror at
# ``mcp-server-gladlabs/oauth_client.py`` — that directory is stripped
# from the public Glad-Labs/poindexter sync (see
# ``scripts/sync-to-github.sh``). The skipif keeps the public-repo CI
# green while still running every gladlabs test in the private repo
# (where the directory exists).
# ---------------------------------------------------------------------------

_GLADLABS_AVAILABLE = (_MCP_GLADLABS_DIR / "oauth_client.py").exists()
pytestmark_gladlabs = pytest.mark.skipif(
    not _GLADLABS_AVAILABLE,
    reason="mcp-server-gladlabs/ is private — stripped from the public mirror.",
)


@pytestmark_gladlabs
class TestGladlabsDecodeJWTExp:
    def test_decodes_exp(self):
        oac = _import_gladlabs_oauth()
        token = _make_jwt(600, sub="pdx_gladlabs")
        exp = oac._decode_jwt_exp(token)  # noqa: SLF001
        assert exp is not None and exp > int(time.time())


@pytestmark_gladlabs
class TestGladlabsMcpOAuthClient:
    @pytest.mark.asyncio
    async def test_mint_then_cache(self):
        oac = _import_gladlabs_oauth()
        mints = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mints
            assert request.url.path == "/token"
            mints += 1
            return httpx.Response(
                200,
                json={"access_token": _make_jwt(3600), "expires_in": 3600},
            )

        c = oac.GladlabsMcpOAuthClient(
            base_url="http://test",
            client_id="pdx_gl", client_secret="gsecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        await c.get_token()
        await c.get_token()
        assert mints == 1
        await c.aclose()

    @pytest.mark.asyncio
    async def test_401_invalidates_cache_and_retries(self):
        oac = _import_gladlabs_oauth()
        downstream_count = 0
        mints = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal downstream_count, mints
            if request.url.path == "/token":
                mints += 1
                return httpx.Response(
                    200,
                    json={"access_token": _make_jwt(), "expires_in": 3600},
                )
            downstream_count += 1
            if downstream_count == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"ok": True})

        c = oac.GladlabsMcpOAuthClient(
            base_url="http://test",
            client_id="pdx_gl", client_secret="gsecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        resp = await c.get("/api/health")
        assert resp.status_code == 200
        assert downstream_count == 2
        assert mints == 2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        """Phase 3 (#249): no static-Bearer fallback. Fail loud."""
        oac = _import_gladlabs_oauth()
        c = oac.GladlabsMcpOAuthClient(base_url="http://test")
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await c.get_token()


@pytestmark_gladlabs
class TestGladlabsPoolConstructor:
    @pytest.mark.asyncio
    async def test_loads_credentials_from_distinct_keys(self):
        """The gladlabs helper uses ``mcp_gladlabs_oauth_*`` keys, NOT
        the public MCP's ``mcp_oauth_*`` keys — verify the read targets
        the right rows so the two clients stay independent."""
        oac = _import_gladlabs_oauth()
        rows_by_key = {
            "mcp_gladlabs_oauth_client_id": {
                "value": "pdx_gl123", "is_secret": True,
            },
            "mcp_gladlabs_oauth_client_secret": {
                "value": "gsecret", "is_secret": True,
            },
        }

        async def _fetchrow(_sql, key):
            return rows_by_key.get(key)

        async def _fetchval(*args, **_kw):
            raise AssertionError(f"unexpected fetchval call: {args}")

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock(side_effect=_fetchval)

        client = await oac.oauth_client_from_pool(pool, base_url="http://test")
        assert client.using_oauth is True
        assert client._client_id == "pdx_gl123"  # noqa: SLF001
        assert client._client_secret == "gsecret"  # noqa: SLF001
        await client.aclose()

    @pytest.mark.asyncio
    async def test_does_not_pick_up_public_mcp_keys(self):
        """The public MCP's ``mcp_oauth_*`` keys must NOT bleed into
        the gladlabs helper's resolution — operator surfaces stay
        independent of the public MCP's revocation cycle."""
        oac = _import_gladlabs_oauth()
        rows_by_key = {
            "mcp_oauth_client_id": {"value": "pdx_public", "is_secret": True},
            "mcp_oauth_client_secret": {"value": "psecret", "is_secret": True},
        }

        async def _fetchrow(_sql, key):
            return rows_by_key.get(key)

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock()

        client = await oac.oauth_client_from_pool(
            pool, base_url="http://test",
        )
        # Public MCP keys present but irrelevant here — gladlabs helper
        # ignores them and (post-#249) raises loudly on first get_token.
        assert client.using_oauth is False
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await client.get_token()
        await client.aclose()


# ---------------------------------------------------------------------------
# CLI ``auth migrate-mcp`` and ``auth migrate-mcp-gladlabs`` command shape
# ---------------------------------------------------------------------------


class TestMigrateCommandsRegistered:
    """Smoke test that the new auth subcommands are registered with the
    expected names + flags. Catches typos at the command-registration
    layer without exercising the (DB-bound) provisioning code path."""

    def test_migrate_mcp_command_exists(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("migrate-mcp")
        assert cmd is not None
        opt_names = {p.name for p in cmd.params}
        assert {"name", "scopes"}.issubset(opt_names)

    def test_migrate_mcp_gladlabs_command_exists(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("migrate-mcp-gladlabs")
        assert cmd is not None
        opt_names = {p.name for p in cmd.params}
        assert {"name", "scopes"}.issubset(opt_names)

    def test_migrate_mcp_default_scopes(self):
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("migrate-mcp")
        scopes_opt = next(p for p in cmd.params if p.name == "scopes")
        assert "api:read" in scopes_opt.default
        assert "api:write" in scopes_opt.default

    def test_migrate_mcp_gladlabs_default_scopes_include_mcp_write(self):
        """Operator MCP defaults to a broader scope — its tools tend
        to write (Discord, customer lookups, subscriber management).
        Public MCP default omits ``mcp:write`` since it's mostly
        publishing-pipeline reads + acting through the worker API."""
        from poindexter.cli.auth import auth_group

        cmd = auth_group.commands.get("migrate-mcp-gladlabs")
        scopes_opt = next(p for p in cmd.params if p.name == "scopes")
        assert "mcp:write" in scopes_opt.default
        assert "api:write" in scopes_opt.default
