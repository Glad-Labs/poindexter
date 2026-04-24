"""Unit tests for outbound handler modules (Phase 1b)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.integrations.handlers import (
    outbound_discord,
    outbound_telegram,
    outbound_vercel_isr,
)


class _FakeSiteConfig:
    def __init__(self, secrets: dict[str, str] | None = None):
        self._secrets = secrets or {}

    async def get_secret(self, key: str, default: str = "") -> str | None:
        return self._secrets.get(key)


class _CapturingTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int = 200, body: bytes = b"ok"):
        self.status_code = status_code
        self.body = body
        self.captured: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.captured = request
        return httpx.Response(self.status_code, content=self.body, request=request)


@pytest.fixture
def patch_httpx(monkeypatch):
    """Return a helper that patches httpx.AsyncClient to use a given transport."""
    def _apply(transport: _CapturingTransport):
        original = httpx.AsyncClient

        class _Patched(original):  # type: ignore[misc]
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _Patched)
        return transport

    return _apply


# ---------------------------------------------------------------------------
# discord_post
# ---------------------------------------------------------------------------


class TestDiscordPost:
    @pytest.mark.asyncio
    async def test_string_payload_wrapped_as_content(self, patch_httpx):
        transport = patch_httpx(_CapturingTransport(status_code=204, body=b""))
        row = {"name": "discord_ops", "url": "https://discord.com/api/webhooks/x/y"}
        result = await outbound_discord.discord_post(
            "hello world",
            site_config=_FakeSiteConfig(),
            row=row,
            pool=None,
        )
        assert result["status_code"] == 204
        req = transport.captured
        assert req is not None
        assert req.url.host == "discord.com"
        assert b"hello world" in req.content

    @pytest.mark.asyncio
    async def test_dict_payload_passthrough(self, patch_httpx):
        transport = patch_httpx(_CapturingTransport(status_code=204, body=b""))
        row = {"name": "d", "url": "https://discord.com/api/webhooks/x/y"}
        await outbound_discord.discord_post(
            {"content": "x", "embeds": [{"title": "T"}]},
            site_config=_FakeSiteConfig(),
            row=row,
            pool=None,
        )
        assert b"embeds" in transport.captured.content

    @pytest.mark.asyncio
    async def test_missing_url_raises(self):
        with pytest.raises(ValueError):
            await outbound_discord.discord_post(
                "x",
                site_config=_FakeSiteConfig(),
                row={"name": "d"},
                pool=None,
            )

    @pytest.mark.asyncio
    async def test_non_2xx_raises(self, patch_httpx):
        patch_httpx(_CapturingTransport(status_code=500, body=b"boom"))
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await outbound_discord.discord_post(
                "x",
                site_config=_FakeSiteConfig(),
                row={"name": "d", "url": "https://discord.com/api/webhooks/x/y"},
                pool=None,
            )


# ---------------------------------------------------------------------------
# telegram_post
# ---------------------------------------------------------------------------


class TestTelegramPost:
    @pytest.mark.asyncio
    async def test_happy_path(self, patch_httpx):
        transport = patch_httpx(_CapturingTransport(status_code=200, body=b'{"ok":true}'))
        row = {
            "name": "tg",
            "url": "https://api.telegram.org",
            "secret_key_ref": "telegram_bot_token",
            "config": {"chat_id": "5318613610"},
        }
        result = await outbound_telegram.telegram_post(
            "hi",
            site_config=_FakeSiteConfig({"telegram_bot_token": "TOKEN"}),
            row=row,
            pool=None,
        )
        assert result["status_code"] == 200
        assert result["chat_id"] == "5318613610"
        assert transport.captured.url.path == "/botTOKEN/sendMessage"
        assert b'"text":"hi"' in transport.captured.content

    @pytest.mark.asyncio
    async def test_missing_token_raises(self):
        row = {
            "name": "tg",
            "url": "https://api.telegram.org",
            "secret_key_ref": "telegram_bot_token",
            "config": {"chat_id": "123"},
        }
        with pytest.raises(RuntimeError, match="bot token"):
            await outbound_telegram.telegram_post(
                "x", site_config=_FakeSiteConfig(), row=row, pool=None
            )

    @pytest.mark.asyncio
    async def test_missing_chat_id_raises(self):
        row = {
            "name": "tg",
            "url": "https://api.telegram.org",
            "secret_key_ref": "telegram_bot_token",
            "config": {},
        }
        with pytest.raises(RuntimeError, match="chat_id"):
            await outbound_telegram.telegram_post(
                "x",
                site_config=_FakeSiteConfig({"telegram_bot_token": "TOKEN"}),
                row=row,
                pool=None,
            )

    @pytest.mark.asyncio
    async def test_dict_payload_with_text(self, patch_httpx):
        transport = patch_httpx(_CapturingTransport(status_code=200))
        row = {
            "name": "tg",
            "url": "https://api.telegram.org",
            "secret_key_ref": "telegram_bot_token",
            "config": {"chat_id": "42"},
        }
        await outbound_telegram.telegram_post(
            {"text": "dict-payload"},
            site_config=_FakeSiteConfig({"telegram_bot_token": "T"}),
            row=row,
            pool=None,
        )
        assert b"dict-payload" in transport.captured.content


# ---------------------------------------------------------------------------
# vercel_isr
# ---------------------------------------------------------------------------


class TestVercelIsr:
    @pytest.mark.asyncio
    async def test_happy_path_uses_defaults(self, patch_httpx):
        transport = patch_httpx(_CapturingTransport(status_code=200))
        row = {
            "name": "vercel_isr",
            "url": "https://gladlabs.io",
            "secret_key_ref": "revalidate_secret",
            "config": {},
        }
        result = await outbound_vercel_isr.vercel_isr(
            {},
            site_config=_FakeSiteConfig({"revalidate_secret": "SECRET"}),
            row=row,
            pool=None,
        )
        assert result["status_code"] == 200
        req = transport.captured
        assert str(req.url) == "https://gladlabs.io/api/revalidate"
        assert req.headers.get("x-revalidate-secret") == "SECRET"
        # Defaults applied when payload has no paths/tags
        assert b'"/"' in req.content
        assert b"post-index" in req.content

    @pytest.mark.asyncio
    async def test_payload_overrides_defaults(self, patch_httpx):
        transport = patch_httpx(_CapturingTransport(status_code=200))
        row = {
            "name": "vercel_isr",
            "url": "https://gladlabs.io",
            "secret_key_ref": "revalidate_secret",
            "config": {"default_paths": ["/"], "default_tags": ["x"]},
        }
        await outbound_vercel_isr.vercel_isr(
            {"paths": ["/posts/foo"], "tags": ["post:foo"]},
            site_config=_FakeSiteConfig({"revalidate_secret": "SECRET"}),
            row=row,
            pool=None,
        )
        assert b"/posts/foo" in transport.captured.content
        assert b"post:foo" in transport.captured.content

    @pytest.mark.asyncio
    async def test_missing_secret_raises(self):
        row = {
            "name": "vercel_isr",
            "url": "https://gladlabs.io",
            "secret_key_ref": "revalidate_secret",
            "config": {},
        }
        with pytest.raises(RuntimeError, match="revalidate secret"):
            await outbound_vercel_isr.vercel_isr(
                {}, site_config=_FakeSiteConfig(), row=row, pool=None
            )

    @pytest.mark.asyncio
    async def test_missing_url_raises(self):
        with pytest.raises(ValueError):
            await outbound_vercel_isr.vercel_isr(
                {},
                site_config=_FakeSiteConfig({"revalidate_secret": "S"}),
                row={"name": "v", "secret_key_ref": "revalidate_secret", "config": {}},
                pool=None,
            )
