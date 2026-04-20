"""
Preview screenshot service

Renders a post's preview URL (http://localhost:8002/preview/{hash}) to a
PNG using Playwright's bundled chromium and returns the raw bytes. Used
by the MultiModelQA vision reviewer to give the final "yup looks good"
sanity check — the programmatic + text-level QA can't catch layout
issues that only show up in the rendered page (missing CSS, overflowing
tables, broken images, mangled quotes, etc.).

Every call launches a fresh browser context and closes it cleanly —
no persistent state, no shared page pool. The cost is a few hundred
milliseconds of startup per call, which is negligible compared to the
vision inference that follows.

Dependencies:
    playwright (in pyproject.toml)
    chromium (installed in the Dockerfile via `playwright install chromium`)

Behavior on missing deps:
    Every public function returns None instead of raising, so the QA
    pipeline can treat the check as "skipped" exactly like when Ollama
    is unreachable.
"""

from __future__ import annotations

from contextlib import suppress

from services.logger_config import get_logger

logger = get_logger(__name__)


async def capture_preview_screenshot(
    preview_url: str,
    *,
    viewport_width: int = 1280,
    viewport_height: int = 1024,
    full_page: bool = True,
    timeout_ms: int = 30000,
    wait_after_load_ms: int = 500,
) -> bytes | None:
    """Render ``preview_url`` in headless chromium and return the PNG bytes.

    Returns ``None`` if playwright is not installed, if the browser
    fails to launch, if the page fails to load, or if any step along
    the way throws. Logs the failure so operators can see what happened
    without the QA pipeline grinding to a halt.

    Args:
        preview_url: URL to navigate to. Usually
            ``http://localhost:8002/preview/{hash}``.
        viewport_width: Desktop-ish width. Defaults to 1280.
        viewport_height: Viewport height. Full-page capture still
            includes the scrolled content beyond this value.
        full_page: Capture the full scrollable page, not just the
            visible viewport. Default True.
        timeout_ms: Navigation + load timeout.
        wait_after_load_ms: Extra settle time after networkidle so
            late-rendering JS (giscus, images) lands in the capture.
    """

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.debug(
            "[preview_screenshot] playwright not installed — skipping capture"
        )
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",  # required inside the worker container
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": viewport_width, "height": viewport_height},
                    device_scale_factor=1,
                )
                page = await context.new_page()
                await page.goto(
                    preview_url,
                    wait_until="networkidle",
                    timeout=timeout_ms,
                )
                if wait_after_load_ms > 0:
                    await page.wait_for_timeout(wait_after_load_ms)
                png_bytes = await page.screenshot(
                    full_page=full_page,
                    type="png",
                )
                return png_bytes
            finally:
                with suppress(Exception):
                    await browser.close()
    except Exception as e:
        logger.warning(
            "[preview_screenshot] capture failed for %s: %s",
            preview_url, str(e)[:200],
        )
        return None


__all__ = ["capture_preview_screenshot"]
