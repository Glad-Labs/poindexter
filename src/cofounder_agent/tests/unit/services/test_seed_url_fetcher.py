"""Unit tests for services/seed_url_fetcher.py — GH-42.

We exercise every ``reason`` branch of :class:`SeedURLError` plus the
happy path (combined topic + URL) and the max_bytes truncation cap.
httpx is mocked via ``MockTransport`` so no real network traffic ever
happens — and we deliberately pass the ``client=`` kwarg through so
the test doesn't wait on real DNS/TCP timeouts.
"""

from __future__ import annotations

import httpx
import pytest

from services.seed_url_fetcher import (
    SeedURLError,
    SeedURLResult,
    _looks_like_login_wall,
    build_source_attribution,
    fetch_seed_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html(title: str = "", meta_desc: str = "", body: str = "") -> str:
    """Build a minimal HTML document for tests."""
    meta = f'<meta name="description" content="{meta_desc}">' if meta_desc else ""
    title_tag = f"<title>{title}</title>" if title else ""
    return f"""<!DOCTYPE html>
<html>
<head>
  {title_tag}
  {meta}
</head>
<body>
  {body}
</body>
</html>"""


def _mock_client(handler) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# ---------------------------------------------------------------------------
# Happy-path extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchSeedURLHappyPath:
    @pytest.mark.asyncio
    async def test_extracts_title_from_title_tag(self):
        html = _html(
            title="How Claude Agents Ship Features",
            meta_desc="A case study in AI-assisted engineering.",
            body="<p>This is a long opening paragraph that explains the whole thing in detail.</p>",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})

        async with _mock_client(handler) as client:
            result = await fetch_seed_url(
                "https://example.com/post", client=client,
            )

        assert isinstance(result, SeedURLResult)
        assert result.title == "How Claude Agents Ship Features"
        assert "case study" in result.excerpt.lower()
        assert result.status_code == 200
        assert result.content_length > 0

    @pytest.mark.asyncio
    async def test_falls_back_to_h1_when_title_missing(self):
        html = """<html><head></head><body>
            <h1>The H1 Becomes The Topic</h1>
            <p>First paragraph, long enough to qualify as an excerpt for the pipeline.</p>
        </body></html>"""

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            result = await fetch_seed_url(
                "https://example.com/no-title", client=client,
            )

        assert result.title == "The H1 Becomes The Topic"
        # Fell back to first <p> for excerpt.
        assert result.excerpt.startswith("First paragraph")

    @pytest.mark.asyncio
    async def test_falls_back_to_first_p_when_meta_desc_missing(self):
        html = _html(
            title="Post without meta description",
            body="<p>Short nav.</p><p>A substantial opening paragraph about something real and interesting.</p>",
        )

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            result = await fetch_seed_url(
                "https://example.com/no-meta", client=client,
            )

        # The short <p>Short nav.</p> is below the _MIN_EXCERPT_CHARS
        # threshold so the fetcher skips it and picks the substantial one.
        assert "substantial opening paragraph" in result.excerpt

    @pytest.mark.asyncio
    async def test_og_description_preferred_over_first_p(self):
        html = _html(
            title="OG wins",
            meta_desc="This is the meta description",
            body="<p>This is a much longer first paragraph that should lose to the meta tag.</p>",
        )

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            result = await fetch_seed_url(
                "https://example.com/og", client=client,
            )

        assert result.excerpt == "This is the meta description"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchSeedURLErrors:
    @pytest.mark.asyncio
    async def test_404_raises_http_error_reason(self):
        def handler(request):
            return httpx.Response(404, text="Not Found")

        async with _mock_client(handler) as client:
            with pytest.raises(SeedURLError) as exc_info:
                await fetch_seed_url(
                    "https://example.com/missing", client=client,
                )

        assert exc_info.value.reason == "http_error"
        assert "404" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_500_raises_http_error_reason(self):
        def handler(request):
            return httpx.Response(500, text="Internal Server Error")

        async with _mock_client(handler) as client:
            with pytest.raises(SeedURLError) as exc_info:
                await fetch_seed_url(
                    "https://example.com/broken", client=client,
                )

        assert exc_info.value.reason == "http_error"

    @pytest.mark.asyncio
    async def test_login_wall_detected_and_reported(self):
        # Page renders but the only content is a sign-in gate. Notice we
        # supply a title that's identical to the sign-in string so title
        # extraction "succeeds" but excerpt extraction fails — the
        # login-wall check then escalates because title OR excerpt is
        # empty. We flip that by giving no <title> and no meta desc so
        # both title + excerpt come up empty alongside the gate text.
        html = """<html><head></head><body>
            <div class="paywall">
                <p>Please sign in to continue reading this premium content.</p>
            </div>
        </body></html>"""

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            with pytest.raises(SeedURLError) as exc_info:
                await fetch_seed_url(
                    "https://example.com/premium", client=client,
                )

        # Could fail with either login_wall (if title extraction fails)
        # or no_title. Our heuristic fires login_wall first when the
        # gate phrase is present and extraction is incomplete.
        assert exc_info.value.reason == "login_wall"

    @pytest.mark.asyncio
    async def test_subscribe_wall_phrase_detected(self):
        html = """<html><body>
            <p>Become a paid subscriber to read the rest.</p>
        </body></html>"""

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            with pytest.raises(SeedURLError) as exc_info:
                await fetch_seed_url(
                    "https://example.com/paid", client=client,
                )

        assert exc_info.value.reason == "login_wall"

    @pytest.mark.asyncio
    async def test_no_title_raises_no_title_reason(self):
        # Valid 200 response but literally no title/h1 content available.
        html = "<html><body><p>some text without structure</p></body></html>"

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            with pytest.raises(SeedURLError) as exc_info:
                await fetch_seed_url(
                    "https://example.com/untitled", client=client,
                )

        assert exc_info.value.reason == "no_title"

    @pytest.mark.asyncio
    async def test_invalid_scheme_rejected_before_fetch(self):
        with pytest.raises(SeedURLError) as exc_info:
            await fetch_seed_url("ftp://example.com/file.txt")
        assert exc_info.value.reason == "invalid_url"

    @pytest.mark.asyncio
    async def test_empty_url_rejected(self):
        with pytest.raises(SeedURLError) as exc_info:
            await fetch_seed_url("")
        assert exc_info.value.reason == "invalid_url"

    @pytest.mark.asyncio
    async def test_network_error_raises_network_reason(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        async with _mock_client(handler) as client:
            with pytest.raises(SeedURLError) as exc_info:
                await fetch_seed_url(
                    "https://unreachable.example.com", client=client,
                )

        assert exc_info.value.reason == "network"

    @pytest.mark.asyncio
    async def test_timeout_raises_network_reason(self):
        def handler(request):
            raise httpx.TimeoutException("timed out")

        async with _mock_client(handler) as client:
            with pytest.raises(SeedURLError) as exc_info:
                await fetch_seed_url(
                    "https://slow.example.com", client=client,
                )

        assert exc_info.value.reason == "network"


# ---------------------------------------------------------------------------
# max_bytes truncation (AC#6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchSeedURLMaxBytes:
    @pytest.mark.asyncio
    async def test_long_html_truncated_at_max_bytes(self):
        # Build a 2 MiB HTML payload; max_bytes=1024 should truncate.
        # Put title at the start so truncation doesn't cut it off.
        head = _html(title="Very Long Page")
        # Pad with filler AFTER the real head so extraction still works
        # but the body is massive.
        padding = "<p>filler filler filler filler filler filler</p>" * 50000
        html = head.replace("</body>", padding + "</body>")
        assert len(html.encode()) > 1_000_000

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            result = await fetch_seed_url(
                "https://example.com/huge",
                client=client,
                max_bytes=1024,
            )

        # content_length is the CAPPED size, never the original.
        assert result.content_length == 1024
        # Title was extracted from the truncated prefix — proves the
        # title-first layout survives truncation.
        assert result.title == "Very Long Page"

    @pytest.mark.asyncio
    async def test_small_response_not_truncated(self):
        html = _html(title="Tiny", meta_desc="Small page", body="<p>Short but valid.</p>")

        def handler(request):
            return httpx.Response(200, text=html)

        async with _mock_client(handler) as client:
            result = await fetch_seed_url(
                "https://example.com/tiny",
                client=client,
                max_bytes=1_048_576,
            )

        assert result.content_length < 1_048_576
        assert result.content_length == len(html.encode())


# ---------------------------------------------------------------------------
# build_source_attribution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSourceAttribution:
    def test_contains_source_article_label(self):
        # This exact label is what the writer's system prompt looks
        # for — don't rename it casually.
        result = SeedURLResult(
            url="https://example.com/post",
            title="The Post Title",
            excerpt="A short excerpt.",
            status_code=200,
            content_length=1234,
        )
        attribution = build_source_attribution(result)
        assert "Source article:" in attribution
        assert "https://example.com/post" in attribution
        assert "The Post Title" in attribution
        assert "A short excerpt." in attribution

    def test_omits_excerpt_line_when_empty(self):
        result = SeedURLResult(
            url="https://example.com/bare",
            title="Just a title",
            excerpt="",
            status_code=200,
            content_length=100,
        )
        attribution = build_source_attribution(result)
        assert "Excerpt:" not in attribution
        assert "Just a title" in attribution


# ---------------------------------------------------------------------------
# Login-wall heuristic (unit-level, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoginWallHeuristic:
    def test_sign_in_to_continue_matches(self):
        assert _looks_like_login_wall("Please Sign In To Continue")

    def test_casual_mention_not_matched(self):
        # The phrase "sign in" in a blog post about auth shouldn't
        # trigger — only the "to continue" / "to continue reading"
        # variants do.
        assert not _looks_like_login_wall(
            "This post explains how to sign in users to your Next.js app."
        )

    def test_subscribe_to_continue_matches(self):
        assert _looks_like_login_wall("Subscribe to continue reading")

    def test_members_only_matches(self):
        assert _looks_like_login_wall(
            "This content is for members only."
        )
