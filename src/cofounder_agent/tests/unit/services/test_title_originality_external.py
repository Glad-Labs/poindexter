"""Unit tests for ``services/title_originality_external.py`` (GH-87).

Covers the acceptance criteria on the GitHub issue:

1. Verbatim match surfaces a penalty.
2. Near-match surfaces a warning (and no penalty).
3. Cache hit skips the HTTP fetch entirely.
4. Rate-limit / CAPTCHA / network error → fail-open + counter bump.
5. Disabled setting short-circuits — no HTTP call, no cache write.

Every test patches ``httpx.AsyncClient`` at the module-import boundary so
we never touch the real network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.title_originality_external import (
    ExternalOriginalityResult,
    _cache_key,
    _normalise_for_compare,
    _parse_ddg_results,
    check_external_title_duplicates,
    clear_cache,
)

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_originality_cache():
    """Drop the process-wide cache before every test.

    The module keeps a module-level dict that persists across test cases.
    Without this fixture, test ordering would matter.
    """
    clear_cache()
    yield
    clear_cache()


def _mock_sc(*, enabled: bool = True, penalty: int = -50, ttl_hours: int = 24) -> MagicMock:
    """Build a MagicMock shaped like SiteConfig for the originality check.

    Post-Phase-H, ``check_external_title_duplicates`` takes ``site_config``
    as a kw-only parameter instead of reading the module singleton. Tests
    wire a purpose-built mock here rather than patching the module.
    """
    sc = MagicMock()
    sc.get_bool.side_effect = lambda key, default=False: {
        "title_originality_external_check_enabled": enabled,
    }.get(key, default)
    sc.get_int.side_effect = lambda key, default=0: {
        "title_originality_external_penalty": penalty,
        "title_originality_cache_ttl_hours": ttl_hours,
    }.get(key, default)
    return sc


@pytest.fixture
def _enabled_settings():
    """Return a SiteConfig mock with the feature enabled + default penalty."""
    return _mock_sc(enabled=True, penalty=-50, ttl_hours=24)


@pytest.fixture
def _disabled_settings():
    return _mock_sc(enabled=False)


def _fake_async_client(*, response=None, raise_exc=None):
    """Build a context-manager AsyncMock that mimics httpx.AsyncClient."""
    client_instance = AsyncMock()
    if raise_exc is not None:
        client_instance.get = AsyncMock(side_effect=raise_exc)
    else:
        client_instance.get = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    return client_instance


def _ddg_body_with(titles_and_urls):
    """Build a minimal DDG-shaped HTML body containing the given results."""
    blocks = []
    for title, url in titles_and_urls:
        blocks.append(
            f'<a class="result__a" href="{url}">{title}</a>'
        )
    return f"<html><body>{''.join(blocks)}</body></html>"


# ---------------------------------------------------------------------------
# _normalise_for_compare / _cache_key
# ---------------------------------------------------------------------------


class TestNormaliseForCompare:
    def test_lowercases(self):
        assert _normalise_for_compare("Hello World") == "hello world"

    def test_strips_trailing_punctuation(self):
        assert _normalise_for_compare("Hello World!") == "hello world"
        assert _normalise_for_compare("Hello World.") == "hello world"
        assert _normalise_for_compare("Hello World???") == "hello world"

    def test_collapses_whitespace(self):
        assert _normalise_for_compare("Hello    World") == "hello world"

    def test_handles_smart_quotes_and_dashes(self):
        assert _normalise_for_compare("Don’t Do That") == _normalise_for_compare(
            "Don't Do That",
        )
        assert _normalise_for_compare("A—B") == _normalise_for_compare("A-B")

    def test_handles_empty(self):
        assert _normalise_for_compare("") == ""
        assert _normalise_for_compare(None) == ""  # type: ignore[arg-type]

    def test_the_gh87_headline_matches_verbatim(self):
        """The exact headline from issue GH-87 should normalise to itself."""
        ours = "AI Doesn’t Fix Weak Engineering. It Just Speeds It Up."
        theirs = "AI Doesn't Fix Weak Engineering. It Just Speeds It Up."
        assert _normalise_for_compare(ours) == _normalise_for_compare(theirs)


class TestCacheKey:
    def test_is_case_insensitive(self):
        assert _cache_key("Hello") == _cache_key("hello")

    def test_ignores_punctuation(self):
        assert _cache_key("Hello, World!") == _cache_key("hello world")


# ---------------------------------------------------------------------------
# _parse_ddg_results
# ---------------------------------------------------------------------------


class TestParseDdgResults:
    def test_extracts_title_and_url(self):
        body = _ddg_body_with([
            ("A Great Post", "https://example.com/a"),
            ("Another Post", "https://example.com/b"),
        ])
        results = _parse_ddg_results(body)
        assert len(results) == 2
        assert results[0]["title"] == "A Great Post"
        assert results[0]["url"] == "https://example.com/a"

    def test_strips_inner_tags(self):
        body = '<a class="result__a" href="https://x.com/1">A <b>Bold</b> Title</a>'
        results = _parse_ddg_results(body)
        assert results[0]["title"] == "A Bold Title"

    def test_respects_limit(self):
        body = _ddg_body_with([
            (f"Post {i}", f"https://example.com/{i}") for i in range(20)
        ])
        results = _parse_ddg_results(body, limit=5)
        assert len(results) == 5

    def test_empty_body_returns_empty(self):
        assert _parse_ddg_results("") == []
        assert _parse_ddg_results("<html><body>no results</body></html>") == []


# ---------------------------------------------------------------------------
# check_external_title_duplicates — end-to-end with mocked httpx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCheckExternalTitleDuplicates:
    async def test_verbatim_match_returns_penalty(self, _enabled_settings):
        """The exact GH-87 scenario: DDG returns the same title we probed."""
        probe = "AI Doesn't Fix Weak Engineering. It Just Speeds It Up."
        resp = MagicMock(
            status_code=200,
            text=_ddg_body_with([
                (probe, "https://dev.to/jonomakesapps/x"),
                ("Unrelated Other Post", "https://example.com/2"),
            ]),
        )
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
            return_value=_fake_async_client(response=resp),
        ):
            result = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )

        assert result.verbatim_match is True
        assert result.near_match is False
        assert result.penalty == 50  # abs of default -50
        assert result.fail_open is False
        assert result.matches
        assert result.matches[0]["url"] == "https://dev.to/jonomakesapps/x"

    async def test_near_match_returns_warning_no_penalty(self, _enabled_settings):
        """Similarity 0.80..0.90 → near-match flag, no penalty."""
        probe = "How GPUs Handle Deep Learning Workloads Today"
        # SequenceMatcher ratio ~0.835 (0.80..0.90 band = near-match).
        near = "How GPUs Handle ML Workloads Today"
        resp = MagicMock(
            status_code=200,
            text=_ddg_body_with([(near, "https://example.com/1")]),
        )
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
            return_value=_fake_async_client(response=resp),
        ):
            result = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )

        # 0.835 lands in the near-match band: warning but no penalty.
        assert result.near_match is True
        assert result.verbatim_match is False
        assert result.penalty == 0
        assert result.matches[0]["url"] == "https://example.com/1"

    async def test_no_match_returns_clean(self, _enabled_settings):
        probe = "A Uniquely Niche Title About Polygon-Fluffing"
        resp = MagicMock(
            status_code=200,
            text=_ddg_body_with([
                ("Completely Unrelated Hummus Recipes", "https://example.com/1"),
            ]),
        )
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
            return_value=_fake_async_client(response=resp),
        ):
            result = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )

        assert result.verbatim_match is False
        assert result.near_match is False
        assert result.penalty == 0
        assert result.fail_open is False

    async def test_cache_hit_skips_fetch(self, _enabled_settings):
        """A repeat call with the same (normalised) title must not hit DDG."""
        probe = "Cache Test Title"
        resp = MagicMock(
            status_code=200,
            text=_ddg_body_with([(probe, "https://example.com/a")]),
        )
        fake_client = _fake_async_client(response=resp)
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
            return_value=fake_client,
        ) as mock_client_cls:
            first = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )
            # Second call (same title, different casing + trailing punct) —
            # should hit the cache, not call httpx again.
            second = await check_external_title_duplicates(
                "CACHE TEST TITLE!", site_config=_enabled_settings,
            )

        assert first.verbatim_match is True
        assert second.verbatim_match is True
        # httpx.AsyncClient was constructed exactly once (the second call
        # short-circuited on cache).
        assert mock_client_cls.call_count == 1

    async def test_429_fail_open(self, _enabled_settings):
        """HTTP 429 → fail_open=True, no penalty, counter bumps."""
        probe = "Rate Limited Title"
        resp = MagicMock(status_code=429, text="rate limit")
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
            return_value=_fake_async_client(response=resp),
        ), patch(
            "services.title_originality_external.TITLE_ORIGINALITY_FAIL_OPEN",
        ) as mock_counter:
            mock_counter.labels.return_value = mock_counter
            result = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )

        assert result.fail_open is True
        assert result.fail_reason == "rate_limited"
        assert result.penalty == 0
        assert result.verbatim_match is False
        # Counter incremented once with the reason label
        mock_counter.labels.assert_called_with(reason="rate_limited")
        mock_counter.inc.assert_called_once()

    async def test_timeout_fail_open(self, _enabled_settings):
        probe = "Timeout Title"
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
            return_value=_fake_async_client(raise_exc=httpx.TimeoutException("boom")),
        ):
            result = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )

        assert result.fail_open is True
        assert result.fail_reason == "timeout"
        assert result.penalty == 0

    async def test_captcha_body_fail_open(self, _enabled_settings):
        """A 200 response containing a CAPTCHA marker must fail open."""
        probe = "Captcha Title"
        resp = MagicMock(
            status_code=200,
            text="<html><body>unusual traffic from your network</body></html>",
        )
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
            return_value=_fake_async_client(response=resp),
        ):
            result = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )

        assert result.fail_open is True
        assert result.fail_reason == "captcha"
        assert result.penalty == 0

    async def test_fail_open_is_not_cached(self, _enabled_settings):
        """A rate-limit today should not silence the check for 24 hours."""
        probe = "Retry Tomorrow Title"
        rate_limited = MagicMock(status_code=429, text="rate limit")
        ok_resp = MagicMock(
            status_code=200,
            text=_ddg_body_with([(probe, "https://example.com/1")]),
        )

        with patch(
            "services.title_originality_external.httpx.AsyncClient",
        ) as mock_cls:
            mock_cls.side_effect = [
                _fake_async_client(response=rate_limited),
                _fake_async_client(response=ok_resp),
            ]
            first = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )
            second = await check_external_title_duplicates(
                probe, site_config=_enabled_settings,
            )

        assert first.fail_open is True
        # Second call went back to the network (fail-open wasn't cached)
        # and actually got the real result this time.
        assert second.fail_open is False
        assert second.verbatim_match is True

    async def test_disabled_setting_skips_fetch(self, _disabled_settings):
        """Kill-switch must short-circuit before any HTTP call."""
        probe = "Anything"
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
        ) as mock_cls:
            result = await check_external_title_duplicates(
                probe, site_config=_disabled_settings,
            )

        assert result == ExternalOriginalityResult()
        assert mock_cls.call_count == 0

    async def test_empty_title_returns_empty_result(self, _enabled_settings):
        """Defensive: no probe = no work."""
        with patch(
            "services.title_originality_external.httpx.AsyncClient",
        ) as mock_cls:
            result = await check_external_title_duplicates(
                "", site_config=_enabled_settings,
            )
            result2 = await check_external_title_duplicates(
                "   ", site_config=_enabled_settings,
            )

        assert result.verbatim_match is False
        assert result2.verbatim_match is False
        assert mock_cls.call_count == 0


# ---------------------------------------------------------------------------
# Wiring: check_title_originality (the parent caller) picks up the external
# result and flips is_original when there's a verbatim match.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCheckTitleOriginalityIntegration:
    async def test_external_verbatim_flips_is_original(self):
        """When the DDG HTML path finds a verbatim match, the legacy
        check_title_originality() must set is_original=False and surface
        the penalty + external match details."""
        from services.title_generation import check_title_originality

        # Internal-corpus path: WebResearcher returns nothing (original vs
        # our own posts). External path: DDG returns a verbatim match.
        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(return_value=[])

        ext_result = ExternalOriginalityResult(
            verbatim_match=True,
            near_match=False,
            penalty=50,
            matches=[{"title": "The Exact Title", "url": "https://ex.com/x"}],
            fail_open=False,
        )

        with patch(
            "services.web_research.WebResearcher",
            return_value=mock_researcher,
        ), patch(
            "services.title_originality_external.check_external_title_duplicates",
            AsyncMock(return_value=ext_result),
        ), patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_float.return_value = 0.6
            mock_cfg.get_bool.return_value = True

            result = await check_title_originality("The Exact Title")

        assert result["is_original"] is False
        assert result["external_verbatim_match"] is True
        assert result["external_penalty"] == 50
        assert result["external_matches"][0]["url"] == "https://ex.com/x"
        # The external title was pushed into similar_titles so the
        # regeneration avoid-list picks it up.
        assert "The Exact Title" in result["similar_titles"]

    async def test_external_fail_open_does_not_break_legacy_result(self):
        """A DDG rate-limit should leave is_original alone."""
        from services.title_generation import check_title_originality

        mock_researcher = MagicMock()
        mock_researcher.search_simple = AsyncMock(return_value=[])

        ext_result = ExternalOriginalityResult(
            fail_open=True, fail_reason="rate_limited",
        )

        with patch(
            "services.web_research.WebResearcher",
            return_value=mock_researcher,
        ), patch(
            "services.title_originality_external.check_external_title_duplicates",
            AsyncMock(return_value=ext_result),
        ), patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_float.return_value = 0.6
            mock_cfg.get_bool.return_value = True

            result = await check_title_originality("Some Title")

        assert result["is_original"] is True
        assert result["external_fail_open"] is True
        assert result["external_penalty"] == 0
