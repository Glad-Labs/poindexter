"""Unit tests for ``routes/voice_routes.py``.

``GET /voice/join`` mints a 7-day LiveKit JWT and serves a self-
contained HTML client. As of 2026-06-02 the route is **tailnet-only**:
it is gated on the ``Tailscale-User-Login`` header that Tailscale Serve
injects for authenticated tailnet devices (``require_tailnet``), NOT the
machine-OAuth bearer it briefly used after the 2026-05-12 audit. Public
Funnel traffic carries no such header (Tailscale strips client-supplied
``Tailscale-*`` headers at ingress). Behavioral coverage:

- auth gate: a request with no (or empty) ``Tailscale-User-Login``
  header (public / Funnel) is refused 403
- fail-loud: with the tailnet header present, refuses (503) when
  ``LIVEKIT_API_SECRET`` / ``LIVEKIT_API_KEY`` are unset OR still the dev
  placeholder — minting forgeable tokens is worse than a 503
- happy path: tailnet header + both env vars set → 200 HTML embedding a
  minted token + the wss URL

The ``_mint_livekit_token`` helper is also unit-tested directly for its
JWT shape (3 dot-delimited segments, no padding).
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.voice_routes import (
    _DEV_PLACEHOLDER_SECRET,
    _mint_livekit_token,
    _resolve_voice_room,
    router,
)

# Tailscale Serve injects this header for authenticated tailnet devices;
# public Funnel traffic never carries it. Presence == tailnet caller.
_TAILNET = {"Tailscale-User-Login": "operator@example.com"}


def _build_app():
    app = FastAPI()
    app.include_router(router)
    return app


def _set_livekit_env(monkeypatch, *, key="lk_api_key", secret="real-secret-value"):
    if key is None:
        monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LIVEKIT_API_KEY", key)
    if secret is None:
        monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    else:
        monkeypatch.setenv("LIVEKIT_API_SECRET", secret)


# ---------------------------------------------------------------------------
# Auth gate — tailnet-only
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceJoinAuth:
    def test_no_tailnet_header_returns_403(self, monkeypatch):
        # Even with valid config, a public/Funnel caller (no
        # Tailscale-User-Login header) is refused.
        _set_livekit_env(monkeypatch)
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join")
        assert resp.status_code == 403
        assert "tailnet" in resp.text.lower()

    def test_empty_tailnet_header_returns_403(self, monkeypatch):
        # Presence alone isn't enough — a blank identity is rejected.
        _set_livekit_env(monkeypatch)
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join", headers={"Tailscale-User-Login": "   "})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Fail-loud config gate (tailnet header present so we reach the config check)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceJoinFailLoud:
    def test_missing_secret_returns_503(self, monkeypatch):
        _set_livekit_env(monkeypatch, key="lk_api_key", secret=None)
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join", headers=_TAILNET)
        assert resp.status_code == 503
        assert "LIVEKIT" in resp.json()["detail"]

    def test_missing_key_returns_503(self, monkeypatch):
        _set_livekit_env(monkeypatch, key=None, secret="real-secret-value")
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join", headers=_TAILNET)
        assert resp.status_code == 503

    def test_dev_placeholder_secret_returns_503(self, monkeypatch):
        """Refuse to mint against the well-known dev placeholder — those
        tokens are forgeable by anyone who knows the placeholder."""
        _set_livekit_env(
            monkeypatch,
            key="lk_api_key",
            secret=_DEV_PLACEHOLDER_SECRET,
        )
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join", headers=_TAILNET)
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceJoinHappyPath:
    def test_configured_returns_200_html_with_token(self, monkeypatch):
        _set_livekit_env(monkeypatch, key="lk_api_key", secret="real-secret-value")
        client = TestClient(_build_app())
        resp = client.get("/voice/join", headers=_TAILNET)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        body = resp.text
        # The page embeds the room + identity + a minted token + wss url.
        assert "Talk to Poindexter" in body
        assert "poindexter" in body
        assert "const TOKEN =" in body
        assert "const WSS_URL =" in body

    def test_respects_public_wss_url_override(self, monkeypatch):
        """voice_agent_livekit_url is used as WSS_URL when public override is unset."""
        monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
        monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
        cfg = _FakeSiteConfig(
            secrets={"livekit_api_key": "lk_api_key", "livekit_api_secret": "real-secret-value"},
            values={"voice_agent_livekit_url": "wss://voice.example.ts.net/livekit"},
        )
        client = TestClient(_build_app_with_site_config(cfg))
        resp = client.get("/voice/join", headers=_TAILNET)
        assert resp.status_code == 200
        assert "wss://voice.example.ts.net/livekit" in resp.text

    def test_browser_url_overrides_bot_url(self, monkeypatch):
        """voice_agent_public_livekit_url takes precedence over voice_agent_livekit_url
        in the HTML page, so the bot's internal ws:// URL stays separate from the
        browser-facing wss:// URL (mixed-content fix)."""
        monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
        monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
        cfg = _FakeSiteConfig(
            secrets={"livekit_api_key": "lk_api_key", "livekit_api_secret": "real-secret-value"},
            values={
                "voice_agent_livekit_url": "ws://livekit:7880",
                "voice_agent_public_livekit_url": "wss://voice.example.ts.net:7880",
            },
        )
        client = TestClient(_build_app_with_site_config(cfg))
        resp = client.get("/voice/join", headers=_TAILNET)
        assert resp.status_code == 200
        assert "wss://voice.example.ts.net:7880" in resp.text
        assert "ws://livekit:7880" not in resp.text


# ---------------------------------------------------------------------------
# Token minter (unit)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMintLivekitToken:
    def test_token_has_three_segments(self):
        token = _mint_livekit_token(
            api_key="lk_api_key",
            api_secret="some-secret",
            identity="matt",
            room="poindexter",
        )
        parts = token.split(".")
        assert len(parts) == 3

    def test_payload_carries_room_join_grant(self):
        token = _mint_livekit_token(
            api_key="lk_api_key",
            api_secret="some-secret",
            identity="matt",
            room="poindexter",
        )
        _h, payload_b64, _sig = token.split(".")
        # base64url decode with padding restored.
        pad = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
        assert payload["iss"] == "lk_api_key"
        assert payload["sub"] == "matt"
        assert payload["video"]["roomJoin"] is True
        assert payload["video"]["room"] == "poindexter"
        # 7-day TTL window.
        assert payload["exp"] - payload["iat"] == 7 * 24 * 3600


# ---------------------------------------------------------------------------
# Room routing for the two-room split (#1006) — `/voice/join?room=`.
# ---------------------------------------------------------------------------


def test_resolve_voice_room_allows_both_rooms():
    assert _resolve_voice_room("poindexter") == "poindexter"
    assert _resolve_voice_room("claude-code") == "claude-code"


def test_resolve_voice_room_normalises_case_and_space():
    assert _resolve_voice_room("  Claude-Code ") == "claude-code"


def test_resolve_voice_room_unknown_or_absent_uses_default():
    """A bad/absent value must NOT mint a token for an arbitrary room — it
    falls back to default_room (so a typo can't escape the allowlist)."""
    assert _resolve_voice_room("../../evil-room") == "poindexter"
    assert _resolve_voice_room("") == "poindexter"
    assert _resolve_voice_room(None) == "poindexter"


def test_resolve_voice_room_explicit_default_room():
    """default_room param overrides the built-in 'poindexter' fallback."""
    # Unknown input → supplied default; allow-listed room still wins.
    assert _resolve_voice_room("nope", default_room="poindexter") == "poindexter"
    assert _resolve_voice_room("claude-code", default_room="poindexter") == "claude-code"


# ---------------------------------------------------------------------------
# DB-first creds (#1000) — voice_join mints from app_settings, env fallback.
# ---------------------------------------------------------------------------


class _FakeSiteConfig:
    """SiteConfig stand-in bound to app.state: sync get for plain settings,
    async get_secret for secrets (#1000/#717)."""

    def __init__(self, secrets: dict, values: dict | None = None):
        self._secrets = secrets
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)

    async def get_secret(self, key, default=""):
        return self._secrets.get(key, default)


def _build_app_with_site_config(site_config):
    from types import SimpleNamespace

    app = FastAPI()
    app.include_router(router)
    # DI seam (#272): the route resolves site_config via app.state.container
    # (get_site_config_dependency), not the retired app.state.site_config.
    app.state.container = SimpleNamespace(site_config=site_config)
    return app


@pytest.mark.unit
def test_voice_join_mints_from_app_settings_when_env_empty(monkeypatch):
    """With NO LiveKit env vars but app_settings populated, the route mints
    from the DB secret (#1000) — proving DB-first, not env-only."""
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    cfg = _FakeSiteConfig(
        {"livekit_api_key": "db-key", "livekit_api_secret": "db-secret-value"},
    )
    client = TestClient(_build_app_with_site_config(cfg))
    resp = client.get("/voice/join", headers=_TAILNET)
    assert resp.status_code == 200
    assert "const TOKEN =" in resp.text


@pytest.mark.unit
def test_voice_join_503_when_db_and_env_both_empty(monkeypatch):
    """Empty DB rows + no env -> the resolver's dev defaults -> fail-loud 503
    (no forgeable token against the well-known placeholder)."""
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    cfg = _FakeSiteConfig({"livekit_api_key": "", "livekit_api_secret": ""})
    client = TestClient(
        _build_app_with_site_config(cfg), raise_server_exceptions=False,
    )
    resp = client.get("/voice/join", headers=_TAILNET)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Unique identity per device (#1006) — two clients must not collide on one
# identity (LiveKit kicks duplicates, causing phone+PC to flap).
# ---------------------------------------------------------------------------


def _token_payload(token: str) -> dict:
    _h, p, _s = token.split(".")
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def _token_from_page(body: str) -> str:
    return body.split('const TOKEN = "', 1)[1].split('"', 1)[0]


@pytest.mark.unit
def test_each_join_gets_unique_identity(monkeypatch):
    """Two /voice/join requests must mint DIFFERENT identities (so a phone +
    PC can coexist), both keeping the human-facing display name."""
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    cfg = _FakeSiteConfig(
        secrets={"livekit_api_key": "lk_api_key", "livekit_api_secret": "real-secret-value"},
        values={"voice_agent_default_identity": "matt"},
    )
    client = TestClient(_build_app_with_site_config(cfg))

    p1 = _token_payload(_token_from_page(client.get("/voice/join", headers=_TAILNET).text))
    p2 = _token_payload(_token_from_page(client.get("/voice/join", headers=_TAILNET).text))

    assert p1["sub"] != p2["sub"], "two joins must get distinct LiveKit identities"
    assert p1["sub"].startswith("matt-") and p2["sub"].startswith("matt-")
    assert p1["name"] == "matt" and p2["name"] == "matt"  # clean display label


@pytest.mark.unit
def test_mint_token_name_distinct_from_unique_identity():
    """The minter signs the unique identity into ``sub`` but a clean ``name``."""
    token = _mint_livekit_token(
        api_key="lk_api_key", api_secret="s", identity="matt-a1b2c3",
        room="claude-code", name="matt",
    )
    payload = _token_payload(token)
    assert payload["sub"] == "matt-a1b2c3"  # unique per device
    assert payload["name"] == "matt"        # human-facing


# ---------------------------------------------------------------------------
# DB-first room + identity (#717) — voice_join reads room/identity from
# app_settings, not env vars.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_voice_join_room_from_app_settings(monkeypatch):
    """voice_agent_room_name in app_settings is used as the default room."""
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.delenv("LIVEKIT_ROOM", raising=False)
    cfg = _FakeSiteConfig(
        secrets={"livekit_api_key": "lk_api_key", "livekit_api_secret": "real-secret-value"},
        values={"voice_agent_room_name": "claude-code"},
    )
    client = TestClient(_build_app_with_site_config(cfg))
    resp = client.get("/voice/join", headers=_TAILNET)
    assert resp.status_code == 200
    payload = _token_payload(_token_from_page(resp.text))
    assert payload["video"]["room"] == "claude-code"


@pytest.mark.unit
def test_voice_join_identity_from_app_settings(monkeypatch):
    """voice_agent_default_identity in app_settings is used as the display name."""
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.delenv("LIVEKIT_DEFAULT_IDENTITY", raising=False)
    cfg = _FakeSiteConfig(
        secrets={"livekit_api_key": "lk_api_key", "livekit_api_secret": "real-secret-value"},
        values={"voice_agent_default_identity": "test-operator"},
    )
    client = TestClient(_build_app_with_site_config(cfg))
    resp = client.get("/voice/join", headers=_TAILNET)
    assert resp.status_code == 200
    payload = _token_payload(_token_from_page(resp.text))
    assert payload["name"] == "test-operator"
    assert payload["sub"].startswith("test-operator-")


@pytest.mark.unit
def test_voice_join_env_fallback_room_when_db_empty(monkeypatch):
    """Empty voice_agent_room_name → LIVEKIT_ROOM env is used (#717 fallback)."""
    _set_livekit_env(monkeypatch, key="lk_api_key", secret="real-secret-value")
    monkeypatch.setenv("LIVEKIT_ROOM", "claude-code")
    cfg = _FakeSiteConfig(
        secrets={},
        values={"voice_agent_room_name": ""},  # empty = unset
    )
    client = TestClient(_build_app_with_site_config(cfg))
    resp = client.get("/voice/join", headers=_TAILNET)
    assert resp.status_code == 200
    payload = _token_payload(_token_from_page(resp.text))
    assert payload["video"]["room"] == "claude-code"


@pytest.mark.unit
def test_voice_join_env_fallback_identity_when_db_empty(monkeypatch):
    """Empty voice_agent_default_identity → LIVEKIT_DEFAULT_IDENTITY env used (#717)."""
    _set_livekit_env(monkeypatch, key="lk_api_key", secret="real-secret-value")
    monkeypatch.setenv("LIVEKIT_DEFAULT_IDENTITY", "test-operator")
    cfg = _FakeSiteConfig(
        secrets={},
        values={"voice_agent_default_identity": ""},  # empty = unset
    )
    client = TestClient(_build_app_with_site_config(cfg))
    resp = client.get("/voice/join", headers=_TAILNET)
    assert resp.status_code == 200
    payload = _token_payload(_token_from_page(resp.text))
    assert payload["name"] == "test-operator"
    assert payload["sub"].startswith("test-operator-")
