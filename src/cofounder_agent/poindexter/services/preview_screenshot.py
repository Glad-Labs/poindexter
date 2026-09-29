"""
Preview screenshot service

Renders a page with Playwright's bundled chromium. Two entry points:

- :func:`capture_preview_screenshot` navigates to a URL (``http://`` or
  ``file://``) and returns one full-page PNG, or ``None`` on any failure. The
  brand hero, the video thumbnail and the ``screenshot`` image provider use it.
- :func:`capture_html_tiles` renders an HTML document handed to it and returns
  it as a few tiles instead of one image (see below), RAISING
  :class:`PreviewScreenshotError` naming the cause. The ``qa.vision``
  rendered-preview leg uses it to screenshot the in-flight draft (rendered by
  ``services.preview_page``), because a leg that cannot say why it has no
  image reads exactly like one that was switched off. The URL-based leg did
  just that for months (``docs/architecture/preview-links.md``).

Why tiles: the vision judge reads at most ~4.2 megapixels per image
(:mod:`poindexter.services.vision_image_budget`). A 1280x13141 draft screenshot
is 16.8 megapixels, so it reached the model at half scale: 16 px body text as 8
px, and the ``IN_PROGRESS | Q: 82`` banner read back as ``TL_PROJECTS | 0:30``.
On that draft the judge then reported a "placeholder" hero and missing images,
none of which were true, in 12 of 20 runs. A viewport-sized tile is 1.3
megapixels, so it arrives at native scale.

The same render also MEASURES what the browser can measure exactly (:class:`PageFacts`):
which images failed to load and whether the page overflows the viewport sideways. That
is not a nicety. Asked pointedly, tile by tile, whether an image was broken, the judge
said no on every one of 32 runs over 8 real drafts that carry a dead image (a 16 px
icon and two lines of alt text between paragraphs look like a caption to it), and told
to hunt for problems it flagged every clean page instead. The browser is never unsure.

Every call launches a fresh browser context and closes it cleanly: no
persistent state, no shared page pool. Startup costs a few hundred
milliseconds per call, which is negligible next to the vision inference that
follows.

Dependencies:
    playwright (in pyproject.toml)
    chromium (installed in the Dockerfile via `playwright install chromium`)
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
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


async def _render(
    navigate: Callable[[Any], Awaitable[None]],
    work: Callable[[Any], Awaitable[Any]],
    *,
    viewport_width: int,
    viewport_height: int,
    wait_after_load_ms: int,
    javascript_enabled: bool,
) -> Any:
    """Launch chromium, run ``navigate(page)``, settle, then return ``work(page)``.

    Raises :class:`PreviewScreenshotError` naming the cause on any failure, and
    always closes the browser.
    """
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
                return await work(page)
            finally:
                with suppress(Exception):  # silent-ok: best-effort browser close in finally
                    await browser.close()
    except PreviewScreenshotError:
        raise
    except Exception as exc:
        raise PreviewScreenshotError(describe_exception(exc)) from exc


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

    async def _shoot(page: Any) -> bytes:
        return await page.screenshot(full_page=full_page, type="png")

    png_bytes = await _render(
        navigate, _shoot,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        wait_after_load_ms=wait_after_load_ms,
        javascript_enabled=javascript_enabled,
    )
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


def _set_content_navigator(html: str, timeout_ms: int) -> Callable[[Any], Awaitable[None]]:
    """The ``navigate`` step for a document handed to us as a string.

    JavaScript is off in the browser context (``capture_html_tiles`` passes
    ``javascript_enabled=False``): the preview page needs none, and the HTTP
    route that serves it forbids scripts with a CSP header
    (``services.preview_page.PREVIEW_PAGE_CSP``). A document rendered from a
    string has no response to carry that header, and its body is LLM output
    that web research can reach, so the browser context enforces the same rule.

    Waiting for images is best-effort. The DOM is in place as soon as
    ``set_content`` starts, so a sub-resource that outlasts ``timeout_ms`` (a
    slow or dead image host) still produces a screenshot. A broken image is one
    of the things the vision judge is there to see, not a reason to skip it.
    """

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

    return _set_content


# ---------------------------------------------------------------------------
# Tiled capture: the page as a few images the judge can read
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TilePlan:
    """Which rows of a page to send, and at what scale.

    ``spans`` are ``(top, bottom)`` page rows in CSS px. ``scale`` is image px
    per CSS px (1.0 unless the page was shrunk so it fits). ``complete`` is
    False when the spans leave gaps between them.
    """

    spans: tuple[tuple[int, int], ...]
    scale: float
    complete: bool


def _contiguous(page_height: int, rows: int) -> tuple[tuple[int, int], ...]:
    return tuple(
        (top, min(top + rows, page_height)) for top in range(0, page_height, rows)
    )


def plan_tiles(
    page_width: int,
    page_height: int,
    *,
    tile_area: int,
    max_tiles: int,
    min_scale: float = 1.0,
) -> TilePlan:
    """Cut a ``page_width`` x ``page_height`` page into at most ``max_tiles`` tiles.

    Every tile is held to about ``tile_area`` image pixels (the viewport's), so
    each one costs the judge about the same number of context tokens and the
    whole request costs about ``max_tiles`` of them, whatever the page looks like:

    1. **Fits at native scale.** Contiguous tiles of ``tile_area / page_width``
       rows, scale 1.0: the judge reads the page at the size a browser draws it.
    2. **Too tall, but shrinkable.** ``max_tiles`` equal tiles cover the whole page
       at ``scale = sqrt(tile_area * max_tiles / (page_width * page_height))``,
       provided that stays at or above ``min_scale``.
    3. **Taller than that.** Scale stops at ``min_scale`` and ``max_tiles`` tiles
       are spread evenly down the page, always including the first and the last,
       with gaps between them (``complete`` is False).

    ``min_scale=1.0`` (never shrink) makes step 2 fall through to step 3.
    """
    page_width = max(1, int(page_width))
    page_height = max(1, int(page_height))
    tile_area = max(1, int(tile_area))
    max_tiles = max(1, int(max_tiles))
    min_scale = min(1.0, max(0.05, float(min_scale)))

    native_rows = max(1, tile_area // page_width)
    if math.ceil(page_height / native_rows) <= max_tiles:
        return TilePlan(_contiguous(page_height, native_rows), 1.0, True)

    scale = math.sqrt(tile_area * max_tiles / (page_width * page_height))
    if scale >= min_scale:
        return TilePlan(_contiguous(page_height, math.ceil(page_height / max_tiles)), scale, True)

    rows = max(1, int(tile_area / (page_width * min_scale * min_scale)))
    if max_tiles == 1:
        tops = [0]
    else:
        span = max(0, page_height - rows)
        tops = [round(i * span / (max_tiles - 1)) for i in range(max_tiles)]
    return TilePlan(
        tuple((top, min(top + rows, page_height)) for top in tops), min_scale, False,
    )


@dataclass(frozen=True)
class FailedImage:
    """An ``<img>`` the browser could not load: ``alt`` is what it shows instead, at page row ``row``."""

    alt: str
    row: int


@dataclass(frozen=True)
class PageFacts:
    """What the browser measured about the rendered page. Exact, so nobody has to judge it.

    ``failed_images`` are the ``<img>`` elements that are not both ``complete`` and
    non-empty (a 404 page served as an image counts: chromium blocks it). ``overflow_px``
    is how far the page is wider than the viewport, i.e. how much content spills past the
    right edge (0 when nothing does).

    The pipeline's inline images carry ``loading="lazy"``. With page JavaScript off
    (``capture_html_tiles`` never turns it on) chromium loads those eagerly, so one far
    below the fold is not reported as failed for never having been scrolled into view:
    on the evidence draft 3 of its 4 images are lazy, the lowest at row 8,563, and all
    four measured as loaded. Measured against a local server, a lazy image 9,000 px down
    was fetched with JavaScript off and never fetched with it on (``complete`` False, so
    it would read as failed): turning JavaScript on here needs a scroll pass first.
    """

    images: int
    failed_images: tuple[FailedImage, ...]
    overflow_px: int


def describe_page_facts(facts: PageFacts | None) -> str:
    """The measured facts as the lines the judge's prompt carries (``{page_facts}``)."""
    if facts is None:
        return "- Nothing was measured for this screenshot."
    if not facts.failed_images:
        images = f"- Images: {facts.images} in the page, all loaded."
    else:
        alts = "; ".join(f'"{f.alt}"' for f in facts.failed_images)
        images = (
            f"- Images: {facts.images} in the page, {len(facts.failed_images)} failed to "
            f"load (alt text: {alts})."
        )
    if facts.overflow_px > 0:
        overflow = f"- Horizontal overflow: the page is {facts.overflow_px} px wider than the viewport."
    else:
        overflow = "- Horizontal overflow: none."
    return f"{images}\n{overflow}"


def measured_issues(facts: PageFacts | None) -> list[str]:
    """One issue per measured defect, worded the way the judge's own issues are.

    A failed image and sideways overflow are serious visual defects by the rubric, so
    the caller treats any of them as an objection whatever the judge says.
    """
    if facts is None:
        return []
    # The verdict text clips each issue to 60 characters: lead with what matters.
    issues = [
        (f'Image failed to load: "{f.alt}" shows in place of the picture (page row {f.row})'
         if f.alt else f"Image failed to load: it has no alt text (page row {f.row})")
        for f in facts.failed_images
    ]
    if facts.overflow_px > 0:
        issues.append(
            f"Content spills {facts.overflow_px} px past the right edge of the page "
            "(horizontal overflow)"
        )
    return issues


@dataclass(frozen=True)
class PageTile:
    """One image the judge is sent: ``png`` covers page rows ``top``..``bottom``."""

    png: bytes
    top: int
    bottom: int
    width: int
    height: int


@dataclass(frozen=True)
class TiledScreenshot:
    """A page as tiles, plus what the tiles leave out."""

    tiles: tuple[PageTile, ...]
    page_width: int
    page_height: int
    scale: float
    complete: bool
    facts: PageFacts | None = None


def png_dimensions(png: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG's IHDR chunk, or None if ``png`` is not one.

    Reads the header only, so it needs no image library and never decodes.
    """
    # signature, then the IHDR chunk (4-byte length, b"IHDR", width, height)
    if png[:8] != b"\x89PNG\r\n\x1a\n" or len(png) < 24 or png[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", png[16:24])
    return int(width), int(height)


def _png_size(png: bytes) -> tuple[int, int]:
    size = png_dimensions(png)
    if size is None:
        raise PreviewScreenshotError("chromium returned something that is not a PNG")
    return size


def _shrink_png(png: bytes, scale: float) -> bytes:
    """``png`` resampled to ``scale`` of its size (Lanczos), as a PNG."""
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        width, height = image.size
        target = (max(1, round(width * scale)), max(1, round(height * scale)))
        if target == (width, height):
            return png  # a scale of 0.9998 changes nothing; skip the resample
        resized = image.convert("RGB").resize(target, Image.Resampling.LANCZOS)
    out = BytesIO()
    resized.save(out, format="PNG")
    return out.getvalue()


# One CDP evaluation reads the page size and the facts together. Playwright evaluates
# through CDP, so this works with page JavaScript off (the capture's default).
_MEASURE_PAGE_JS = """() => {
  const de = document.documentElement;
  const imgs = [...document.images];
  return {
    width: de.scrollWidth,
    height: de.scrollHeight,
    viewport: de.clientWidth,
    images: imgs.length,
    failed: imgs.filter(i => !(i.complete && i.naturalWidth > 0)).map(i => ({
      alt: (i.getAttribute('alt') || '').trim().slice(0, 120),
      row: Math.round(i.getBoundingClientRect().top + window.scrollY),
    })),
  };
}"""


def _facts_from(measured: dict[str, Any]) -> PageFacts:
    return PageFacts(
        images=int(measured.get("images") or 0),
        failed_images=tuple(
            FailedImage(alt=str(f.get("alt") or ""), row=int(f.get("row") or 0))
            for f in (measured.get("failed") or [])
        ),
        overflow_px=max(0, int(measured.get("width") or 0) - int(measured.get("viewport") or 0)),
    )


async def capture_html_tiles(
    html: str,
    *,
    viewport_width: int = 1280,
    viewport_height: int = 1024,
    max_tiles: int = 8,
    min_scale: float = 1.0,
    timeout_ms: int = 30000,
    wait_after_load_ms: int = 500,
    javascript_enabled: bool = False,
) -> TiledScreenshot:
    """Render an HTML document and return it as tiles the judge can read.

    JavaScript is off by default (see :func:`_set_content_navigator`), a slow image
    is not fatal, and failures raise :class:`PreviewScreenshotError`.
    The page is measured, cut by :func:`plan_tiles` and captured tile by tile
    with clipped screenshots, so a 40,000 px page needs no single 40,000 px
    image. Tiles span the page's real width, so a table that overflows the
    viewport shows up wider than 1280 px instead of being cut off. The result also
    carries :class:`PageFacts`, measured in the same evaluation.
    """
    if not (html or "").strip():
        raise PreviewScreenshotError("no HTML to render")

    async def _tile_page(page: Any) -> TiledScreenshot:
        measured = await page.evaluate(_MEASURE_PAGE_JS)
        facts = _facts_from(measured)
        page_width = max(int(measured["width"]), viewport_width)
        page_height = max(int(measured["height"]), 1)
        plan = plan_tiles(
            page_width, page_height,
            tile_area=viewport_width * viewport_height,
            max_tiles=max_tiles, min_scale=min_scale,
        )
        tiles: list[PageTile] = []
        for top, bottom in plan.spans:
            png = await page.screenshot(
                type="png", full_page=True,
                clip={"x": 0, "y": top, "width": page_width, "height": bottom - top},
            )
            if not png:
                raise PreviewScreenshotError("chromium returned an empty screenshot")
            if plan.scale < 1.0:
                # Resampling a ~1.3 MP tile is CPU work: keep it off the event loop
                # (Pillow releases the GIL while it resizes).
                png = await asyncio.to_thread(_shrink_png, png, plan.scale)
            width, height = _png_size(png)
            tiles.append(PageTile(png=png, top=top, bottom=bottom, width=width, height=height))
        return TiledScreenshot(
            tiles=tuple(tiles), page_width=page_width, page_height=page_height,
            scale=plan.scale, complete=plan.complete, facts=facts,
        )

    return await _render(
        _set_content_navigator(html, timeout_ms), _tile_page,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        wait_after_load_ms=wait_after_load_ms,
        javascript_enabled=javascript_enabled,
    )


__all__ = [
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
