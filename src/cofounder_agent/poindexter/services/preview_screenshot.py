"""
Preview screenshot service

Renders a page to a PNG using Playwright's bundled chromium and returns the
raw bytes. Two entry points:

- :func:`capture_preview_screenshot` navigates to a URL (``http://`` or
  ``file://``) and returns ``None`` on any failure. The brand hero, the
  video thumbnail and the ``screenshot`` image provider use it.
- :func:`capture_html_screenshot` renders an HTML document handed to it and
  RAISES :class:`PreviewScreenshotError` naming the cause. The ``qa.vision``
  rendered-preview leg uses it to screenshot the in-flight draft (rendered by
  ``services.preview_page``), because a leg that cannot say why it has no
  image reads exactly like one that was switched off. The URL-based leg did
  just that for months (``docs/architecture/preview-links.md``).

Every call launches a fresh browser context and closes it cleanly: no
persistent state, no shared page pool. Startup costs a few hundred
milliseconds per call, which is negligible next to the vision inference that
follows.

Dependencies:
    playwright (in pyproject.toml)
    chromium (installed in the Dockerfile via `playwright install chromium`)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from poindexter.services.logger_config import get_logger
from poindexter.utils.exception_format import describe_exception

logger = get_logger(__name__)

_CHROMIUM_ARGS = [
    "--no-sandbox",  # required inside the worker container
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


class PreviewScreenshotError(RuntimeError):
    """A capture produced no image. ``str()`` names the cause and is never empty."""


async def _capture(
    navigate: Callable[[Any], Awaitable[None]],
    *,
    viewport_width: int,
    viewport_height: int,
    full_page: bool,
    wait_after_load_ms: int,
    javascript_enabled: bool,
) -> bytes:
    """Launch chromium, run ``navigate(page)``, screenshot. Raises on any failure."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise PreviewScreenshotError(
            "playwright is not installed in this process"
        ) from exc

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=list(_CHROMIUM_ARGS))
            try:
                context = await browser.new_context(
                    viewport={"width": viewport_width, "height": viewport_height},
                    device_scale_factor=1,
                    java_script_enabled=javascript_enabled,
                )
                page = await context.new_page()
                await navigate(page)
                if wait_after_load_ms > 0:
                    await page.wait_for_timeout(wait_after_load_ms)
                png_bytes = await page.screenshot(full_page=full_page, type="png")
            finally:
                with suppress(Exception):  # silent-ok: best-effort browser close in finally
                    await browser.close()
    except PreviewScreenshotError:
        raise
    except Exception as exc:
        raise PreviewScreenshotError(describe_exception(exc)) from exc
    if not png_bytes:
        raise PreviewScreenshotError("chromium returned an empty screenshot")
    return png_bytes


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
    without the caller grinding to a halt.

    Args:
        preview_url: URL to navigate to (``http://`` or ``file://``).
        viewport_width: Desktop-ish width. Defaults to 1280.
        viewport_height: Viewport height. Full-page capture still
            includes the scrolled content beyond this value.
        full_page: Capture the full scrollable page, not just the
            visible viewport. Default True.
        timeout_ms: Navigation + load timeout.
        wait_after_load_ms: Extra settle time after networkidle so
            late-rendering JS (giscus, images) lands in the capture.
    """

    async def _goto(page: Any) -> None:
        await page.goto(preview_url, wait_until="networkidle", timeout=timeout_ms)

    try:
        return await _capture(
            _goto,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            full_page=full_page,
            wait_after_load_ms=wait_after_load_ms,
            javascript_enabled=True,
        )
    except PreviewScreenshotError as exc:
        if isinstance(exc.__cause__, ImportError):
            logger.debug(
                "[preview_screenshot] playwright not installed — skipping capture"
            )
        else:
            logger.warning(
                "[preview_screenshot] capture failed for %s: %s",
                preview_url, str(exc)[:200],
            )
        return None


async def capture_html_screenshot(
    html: str,
    *,
    viewport_width: int = 1280,
    viewport_height: int = 1024,
    full_page: bool = True,
    timeout_ms: int = 30000,
    wait_after_load_ms: int = 500,
    javascript_enabled: bool = False,
) -> bytes:
    """Render an HTML document in headless chromium and return the PNG bytes.

    Raises :class:`PreviewScreenshotError` with the cause when no image comes
    back, rather than returning ``None``: the caller reports it.

    JavaScript is off by default. The preview page needs none, and the HTTP
    route that serves it forbids scripts with a CSP header
    (``services.preview_page.PREVIEW_PAGE_CSP``). A document rendered from a
    string has no response to carry that header, and its body is LLM output
    that web research can reach, so the browser context enforces the same rule.

    Waiting for images is best-effort. The DOM is in place as soon as
    ``set_content`` starts, so a sub-resource that outlasts ``timeout_ms`` (a
    slow or dead image host) still produces a screenshot. A broken image is one
    of the things the vision judge is there to see, not a reason to skip it.
    """
    if not (html or "").strip():
        raise PreviewScreenshotError("no HTML to render")

    async def _set_content(page: Any) -> None:
        try:
            await page.set_content(html, wait_until="networkidle", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001 - see docstring: a slow sub-resource is not fatal
            if "timeout" not in type(exc).__name__.lower():
                raise
            logger.warning(
                "[preview_screenshot] sub-resources still loading after %d ms (%s); "
                "screenshotting the page as it stands",
                timeout_ms, describe_exception(exc),
            )

    return await _capture(
        _set_content,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        full_page=full_page,
        wait_after_load_ms=wait_after_load_ms,
        javascript_enabled=javascript_enabled,
    )


__all__ = [
    "PreviewScreenshotError",
    "capture_html_screenshot",
    "capture_preview_screenshot",
]
