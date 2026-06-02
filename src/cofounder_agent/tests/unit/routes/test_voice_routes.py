"""Unit tests for ``routes/voice_routes.py`` (Glad-Labs/poindexter#647).

``GET /voice/join`` mints a 7-day LiveKit JWT and serves a self-
contained HTML client. Behavioral coverage:

- auth gate: unauthenticated request 401s (2026-05-12 security audit —
  the route was public over the Tailscale Funnel and any visitor could
  mint a room-join token)
- fail-loud: refuses (503) when ``LIVEKIT_API_SECRET`` /
  ``LIVEKIT_API_KEY`` are unset OR still the dev placeholder — minting
  forgeable tokens is worse than a 503
- happy path: with both env vars set, returns 200 HTML embedding a
  minted token + the wss URL

The ``_mint_livekit_token`` helper is also unit-tested directly for
its JWT shape (3 dot-delimited segments, no padding).
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.voice_routes import _DEV_PLACEHOLDER_SECRET, _mint_livekit_token, router


def _build_app(*, authed=True):
    app = FastAPI()
    app.include_router(router)
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-principal"
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
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceJoinAuth:
    def test_unauthenticated_returns_401(self, monkeypatch):
        # Even with valid config, no auth → 401 (the audit fix).
        _set_livekit_env(monkeypatch)
        client = TestClient(_build_app(authed=False), raise_server_exceptions=False)
        resp = client.get("/voice/join")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Fail-loud config gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceJoinFailLoud:
    def test_missing_secret_returns_503(self, monkeypatch):
        _set_livekit_env(monkeypatch, key="lk_api_key", secret=None)
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join")
        assert resp.status_code == 503
        assert "LIVEKIT" in resp.json()["detail"]

    def test_missing_key_returns_503(self, monkeypatch):
        _set_livekit_env(monkeypatch, key=None, secret="real-secret-value")
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join")
        assert resp.status_code == 503

    def test_dev_placeholder_secret_returns_503(self, monkeypatch):
        """Refuse to mint against the well-known dev placeholder — those
        tokens are forgeable by anyone who knows the placeholder."""
        _set_livekit_env(
            monkeypatch, key="lk_api_key", secret=_DEV_PLACEHOLDER_SECRET,
        )
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/voice/join")
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
        resp = client.get("/voice/join")
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
            "LIVEKIT_PUBLIC_WSS_URL", "wss://voice.example.ts.net/livekit",
        )
        client = TestClient(_build_app())
        resp = client.get("/voice/join")
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
