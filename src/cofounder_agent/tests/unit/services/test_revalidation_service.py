"""
Unit tests for services/revalidation_service.py.

Glad-Labs/poindexter#327: covers the new trigger_isr_revalidate
helper that every publish path must call. Verifies:

* Header — secret is sent in `x-revalidate-secret`, NEVER as a query
  param.
* Body — JSON payload contains both ``paths`` and ``tags``.
* URL — pulled from the new DB-configurable
  ``public_site_revalidate_url`` setting (with the legacy
  ``public_site_url`` chain as fallback).
* Canonical paths/tags — every call always includes ``/``,
  ``/archive``, ``/posts``, ``/sitemap.xml``, ``/posts/<slug>`` and
  the matching ``post:<slug>`` tag.
* Idempotent — duplicate paths/tags from the caller are deduped.
* Async secret — the helper resolves ``revalidate_secret`` via
  ``site_cfg.get_secret(...)`` (async), per the spec.
* Empty secret — call is skipped with a logger warning, returns
  False without raising.
* Never raises — httpx errors return False instead of bubbling up.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.revalidation_service import (
    _CANONICAL_PATHS,
    _CANONICAL_TAGS,
    DEFAULT_REVALIDATE_URL,
    RevalidationResult,
    _resolve_revalidate_url,
    trigger_isr_revalidate,
    trigger_nextjs_revalidation,
    trigger_nextjs_revalidation_detailed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_site_config(*, secret: str = "shh", url: str | None = None) -> MagicMock:
    """Build a stub site_config with the keys the helpers read."""
    cfg = MagicMock()
    settings: dict[str, str] = {}
    if url is not None:
        settings["public_site_revalidate_url"] = url
    cfg.get = lambda key, default=None: settings.get(key, default)
    cfg.get_secret = AsyncMock(return_value=secret)
    return cfg


def _build_httpx_client(status_code: int = 200, text: str = "ok"):
    """Build an httpx.AsyncClient context-manager mock."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# trigger_nextjs_revalidation — header, body, URL
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTriggerNextjsRevalidationHeaderAndBody:
    @pytest.mark.asyncio
    async def test_secret_is_sent_in_x_revalidate_secret_header(self):
        cfg = _build_site_config(
            secret="my-secret-token",
            url="https://www.gladlabs.io/api/revalidate",
        )
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            ok = await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        assert ok is True
        # Verify the header is present and the value matches the secret.
        sent_headers = client.post.call_args.kwargs["headers"]
        assert sent_headers["x-revalidate-secret"] == "my-secret-token"
        # Belt + suspenders: secret MUST NEVER appear as a URL param.
        url_arg = client.post.call_args.args[0]
        assert "my-secret-token" not in url_arg

    @pytest.mark.asyncio
    async def test_body_contains_paths_and_tags(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            ok = await trigger_nextjs_revalidation(
                ["/foo", "/bar"], ["tag-a", "tag-b"], site_config=cfg,
            )
        assert ok is True
        body = client.post.call_args.kwargs["json"]
        assert body == {"paths": ["/foo", "/bar"], "tags": ["tag-a", "tag-b"]}

    @pytest.mark.asyncio
    async def test_content_type_header_is_application_json(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        sent_headers = client.post.call_args.kwargs["headers"]
        assert sent_headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_uses_explicit_revalidate_url_setting(self):
        """Explicit public_site_revalidate_url overrides the legacy chain."""
        cfg = _build_site_config(url="https://staging.example.com/api/revalidate")
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        url_arg = client.post.call_args.args[0]
        assert url_arg == "https://staging.example.com/api/revalidate"

    @pytest.mark.asyncio
    async def test_skips_with_warning_when_secret_empty(self):
        """Empty secret = warn + return False, no httpx call."""
        cfg = _build_site_config(secret="", url="https://www.gladlabs.io/api/revalidate")
        with patch("services.revalidation_service.httpx.AsyncClient") as mock_client_cls, \
             patch("services.revalidation_service.logger") as mock_logger:
            ok = await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        assert ok is False
        mock_client_cls.assert_not_called()  # never even opened the client
        # warning logged, no exception raised
        mock_logger.warning.assert_called()
        warn_msg = " ".join(str(c) for c in mock_logger.warning.call_args_list)
        assert "revalidate_secret" in warn_msg

    @pytest.mark.asyncio
    async def test_returns_false_on_500_without_raising(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(500, text="boom")
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            ok = await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        assert ok is False

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout_without_raising(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            ok = await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        assert ok is False

    @pytest.mark.asyncio
    async def test_returns_false_on_arbitrary_exception_without_raising(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=RuntimeError("network kaput"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            ok = await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        assert ok is False


# ---------------------------------------------------------------------------
# _resolve_revalidate_url — fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveRevalidateUrl:
    def test_explicit_setting_wins(self):
        cfg = _build_site_config(url="https://override.example.com/api/revalidate")
        assert (
            _resolve_revalidate_url(cfg)
            == "https://override.example.com/api/revalidate"
        )

    def test_falls_back_to_public_site_url(self):
        cfg = MagicMock()
        cfg.get = lambda key, default=None: {
            "public_site_url": "https://www.gladlabs.io",
        }.get(key, default)
        assert (
            _resolve_revalidate_url(cfg)
            == "https://www.gladlabs.io/api/revalidate"
        )

    def test_strips_trailing_api_suffix_from_base_url(self):
        cfg = MagicMock()
        cfg.get = lambda key, default=None: {
            "public_site_url": "https://www.gladlabs.io/api",
        }.get(key, default)
        assert (
            _resolve_revalidate_url(cfg)
            == "https://www.gladlabs.io/api/revalidate"
        )

    def test_falls_back_through_legacy_chain_to_default_public_site_url(self):
        """When no setting is wired, the legacy chain still uses
        ``DEFAULT_PUBLIC_SITE_URL`` from bootstrap_defaults — that
        constant is the historical "out-of-the-box compose" fallback.
        """
        cfg = MagicMock()
        cfg.get = lambda key, default=None: default
        # The legacy chain provides DEFAULT_PUBLIC_SITE_URL as the
        # final default for next_public_api_base_url, so the resolved
        # URL is built off http://localhost:3000.
        url = _resolve_revalidate_url(cfg)
        assert url.endswith("/api/revalidate")
        assert "localhost:3000" in url

    def test_returns_default_when_legacy_chain_completely_empty(self):
        """If even the legacy chain returns "" (operator manually
        cleared every URL key), DEFAULT_REVALIDATE_URL is the safety
        net so revalidation still hits production.
        """
        cfg = MagicMock()
        # Return "" for everything (including the default arg).
        cfg.get = lambda key, default=None: ""
        assert _resolve_revalidate_url(cfg) == DEFAULT_REVALIDATE_URL


# ---------------------------------------------------------------------------
# trigger_isr_revalidate — slug-aware publish-time wrapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTriggerIsrRevalidate:
    @pytest.mark.asyncio
    async def test_includes_canonical_paths_and_slug(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(200)
        slug = "great-article-aaaaaaaa"
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            ok = await trigger_isr_revalidate(slug, site_config=cfg)
        assert ok is True
        body = client.post.call_args.kwargs["json"]
        # Every canonical path is present.
        for path in _CANONICAL_PATHS:
            assert path in body["paths"], f"missing canonical path: {path}"
        # Slug-specific path is present.
        assert f"/posts/{slug}" in body["paths"]
        # Every canonical tag is present.
        for tag in _CANONICAL_TAGS:
            assert tag in body["tags"], f"missing canonical tag: {tag}"
        # Slug-specific cache tag is present.
        assert f"post:{slug}" in body["tags"]

    @pytest.mark.asyncio
    async def test_extra_paths_and_tags_are_unioned(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            await trigger_isr_revalidate(
                "myslug",
                paths=["/archive/1", "/special"],
                tags=["custom-tag"],
                site_config=cfg,
            )
        body = client.post.call_args.kwargs["json"]
        assert "/archive/1" in body["paths"]
        assert "/special" in body["paths"]
        assert "custom-tag" in body["tags"]
        # Canonical entries are still in there too.
        assert "/" in body["paths"]
        assert "posts" in body["tags"]

    @pytest.mark.asyncio
    async def test_dedupes_when_caller_repeats_canonical_paths(self):
        """Idempotent: passing /posts (already canonical) doesn't double it."""
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            await trigger_isr_revalidate(
                "myslug",
                paths=["/posts", "/", "/sitemap.xml"],  # all canonical already
                tags=["posts"],  # also canonical
                site_config=cfg,
            )
        body = client.post.call_args.kwargs["json"]
        # Each appears exactly once.
        assert body["paths"].count("/posts") == 1
        assert body["paths"].count("/") == 1
        assert body["paths"].count("/sitemap.xml") == 1
        assert body["tags"].count("posts") == 1

    @pytest.mark.asyncio
    async def test_uses_async_get_secret(self):
        """#327 spec: secret MUST be fetched via the async get_secret method."""
        cfg = _build_site_config(secret="async-secret", url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            await trigger_isr_revalidate("myslug", site_config=cfg)
        # get_secret was awaited at least once with the right key.
        cfg.get_secret.assert_awaited_with("revalidate_secret", "")
        # The header reflected the async-fetched value.
        sent_headers = client.post.call_args.kwargs["headers"]
        assert sent_headers["x-revalidate-secret"] == "async-secret"

    @pytest.mark.asyncio
    async def test_skips_safely_when_secret_empty(self):
        """Empty secret = log warning, return False, never raise."""
        cfg = _build_site_config(secret="", url="https://www.gladlabs.io/api/revalidate")
        with patch("services.revalidation_service.httpx.AsyncClient") as mock_client_cls:
            ok = await trigger_isr_revalidate("myslug", site_config=cfg)
        assert ok is False
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_raises_on_httpx_error(self):
        """Revalidation failure must not roll back a publish."""
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=httpx.HTTPError("connection refused"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            # No exception should propagate.
            ok = await trigger_isr_revalidate("myslug", site_config=cfg)
        assert ok is False

    @pytest.mark.asyncio
    async def test_uses_explicit_revalidate_url_setting(self):
        """The new DB-configurable setting overrides the legacy URL chain."""
        cfg = _build_site_config(url="https://staging.example.com/api/revalidate")
        client = _build_httpx_client(200)
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            await trigger_isr_revalidate("myslug", site_config=cfg)
        url_arg = client.post.call_args.args[0]
        assert url_arg == "https://staging.example.com/api/revalidate"


# ---------------------------------------------------------------------------
# Bypass-path test — the alternate publish path now calls the helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScheduledPublisherCallsHelper:
    """The scheduled_publisher loop now calls trigger_isr_revalidate
    after promoting each scheduled→published row."""

    @pytest.mark.asyncio
    async def test_promotes_and_revalidates_each_row(self):
        from services.scheduled_publisher import run_scheduled_publisher

        rows = [
            {"id": "id-1", "title": "First", "slug": "first-post-aaaaaaaa"},
            {"id": "id-2", "title": "Second", "slug": "second-post-bbbbbbbb"},
        ]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        acm = MagicMock()
        acm.__aenter__ = AsyncMock(return_value=conn)
        acm.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire.return_value = acm

        async def get_pool():
            return pool

        called_with: list[str] = []

        async def fake_revalidate(slug: str) -> bool:
            called_with.append(slug)
            return True

        with patch(
            "services.revalidation_service.trigger_isr_revalidate",
            new=fake_revalidate,
        ):
            task = asyncio.create_task(run_scheduled_publisher(get_pool))
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        # Both rows triggered revalidation.
        assert called_with == ["first-post-aaaaaaaa", "second-post-bbbbbbbb"]
        # The UPDATE query also pulls back the slug column.
        sql = conn.fetch.call_args.args[0]
        assert "RETURNING id, title, slug" in sql

    @pytest.mark.asyncio
    async def test_revalidation_failure_does_not_poison_loop(self):
        """A revalidation exception must not break subsequent rows."""
        from services.scheduled_publisher import run_scheduled_publisher

        rows = [
            {"id": "id-1", "title": "First", "slug": "first"},
            {"id": "id-2", "title": "Second", "slug": "second"},
        ]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        acm = MagicMock()
        acm.__aenter__ = AsyncMock(return_value=conn)
        acm.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire.return_value = acm

        async def get_pool():
            return pool

        seen: list[str] = []

        async def fake_revalidate(slug: str) -> bool:
            seen.append(slug)
            if slug == "first":
                raise RuntimeError("revalidate exploded")
            return True

        with patch(
            "services.revalidation_service.trigger_isr_revalidate",
            new=fake_revalidate,
        ):
            task = asyncio.create_task(run_scheduled_publisher(get_pool))
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        # Both rows were attempted — exception on row 1 didn't skip row 2.
        assert seen == ["first", "second"]


# ---------------------------------------------------------------------------
# trigger_nextjs_revalidation_detailed — surfaces upstream cause (poindexter#458)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTriggerNextjsRevalidationDetailed:
    @pytest.mark.asyncio
    async def test_success_returns_status_and_url(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(200, text='{"success":true}')
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            result = await trigger_nextjs_revalidation_detailed(["/"], ["posts"], site_config=cfg)
        assert isinstance(result, RevalidationResult)
        assert result.success is True
        assert result.skipped is False
        assert result.status_code == 200
        assert result.error == ""
        assert result.error_kind == ""
        assert result.url == "https://www.gladlabs.io/api/revalidate"
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_http_failure_captures_status_and_body(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(401, text="Unauthorized: invalid x-revalidate-secret")
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            result = await trigger_nextjs_revalidation_detailed(["/"], ["posts"], site_config=cfg)
        assert result.success is False
        assert result.skipped is False
        assert result.status_code == 401
        assert result.error_kind == "http"
        assert "Unauthorized" in result.error

    @pytest.mark.asyncio
    async def test_timeout_tagged_as_timeout(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            result = await trigger_nextjs_revalidation_detailed(["/"], ["posts"], site_config=cfg)
        assert result.success is False
        assert result.status_code is None
        assert result.error_kind == "timeout"
        assert "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_exception_tagged_as_exception(self):
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=RuntimeError("connection refused"))
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            result = await trigger_nextjs_revalidation_detailed(["/"], ["posts"], site_config=cfg)
        assert result.success is False
        assert result.status_code is None
        assert result.error_kind == "exception"
        assert "RuntimeError" in result.error
        assert "connection refused" in result.error

    @pytest.mark.asyncio
    async def test_skipped_when_secret_empty(self):
        cfg = _build_site_config(secret="", url="https://www.gladlabs.io/api/revalidate")
        result = await trigger_nextjs_revalidation_detailed(["/"], ["posts"], site_config=cfg)
        assert result.success is False
        assert result.skipped is True
        assert result.status_code is None
        assert result.error_kind == "skipped"
        assert "revalidate_secret" in result.error

    @pytest.mark.asyncio
    async def test_legacy_bool_api_still_wraps_detailed(self):
        # The legacy trigger_nextjs_revalidation MUST keep returning a plain bool
        # so every existing caller (publish_service, scheduled_publisher,
        # task_publishing_routes) stays untouched.
        cfg = _build_site_config(url="https://www.gladlabs.io/api/revalidate")
        client = _build_httpx_client(500, text="boom")
        with patch("services.revalidation_service.httpx.AsyncClient", return_value=client):
            ok = await trigger_nextjs_revalidation(["/"], ["posts"], site_config=cfg)
        assert ok is False
        assert isinstance(ok, bool)
