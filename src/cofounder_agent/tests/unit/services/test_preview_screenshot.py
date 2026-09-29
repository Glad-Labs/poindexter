"""
Unit tests for services/preview_screenshot.py.

Tests cover:
- plan_tiles: how a page is cut into the tiles the vision judge reads (pure)
- capture_html_tiles: renders a given document with JavaScript off, tolerates a
  slow sub-resource, takes clipped screenshots at the page's real width, shrinks
  to fit only when allowed, and raises PreviewScreenshotError naming the cause
  instead of returning None
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
            "poindexter.services.preview_screenshot.async_playwright",
            create=True,
        ):
            # We need to patch the import inside the function.
            # The function does `from playwright.async_api import async_playwright`
            # so we patch that module.
            with patch.dict(
                "sys.modules",
                {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
            ):
                import poindexter.services.preview_screenshot as mod

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
            import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
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
                import poindexter.services.preview_screenshot as mod
                await mod.capture_preview_screenshot(PREVIEW_URL)
        mock_chromium.launch.assert_awaited_once()
        call_kwargs = mock_chromium.launch.call_args[1]
        assert call_kwargs["headless"] is True
        assert "--no-sandbox" in call_kwargs["args"]
        assert "--disable-gpu" in call_kwargs["args"]


    async def test_url_capture_keeps_javascript_on(self):
        """The URL path serves the brand hero / thumbnail / screenshot provider,
        some of which render JS-driven pages: JavaScript stays enabled."""
        mock_pw_cm, mock_browser, _, _, _ = _build_playwright_mocks()
        with patch.dict(
            "sys.modules",
            {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
        ):
            with patch(
                "playwright.async_api.async_playwright",
                return_value=mock_pw_cm,
            ):
                import poindexter.services.preview_screenshot as mod
                await mod.capture_preview_screenshot(PREVIEW_URL)
        assert mock_browser.new_context.call_args[1]["java_script_enabled"] is True


PREVIEW_HTML = "<!DOCTYPE html><html><body><h1>Draft</h1></body></html>"


# ---------------------------------------------------------------------------
# Tiles: the page as a few images the judge can read
# ---------------------------------------------------------------------------

TILE_AREA = 1280 * 1024  # the default viewport, in px


def _png(width: int, height: int) -> bytes:
    """A real PNG of this size. Content is irrelevant to the tiler."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), (128, 128, 128)).save(buf, format="PNG")
    return buf.getvalue()


def _tile_mocks(page_size, *, viewport=1280, images=0, failed=()):
    """Playwright mocks for a ``page_size`` = (width, height) page.

    ``evaluate`` answers like the one CDP measurement the capture makes (page size
    plus the image facts). ``screenshot`` answers with a PNG as big as the clip it
    was asked for, the way chromium does, so the tiler's size bookkeeping is
    exercised for real.
    """
    mock_pw_cm, mock_browser, mock_page, _, _ = _build_playwright_mocks()
    mock_page.evaluate = AsyncMock(return_value={
        "width": page_size[0], "height": page_size[1], "viewport": viewport,
        "images": images, "failed": [dict(f) for f in failed],
    })

    async def _shot(**kwargs):
        clip = kwargs["clip"]
        return _png(int(clip["width"]), int(clip["height"]))

    mock_page.screenshot = AsyncMock(side_effect=_shot)
    return mock_pw_cm, mock_browser, mock_page


async def _tiles_capture(mock_pw_cm, html=PREVIEW_HTML, **kwargs):
    with patch.dict(
        "sys.modules",
        {"playwright": MagicMock(), "playwright.async_api": MagicMock()},
    ):
        with patch("playwright.async_api.async_playwright", return_value=mock_pw_cm):
            import poindexter.services.preview_screenshot as mod
            return await mod.capture_html_tiles(html, **kwargs)


def _plan(width, height, *, max_tiles=8, min_scale=1.0):
    from poindexter.services.preview_screenshot import plan_tiles

    return plan_tiles(
        width, height, tile_area=TILE_AREA, max_tiles=max_tiles, min_scale=min_scale,
    )


class TestPlanTiles:
    """plan_tiles decides what the judge is shown. Every tile is held to about
    a viewport's worth of pixels, so a request costs about ``max_tiles`` tiles
    of context whatever the page looks like."""

    def test_a_short_page_is_one_tile(self):
        plan = _plan(1280, 700)
        assert plan.spans == ((0, 700),)
        assert (plan.scale, plan.complete) == (1.0, True)

    def test_a_page_that_fits_is_cut_at_native_scale(self):
        plan = _plan(1280, 3000)
        assert plan.spans == ((0, 1024), (1024, 2048), (2048, 3000))
        assert (plan.scale, plan.complete) == (1.0, True)

    def test_an_exact_multiple_leaves_no_sliver(self):
        plan = _plan(1280, 8192)
        assert len(plan.spans) == 8
        assert {bottom - top for top, bottom in plan.spans} == {1024}
        assert plan.complete

    def test_the_evidence_page_needs_more_than_eight_native_tiles(self):
        """1280x13141 (draft 3ceda1c0) is 13 native tiles: past an 8-tile budget."""
        plan = _plan(1280, 13141)
        assert len(plan.spans) == 8
        assert plan.scale == 1.0 and plan.complete is False

    def test_shrinking_off_samples_the_page_at_native_scale(self):
        plan = _plan(1280, 13141, min_scale=1.0)
        assert plan.scale == 1.0
        assert not plan.complete
        assert plan.spans[0] == (0, 1024)
        assert plan.spans[-1][1] == 13141  # the end of the page is always seen
        assert {bottom - top for top, bottom in plan.spans} == {1024}

    def test_a_too_tall_page_shrinks_to_fit_when_allowed(self):
        import math

        plan = _plan(1280, 13141, min_scale=0.6)
        assert plan.complete
        assert len(plan.spans) == 8
        assert plan.scale == pytest.approx(math.sqrt(TILE_AREA * 8 / (1280 * 13141)))
        assert 0.78 < plan.scale < 0.80
        # contiguous: every row of the page is in some tile
        assert plan.spans[0][0] == 0 and plan.spans[-1][1] == 13141
        assert all(a[1] == b[0] for a, b in zip(plan.spans, plan.spans[1:], strict=False))

    def test_the_floor_holds_and_the_rest_is_sampled(self):
        plan = _plan(1280, 40000, min_scale=0.6)
        assert plan.scale == 0.6
        assert not plan.complete
        assert len(plan.spans) == 8
        assert plan.spans[0][0] == 0
        assert plan.spans[-1][1] == 40000

    def test_a_page_at_the_floor_edge_is_still_whole(self):
        # 8 tiles at min_scale 0.6 hold 8 * 1024 / 0.36 = 22,755 rows of a 1280 px page
        assert _plan(1280, 22700, min_scale=0.6).complete
        assert not _plan(1280, 22800, min_scale=0.6).complete

    def test_a_wide_page_gets_shorter_tiles_so_each_costs_the_same(self):
        """A table that overflows to 2000 px makes the screenshot 2000 px wide."""
        plan = _plan(2000, 6000)
        assert plan.spans[0] == (0, TILE_AREA // 2000)
        assert all((b - t) * 2000 <= TILE_AREA for t, b in plan.spans)

    def test_a_single_tile_budget_shows_the_top_of_a_tall_page(self):
        plan = _plan(1280, 13141, max_tiles=1)
        assert plan.spans == ((0, 1024),)
        assert not plan.complete

    def test_degenerate_inputs_are_clamped_not_raised(self):
        plan = _plan(0, 0, max_tiles=0, min_scale=-3)
        assert plan.spans and plan.spans[0][0] == 0

    @pytest.mark.parametrize("width", [1280, 1500, 2400])
    @pytest.mark.parametrize("height", [1, 900, 1024, 1025, 5000, 8192, 8193, 13141, 21000, 60000])
    @pytest.mark.parametrize("max_tiles", [1, 3, 8, 10])
    @pytest.mark.parametrize("min_scale", [1.0, 0.75, 0.6])
    def test_every_plan_is_ordered_in_bounds_and_within_budget(
        self, width, height, max_tiles, min_scale,
    ):
        plan = _plan(width, height, max_tiles=max_tiles, min_scale=min_scale)
        assert 1 <= len(plan.spans) <= max_tiles
        assert min_scale - 1e-9 <= plan.scale <= 1.0
        tops = [t for t, _ in plan.spans]
        assert tops == sorted(tops)
        for (top, bottom), nxt in zip(plan.spans, plan.spans[1:] + (None,), strict=False):
            assert 0 <= top < bottom <= height
            assert nxt is None or bottom <= nxt[0], "tiles must not overlap"
            # no tile carries more than a viewport of pixels (1.5% slack for row rounding)
            assert (bottom - top) * plan.scale * (width * plan.scale) <= TILE_AREA * 1.015
        if plan.complete:
            assert plan.spans[0][0] == 0 and plan.spans[-1][1] == height
            assert all(a[1] == b[0] for a, b in zip(plan.spans, plan.spans[1:], strict=False))
        else:
            assert plan.spans[0][0] == 0
            if max_tiles >= 2:
                assert plan.spans[-1][1] == height, "the end of the page is shown when there is room for two tiles"
            else:
                assert len(plan.spans) == 1, "one tile is the top of the page"


class TestPngDimensions:
    def test_reads_width_and_height_from_the_header(self):
        from poindexter.services.preview_screenshot import png_dimensions

        assert png_dimensions(_png(320, 200)) == (320, 200)

    @pytest.mark.parametrize("junk", [b"", b"not a png at all, just text", b"\x89PNG\r\n\x1a\n", FAKE_PNG])
    def test_anything_else_is_none(self, junk):
        from poindexter.services.preview_screenshot import png_dimensions

        assert png_dimensions(junk) is None


@pytest.mark.asyncio
class TestCaptureHtmlTiles:
    """capture_html_tiles measures the page, cuts it with plan_tiles and takes
    one clipped screenshot per tile, so no single image is page-sized."""

    async def test_renders_the_html_it_is_given(self):
        mock_pw_cm, _, mock_page = _tile_mocks((1280, 900))
        await _tiles_capture(mock_pw_cm, html=PREVIEW_HTML)
        mock_page.set_content.assert_awaited_once()
        assert mock_page.set_content.call_args[0][0] == PREVIEW_HTML
        mock_page.goto.assert_not_awaited()

    async def test_launch_failure_raises_with_the_cause(self):
        mock_pw_cm, _, _, _, _ = _build_playwright_mocks(
            launch_side_effect=RuntimeError("chromium not found")
        )
        import poindexter.services.preview_screenshot as mod
        with pytest.raises(mod.PreviewScreenshotError, match="chromium not found"):
            await _tiles_capture(mock_pw_cm)

    async def test_playwright_missing_raises_named_error(self):
        import builtins
        import sys

        saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k.startswith("playwright")}
        original_import = builtins.__import__

        def _fail_playwright(name, *args, **kwargs):
            if name.startswith("playwright"):
                raise ImportError("No module named 'playwright'")
            return original_import(name, *args, **kwargs)

        try:
            builtins.__import__ = _fail_playwright
            import poindexter.services.preview_screenshot as mod
            with pytest.raises(mod.PreviewScreenshotError, match="playwright is not installed"):
                await mod.capture_html_tiles(PREVIEW_HTML)
        finally:
            builtins.__import__ = original_import
            sys.modules.update(saved)

    async def test_cuts_the_page_into_clipped_tiles(self):
        mock_pw_cm, _, mock_page = _tile_mocks((1280, 2500))
        shot = await _tiles_capture(mock_pw_cm)
        clips = [call.kwargs["clip"] for call in mock_page.screenshot.await_args_list]
        assert clips == [
            {"x": 0, "y": 0, "width": 1280, "height": 1024},
            {"x": 0, "y": 1024, "width": 1280, "height": 1024},
            {"x": 0, "y": 2048, "width": 1280, "height": 452},
        ]
        assert all(c.kwargs["full_page"] is True and c.kwargs["type"] == "png"
                   for c in mock_page.screenshot.await_args_list)
        assert [(t.top, t.bottom, t.width, t.height) for t in shot.tiles] == [
            (0, 1024, 1280, 1024), (1024, 2048, 1280, 1024), (2048, 2500, 1280, 452),
        ]
        assert (shot.page_width, shot.page_height, shot.scale, shot.complete) == (1280, 2500, 1.0, True)
        mock_page.set_content.assert_awaited_once()

    async def test_javascript_is_off_and_the_size_still_reads(self):
        """Playwright evaluates through CDP, so measuring needs no page script."""
        mock_pw_cm, mock_browser, mock_page = _tile_mocks((1280, 900))
        await _tiles_capture(mock_pw_cm)
        assert mock_browser.new_context.call_args[1]["java_script_enabled"] is False
        mock_page.evaluate.assert_awaited_once()

    async def test_tiles_span_the_pages_real_width(self):
        """A table that overflows the viewport must show up, not be cut off."""
        mock_pw_cm, _, mock_page = _tile_mocks((1700, 1200))
        shot = await _tiles_capture(mock_pw_cm)
        assert {c.kwargs["clip"]["width"] for c in mock_page.screenshot.await_args_list} == {1700}
        assert shot.page_width == 1700
        assert all(t.width == 1700 for t in shot.tiles)

    async def test_viewport_and_budget_arguments_are_used(self):
        mock_pw_cm, mock_browser, _ = _tile_mocks((800, 5000))
        shot = await _tiles_capture(
            mock_pw_cm, viewport_width=800, viewport_height=600, max_tiles=4,
        )
        assert mock_browser.new_context.call_args[1]["viewport"] == {"width": 800, "height": 600}
        assert len(shot.tiles) == 4 and not shot.complete  # 5000 rows do not fit 4 x 600

    async def test_a_tall_page_is_shrunk_to_fit_when_allowed(self):
        mock_pw_cm, _, mock_page = _tile_mocks((1280, 13141))
        shot = await _tiles_capture(mock_pw_cm, max_tiles=8, min_scale=0.6)
        assert len(shot.tiles) == 8 and shot.complete
        assert 0.78 < shot.scale < 0.80
        # clips are page rows in CSS px; the images are what the judge reads, at scale
        assert mock_page.screenshot.await_args_list[0].kwargs["clip"]["height"] == 1643
        for tile in shot.tiles[:-1]:
            assert tile.width == round(1280 * shot.scale)
            assert tile.height == round((tile.bottom - tile.top) * shot.scale)
        # ~1.3 megapixels a tile, the same as a native viewport tile
        assert all(t.width * t.height <= TILE_AREA * 1.02 for t in shot.tiles)

    async def test_a_page_too_tall_for_the_floor_is_reported_incomplete(self):
        mock_pw_cm, _, _ = _tile_mocks((1280, 40000))
        shot = await _tiles_capture(mock_pw_cm, max_tiles=8, min_scale=0.6)
        assert not shot.complete
        assert shot.tiles[0].top == 0 and shot.tiles[-1].bottom == 40000

    async def test_a_slow_subresource_still_produces_tiles(self):
        mock_pw_cm, _, mock_page = _tile_mocks((1280, 900))
        mock_page.set_content = AsyncMock(side_effect=TimeoutError("Timeout 30000ms exceeded"))
        shot = await _tiles_capture(mock_pw_cm)
        assert len(shot.tiles) == 1

    async def test_other_render_errors_raise_with_the_cause_and_close_the_browser(self):
        mock_pw_cm, mock_browser, mock_page = _tile_mocks((1280, 900))
        mock_page.set_content = AsyncMock(side_effect=RuntimeError("target crashed"))
        import poindexter.services.preview_screenshot as mod
        with pytest.raises(mod.PreviewScreenshotError, match="target crashed"):
            await _tiles_capture(mock_pw_cm)
        mock_browser.close.assert_awaited_once()

    async def test_an_empty_tile_raises(self):
        mock_pw_cm, _, mock_page = _tile_mocks((1280, 900))
        mock_page.screenshot = AsyncMock(return_value=b"")
        import poindexter.services.preview_screenshot as mod
        with pytest.raises(mod.PreviewScreenshotError, match="empty screenshot"):
            await _tiles_capture(mock_pw_cm)

    async def test_something_that_is_not_a_png_raises(self):
        mock_pw_cm, _, mock_page = _tile_mocks((1280, 900))
        mock_page.screenshot = AsyncMock(return_value=b"definitely not an image, but 24+ bytes")
        import poindexter.services.preview_screenshot as mod
        with pytest.raises(mod.PreviewScreenshotError, match="not a PNG"):
            await _tiles_capture(mock_pw_cm)

    async def test_empty_html_raises(self):
        import poindexter.services.preview_screenshot as mod
        with pytest.raises(mod.PreviewScreenshotError, match="no HTML"):
            await mod.capture_html_tiles("  ")

    async def test_a_full_size_tile_is_not_resampled(self):
        """Native scale ships the browser's own pixels, byte for byte."""
        mock_pw_cm, _, mock_page = _tile_mocks((1280, 900))
        shot = await _tiles_capture(mock_pw_cm)
        raw = await mock_page.screenshot(type="png", full_page=True,
                                         clip={"x": 0, "y": 0, "width": 1280, "height": 900})
        assert shot.tiles[0].png == raw



class TestPageFacts:
    """The capture also MEASURES what the browser can measure exactly: images that
    failed to load, and how far the page overflows sideways. The judge cannot be
    trusted with either: shown a broken-image icon and asked pointedly per tile,
    it said "no" on 32 of 32 runs over 8 real drafts carrying a dead image."""

    @pytest.mark.asyncio
    async def test_the_facts_come_from_the_same_measurement_as_the_page_size(self):
        mock_pw_cm, _, mock_page = _tile_mocks(
            (1280, 2000), images=4, failed=[{"alt": "Hero", "row": 65}],
        )
        shot = await _tiles_capture(mock_pw_cm)
        mock_page.evaluate.assert_awaited_once()
        assert shot.facts == _facts_type()(
            images=4, failed_images=(_failed_type()(alt="Hero", row=65),), overflow_px=0,
        )

    @pytest.mark.asyncio
    async def test_a_clean_page_has_no_failures_and_no_overflow(self):
        mock_pw_cm, _, _ = _tile_mocks((1280, 2000), images=3)
        shot = await _tiles_capture(mock_pw_cm)
        assert shot.facts.images == 3
        assert shot.facts.failed_images == ()
        assert shot.facts.overflow_px == 0

    @pytest.mark.asyncio
    async def test_overflow_is_how_far_the_page_is_wider_than_the_viewport(self):
        mock_pw_cm, _, _ = _tile_mocks((1936, 2000))
        shot = await _tiles_capture(mock_pw_cm)
        assert shot.facts.overflow_px == 656
        assert shot.page_width == 1936

    @pytest.mark.asyncio
    async def test_a_page_the_viewport_holds_does_not_overflow(self):
        mock_pw_cm, _, _ = _tile_mocks((1280, 2000), viewport=1280)
        assert (await _tiles_capture(mock_pw_cm)).facts.overflow_px == 0

    @pytest.mark.asyncio
    async def test_javascript_stays_off_because_lazy_images_then_load_eagerly(self):
        """The pipeline's inline images carry ``loading="lazy"``. Chromium fetches those
        at once only while page JavaScript is off; with it on, an image far below the fold
        is never fetched (``complete`` False) and would be reported as failed. So the
        default is pinned, not just the value one call happens to pass."""
        import inspect

        import poindexter.services.preview_screenshot as mod

        assert inspect.signature(mod.capture_html_tiles).parameters["javascript_enabled"].default is False
        mock_pw_cm, mock_browser, _ = _tile_mocks((1280, 900))
        await _tiles_capture(mock_pw_cm)
        assert mock_browser.new_context.call_args[1]["java_script_enabled"] is False

    def test_the_measuring_script_is_what_defines_a_failed_image(self):
        """A failed image is one that is not both complete and non-empty: chromium blocks
        an HTML 404 served as an image (ERR_BLOCKED_BY_ORB), leaving naturalWidth 0."""
        import poindexter.services.preview_screenshot as mod

        assert "!(i.complete && i.naturalWidth > 0)" in mod._MEASURE_PAGE_JS
        assert "scrollWidth" in mod._MEASURE_PAGE_JS and "clientWidth" in mod._MEASURE_PAGE_JS

    def test_missing_keys_read_as_nothing_measured(self):
        import poindexter.services.preview_screenshot as mod

        facts = mod._facts_from({"width": 1280, "viewport": 1280})
        assert (facts.images, facts.failed_images, facts.overflow_px) == (0, (), 0)


def _facts_type():
    import poindexter.services.preview_screenshot as mod
    return mod.PageFacts


def _failed_type():
    import poindexter.services.preview_screenshot as mod
    return mod.FailedImage


class TestDescribePageFacts:
    """The lines the judge's prompt carries under ``{page_facts}``."""

    def test_a_clean_page(self):
        from poindexter.services.preview_screenshot import PageFacts, describe_page_facts

        assert describe_page_facts(PageFacts(4, (), 0)) == (
            "- Images: 4 in the page, all loaded.\n- Horizontal overflow: none."
        )

    def test_failed_images_are_named_by_their_alt_text(self):
        from poindexter.services.preview_screenshot import (
            FailedImage,
            PageFacts,
            describe_page_facts,
        )

        text = describe_page_facts(PageFacts(3, (FailedImage("Chart A", 400), FailedImage("Chart B", 900)), 0))
        assert '2 failed to load (alt text: "Chart A"; "Chart B")' in text
        assert "3 in the page" in text

    def test_overflow_says_how_far(self):
        from poindexter.services.preview_screenshot import PageFacts, describe_page_facts

        assert "the page is 656 px wider than the viewport" in describe_page_facts(PageFacts(1, (), 656))

    def test_nothing_measured_is_said_plainly(self):
        from poindexter.services.preview_screenshot import describe_page_facts

        assert describe_page_facts(None) == "- Nothing was measured for this screenshot."


class TestMeasuredIssues:
    """One issue per measured defect. Any of them is an objection."""

    def test_a_clean_page_and_an_unmeasured_one_have_none(self):
        from poindexter.services.preview_screenshot import PageFacts, measured_issues

        assert measured_issues(PageFacts(4, (), 0)) == []
        assert measured_issues(None) == []

    def test_a_failed_image_leads_with_what_matters(self):
        """The verdict text clips each issue to 60 characters."""
        from poindexter.services.preview_screenshot import FailedImage, PageFacts, measured_issues

        (issue,) = measured_issues(PageFacts(2, (FailedImage("The Mission Control dashboard", 1954),), 0))
        assert issue.startswith('Image failed to load: "The Mission Control dashboard"')
        assert "page row 1954" in issue
        assert 'Image failed to load: "The Mission Control dashboard' in issue[:60]

    def test_an_image_with_no_alt_text_is_still_reported(self):
        from poindexter.services.preview_screenshot import FailedImage, PageFacts, measured_issues

        (issue,) = measured_issues(PageFacts(2, (FailedImage("", 300),), 0))
        assert issue == "Image failed to load: it has no alt text (page row 300)"

    def test_overflow_is_reported_after_the_images(self):
        from poindexter.services.preview_screenshot import FailedImage, PageFacts, measured_issues

        issues = measured_issues(PageFacts(2, (FailedImage("A", 1), FailedImage("B", 2)), 328))
        assert len(issues) == 3
        assert issues[0].startswith('Image failed to load: "A"')
        assert issues[1].startswith('Image failed to load: "B"')
        assert "328 px past the right edge" in issues[2]


class TestShrinkPng:
    def test_shrinking_by_a_hair_does_not_resample(self):
        from poindexter.services.preview_screenshot import _shrink_png

        png = _png(1280, 1025)
        assert _shrink_png(png, 0.9998) is png

    def test_shrinking_resamples_to_the_target_size(self):
        from poindexter.services.preview_screenshot import _shrink_png, png_dimensions

        assert png_dimensions(_shrink_png(_png(1280, 1643), 0.79)) == (1011, 1298)


@pytest.mark.asyncio
class TestModuleExports:
    """Verify __all__ is correct."""

    async def test_all_exports(self):
        import poindexter.services.preview_screenshot as mod
        assert mod.__all__ == [
            "FailedImage",
            "PageFacts",
            "PageTile",
            "PreviewScreenshotError",
            "TilePlan",
            "TiledScreenshot",
            "capture_html_tiles",
            "capture_preview_screenshot",
            "describe_page_facts",
            "measured_issues",
            "plan_tiles",
            "png_dimensions",
        ]
