"""
Unit tests for services/devto_service.py

Tests markdown cleaning, tag normalization, cross-posting via httpx,
and graceful skip when the Dev.to API key is not configured.
All database and HTTP calls are mocked — no real connections required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.devto_service import (
    DEVTO_STATUS_ALREADY_EXISTS,
    DEVTO_STATUS_GAVE_UP,
    DEVTO_STATUS_POSTED,
    CrossPostResult,
    DevToCrossPostService,
)
from services.site_config import SiteConfig

# Per glad-labs-stack#330: tests construct an explicit SiteConfig
# instead of mutating the module singleton. _TEST_SC is threaded into
# every DevToCrossPostService(pool, site_config=...) construction
# below so the tests don't depend on lifespan startup ordering.
_TEST_SC = SiteConfig(initial_config={"site_url": "https://test.example.com"})
SITE_URL = "https://test.example.com"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_mock_pool(api_key_row=None) -> AsyncMock:
    """Return an AsyncMock that behaves like an asyncpg pool.

    Supports both the legacy ``pool.fetchrow`` path (still used by
    other code) and the new ``async with pool.acquire() as conn``
    pattern used by _get_api_key after the encrypt migration.

    ``api_key_row`` — if a dict with ``value``, the acquire context
    yields a conn whose fetchrow returns it (so plugins.secrets can
    decide encrypted vs plaintext). None means "no row".
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=api_key_row)

    # acquire() must return an async context manager yielding a conn.
    conn = AsyncMock()
    if api_key_row is None:
        conn.fetchrow = AsyncMock(return_value=None)
    else:
        # get_secret reads both value AND is_secret. Provide both.
        row_with_secret = {**api_key_row, "is_secret": False}
        conn.fetchrow = AsyncMock(return_value=row_with_secret)

    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


# ---------------------------------------------------------------------------
# _clean_markdown
# ---------------------------------------------------------------------------


class TestCleanMarkdown:
    """Test markdown preparation for Dev.to."""

    def test_relative_links_converted_to_absolute(self):
        md = "Read [this post](/posts/my-slug) for details."
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert f"{SITE_URL}/posts/my-slug" in result
        assert "(/posts/my-slug)" not in result

    def test_relative_image_paths_converted_to_absolute(self):
        md = "![screenshot](/images/demo.png)"
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert f"{SITE_URL}/images/demo.png" in result

    def test_absolute_links_unchanged(self):
        md = "[example](https://example.com/page)"
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert "https://example.com/page" in result
        assert SITE_URL not in result

    def test_script_tags_stripped(self):
        md = "Before<script>alert('xss')</script>After"
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert "<script" not in result
        assert "alert" not in result
        assert "BeforeAfter" in result

    def test_iframe_tags_stripped(self):
        md = 'Text<iframe src="https://evil.com"></iframe>More'
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert "<iframe" not in result
        assert "TextMore" in result

    def test_html_comments_stripped(self):
        md = "Visible<!-- hidden comment -->Also visible"
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert "<!--" not in result
        assert "hidden comment" not in result
        assert "VisibleAlso visible" in result

    def test_custom_react_components_stripped(self):
        md = "Before\n<ViewTracker />\n<AdSense slot=\"123\" />\nAfter"
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert "<ViewTracker" not in result
        assert "<AdSense" not in result
        assert "Before" in result
        assert "After" in result

    def test_plain_markdown_unchanged(self):
        md = "## Hello World\n\nThis is a paragraph."
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert result == md

    def test_empty_input(self):
        assert DevToCrossPostService._clean_markdown("", _TEST_SC) == ""

    def test_multiple_relative_links(self):
        md = "[A](/posts/a) and [B](/posts/b)"
        result = DevToCrossPostService._clean_markdown(md, _TEST_SC)
        assert f"{SITE_URL}/posts/a" in result
        assert f"{SITE_URL}/posts/b" in result


# ---------------------------------------------------------------------------
# _normalize_tags
# ---------------------------------------------------------------------------


class TestNormalizeTags:
    """Test tag normalization for Dev.to."""

    def test_lowercase(self):
        assert DevToCrossPostService._normalize_tags(["LLM", "AI"]) == ["llm", "ai"]

    def test_max_four_tags(self):
        tags = ["python", "javascript", "docker", "kubernetes", "react", "fastapi"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert len(result) == 4

    def test_alphanumeric_only(self):
        tags = ["self-hosting", "machine learning", "c++"]
        result = DevToCrossPostService._normalize_tags(tags)
        # "c" is rejected (single char after stripping ++)
        assert result == ["selfhosting", "machinelearning"]

    def test_single_char_tags_rejected(self):
        tags = ["a", "b", "ai", "ml"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert result == ["ai", "ml"]

    def test_duplicates_removed(self):
        tags = ["AI", "ai", "Ai"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert result == ["ai"]

    def test_empty_tags_skipped(self):
        tags = ["", "  ", "valid"]
        result = DevToCrossPostService._normalize_tags(tags)
        assert result == ["valid"]

    def test_empty_list(self):
        assert DevToCrossPostService._normalize_tags([]) == []


# ---------------------------------------------------------------------------
# cross_post — API key missing
# ---------------------------------------------------------------------------


class TestCrossPostNoApiKey:
    """Test graceful skip when devto_api_key is not configured."""

    @pytest.mark.asyncio
    async def test_skipped_when_no_api_key_in_settings(self):
        pool = make_mock_pool(api_key_row=None)
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)
        result = await svc.cross_post(
            title="Test",
            content_markdown="Content",
            canonical_url="https://www.gladlabs.io/posts/test",
        )
        assert isinstance(result, CrossPostResult)
        assert result.status == "skipped"
        assert result.url is None

    @pytest.mark.asyncio
    async def test_skipped_when_api_key_empty(self):
        pool = make_mock_pool(api_key_row={"value": ""})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)
        result = await svc.cross_post(
            title="Test",
            content_markdown="Content",
            canonical_url="https://www.gladlabs.io/posts/test",
        )
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_db_fetch_failure_propagates(self):
        """After the 2026-04-20 secrets refactor, _get_api_key no longer
        swallows pool errors. Matt explicitly asked for no try/except
        eating — if the DB pool is borked, we want to see it, not
        silently skip Dev.to."""
        pool = MagicMock()
        failing_ctx = AsyncMock()
        failing_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("connection refused"))
        failing_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=failing_ctx)

        svc = DevToCrossPostService(pool, site_config=_TEST_SC)
        with pytest.raises(RuntimeError, match="connection refused"):
            await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

    @pytest.mark.asyncio
    async def test_api_key_cached_after_first_load(self):
        # After the encrypt migration, the key comes via a conn from
        # pool.acquire() rather than pool.fetchrow() directly. Caching
        # is verified by checking the conn inside the acquired context
        # is only queried once.
        pool = make_mock_pool(api_key_row=None)
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)
        await svc.cross_post("T", "C", "https://www.gladlabs.io/posts/t")
        await svc.cross_post("T2", "C2", "https://www.gladlabs.io/posts/t2")
        # pool.acquire was called once (first cross_post); the second
        # short-circuits on _api_key_loaded.
        assert pool.acquire.call_count == 1


# ---------------------------------------------------------------------------
# cross_post — successful API call
# ---------------------------------------------------------------------------


class TestCrossPostSuccess:
    """Test cross_post with a mocked httpx client."""

    @pytest.mark.asyncio
    async def test_successful_cross_post_returns_posted_result(self):
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "url": "https://dev.to/gladlabs/test-article",
            "id": 12345,
        }

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test Article",
                content_markdown="## Hello\n[link](/posts/slug)",
                canonical_url="https://www.gladlabs.io/posts/test-article",
                tags=["ai", "python"],
            )

        assert result.status == "posted"
        assert result.url == "https://dev.to/gladlabs/test-article"
        assert result.article_id == "12345"
        # Verify the POST was called with the right structure
        call_kwargs = mock_client_instance.post.call_args
        assert call_kwargs[0][0] == "https://dev.to/api/articles"
        payload = call_kwargs[1]["json"]
        assert payload["article"]["title"] == "Test Article"
        assert payload["article"]["published"] is True  # Auto-publish by default
        assert payload["article"]["canonical_url"] == "https://www.gladlabs.io/posts/test-article"
        assert payload["article"]["tags"] == ["ai", "python"]
        # Verify markdown was cleaned (relative link -> absolute)
        assert SITE_URL in payload["article"]["body_markdown"]
        # Verify API key header
        assert call_kwargs[1]["headers"]["api-key"] == "fake-api-key"

    @pytest.mark.asyncio
    async def test_422_canonical_url_taken_returns_already_exists(self, caplog):
        """Regression for #404 — the canonical-URL-taken 422 must be
        promoted from ``gave_up`` (the #397 default for permanent 4xx)
        to a distinct ``already_exists`` status that the job counts as
        success-at-destination. Also verifies the log line is demoted
        from WARNING to INFO so the every-4-hour message stops paging."""
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = (
            '{"error":"Canonical url has already been taken. '
            'Email support@dev.to for further details.","status":422}'
        )
        mock_response.json.return_value = {
            "error": (
                "Canonical url has already been taken. "
                "Email support@dev.to for further details."
            ),
            "status": 422,
        }

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            import logging as _logging
            with caplog.at_level(_logging.INFO, logger="services.devto_service"):
                result = await svc.cross_post(
                    title="Test",
                    content_markdown="Content",
                    canonical_url="https://www.gladlabs.io/posts/test",
                )

        assert result.status == "already_exists"
        assert result.http_status == 422
        assert "Canonical url has already been taken" in (result.error or "")
        # Log demoted from WARNING to INFO — no WARNING records should
        # mention the canonical-URL message (#404 acceptance criterion).
        warning_records = [
            r for r in caplog.records
            if r.levelno >= _logging.WARNING
            and "canonical" in r.getMessage().lower()
        ]
        assert warning_records == [], (
            "Canonical-URL 422 must log at INFO, not WARNING (#404). "
            "Got %r" % [r.getMessage() for r in warning_records]
        )

    @pytest.mark.asyncio
    async def test_422_canonical_url_taken_case_insensitive_match(self):
        """The canonical-URL match must be case-insensitive so future
        capitalization tweaks on Dev.to's side don't silently re-open
        the retry loop. (#404 — match the JSON ``error`` field via
        ``.lower()`` substring check.)"""
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {
            "error": "CANONICAL URL HAS ALREADY BEEN TAKEN",
            "status": 422,
        }
        mock_response.text = (
            '{"error":"CANONICAL URL HAS ALREADY BEEN TAKEN","status":422}'
        )

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )
        assert result.status == "already_exists"

    @pytest.mark.asyncio
    async def test_422_other_error_message_still_gives_up(self):
        """Generic 422 (validation error, etc.) with a different error
        message must STILL hit the gave_up branch and log at WARNING.
        Only the exact canonical-URL message is special-cased (#404)."""
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {
            "error": "Title is too long (maximum is 128 characters)",
            "status": 422,
        }
        mock_response.text = (
            '{"error":"Title is too long (maximum is 128 characters)",'
            '"status":422}'
        )

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result.status == "gave_up"
        assert result.http_status == 422
        assert "Title is too long" in (result.error or "")

    @pytest.mark.asyncio
    async def test_422_unparseable_json_falls_through_to_gave_up(self):
        """If Dev.to returns 422 with a body that can't be JSON-parsed
        (rare, but defensive), don't crash — fall through to gave_up
        so we still stop hammering the endpoint."""
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "<html>500 from upstream cdn</html>"

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result.status == "gave_up"

    @pytest.mark.asyncio
    async def test_415_unsupported_media_still_gives_up(self):
        """Other 4xx (415, 401, etc.) must still error loudly — only
        the canonical-URL 422 is the success-at-destination case."""
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 415
        mock_response.text = "Unsupported Media Type"

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result.status == "gave_up"
        assert result.http_status == 415

    @pytest.mark.asyncio
    async def test_503_returns_transient(self):
        """5xx responses should NOT be terminal — the cron retries on
        the next tick. (#397 distinguishes these from 422.)"""
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result.status == "transient"
        assert result.http_status == 503

    @pytest.mark.asyncio
    async def test_429_rate_limit_returns_transient(self):
        """429 is rate-limit, not a permanent reject — keep retrying."""
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result.status == "transient"
        assert result.http_status == 429

    @pytest.mark.asyncio
    async def test_network_error_returns_transient(self):
        pool = make_mock_pool(api_key_row={"value": "fake-api-key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(
                side_effect=Exception("Connection timeout")
            )
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await svc.cross_post(
                title="Test",
                content_markdown="Content",
                canonical_url="https://www.gladlabs.io/posts/test",
            )

        assert result.status == "transient"
        assert "Connection timeout" in (result.error or "")

    @pytest.mark.asyncio
    async def test_tags_normalized_in_payload(self):
        pool = make_mock_pool(api_key_row={"value": "key"})
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"url": "https://dev.to/x", "id": 1}

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await svc.cross_post(
                title="T",
                content_markdown="C",
                canonical_url="https://www.gladlabs.io/posts/t",
                tags=["Machine Learning", "Self-Hosting", "AI", "Python", "Extra"],
            )

        payload = mock_client_instance.post.call_args[1]["json"]
        tags = payload["article"]["tags"]
        assert len(tags) <= 4
        assert all(t == t.lower() for t in tags)
        assert all(t.isalnum() for t in tags)


# ---------------------------------------------------------------------------
# cross_post_by_post_id — dedup metadata writes (#397)
# ---------------------------------------------------------------------------


class TestCrossPostByPostIdDedup:
    """Verify the post-by-id wrapper writes the right ``devto_status``
    into ``posts.metadata`` so the cron stops retrying permanent
    rejections (#397)."""

    @staticmethod
    def _make_post_pool(api_key="fake-api-key", post_row=None):
        """Build a pool that supports both pool.acquire (API key path)
        AND pool.fetchrow (post lookup) AND pool.execute (metadata
        UPDATE). Returns (pool, executions_list) so tests can assert
        on what UPDATE statements ran."""
        pool = MagicMock()

        # pool.acquire context for the API key lookup
        secret_conn = AsyncMock()
        secret_conn.fetchrow = AsyncMock(
            return_value={"value": api_key, "is_secret": False} if api_key else None
        )
        secret_ctx = AsyncMock()
        secret_ctx.__aenter__ = AsyncMock(return_value=secret_conn)
        secret_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=secret_ctx)

        # pool.fetchrow handles BOTH the devto_publish_immediately
        # toggle (returns None, default True) AND the post row lookup.
        # We discriminate by the SQL string.
        async def _fetchrow(sql, *args, **kwargs):
            if "posts" in sql.lower():
                return post_row
            return None  # devto_publish_immediately = use default

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)

        executions: list[tuple] = []

        async def _execute(sql, *args, **kwargs):
            executions.append((sql, args))
            return "UPDATE 1"

        pool.execute = AsyncMock(side_effect=_execute)

        return pool, executions

    @pytest.mark.asyncio
    async def test_2xx_records_devto_url_and_status_posted(self):
        post_id = "11111111-1111-1111-1111-111111111111"
        post_row = {
            "id": post_id,
            "title": "Hello World",
            "slug": "hello-world",
            "content": "Body",
            "seo_keywords": "ai,python",
            "metadata": {},
        }
        pool, executions = self._make_post_pool(post_row=post_row)
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "url": "https://dev.to/g/hello-world",
            "id": 999,
        }

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            url = await svc.cross_post_by_post_id(post_id)

        assert url == "https://dev.to/g/hello-world"
        # Exactly one metadata UPDATE happened, and it carried the
        # posted marker plus the article id.
        update_sqls = [
            (sql, args) for sql, args in executions if "UPDATE posts" in sql
        ]
        assert len(update_sqls) == 1
        patch_json = update_sqls[0][1][0]
        assert '"devto_url": "https://dev.to/g/hello-world"' in patch_json
        assert f'"devto_status": "{DEVTO_STATUS_POSTED}"' in patch_json
        assert '"devto_article_id": "999"' in patch_json

    @pytest.mark.asyncio
    async def test_422_canonical_marks_post_already_exists_returns_url(self):
        """Regression for #404 — the canonical-URL-already-taken 422
        must persist devto_status='already_exists' (NOT 'gave_up' as
        in the original #397 implementation) AND return the canonical
        URL so the job counts it as success-at-destination. The post
        IS on Dev.to; this run just didn't put it there."""
        post_id = "22222222-2222-2222-2222-222222222222"
        post_row = {
            "id": post_id,
            "title": "Already Posted",
            "slug": "already-posted",
            "content": "Body",
            "seo_keywords": "",
            "metadata": {},
        }
        pool, executions = self._make_post_pool(post_row=post_row)
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = (
            '{"error":"Canonical url has already been taken. '
            'Email support@dev.to for further details.","status":422}'
        )
        mock_response.json.return_value = {
            "error": (
                "Canonical url has already been taken. "
                "Email support@dev.to for further details."
            ),
            "status": 422,
        }

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            url = await svc.cross_post_by_post_id(post_id)

        # We DO get a URL back (the canonical URL — the post exists at
        # this URL on the public site, and Dev.to confirms a copy is
        # already on its end). The job uses this as a truthy success
        # signal, not as the Dev.to article URL.
        assert url == f"{SITE_URL}/posts/already-posted"

        update_sqls = [
            (sql, args) for sql, args in executions if "UPDATE posts" in sql
        ]
        assert len(update_sqls) == 1, (
            "Expected exactly one metadata UPDATE marking the post "
            "as already_exists; got %r" % executions
        )
        patch_json = update_sqls[0][1][0]
        assert (
            f'"devto_status": "{DEVTO_STATUS_ALREADY_EXISTS}"' in patch_json
        )
        # Distinct sentinel — must NOT regress to either 'posted' or
        # 'gave_up' (the audit trail tracks WHO put it there).
        assert f'"devto_status": "{DEVTO_STATUS_POSTED}"' not in patch_json
        assert f'"devto_status": "{DEVTO_STATUS_GAVE_UP}"' not in patch_json
        assert '"devto_last_http_status": "422"' in patch_json
        assert "Canonical url has already been taken" in patch_json
        # We don't write a fabricated devto_url for already_exists —
        # we don't have the Dev.to article URL, only the canonical.
        assert '"devto_url"' not in patch_json

    @pytest.mark.asyncio
    async def test_422_other_message_marks_post_gave_up(self):
        """Generic 422 (validation error etc.) keeps the original
        #397 gave_up path — only the canonical-URL 422 is special."""
        post_id = "44444444-4444-4444-4444-444444444444"
        post_row = {
            "id": post_id,
            "title": "Bad Title",
            "slug": "bad-title",
            "content": "Body",
            "seo_keywords": "",
            "metadata": {},
        }
        pool, executions = self._make_post_pool(post_row=post_row)
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {
            "error": "Title is too long",
            "status": 422,
        }
        mock_response.text = '{"error":"Title is too long","status":422}'

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            url = await svc.cross_post_by_post_id(post_id)

        assert url is None
        update_sqls = [
            (sql, args) for sql, args in executions if "UPDATE posts" in sql
        ]
        assert len(update_sqls) == 1
        patch_json = update_sqls[0][1][0]
        assert f'"devto_status": "{DEVTO_STATUS_GAVE_UP}"' in patch_json
        assert (
            f'"devto_status": "{DEVTO_STATUS_ALREADY_EXISTS}"'
            not in patch_json
        )

    @pytest.mark.asyncio
    async def test_503_leaves_metadata_alone_so_cron_retries(self):
        """5xx must NOT write devto_status — the next tick should
        pick the post up again."""
        post_id = "33333333-3333-3333-3333-333333333333"
        post_row = {
            "id": post_id,
            "title": "Transient",
            "slug": "transient",
            "content": "Body",
            "seo_keywords": "",
            "metadata": {},
        }
        pool, executions = self._make_post_pool(post_row=post_row)
        svc = DevToCrossPostService(pool, site_config=_TEST_SC)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        with patch("services.devto_service.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            url = await svc.cross_post_by_post_id(post_id)

        assert url is None
        update_sqls = [
            (sql, args) for sql, args in executions if "UPDATE posts" in sql
        ]
        assert update_sqls == [], (
            "5xx must leave metadata untouched so the next cron tick "
            "retries; saw %r" % update_sqls
        )
