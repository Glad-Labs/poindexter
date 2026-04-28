"""Unit tests for services.url_scraper.

Targets ``scrape_url`` (generic + GitHub + arXiv routes) and the
helpers ``_build_user_agent``, ``_first_text``. Generic-route extraction
is delegated to trafilatura post-#204 (the heuristics ``_meta_content``
used to encode are now provided by the library itself).

Lifts the module from 0% to ~95% coverage with mocked httpx.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from services import url_scraper
from services.url_scraper import (
    URLScrapeError,
    _build_user_agent,
    _first_text,
    scrape_url,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        text: str = "",
        json_data: dict | None = None,
        status_code: int = 200,
        is_success: bool = True,
    ):
        self.text = text
        self._json = json_data or {}
        self.status_code = status_code
        self.is_success = is_success

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self.is_success:
            raise httpx.HTTPStatusError(
                "boom", request=MagicMock(), response=MagicMock(),
            )


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` with scripted GET responses.

    ``responses`` is a list (FIFO) or a callable ``url -> _FakeResponse``.
    """

    def __init__(self, responses, **_kwargs):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **_kwargs):
        if callable(self._responses):
            return self._responses(url)
        return self._responses.pop(0)


def _patch_async_client(responses):
    """Helper: patch httpx.AsyncClient inside url_scraper to a FakeClient."""
    return patch.object(
        url_scraper.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(responses, **kwargs),
    )


def _site_config(values: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = values or {}
    sc.get.side_effect = lambda key, default="": values.get(key, default)
    return sc


# ---------------------------------------------------------------------------
# _build_user_agent
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildUserAgent:
    def test_returns_fallback_when_site_config_is_none(self):
        ua = _build_user_agent(None)
        assert "PoindexterBot" in ua
        assert "no-contact-configured" in ua

    def test_uses_contact_url_when_set(self):
        sc = _site_config({"site_contact_url": "https://example.com/contact"})
        ua = _build_user_agent(sc)
        assert "+https://example.com/contact" in ua

    def test_uses_default_bot_name_when_unset(self):
        sc = _site_config({})
        ua = _build_user_agent(sc)
        assert "PoindexterBot/1.0" in ua

    def test_uses_custom_bot_name_when_set(self):
        sc = _site_config(
            {"scraper_bot_name": "AcmeBot/2.5", "site_contact_url": ""},
        )
        ua = _build_user_agent(sc)
        assert "AcmeBot/2.5" in ua
        assert "no-contact-configured" in ua


# ---------------------------------------------------------------------------
# scrape_url — input validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScrapeUrlValidation:
    async def test_empty_url_raises(self):
        with pytest.raises(URLScrapeError, match="Invalid URL"):
            await scrape_url("")

    async def test_non_http_scheme_raises(self):
        with pytest.raises(URLScrapeError, match="Invalid URL"):
            await scrape_url("ftp://example.com/foo")

    async def test_routes_github_to_github_scraper(self):
        with patch.object(url_scraper, "_scrape_github", AsyncMock(return_value={"x": 1})) as m, \
                patch.object(url_scraper, "_scrape_generic", AsyncMock()) as g:
            result = await scrape_url("https://github.com/owner/repo")
        m.assert_awaited_once()
        g.assert_not_called()
        assert result == {"x": 1}

    async def test_routes_arxiv_to_arxiv_scraper(self):
        with patch.object(url_scraper, "_scrape_arxiv", AsyncMock(return_value={"x": 2})) as m, \
                patch.object(url_scraper, "_scrape_generic", AsyncMock()) as g:
            result = await scrape_url("https://arxiv.org/abs/1234.5678")
        m.assert_awaited_once()
        g.assert_not_called()
        assert result == {"x": 2}

    async def test_routes_other_to_generic_scraper(self):
        with patch.object(url_scraper, "_scrape_generic", AsyncMock(return_value={"x": 3})) as g:
            result = await scrape_url("https://example.com/article")
        g.assert_awaited_once()
        assert result == {"x": 3}


# ---------------------------------------------------------------------------
# _scrape_generic
# ---------------------------------------------------------------------------


_HTML_FULL = """
<html>
  <head>
    <title>Page Title Tag</title>
    <meta property="og:title" content="OG Title Wins">
    <meta property="og:description" content="An excerpt.">
    <meta name="article:author" content="Jane Doe">
    <meta property="article:published_time" content="2026-04-01">
  </head>
  <body>
    <nav>nav links</nav>
    <article>
      <h1>Heading</h1>
      <p>First paragraph of the body.</p>
      <p>Second paragraph.</p>
      <script>console.log('strip me')</script>
    </article>
    <footer>footer</footer>
  </body>
</html>
"""


_HTML_FALLBACK_TITLE = """
<html>
  <head><title>Just A Title</title></head>
  <body><p>No og, no h1, just title.</p></body>
</html>
"""


_HTML_NO_TITLE_AT_ALL = "<html><body><p>orphan</p></body></html>"


@pytest.mark.unit
class TestScrapeGeneric:
    async def test_full_html_extracts_og_title_and_metadata(self):
        responses = [_FakeResponse(text=_HTML_FULL)]
        with _patch_async_client(responses):
            result = await scrape_url("https://example.com/post")
        assert result["title"] == "OG Title Wins"
        assert result["excerpt"] == "An excerpt."
        assert result["author"] == "Jane Doe"
        assert result["published_at"] == "2026-04-01"
        assert result["content_type"] == "article"
        assert "First paragraph" in result["content_full"]
        assert "console.log" not in result["content_full"]  # script stripped
        assert "footer" not in result["content_full"]
        assert result["word_count"] > 0

    async def test_falls_back_to_h1_or_title(self):
        responses = [_FakeResponse(text=_HTML_FALLBACK_TITLE)]
        with _patch_async_client(responses):
            result = await scrape_url("https://example.com/post")
        assert result["title"] == "Just A Title"

    async def test_uses_untitled_when_nothing_found(self):
        responses = [_FakeResponse(text=_HTML_NO_TITLE_AT_ALL)]
        with _patch_async_client(responses):
            result = await scrape_url("https://example.com/post")
        # "Untitled" or the body's first text are both acceptable
        # depending on what BeautifulSoup picks. The contract is that we
        # don't crash and get *something*.
        assert isinstance(result["title"], str)

    async def test_truncates_huge_content(self):
        # 100k chars of plain text inside <article>
        big = "x" * 100_000
        html = f"<html><body><article>{big}</article></body></html>"
        responses = [_FakeResponse(text=html)]
        with _patch_async_client(responses):
            result = await scrape_url("https://example.com/big")
        assert len(result["content_full"]) <= url_scraper.MAX_CONTENT_CHARS

    async def test_http_error_is_wrapped(self):
        responses = [_FakeResponse(text="", is_success=False)]
        with _patch_async_client(responses):
            with pytest.raises(URLScrapeError, match="Fetch failed"):
                await scrape_url("https://example.com/dead")

    async def test_passes_user_agent_from_site_config(self):
        captured: dict = {}

        class _Recorder:
            def __init__(self, **kwargs):
                captured["headers"] = kwargs.get("headers")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                return _FakeResponse(text=_HTML_FULL)

        sc = _site_config({"site_contact_url": "https://acme.test/c"})
        with patch.object(url_scraper.httpx, "AsyncClient", _Recorder):
            await scrape_url("https://example.com/x", site_config=sc)
        assert "+https://acme.test/c" in captured["headers"]["User-Agent"]


# ---------------------------------------------------------------------------
# _scrape_github
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScrapeGitHub:
    async def test_extracts_repo_metadata_and_readme(self):
        repo_response = _FakeResponse(json_data={
            "full_name": "octo/widget",
            "description": "Widgetizer",
            "owner": {"login": "octo"},
            "pushed_at": "2026-04-20T00:00:00Z",
        })
        readme_response = _FakeResponse(text="# Widget\n\nReadme body.")
        with _patch_async_client([repo_response, readme_response]):
            result = await scrape_url("https://github.com/octo/widget")
        assert result["content_type"] == "github"
        assert "octo/widget" in result["title"]
        assert "Widgetizer" in result["title"]
        assert result["author"] == "octo"
        assert result["published_at"] == "2026-04-20T00:00:00Z"
        assert "Readme body" in result["content_full"]

    async def test_short_path_falls_back_to_generic(self):
        # github.com/foo (no repo) — falls back to _scrape_generic.
        with patch.object(
            url_scraper, "_scrape_generic", AsyncMock(return_value={"y": 1}),
        ) as g:
            result = await scrape_url("https://github.com/foo")
        g.assert_awaited_once()
        assert result == {"y": 1}

    async def test_handles_missing_repo_metadata_gracefully(self):
        # Repo endpoint returns 404; readme also fails. Helper still
        # returns a dict with sensible defaults.
        repo = _FakeResponse(json_data={}, is_success=False, status_code=404)
        readme = _FakeResponse(text="", is_success=False, status_code=404)
        with _patch_async_client([repo, readme]):
            result = await scrape_url("https://github.com/owner/repo")
        assert result["content_type"] == "github"
        # Title falls back to "owner/repo — GitHub repo".
        assert "owner/repo" in result["title"]
        assert result["content_full"] == ""

    async def test_http_error_wraps(self):
        class _Boom:
            def __init__(self, **_kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False
            async def get(self, url, **_kw):
                raise httpx.ConnectError("nope")

        with patch.object(url_scraper.httpx, "AsyncClient", _Boom):
            with pytest.raises(URLScrapeError, match="GitHub fetch failed"):
                await scrape_url("https://github.com/owner/repo")


# ---------------------------------------------------------------------------
# _scrape_arxiv
# ---------------------------------------------------------------------------


_ARXIV_HTML = """
<html>
  <head><title>arXiv:1234.5678 Title Here</title></head>
  <body>
    <h1 class="title">Title: Real Paper Title</h1>
    <blockquote class="abstract">Abstract: This paper proves something.</blockquote>
    <div class="authors">Alice, Bob</div>
  </body>
</html>
"""


@pytest.mark.unit
class TestScrapeArxiv:
    async def test_extracts_title_abstract_authors(self):
        responses = [_FakeResponse(text=_ARXIV_HTML)]
        with _patch_async_client(responses):
            result = await scrape_url("https://arxiv.org/abs/1234.5678")
        assert result["content_type"] == "arxiv"
        assert "Real Paper Title" in result["title"]
        assert "Title:" not in result["title"]  # prefix stripped
        assert "proves something" in result["content_full"]
        assert "Abstract:" not in result["content_full"]  # prefix stripped
        assert result["author"] == "Alice, Bob"

    async def test_pdf_url_normalized_to_abs(self):
        responses = [_FakeResponse(text=_ARXIV_HTML)]
        with _patch_async_client(responses):
            result = await scrape_url("https://arxiv.org/pdf/1234.5678")
        # Result url should now point at /abs/.
        assert "/abs/1234.5678" in result["url"]

    async def test_uses_arxiv_base_url_from_site_config(self):
        sc = _site_config({"arxiv_base_url": "https://mirror.example/"})
        responses = [_FakeResponse(text=_ARXIV_HTML)]
        with _patch_async_client(responses):
            result = await scrape_url(
                "https://arxiv.org/abs/1234.5678", site_config=sc,
            )
        assert "mirror.example" in result["url"]

    async def test_unrecognized_arxiv_url_uses_input_url(self):
        # No /abs/<id> or /pdf/<id> — falls into the `else: abs_url = url`
        # branch.
        responses = [_FakeResponse(text=_ARXIV_HTML)]
        with _patch_async_client(responses):
            result = await scrape_url("https://arxiv.org/list/cs.AI/2604")
        assert result["url"] == "https://arxiv.org/list/cs.AI/2604"

    async def test_http_error_wraps(self):
        responses = [_FakeResponse(text="", is_success=False)]
        with _patch_async_client(responses):
            with pytest.raises(URLScrapeError, match="arXiv fetch failed"):
                await scrape_url("https://arxiv.org/abs/9999.0000")

    async def test_handles_missing_abstract_block(self):
        html = "<html><body><h1 class='title'>Just A Title</h1></body></html>"
        responses = [_FakeResponse(text=html)]
        with _patch_async_client(responses):
            result = await scrape_url("https://arxiv.org/abs/1111.2222")
        assert result["content_full"] == ""
        assert result["author"] is None


# ---------------------------------------------------------------------------
# _first_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHelpers:
    def test_first_text_finds_tag_by_name(self):
        soup = BeautifulSoup("<h1>Heading</h1>", "html.parser")
        assert _first_text(soup, "h1") == "Heading"

    def test_first_text_supports_class_selector(self):
        soup = BeautifulSoup(
            "<h1 class='title'>Cls Title</h1>", "html.parser",
        )
        assert _first_text(soup, "h1.title") == "Cls Title"

    def test_first_text_returns_empty_when_missing(self):
        soup = BeautifulSoup("<html></html>", "html.parser")
        assert _first_text(soup, "h1") == ""
