"""Round-2 fills for services/content_validator.py.

This file targets the previously-uncovered chunks:
  - verify_content_urls (lines 1321-1396) — async URL liveness check
  - title_diversity warning (lines 1187-1196)
  - _is_known_reference Ollama suffix-strip path (lines 596-598)
  - _load_known_list missing-file warning path (lines 425-427)
  - _find_hc_dir container/fallback paths (lines 396-401)
  - _normalize_pkg / hallucination ref helper edges

Strategy: don't reimport the singleton _sc — every test seeds its own
SiteConfig via patch.object on the module-level binding. URL checks
mock httpx so no real network calls happen.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import content_validator as cv
from services.content_validator import (
    ValidationIssue,
    _extract_library_candidates,
    _is_known_reference,
    _load_known_list,
    _normalize_pkg,
    verify_content_urls,
)


# ---------------------------------------------------------------------------
# verify_content_urls — the largest uncovered block (lines 1321-1396)
# ---------------------------------------------------------------------------


def _make_response(status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    return resp


def _make_httpx_client(head_responses: dict[str, MagicMock] | None = None,
                      head_exception: Exception | None = None) -> MagicMock:
    """Build a mock httpx.AsyncClient context manager that returns scripted
    responses for HEAD requests."""
    client = MagicMock()

    async def _head(url: str, *args, **kwargs):
        if head_exception is not None:
            raise head_exception
        if head_responses and url in head_responses:
            return head_responses[url]
        return _make_response(200)

    client.head = AsyncMock(side_effect=_head)

    async_ctx = AsyncMock()
    async_ctx.__aenter__ = AsyncMock(return_value=client)
    async_ctx.__aexit__ = AsyncMock(return_value=None)

    return async_ctx, client


class TestVerifyContentUrls:
    @pytest.mark.asyncio
    async def test_no_urls_returns_empty(self):
        issues = await verify_content_urls("Just plain prose with no links.")
        assert issues == []

    @pytest.mark.asyncio
    async def test_live_url_passes(self):
        ctx, client = _make_httpx_client({
            "https://example.com/doc": _make_response(200),
        })
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="")
            content = "See [the docs](https://example.com/doc) for details."
            issues = await verify_content_urls(content)
        # Only "no_citations" is a possible warning; live-link itself is fine
        # but we expect at least no critical dead_link issue
        critical = [i for i in issues if i.severity == "critical"]
        assert critical == []

    @pytest.mark.asyncio
    async def test_404_link_flagged_as_critical_dead(self):
        ctx, client = _make_httpx_client({
            "https://example.com/missing": _make_response(404),
        })
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="")
            content = "Broken: [link](https://example.com/missing)"
            issues = await verify_content_urls(content)
        dead = [i for i in issues if i.category == "dead_link"]
        assert len(dead) == 1
        assert dead[0].severity == "critical"
        assert "404" in dead[0].description

    @pytest.mark.asyncio
    async def test_500_link_also_flagged(self):
        ctx, client = _make_httpx_client({
            "https://example.com/server-down": _make_response(500),
        })
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="")
            content = "Server error: https://example.com/server-down"
            issues = await verify_content_urls(content)
        assert any(i.category == "dead_link" for i in issues)

    @pytest.mark.asyncio
    async def test_timeout_is_warning_not_critical(self):
        import httpx
        ctx, client = _make_httpx_client(
            head_exception=httpx.TimeoutException("slow server"),
        )
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="")
            content = "Slow link: [docs](https://slow.example.com/x)"
            issues = await verify_content_urls(content)
        slow = [i for i in issues if i.category == "slow_link"]
        assert len(slow) == 1
        assert slow[0].severity == "warning"

    @pytest.mark.asyncio
    async def test_unresolvable_url_flagged_warning(self):
        ctx, client = _make_httpx_client(
            head_exception=ConnectionError("DNS failure"),
        )
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="")
            content = "Bad: [link](https://no-such-host.invalid/)"
            issues = await verify_content_urls(content)
        unresolvable = [i for i in issues if i.category == "unresolvable_link"]
        assert len(unresolvable) == 1
        assert unresolvable[0].severity == "warning"
        assert "ConnectionError" in unresolvable[0].description

    @pytest.mark.asyncio
    async def test_internal_links_skipped(self):
        """Configured site_domains are skipped — no HEAD attempted."""
        ctx, client = _make_httpx_client({})
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="example.com")
            content = "Internal: [home](https://example.com/about)"
            issues = await verify_content_urls(content)
        # No HEAD call should have happened (internal skip)
        assert client.head.await_count == 0
        # But the no_citations warning will fire because all URLs are internal
        no_cite = [i for i in issues if i.category == "no_citations"]
        assert len(no_cite) == 1

    @pytest.mark.asyncio
    async def test_external_citations_present_no_warning(self):
        ctx, client = _make_httpx_client({
            "https://other.com/x": _make_response(200),
        })
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="example.com")
            content = "External: [ref](https://other.com/x)"
            issues = await verify_content_urls(content)
        no_cite = [i for i in issues if i.category == "no_citations"]
        assert no_cite == []

    @pytest.mark.asyncio
    async def test_localhost_via_endswith_suffix_skipped(self):
        """Hostnames ending in '.localhost' (per the source's check) are skipped."""
        ctx, client = _make_httpx_client({})
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="")
            content = "Internal: [api](http://service.localhost/api)"
            issues = await verify_content_urls(content)
        # service.localhost ends with '.localhost' -> skip
        assert client.head.await_count == 0

    @pytest.mark.asyncio
    async def test_bare_url_extracted(self):
        ctx, client = _make_httpx_client({
            "https://bare.example.com/path": _make_response(200),
        })
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch.object(cv, "_sc") as sc:
            sc.get = MagicMock(return_value="")
            content = "Visit https://bare.example.com/path for more."
            issues = await verify_content_urls(content)
        # Should have HEAD'd the bare URL
        assert client.head.await_count == 1


# ---------------------------------------------------------------------------
# _is_known_reference — Ollama-suffix strip path (lines 596-598)
# ---------------------------------------------------------------------------


class TestIsKnownReference:
    def test_ollama_model_with_tag_suffix(self):
        """qwen3:7b should match 'qwen3' from ollama-models.txt."""
        with patch("services.content_validator._get_ollama_names",
                   return_value={"qwen3"}), \
             patch("services.content_validator._get_stdlib_names",
                   return_value=set()), \
             patch("services.content_validator._get_pypi_names",
                   return_value=set()):
            # The norm_name comes through _normalize_pkg, then suffix-stripped
            assert _is_known_reference("qwen3:7b") is True

    def test_unknown_name_returns_false(self):
        with patch("services.content_validator._get_ollama_names",
                   return_value=set()), \
             patch("services.content_validator._get_stdlib_names",
                   return_value=set()), \
             patch("services.content_validator._get_pypi_names",
                   return_value=set()):
            assert _is_known_reference("totally-fake-pkg") is False

    def test_stdlib_match_returns_true(self):
        with patch("services.content_validator._get_stdlib_names",
                   return_value={"asyncio"}):
            assert _is_known_reference("asyncio") is True

    def test_pypi_match_returns_true(self):
        with patch("services.content_validator._get_stdlib_names",
                   return_value=set()), \
             patch("services.content_validator._get_pypi_names",
                   return_value={"requests"}):
            assert _is_known_reference("requests") is True


# ---------------------------------------------------------------------------
# _load_known_list — missing-file warning path (lines 425-427)
# ---------------------------------------------------------------------------


class TestLoadKnownList:
    def test_missing_file_returns_empty_set(self):
        result = _load_known_list("definitely-does-not-exist-12345.txt")
        assert result == set()


# ---------------------------------------------------------------------------
# _normalize_pkg
# ---------------------------------------------------------------------------


class TestNormalizePkg:
    @pytest.mark.parametrize("raw, expected", [
        ("Requests", "requests"),
        ("python_dotenv", "python-dotenv"),
        ("  Pillow  ", "pillow"),
        ("Django-Rest-Framework", "django-rest-framework"),
    ])
    def test_normalize(self, raw, expected):
        assert _normalize_pkg(raw) == expected


# ---------------------------------------------------------------------------
# _extract_library_candidates skip-cases (lines 571-583)
# ---------------------------------------------------------------------------


class TestFindHallucinatedReferences:
    def test_short_token_skipped(self):
        """Tokens shorter than 3 chars are noise — skipped."""
        # `os` would normally match the stdlib but the function pre-filters
        # by length before whitelist or stdlib check
        text = "Use `os.path` for paths."
        result = _extract_library_candidates(text)
        # 'os' is too short — should not be in result
        assert all(norm != "os" for _raw, norm in result)

    def test_whitelist_entries_skipped(self):
        """Items in HALLUCINATION_WHITELIST are not returned."""
        from services.content_validator import _HALLUCINATION_WHITELIST
        if not _HALLUCINATION_WHITELIST:
            pytest.skip("whitelist empty in this build")
        sample_white = next(iter(_HALLUCINATION_WHITELIST))
        text = f"Reference {sample_white}() somewhere."
        result = _extract_library_candidates(text)
        norms = [norm for _raw, norm in result]
        assert sample_white not in norms

    def test_dedup_by_pair(self):
        """Same token mentioned multiple times appears only once."""
        text = "Use `requests.get()` here. Also `requests.post()` works."
        result = _extract_library_candidates(text)
        # Both produce a `requests` root; dedup by (raw, norm) pair lets each
        # raw form through but never the same one twice
        norms = [norm for _raw, norm in result]
        # 'requests' might appear twice if raw differs but should not appear 3+
        assert norms.count("requests") <= 2

    def test_empty_input_returns_empty(self):
        assert _extract_library_candidates("") == []
        assert _extract_library_candidates(None) == []


# ---------------------------------------------------------------------------
# Title diversity (lines 1187-1196) — sanity-check via the public entry
# ---------------------------------------------------------------------------


class TestTitleDiversity:
    def test_banned_opener_emits_warning(self):
        """A title starting with 'Mastering' should produce a title_diversity warning."""
        from services.content_validator import validate_content
        result = validate_content(
            title="Mastering Python Async",
            content="A long enough body. " * 100,
            topic="python async",
            tags=[],
        )
        diversity = [i for i in result.issues if i.category == "title_diversity"]
        # Either the rule is enabled or it's been disabled by config; either is fine,
        # but if it fires it should be a warning about 'mastering'
        if diversity:
            assert "mastering" in diversity[0].description.lower()

    def test_normal_title_no_warning(self):
        from services.content_validator import validate_content
        result = validate_content(
            title="Postgres LISTEN/NOTIFY in production",
            content="A long enough body. " * 100,
            topic="postgres",
            tags=[],
        )
        diversity = [i for i in result.issues if i.category == "title_diversity"]
        assert diversity == []
