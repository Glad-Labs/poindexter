"""
Unit tests for exception-logging behavior in services/web_research.py.

Verifies that exceptions returned from asyncio.gather(*tasks,
return_exceptions=True) in WebResearcher.search() are logged as warnings
rather than silently discarded (#1301 QaRailFullySkipped root cause).
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from services.site_config import SiteConfig
from services.web_research import WebResearcher


class TestWebResearchExceptionLogging:
    """Exceptions in the gather result produce a warning log, not silent drops."""

    @pytest.mark.asyncio
    async def test_exception_in_gather_logs_warning(self, caplog):
        """When asyncio.gather returns an Exception for a fetch task, a warning
        log is emitted and the result is not added to the returned list."""
        researcher = WebResearcher(site_config=SiteConfig())

        ddg_results = [
            {"title": "Good", "url": "https://example.com", "snippet": "ok", "content": ""},
            {"title": "Bad", "url": "https://broken.example.com", "snippet": "fail", "content": ""},
        ]
        good_result = {**ddg_results[0], "content": "fetched content"}

        with patch.object(researcher, "_ddg_search", new_callable=AsyncMock) as mock_ddg:
            mock_ddg.return_value = ddg_results

            # Patch asyncio.gather so it returns [good_dict, Exception] as gather
            # would when return_exceptions=True and one coro raised.
            gather_return = [good_result, ConnectionError("simulated fetch failure")]

            async def _fake_gather(*args, **kwargs):
                return gather_return

            with (
                patch("services.web_research.asyncio.gather", side_effect=_fake_gather),
                caplog.at_level(logging.WARNING, logger="services.web_research"),
            ):
                results = await researcher.search("test query")

        # Only the successful result is returned
        assert len(results) == 1
        assert results[0]["url"] == "https://example.com"

        # The exception was logged as a warning
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "[RESEARCH] Web fetch failed (non-fatal):" in msg for msg in warning_messages
        ), f"Expected warning log for fetch exception, got: {warning_messages}"

    @pytest.mark.asyncio
    async def test_all_exceptions_still_returns_empty_list(self, caplog):
        """When ALL gather results are exceptions, search returns [] and logs each one."""
        researcher = WebResearcher(site_config=SiteConfig())

        ddg_results = [
            {"title": "A", "url": "https://a.example.com", "snippet": "a", "content": ""},
            {"title": "B", "url": "https://b.example.com", "snippet": "b", "content": ""},
        ]

        gather_return = [
            ConnectionError("fetch failed A"),
            TimeoutError("fetch timed out B"),
        ]

        async def _fake_gather(*args, **kwargs):
            return gather_return

        with patch.object(researcher, "_ddg_search", new_callable=AsyncMock) as mock_ddg:
            mock_ddg.return_value = ddg_results

            with (
                patch("services.web_research.asyncio.gather", side_effect=_fake_gather),
                caplog.at_level(logging.WARNING, logger="services.web_research"),
            ):
                results = await researcher.search("test query")

        assert results == []

        # Both exceptions should be logged
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        logged_fetch_warnings = sum(
            "[RESEARCH] Web fetch failed (non-fatal):" in msg for msg in warning_messages
        )
        assert logged_fetch_warnings == 2, (
            f"Expected 2 fetch-failure warnings, got {logged_fetch_warnings}: {warning_messages}"
        )

    @pytest.mark.asyncio
    async def test_no_exception_no_warning(self, caplog):
        """When all gather results are successful dicts, no fetch-failure warning is logged."""
        researcher = WebResearcher(site_config=SiteConfig())

        ddg_results = [
            {"title": "OK", "url": "https://ok.example.com", "snippet": "ok", "content": ""},
        ]
        good_result = {"title": "OK", "url": "https://ok.example.com", "snippet": "ok", "content": "good"}

        async def _fake_gather(*args, **kwargs):
            return [good_result]

        with patch.object(researcher, "_ddg_search", new_callable=AsyncMock) as mock_ddg:
            mock_ddg.return_value = ddg_results

            with (
                patch("services.web_research.asyncio.gather", side_effect=_fake_gather),
                caplog.at_level(logging.WARNING, logger="services.web_research"),
            ):
                results = await researcher.search("test query")

        assert len(results) == 1
        warning_messages = [
            r.message for r in caplog.records
            if r.levelname == "WARNING" and "Web fetch failed" in r.message
        ]
        assert warning_messages == [], f"Unexpected fetch-failure warnings: {warning_messages}"
