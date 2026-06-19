"""Unit tests for the vercel_isr outbound handler.

The discord_post / telegram_post handler tests were removed when those
per-channel handlers were superseded by the generic
``outbound.apprise_notify`` handler (see
``tests/unit/services/integrations/handlers/test_outbound_apprise.py``).
The Telegram Bot API helpers that survived the cutover are covered by
``tests/unit/services/test_pipeline_streaming.py``.
"""

from __future__ import annotations

import httpx
import pytest

from services.integrations.handlers import outbound_vercel_isr


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
