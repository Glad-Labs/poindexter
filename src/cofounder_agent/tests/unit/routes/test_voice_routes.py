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
_TAILNET = {"Tailscale-User-Login": "matt@gladlabs.io"}


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
        monkeypatch.setenv("LIVEKIT_ROOM", "poindexter")
        monkeypatch.setenv("LIVEKIT_DEFAULT_IDENTITY", "operator")
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
        _set_livekit_env(monkeypatch, key="lk_api_key", secret="real-secret-value")
        monkeypatch.setenv(
            "LIVEKIT_PUBLIC_WSS_URL",
            "wss://voice.example.ts.net/livekit",
        )
        client = TestClient(_build_app())
        resp = client.get("/voice/join", headers=_TAILNET)
        assert resp.status_code == 200
        assert "wss://voice.example.ts.net/livekit" in resp.text


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


def test_resolve_voice_room_allows_both_rooms(monkeypatch):
    monkeypatch.delenv("LIVEKIT_ROOM", raising=False)
    assert _resolve_voice_room("poindexter") == "poindexter"
    assert _resolve_voice_room("claude-code") == "claude-code"


def test_resolve_voice_room_normalises_case_and_space(monkeypatch):
    monkeypatch.delenv("LIVEKIT_ROOM", raising=False)
    assert _resolve_voice_room("  Claude-Code ") == "claude-code"


def test_resolve_voice_room_unknown_or_absent_uses_default(monkeypatch):
    """A bad/absent value must NOT mint a token for an arbitrary room — it
    falls back to the default (so a typo can't escape the allowlist)."""
    monkeypatch.delenv("LIVEKIT_ROOM", raising=False)  # default -> poindexter
    assert _resolve_voice_room("../../evil-room") == "poindexter"
    assert _resolve_voice_room("") == "poindexter"
    assert _resolve_voice_room(None) == "poindexter"


def test_resolve_voice_room_default_honours_env(monkeypatch):
    monkeypatch.setenv("LIVEKIT_ROOM", "poindexter")
    # Unknown -> the env default; an allow-listed room still wins.
    assert _resolve_voice_room("nope") == "poindexter"
    assert _resolve_voice_room("claude-code") == "claude-code"
