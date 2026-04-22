"""
Unit tests for routes/revalidate_routes.py.

Tests cover:
- POST /api/revalidate-cache — revalidate_cache
- trigger_nextjs_revalidation — helper function (success, non-200, timeout, HTTP error, env vars)

The httpx network call is patched; no real HTTP I/O occurs.
Auth is overridden via dependency injection.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.revalidate_routes import router, trigger_nextjs_revalidation

AUTH_HEADERS = {"Authorization": "Bearer test-token-for-revalidate"}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


# ---------------------------------------------------------------------------
# POST /api/revalidate-cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRevalidateCache:
    def test_returns_200_on_success(self):
        with patch(
            "routes.revalidate_routes.trigger_nextjs_revalidation",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/revalidate-cache",
                json={"paths": ["/"]},
                headers=AUTH_HEADERS,
            )
        assert resp.status_code == 200

    def test_response_has_success_true_when_revalidation_succeeds(self):
        with patch(
            "routes.revalidate_routes.trigger_nextjs_revalidation",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(_build_app())
            data = client.post(
                "/api/revalidate-cache",
                json={"paths": ["/"]},
                headers=AUTH_HEADERS,
            ).json()
        assert data["success"] is True
        assert "successful" in data["message"].lower()

    def test_response_has_success_false_when_revalidation_fails(self):
        with patch(
            "routes.revalidate_routes.trigger_nextjs_revalidation",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(_build_app())
            data = client.post(
                "/api/revalidate-cache",
                json={"paths": ["/"]},
                headers=AUTH_HEADERS,
            ).json()
        assert data["success"] is False
        assert "failed" in data["message"].lower()

    def test_response_includes_paths(self):
        with patch(
            "routes.revalidate_routes.trigger_nextjs_revalidation",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(_build_app())
            data = client.post(
                "/api/revalidate-cache",
                json={"paths": ["/", "/archive"]},
                headers=AUTH_HEADERS,
            ).json()
        assert data["paths"] == ["/", "/archive"]

    def test_defaults_to_root_and_archive_when_paths_omitted(self):
        with patch(
            "routes.revalidate_routes.trigger_nextjs_revalidation",
            new=AsyncMock(return_value=True),
        ) as mock_trigger:
            client = TestClient(_build_app())
            client.post("/api/revalidate-cache", json={}, headers=AUTH_HEADERS)
        # trigger_nextjs_revalidation should have been called with default paths
        mock_trigger.assert_called_once()
        called_paths = mock_trigger.call_args[0][0]
        assert "/" in called_paths
        assert "/archive" in called_paths

    def test_requires_auth(self):
        """Without auth, the endpoint returns 401 (uses Depends(verify_api_token))."""
        # Build app without auth override to test the actual auth guard
        app = FastAPI()
        app.include_router(router)
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/revalidate-cache", json={"paths": ["/"]})
        assert resp.status_code == 401

    def test_custom_paths_are_forwarded(self):
        # Since ae8d57b2 the revalidation call also forwards tags (for
        # tag-based ISR cache invalidation). Default tags are
        # ['posts', 'post-index']. Verify both arguments are passed.
        custom_paths = ["/blog", "/about"]
        with patch(
            "routes.revalidate_routes.trigger_nextjs_revalidation",
            new=AsyncMock(return_value=True),
        ) as mock_trigger:
            client = TestClient(_build_app())
            client.post(
                "/api/revalidate-cache",
                json={"paths": custom_paths},
                headers=AUTH_HEADERS,
            )
        mock_trigger.assert_called_once()
        call_args = mock_trigger.call_args.args
        assert call_args[0] == custom_paths
        # Second positional is default tags (or the caller-provided list)
        assert call_args[1] == ["posts", "post-index"]

    def test_returns_200_with_empty_paths_list(self):
        """Empty paths list falls back to default paths inside the route handler."""
        with patch(
            "routes.revalidate_routes.trigger_nextjs_revalidation",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/revalidate-cache",
                json={"paths": []},
                headers=AUTH_HEADERS,
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# trigger_nextjs_revalidation — direct unit tests
# ---------------------------------------------------------------------------


def _make_mock_httpx_client(status_code: int = 200, text: str = "ok"):
    """Build a context-manager mock for httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


def _mock_site_config():
    """Return a mock site_config that provides a revalidate_secret."""
    cfg = MagicMock()
    cfg.get = lambda key, default=None: {
        "revalidate_secret": "test-secret",
        "public_site_url": "http://localhost:3000",
    }.get(key, default)
    return cfg


@pytest.mark.unit
class TestTriggerNextjsRevalidation:
    """Direct tests of the revalidation helper.

    Post-Phase-H (GH#95) site_config is injected via keyword arg. Each
    test builds a fresh mock and passes it explicitly — no module-level
    singleton to mutate.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_true_on_200(self):
        mock_client = _make_mock_httpx_client(status_code=200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/", "/archive"], site_config=_mock_site_config(),
            ))
        assert result is True

    def test_returns_false_on_non_200(self):
        mock_client = _make_mock_httpx_client(status_code=500, text="Internal Server Error")
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/blog"], site_config=_mock_site_config(),
            ))
        assert result is False

    def test_returns_false_on_404(self):
        mock_client = _make_mock_httpx_client(status_code=404, text="Not Found")
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/about"], site_config=_mock_site_config(),
            ))
        assert result is False

    def test_returns_false_on_timeout(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/", "/archive"], site_config=_mock_site_config(),
            ))
        assert result is False

    def test_returns_false_on_http_error(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("Connection refused"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/blog"], site_config=_mock_site_config(),
            ))
        assert result is False

    def test_returns_false_on_os_error(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=OSError("Network unreachable"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/blog"], site_config=_mock_site_config(),
            ))
        assert result is False

    def test_default_paths_are_root_and_archive(self):
        """When paths=None the helper should call with ["/", "/archive"]."""
        mock_client = _make_mock_httpx_client(status_code=200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                None, site_config=_mock_site_config(),
            ))
        assert result is True
        call_kwargs = mock_client.post.call_args
        posted_json = call_kwargs[1].get("json") or call_kwargs[0][1] if call_kwargs[0] else {}
        # Confirm paths in the posted body
        assert "/" in (posted_json.get("paths", []) if isinstance(posted_json, dict) else [])

    def test_uses_site_config_for_nextjs_url(self):
        """site_config URL overrides the default localhost URL."""
        mock_client = _make_mock_httpx_client(status_code=200)
        mock_cfg = MagicMock()
        mock_cfg.get = lambda key, default=None: {
            "revalidate_secret": "test-secret",
            "public_site_url": "http://my-site.example.com",
        }.get(key, default)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/blog"], site_config=mock_cfg,
            ))
        assert result is True
        # Confirm the call URL contains the custom host
        post_args = mock_client.post.call_args
        url = post_args[0][0] if post_args[0] else post_args[1].get("url", "")
        assert "my-site.example.com" in url

    def test_strips_api_suffix_from_base_url(self):
        """If NEXT_PUBLIC_API_BASE_URL ends in /api it is stripped before appending /api/revalidate."""
        mock_client = _make_mock_httpx_client(status_code=200)
        mock_cfg = MagicMock()
        mock_cfg.get = lambda key, default=None: {
            "revalidate_secret": "test-secret",
            "public_site_url": "",
            "next_public_api_base_url": "http://stripped.example.com/api",
        }.get(key, default)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=mock_client):
            result = self._run(trigger_nextjs_revalidation(
                ["/blog"], site_config=mock_cfg,
            ))
        assert result is True
