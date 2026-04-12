"""
Unit tests for services/preview_screenshot.py.

Tests cover:
- Successful screenshot capture (mocked playwright)
- Correct arguments passed to browser, context, page, and screenshot calls
- Custom viewport / timeout / wait parameters
- Graceful None return when playwright is not installed
- Graceful None return when browser launch fails
- Graceful None return when page navigation fails
- Browser is always closed (even after errors)

Playwright is always mocked — no real browser is launched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PREVIEW_URL = "http://localhost:8002/preview/abc123"
FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def _build_playwright_mocks(
    *,
    screenshot_result: bytes = FAKE_PNG,
    goto_side_effect=None,
    launch_side_effect=None,
):
    """Build a full mock chain for playwright's async API.

    Returns (mock_async_playwright_cm, mock_browser, mock_page) so tests
    can inspect calls and configure side effects.
    """
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock(side_effect=goto_side_effect)
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=screenshot_result)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = AsyncMock()
    if launch_side_effect:
        mock_chromium.launch = AsyncMock(side_effect=launch_side_effect)
    else:
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    # async_playwright() returns an async context manager
    mock_async_pw = AsyncMock()
    mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_async_pw.__aexit__ = AsyncMock(return_value=False)

    return mock_async_pw, mock_browser, mock_page, mock_context, mock_chromium


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCapturePreviewScreenshot:
    """Tests for capture_preview_screenshot."""

    async def test_successful_capture(self):
        mock_pw_cm, mock_browser, mock_page, _, _ = _build_playwright_mocks()

        with patch(
            "services.preview_screenshot.async_playwright",
            create=True,
        ) as patched:
            # We need to patch the import inside the function.
            # The function does `from playwright.async_api import async_playwright`
            # so we patch that module.
            with patch.dict(
                "sys.modules",
                {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
            ):
                import importlib
                import services.preview_screenshot as mod

                # Patch the import within the function by replacing the whole
                # function's import mechanism. Simpler: just call the function
                # and patch playwright.async_api.async_playwright.
                with patch(
                    "playwright.async_api.async_playwright",
                    return_value=mock_pw_cm,
                ):
                    result = await mod.capture_preview_screenshot(PREVIEW_URL)

        assert result == FAKE_PNG

    async def test_returns_none_when_playwright_not_installed(self):
        """When playwright is not importable, should return None."""
        import sys
        # Temporarily remove playwright from sys.modules and make import fail
        saved_modules = {}
        for key in list(sys.modules.keys()):
            if key.startswith("playwright"):
                saved_modules[key] = sys.modules.pop(key)

        import builtins
        original_import = builtins.__import__

        def _fail_playwright(name, *args, **kwargs):
            if name.startswith("playwright"):
                raise ImportError("No module named 'playwright'")
            return original_import(name, *args, **kwargs)

        try:
            builtins.__import__ = _fail_playwright
            # Re-import to get a fresh module
            import services.preview_screenshot as mod
            result = await mod.capture_preview_screenshot(PREVIEW_URL)
            assert result is None
        finally:
            builtins.__import__ = original_import
            sys.modules.update(saved_modules)

    async def test_returns_none_on_browser_launch_failure(self):
        mock_pw_cm, _, _, _, _ = _build_playwright_mocks(
            launch_side_effect=RuntimeError("chromium not found")
        )
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                result = await mod.capture_preview_screenshot(PREVIEW_URL)
        assert result is None

    async def test_returns_none_on_navigation_failure(self):
        mock_pw_cm, mock_browser, _, _, _ = _build_playwright_mocks(
            goto_side_effect=TimeoutError("page load timed out")
        )
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                result = await mod.capture_preview_screenshot(PREVIEW_URL)
        # Navigation error is caught; browser.close() is called in finally
        assert result is None
        mock_browser.close.assert_awaited_once()

    async def test_browser_closed_on_success(self):
        mock_pw_cm, mock_browser, _, _, _ = _build_playwright_mocks()
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                await mod.capture_preview_screenshot(PREVIEW_URL)
        mock_browser.close.assert_awaited_once()

    async def test_viewport_params_passed(self):
        mock_pw_cm, mock_browser, _, mock_context, _ = _build_playwright_mocks()
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                await mod.capture_preview_screenshot(
                    PREVIEW_URL,
                    viewport_width=1920,
                    viewport_height=1080,
                )
        mock_browser.new_context.assert_awaited_once()
        call_kwargs = mock_browser.new_context.call_args[1]
        assert call_kwargs["viewport"] == {"width": 1920, "height": 1080}

    async def test_full_page_false(self):
        mock_pw_cm, _, mock_page, _, _ = _build_playwright_mocks()
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                await mod.capture_preview_screenshot(
                    PREVIEW_URL, full_page=False
                )
        mock_page.screenshot.assert_awaited_once()
        call_kwargs = mock_page.screenshot.call_args[1]
        assert call_kwargs["full_page"] is False

    async def test_wait_after_load_zero_skips_wait(self):
        mock_pw_cm, _, mock_page, _, _ = _build_playwright_mocks()
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                await mod.capture_preview_screenshot(
                    PREVIEW_URL, wait_after_load_ms=0
                )
        # wait_for_timeout should NOT be called when wait_after_load_ms=0
        mock_page.wait_for_timeout.assert_not_awaited()

    async def test_timeout_passed_to_goto(self):
        mock_pw_cm, _, mock_page, _, _ = _build_playwright_mocks()
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                await mod.capture_preview_screenshot(
                    PREVIEW_URL, timeout_ms=15000
                )
        mock_page.goto.assert_awaited_once()
        call_kwargs = mock_page.goto.call_args[1]
        assert call_kwargs["timeout"] == 15000

    async def test_chromium_launch_args(self):
        """Verify the browser is launched with the expected sandbox flags."""
        mock_pw_cm, _, _, _, mock_chromium = _build_playwright_mocks()
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import services.preview_screenshot as mod
                await mod.capture_preview_screenshot(PREVIEW_URL)
        mock_chromium.launch.assert_awaited_once()
        call_kwargs = mock_chromium.launch.call_args[1]
        assert call_kwargs["headless"] is True
        assert "--no-sandbox" in call_kwargs["args"]
        assert "--disable-gpu" in call_kwargs["args"]


@pytest.mark.asyncio
class TestModuleExports:
    """Verify __all__ is correct."""

    async def test_all_exports(self):
        import services.preview_screenshot as mod
        assert mod.__all__ == ["capture_preview_screenshot"]
