"""ScreenshotProvider — allow-listed operator-surface capture (poindexter#1002).

The security-relevant property under test: a target key from an LLM-authored
marker can only ever resolve to a URL the operator put in app_settings. The
worker sits on the Docker network with reachable admin surfaces, so a provider
that accepted raw URLs would be an SSRF seam.
"""
from __future__ import annotations

import json

import pytest

from services.image_providers.screenshot import (
    ScreenshotProvider,
    ScreenshotTargetError,
    parse_targets,
    png_size,
    resolve_target,
)

# 1x1 PNG — real IHDR, enough for png_size to parse.
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _png_with_size(width: int, height: int) -> bytes:
    """Splice a width/height into the 1x1 PNG's IHDR (only the header matters)."""
    import struct
    head = bytearray(_PNG_1X1)
    head[16:24] = struct.pack(">II", width, height)
    return bytes(head)


# --- allowlist parsing ------------------------------------------------------


def test_parse_targets_accepts_object_form():
    raw = json.dumps({"qa-rails": {"url": "http://g/d/qa", "width": 1600}})
    assert parse_targets(raw) == {"qa-rails": {"url": "http://g/d/qa", "width": 1600}}


def test_parse_targets_accepts_bare_url_shorthand():
    assert parse_targets('{"console": "http://localhost:8002/console/"}') == {
        "console": {"url": "http://localhost:8002/console/"},
    }


def test_parse_targets_empty_is_empty_map():
    assert parse_targets("") == {}
    assert parse_targets(None) == {}


def test_parse_targets_rejects_target_without_url():
    # A target that parses but can't capture would surface much later as a
    # confusing "returned nothing"; fail at parse time instead.
    with pytest.raises(ValueError, match="missing 'url'"):
        parse_targets('{"broken": {"width": 100}}')


def test_parse_targets_rejects_non_object():
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_targets('["qa-rails"]')


def test_resolve_target_unknown_key_names_the_valid_ones():
    targets = parse_targets('{"qa-rails": "http://g/d/qa", "console": "http://c/"}')
    with pytest.raises(ScreenshotTargetError) as exc:
        resolve_target("mission-control", targets)
    msg = str(exc.value)
    assert "mission-control" in msg
    assert "console, qa-rails" in msg  # sorted, so the operator can copy one


# --- png_size ---------------------------------------------------------------


def test_png_size_reads_ihdr():
    assert png_size(_png_with_size(1600, 4320)) == (1600, 4320)


def test_png_size_returns_zeros_for_non_png():
    assert png_size(b"not a png at all") == (0, 0)


# --- fetch ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_unknown_target_returns_empty_without_capturing(monkeypatch):
    """An invented target must never reach the browser."""
    called: list[str] = []

    async def _capture(url, **kwargs):
        called.append(url)
        return _PNG_1X1

    monkeypatch.setattr(
        "services.preview_screenshot.capture_preview_screenshot", _capture,
    )
    out = await ScreenshotProvider().fetch(
        "http://169.254.169.254/latest/meta-data/",
        {"targets": '{"qa-rails": "http://g/d/qa"}'},
    )
    assert out == []
    assert called == [], "provider must not fetch a URL that isn't allow-listed"


@pytest.mark.asyncio
async def test_fetch_captures_allowlisted_target(monkeypatch):
    seen: dict[str, object] = {}

    async def _capture(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return _png_with_size(1600, 1150)

    monkeypatch.setattr(
        "services.preview_screenshot.capture_preview_screenshot", _capture,
    )
    targets = json.dumps({
        "qa-rails": {
            "url": "http://grafana:3000/d/qa-rails?kiosk",
            "width": 1600, "height": 1150, "alt": "The QA Rails board",
        },
    })
    out = await ScreenshotProvider().fetch(
        "qa-rails", {"targets": targets, "upload_to": "none"},
    )

    assert len(out) == 1
    result = out[0]
    assert seen["url"] == "http://grafana:3000/d/qa-rails?kiosk"
    assert seen["viewport_width"] == 1600
    assert result.source == "screenshot"
    assert result.alt_text == "The QA Rails board"
    assert result.url.startswith("file://")  # upload_to=none
    assert result.metadata["screenshot_target"] == "qa-rails"
    # Dimensions come from the PNG, not the request — a full_page capture is
    # taller than its viewport and a wrong ratio means layout shift.
    assert (result.width, result.height) == (1600, 1150)


@pytest.mark.asyncio
async def test_fetch_reads_real_dimensions_for_full_page(monkeypatch):
    async def _capture(url, **kwargs):
        return _png_with_size(1600, 4320)  # full page, far taller than viewport

    monkeypatch.setattr(
        "services.preview_screenshot.capture_preview_screenshot", _capture,
    )
    targets = json.dumps({
        "console": {"url": "http://c/", "width": 1600, "height": 1000,
                    "full_page": True},
    })
    out = await ScreenshotProvider().fetch(
        "console", {"targets": targets, "upload_to": "none"},
    )
    assert (out[0].width, out[0].height) == (1600, 4320)


@pytest.mark.asyncio
async def test_fetch_returns_empty_when_capture_fails(monkeypatch):
    """capture_preview_screenshot returns None (never raises) on failure."""
    async def _capture(url, **kwargs):
        return None

    monkeypatch.setattr(
        "services.preview_screenshot.capture_preview_screenshot", _capture,
    )
    out = await ScreenshotProvider().fetch(
        "qa-rails", {"targets": '{"qa-rails": "http://g/d/qa"}'},
    )
    assert out == []


@pytest.mark.asyncio
async def test_fetch_malformed_allowlist_is_inert_not_fatal(monkeypatch):
    called: list[str] = []

    async def _capture(url, **kwargs):
        called.append(url)
        return _PNG_1X1

    monkeypatch.setattr(
        "services.preview_screenshot.capture_preview_screenshot", _capture,
    )
    out = await ScreenshotProvider().fetch("qa-rails", {"targets": "{not json"})
    assert out == []
    assert called == []


@pytest.mark.asyncio
async def test_fetch_empty_target_key_is_a_noop():
    assert await ScreenshotProvider().fetch("", {"targets": "{}"}) == []
