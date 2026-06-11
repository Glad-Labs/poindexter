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
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
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


def _import_server(server_dir, mod_name):
    """Import an MCP ``server.py`` fresh for the ``_get_oauth`` rebuild tests.

    The MCP servers normally run in their own ``uv`` venvs; importing
    ``server.py`` here pulls in FastMCP + (for the public server) the
    worker ``services`` boot block. Both are available in the main poetry
    env, but if that ever stops being true in some CI shape we ``skip``
    rather than hard-fail — the same posture the gladlabs ``skipif`` takes.

    ``server.py`` does a plain ``from oauth_client import ...`` against the
    sibling mirror, so we drop any stale ``oauth_client`` from sys.modules
    and put ``server_dir`` first on the path to guarantee the right mirror
    resolves (the two mirrors are both literally ``oauth_client.py``).
    """
    import importlib.util

    if mod_name in sys.modules:
        return sys.modules[mod_name]
    sys.modules.pop("oauth_client", None)
    sys.path.insert(0, str(server_dir))
    spec = importlib.util.spec_from_file_location(
        mod_name, server_dir / "server.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover — env-dependent
        sys.modules.pop(mod_name, None)
        pytest.skip(f"{server_dir.name}/server.py not importable here: {exc!r}")
    finally:
        # server.py bound ``oauth_client_from_pool`` etc. into its own
        # namespace at import, so the transient bare ``oauth_client`` entry
        # is no longer needed — drop it so it can't leak the wrong mirror
        # into another test in the same process.
        sys.modules.pop("oauth_client", None)
    return module


def _write_bootstrap_home(monkeypatch, tmp_path, secret_key):
    """Point ``~`` at ``tmp_path`` and seed ~/.poindexter/bootstrap.toml.

    The vendored ``_bootstrap_secret_key`` in each MCP mirror resolves the
    file via ``os.path.expanduser("~/...")``. Overriding both ``HOME`` and
    ``USERPROFILE`` covers posix (CI) and Windows (Matt's host) — ntpath's
    expanduser prefers ``USERPROFILE``. Pass ``secret_key=None`` to create
    an empty home (no bootstrap.toml at all).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    if secret_key is not None:
        pdx = tmp_path / ".poindexter"
        pdx.mkdir(parents=True, exist_ok=True)
        (pdx / "bootstrap.toml").write_text(
            f'poindexter_secret_key = "{secret_key}"\n', encoding="utf-8",
        )


class _FakeOAuth:
    """Minimal stand-in carrying just the ``using_oauth`` flag that the
    server's ``_get_oauth`` rebuild guard checks."""

    def __init__(self, using_oauth):
        self.using_oauth = using_oauth


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


class TestMcpReadAppSetting:
    """``read_app_setting`` secret-key resolution (Glad-Labs/poindexter#243
    self-heal): env var first, then ~/.poindexter/bootstrap.toml."""

    @pytest.mark.asyncio
    async def test_bootstrap_fallback_decrypts_when_env_unset(
        self, monkeypatch, tmp_path,
    ):
        """Env var missing but bootstrap.toml has the key → decrypt using it.

        Reproduces the original brick: the MCP process was launched before
        ``POINDEXTER_SECRET_KEY`` was in its env, so the encrypted creds
        read returned "" and the server cached a creds-less OAuth client.
        """
        oac = _import_mcp_oauth()
        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)
        _write_bootstrap_home(monkeypatch, tmp_path, "boot-key")

        rows = {"mcp_oauth_client_secret": {"value": "enc:v1:ZmFrZQ==", "is_secret": True}}

        async def _fetchrow(_sql, key):
            return rows.get(key)

        async def _fetchval(*_a, **_k):
            return "decrypted-secret"

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock(side_effect=_fetchval)

        result = await oac.read_app_setting(pool, "mcp_oauth_client_secret")
        assert result == "decrypted-secret"
        # The bootstrap key — not the (unset) env var — drove the decrypt.
        _sql, *args = pool.fetchval.await_args.args
        assert args[0] == "ZmFrZQ=="
        assert args[1] == "boot-key"

    @pytest.mark.asyncio
    async def test_no_key_anywhere_returns_default_no_decrypt(
        self, monkeypatch, tmp_path,
    ):
        """Env unset AND no bootstrap.toml → default, no decrypt attempt."""
        oac = _import_mcp_oauth()
        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)
        _write_bootstrap_home(monkeypatch, tmp_path, None)

        async def _fetchrow(_sql, key):
            return {"value": "enc:v1:ABC=", "is_secret": True}

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock()

        result = await oac.read_app_setting(
            pool, "mcp_oauth_client_secret", default="fallback",
        )
        assert result == "fallback"
        pool.fetchval.assert_not_awaited()


class TestMcpGetOauthRebuild:
    """``server._get_oauth`` must not pin a creds-less client for the
    process lifetime (Glad-Labs/poindexter#243 follow-up). A transiently
    unusable client is rebuilt on the next call so it self-heals once the
    env/creds are good — no manual restart."""

    @pytest.mark.asyncio
    async def test_unusable_cached_client_is_rebuilt(self, monkeypatch):
        srv = _import_server(_MCP_DIR, "mcp_server_under_test")
        rebuilt = _FakeOAuth(using_oauth=True)
        calls = {"n": 0}

        async def _fake_from_pool(*_a, **_k):
            calls["n"] += 1
            return rebuilt

        async def _fake_pool():
            return MagicMock()

        monkeypatch.setattr(srv, "oauth_client_from_pool", _fake_from_pool)
        monkeypatch.setattr(srv, "_get_pool", _fake_pool)
        # Cache holds a client that came up without usable creds earlier.
        monkeypatch.setattr(srv, "_oauth", _FakeOAuth(using_oauth=False))

        got = await srv._get_oauth()  # noqa: SLF001
        assert got is rebuilt
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_usable_cached_client_is_reused(self, monkeypatch):
        srv = _import_server(_MCP_DIR, "mcp_server_under_test")

        async def _boom(*_a, **_k):
            raise AssertionError("should not rebuild a usable cached client")

        monkeypatch.setattr(srv, "oauth_client_from_pool", _boom)
        cached = _FakeOAuth(using_oauth=True)
        monkeypatch.setattr(srv, "_oauth", cached)

        got = await srv._get_oauth()  # noqa: SLF001
        assert got is cached


class TestClassifyWorkerHealth:
    """check_health must not report a FALSE ``Worker: offline`` when the
    authenticated probe fails for an auth reason (Glad-Labs/poindexter#243)
    — a credential/401 error means "couldn't ask", not "worker is down"."""

    def test_oauth_error_is_unknown_auth_not_offline(self):
        srv = _import_server(_MCP_DIR, "mcp_server_under_test")
        line = srv._classify_worker_health(  # noqa: SLF001
            {"error": "oauth init failed: RuntimeError: client_id/client_secret are required"},
        )
        assert "unknown (auth" in line
        assert "offline" not in line

    def test_http_401_is_unknown_auth(self):
        srv = _import_server(_MCP_DIR, "mcp_server_under_test")
        line = srv._classify_worker_health(  # noqa: SLF001
            {"error": "HTTP 401", "detail": "invalid token"},
        )
        assert "unknown (auth" in line

    def test_connection_error_is_offline(self):
        srv = _import_server(_MCP_DIR, "mcp_server_under_test")
        line = srv._classify_worker_health(  # noqa: SLF001
            {"error": "All connection attempts failed"},
        )
        assert line.startswith("offline")
        assert "unknown" not in line

    def test_healthy_reports_running_and_processed(self):
        srv = _import_server(_MCP_DIR, "mcp_server_under_test")
        line = srv._classify_worker_health(  # noqa: SLF001
            {"components": {"task_executor": {"running": True, "total_processed": 42}}},
        )
        assert "running=True" in line
        assert "processed=42" in line


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


@pytestmark_gladlabs
class TestGladlabsReadAppSetting:
    """Same #243 self-heal bootstrap fallback as the public mirror — the
    two ``read_app_setting`` copies must stay behaviourally identical."""

    @pytest.mark.asyncio
    async def test_bootstrap_fallback_decrypts_when_env_unset(
        self, monkeypatch, tmp_path,
    ):
        oac = _import_gladlabs_oauth()
        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)
        _write_bootstrap_home(monkeypatch, tmp_path, "boot-key")

        rows = {
            "mcp_gladlabs_oauth_client_secret": {
                "value": "enc:v1:ZmFrZQ==", "is_secret": True,
            },
        }

        async def _fetchrow(_sql, key):
            return rows.get(key)

        async def _fetchval(*_a, **_k):
            return "decrypted-secret"

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock(side_effect=_fetchval)

        result = await oac.read_app_setting(
            pool, "mcp_gladlabs_oauth_client_secret",
        )
        assert result == "decrypted-secret"
        _sql, *args = pool.fetchval.await_args.args
        assert args[0] == "ZmFrZQ=="
        assert args[1] == "boot-key"

    @pytest.mark.asyncio
    async def test_no_key_anywhere_returns_default_no_decrypt(
        self, monkeypatch, tmp_path,
    ):
        oac = _import_gladlabs_oauth()
        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)
        _write_bootstrap_home(monkeypatch, tmp_path, None)

        async def _fetchrow(_sql, key):
            return {"value": "enc:v1:ABC=", "is_secret": True}

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock()

        result = await oac.read_app_setting(
            pool, "mcp_gladlabs_oauth_client_secret", default="fallback",
        )
        assert result == "fallback"
        pool.fetchval.assert_not_awaited()


@pytestmark_gladlabs
class TestGladlabsGetOauthRebuild:
    """``server._get_oauth`` rebuild guard — mirror of the public server
    test. A creds-less cached client self-heals on the next call."""

    @pytest.mark.asyncio
    async def test_unusable_cached_client_is_rebuilt(self, monkeypatch):
        srv = _import_server(_MCP_GLADLABS_DIR, "gladlabs_server_under_test")
        rebuilt = _FakeOAuth(using_oauth=True)
        calls = {"n": 0}

        async def _fake_from_pool(*_a, **_k):
            calls["n"] += 1
            return rebuilt

        async def _fake_pool():
            return MagicMock()

        monkeypatch.setattr(srv, "oauth_client_from_pool", _fake_from_pool)
        monkeypatch.setattr(srv, "_get_pool", _fake_pool)
        monkeypatch.setattr(srv, "_oauth", _FakeOAuth(using_oauth=False))

        got = await srv._get_oauth()  # noqa: SLF001
        assert got is rebuilt
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_usable_cached_client_is_reused(self, monkeypatch):
        srv = _import_server(_MCP_GLADLABS_DIR, "gladlabs_server_under_test")

        async def _boom(*_a, **_k):
            raise AssertionError("should not rebuild a usable cached client")

        monkeypatch.setattr(srv, "oauth_client_from_pool", _boom)
        cached = _FakeOAuth(using_oauth=True)
        monkeypatch.setattr(srv, "_oauth", cached)

        got = await srv._get_oauth()  # noqa: SLF001
        assert got is cached


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
