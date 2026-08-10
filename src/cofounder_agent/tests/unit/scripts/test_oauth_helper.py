"""Unit tests for ``scripts/_oauth_helper.py`` (Glad-Labs/poindexter#248).

Mirrors ``tests/unit/services/auth/test_oauth_client.py`` — same five
behaviours, same MockTransport rig — but exercises the standalone
``ScriptsOAuthClient`` mirror that ships in ``scripts/`` for consumers
that don't have ``services/`` on PYTHONPATH.

Also covers the bootstrap.toml + app_settings credential resolution
chain unique to this helper.
"""

from __future__ import annotations

import base64
import importlib
import json
import time
from pathlib import Path

import httpx
import pytest

# ``scripts/`` isn't on the default test PYTHONPATH; insert it so we
# can import the helper as a top-level module the same way the real
# scripts do.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_SCRIPTS = _REPO_ROOT / "scripts"
import sys

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import _oauth_helper  # noqa: E402
from _oauth_helper import (  # noqa: E402
    ScriptsOAuthClient,
    _decode_jwt_exp,
)

# Pristine state, captured at IMPORT time — i.e. during collection, before
# any test in any file has run. The restore fixture below must hand exactly
# these objects back to whoever runs next; ``TestReloadLeavesNoCrossFileDamage``
# asserts against them. Capturing inside a test would be worthless: by then a
# reload may already have replaced them, and the guard would happily compare a
# leaked object to itself.
_ORIGINAL_NAMESPACE = dict(_oauth_helper.__dict__)
_ORIGINAL_CLIENT = ScriptsOAuthClient
_ORIGINAL_CACHED_TOKEN = _oauth_helper._CachedToken
_ORIGINAL_BOOTSTRAP_PATH = _oauth_helper._BOOTSTRAP_PATH


def _reload_helper():
    """Re-execute the helper against the currently-patched ``Path.home``.

    ``_BOOTSTRAP_PATH`` is computed once at module scope, so the bootstrap.toml
    tests below can't just point HOME at a ``tmp_path`` — they have to re-run
    the module body for the new HOME to take. Reloading (rather than
    monkeypatching the global directly) is deliberate: it keeps the tests
    honest about the real import-time computation, which is what every
    ``scripts/`` consumer actually gets.

    The cost is namespace damage, which ``_restore_oauth_helper_module`` undoes.
    """
    import _oauth_helper as helper

    return importlib.reload(helper)


@pytest.fixture(autouse=True)
def _restore_oauth_helper_module():
    """Undo ``importlib.reload``'s damage to the module namespace.

    Two distinct leaks escape this file without it, both verified by probe:

    1. **Stale module globals.** The reloads run while ``Path.home`` is
       monkeypatched to a ``tmp_path``, so the module recomputes
       ``_BOOTSTRAP_PATH`` against the temp HOME. ``monkeypatch`` restores
       ``Path.home`` at teardown, but nothing restores the global it fed —
       so for the rest of the session ``_oauth_helper._BOOTSTRAP_PATH`` points
       at a deleted ``/tmp/pytest-of-*/…/bootstrap.toml``, and any later test
       exercising credential resolution silently reads from nowhere.

    2. **Class identity.** ``importlib.reload`` re-executes the module in its
       EXISTING ``__dict__``, so every class it defines is replaced by a
       brand-new object with the same name. ``ScriptsOAuthClient`` bound at
       this file's import time is no longer the class the reloaded module
       exposes; ``isinstance`` between them is False.

    Hazard 2 is not hypothetical — it is the exact mechanism behind the
    order-dependent failures fixed in stack#3155, where
    ``test_litellm_langfuse_callback.py`` bound an exception class at
    collection and could no longer catch it once another file had reloaded the
    module underneath it. Those tests passed in isolation and failed only in a
    full-suite run, which read as flakiness for three days.

    Nothing imports ``_oauth_helper`` outside this file today, so both leaks
    are currently latent. That is a property of the test suite on one
    particular day, not a fix.

    Re-reloading at the end does NOT clean up: it mints a third set of class
    objects, equal to neither. The only restoration that works is putting the
    ORIGINAL objects back, so snapshot the namespace and hard-restore it.
    """
    import _oauth_helper as helper

    snapshot = dict(helper.__dict__)
    try:
        yield
    finally:
        helper.__dict__.clear()
        helper.__dict__.update(snapshot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(exp_offset: int = 3600, sub: str = "pdx_test") -> str:
    """Build a JWT-shaped string with the given expiry offset.

    No signature verification client-side; we just need something that
    decodes through ``_decode_jwt_exp``.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "poindexter",
        "sub": sub,
        "scope": "api:read api:write",
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
        "jti": "test-jti",
    }

    def _b64(d):
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_b64(header)}.{_b64(payload)}.signature"


# ---------------------------------------------------------------------------
# JWT exp parsing
# ---------------------------------------------------------------------------


class TestDecodeJWTExp:
    def test_valid_jwt_returns_exp(self):
        token = _make_jwt(exp_offset=600)
        exp = _decode_jwt_exp(token)
        assert exp is not None
        assert exp > int(time.time())

    def test_non_jwt_returns_none(self):
        assert _decode_jwt_exp("plaintext-static-token") is None

    def test_two_segment_token_returns_none(self):
        assert _decode_jwt_exp("aaa.bbb") is None

    def test_garbage_payload_returns_none(self):
        assert _decode_jwt_exp("aaa.not-base64.zzz") is None


# ---------------------------------------------------------------------------
# Caching + expiry
# ---------------------------------------------------------------------------


class TestScriptsOAuthClientCaching:
    @pytest.mark.asyncio
    async def test_first_call_mints_subsequent_calls_use_cache(self):
        mint_count = 0
        token = _make_jwt(exp_offset=3600)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mint_count
            assert request.url.path == "/token"
            mint_count += 1
            return httpx.Response(
                200,
                json={
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "api:read api:write",
                },
            )

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        t1 = await client.get_token()
        t2 = await client.get_token()
        t3 = await client.get_token()
        assert t1 == t2 == t3 == token
        assert mint_count == 1
        await client.aclose()


class TestScriptsOAuthClientExpiry:
    @pytest.mark.asyncio
    async def test_cache_expiry_triggers_refresh(self):
        mint_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mint_count
            mint_count += 1
            return httpx.Response(
                200,
                json={
                    "access_token": _make_jwt(exp_offset=3600),
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        await client.get_token()
        # Force the cached refresh deadline into the past.
        client._cached.refresh_at = time.time() - 1  # noqa: SLF001
        await client.get_token()
        assert mint_count == 2
        await client.aclose()


# ---------------------------------------------------------------------------
# 401 retry
# ---------------------------------------------------------------------------


class TestScriptsOAuthClient401Retry:
    @pytest.mark.asyncio
    async def test_401_invalidates_and_retries(self):
        mint_count = 0
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mint_count, request_count
            if request.url.path == "/token":
                mint_count += 1
                return httpx.Response(
                    200,
                    json={
                        "access_token": _make_jwt(exp_offset=3600),
                        "expires_in": 3600,
                    },
                )
            request_count += 1
            if request_count == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"ok": True})

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        resp = await client.get("/api/posts")
        assert resp.status_code == 200
        assert mint_count == 2  # initial + post-401 refresh
        assert request_count == 2  # original + retry
        await client.aclose()

    @pytest.mark.asyncio
    async def test_401_retry_off_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(
                    200,
                    json={"access_token": _make_jwt(), "expires_in": 3600},
                )
            return httpx.Response(401, json={"error": "invalid_token"})

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        resp = await client.request("GET", "/api/posts", retry_on_401=False)
        assert resp.status_code == 401
        await client.aclose()


# ---------------------------------------------------------------------------
# Fail-loud when OAuth credentials are missing (Phase 3 #249)
# ---------------------------------------------------------------------------


class TestScriptsOAuthClientFailLoud:
    @pytest.mark.asyncio
    async def test_no_credentials_at_all_raises(self):
        """Phase 3 (#249): no static-Bearer fallback. Fail loud."""
        client = ScriptsOAuthClient(base_url="http://test")
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await client.get_token()


# ---------------------------------------------------------------------------
# bootstrap.toml + app_settings credential resolution
# ---------------------------------------------------------------------------


class TestBootstrapValueReader:
    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        # Point HOME at a directory with no bootstrap.toml — the helper
        # is expected to swallow the missing-file case silently.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Reload module so the cached _BOOTSTRAP_PATH picks up the new HOME.
        helper = _reload_helper()
        assert helper._read_bootstrap_value("scripts_oauth_client_id") == ""

    def test_present_value_returned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        bootstrap_dir = tmp_path / ".poindexter"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        (bootstrap_dir / "bootstrap.toml").write_text(
            'scripts_oauth_client_id = "pdx_from_toml"\n'
            'scripts_oauth_client_secret = "toml-secret"\n',
            encoding="utf-8",
        )

        helper = _reload_helper()
        assert helper._read_bootstrap_value("scripts_oauth_client_id") == "pdx_from_toml"
        assert helper._read_bootstrap_value("scripts_oauth_client_secret") == "toml-secret"
        assert helper._read_bootstrap_value("missing_key") == ""


class TestResolveCredentialsResolutionOrder:
    """The resolver layers three sources: explicit args >
    bootstrap.toml > app_settings. Walk through each layer to make
    sure higher-priority values win.

    Phase 3 (#249) removed the legacy ``api_token`` fallback layer.
    """

    @pytest.mark.asyncio
    async def test_explicit_args_win_over_everything(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Pre-seed bootstrap.toml with a value that should be ignored.
        bootstrap_dir = tmp_path / ".poindexter"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        (bootstrap_dir / "bootstrap.toml").write_text(
            'scripts_oauth_client_id = "ignored-from-toml"\n'
            'scripts_oauth_client_secret = "ignored-from-toml"\n',
            encoding="utf-8",
        )
        helper = _reload_helper()

        client_id, client_secret = await helper.resolve_credentials(
            pool=None,
            explicit_client_id="explicit-id",
            explicit_client_secret="explicit-secret",
        )
        assert client_id == "explicit-id"
        assert client_secret == "explicit-secret"

    @pytest.mark.asyncio
    async def test_bootstrap_toml_used_when_no_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        bootstrap_dir = tmp_path / ".poindexter"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        (bootstrap_dir / "bootstrap.toml").write_text(
            'scripts_oauth_client_id = "pdx_from_toml"\n'
            'scripts_oauth_client_secret = "toml-secret"\n',
            encoding="utf-8",
        )
        helper = _reload_helper()

        client_id, client_secret = await helper.resolve_credentials(
            pool=None,
        )
        assert client_id == "pdx_from_toml"
        assert client_secret == "toml-secret"

    @pytest.mark.asyncio
    async def test_app_settings_consulted_when_bootstrap_blank(self, tmp_path, monkeypatch):
        # No bootstrap.toml — the helper must fall through to the pool.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        helper = _reload_helper()

        # Stub the pool's fetchrow to return a different value per key.
        # The real read path uses ``await pool.fetchrow`` directly (no
        # ``async with pool.acquire()``), matching the brain's helper.
        seen_keys = []

        async def _fetchrow(query, key):
            seen_keys.append(key)
            mapping = {
                "scripts_oauth_client_id": ("pdx_from_db", False),
                "scripts_oauth_client_secret": ("db-secret", False),
            }
            value, is_secret = mapping.get(key, (None, False))
            if value is None:
                return None
            return {"value": value, "is_secret": is_secret}

        class _StubPool:
            fetchrow = staticmethod(_fetchrow)

        pool = _StubPool()
        client_id, client_secret = await helper.resolve_credentials(pool=pool)

        assert client_id == "pdx_from_db"
        assert client_secret == "db-secret"
        # Only the OAuth keys are queried — api_token is no longer read (#249).
        assert "scripts_oauth_client_id" in seen_keys
        assert "scripts_oauth_client_secret" in seen_keys
        assert "api_token" not in seen_keys

    @pytest.mark.asyncio
    async def test_no_creds_anywhere_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        helper = _reload_helper()

        client_id, client_secret = await helper.resolve_credentials(
            pool=None,
        )
        assert client_id == ""
        assert client_secret == ""


# ---------------------------------------------------------------------------
# The reloads above must not leak (stack#3155 hazard class)
# ---------------------------------------------------------------------------


class TestReloadLeavesNoCrossFileDamage:
    """Regression guard for the leaks documented on ``_restore_oauth_helper_module``.

    The guarantee is *between* tests, not within one — a test that reloads
    legitimately sees a rebuilt module while it runs; what must never happen is
    the NEXT test (or the next FILE) inheriting it.

    So these assert against the ``_ORIGINAL_*`` values captured at this file's
    import time — collection, before any test in any file has run. Passing means
    the module was handed to this test in its pristine state, which is precisely
    what ``test_litellm_langfuse_callback.py`` needed and did not get.
    """

    def test_module_globals_are_pristine_at_test_start(self):
        # Hazard 1: reloads run under a monkeypatched ``Path.home``, so an
        # unrestored module keeps a ``_BOOTSTRAP_PATH`` under a torn-down
        # ``tmp_path`` — a real path that never exists, so credential
        # resolution reads nothing and says so with a passing test.
        import _oauth_helper as helper

        assert helper._BOOTSTRAP_PATH == _ORIGINAL_BOOTSTRAP_PATH, (
            "_BOOTSTRAP_PATH is not what it was at import time — a previous "
            "test's importlib.reload leaked while Path.home was monkeypatched. "
            "Every later reader of bootstrap.toml now resolves against a "
            "deleted pytest tmp_path."
        )

    def test_class_identity_is_pristine_at_test_start(self):
        # Hazard 2: the stack#3155 mechanism. A reloaded class shares its name
        # with the original and nothing else.
        import _oauth_helper as helper

        assert helper.ScriptsOAuthClient is _ORIGINAL_CLIENT, (
            "ScriptsOAuthClient is not the object it was at import time — a "
            "previous test's importlib.reload leaked. Any file that bound this "
            "class at collection now holds a stranger: isinstance is False "
            "across the two, which is the order-dependent failure shape fixed "
            "in stack#3155."
        )
        assert helper._CachedToken is _ORIGINAL_CACHED_TOKEN
        assert isinstance(
            ScriptsOAuthClient(base_url="http://test"), helper.ScriptsOAuthClient,
        )

    def test_whole_namespace_is_pristine_at_test_start(self):
        """Catch-all, so a symbol added later inherits the guard for free.

        The two tests above name the symbols we know bite. This one holds the
        line for every other thing the module defines, including whatever
        someone adds after this file stops being read.
        """
        import _oauth_helper as helper

        current = helper.__dict__
        assert set(current) == set(_ORIGINAL_NAMESPACE), (
            "module namespace gained/lost keys since import: "
            f"added={sorted(set(current) - set(_ORIGINAL_NAMESPACE))} "
            f"removed={sorted(set(_ORIGINAL_NAMESPACE) - set(current))}"
        )
        rebound = sorted(
            name for name, obj in _ORIGINAL_NAMESPACE.items()
            if current[name] is not obj
        )
        assert not rebound, (
            f"module attributes were rebound since import: {rebound}. A test "
            "mutated or reloaded _oauth_helper without restoring it."
        )

    def test_a_reload_without_the_fixture_would_break_both(self, tmp_path, monkeypatch):
        """Proves the fixture does real work rather than passing vacuously.

        If reload were harmless, the guards above would be theatre. It is not.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        helper = _reload_helper()

        # Hazard 1 — the global followed the patched HOME and would have stayed.
        assert helper._BOOTSTRAP_PATH != _ORIGINAL_BOOTSTRAP_PATH
        assert helper._BOOTSTRAP_PATH == tmp_path / ".poindexter" / "bootstrap.toml"

        # Hazard 2 — same name, different object, isinstance False both ways.
        assert helper.ScriptsOAuthClient is not _ORIGINAL_CLIENT
        assert not isinstance(
            ScriptsOAuthClient(base_url="http://test"), helper.ScriptsOAuthClient,
        )
        # …and the autouse fixture puts it all back for whoever runs next.
