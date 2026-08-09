"""Client-side vocabulary for the image-gen server's OCR text-leakage gate.

``scripts/image-gen-server.py`` OCR-scans every ``/generate`` render and, when
leaked text stays over ``image_ocr_gate_max_chars`` after every retry, returns
**HTTP 422** instead of the image (``image_ocr_gate_enforce``, default on).

This module exists because that rejection has to read identically at all five
render call sites — the inline single + batch paths in
``modules/content/atoms/_image_helpers.py``, the featured path in
``modules/content/stages/source_featured_image.py``, ``flux_schnell``, and the
video shot-list renderer. It lives in ``services/`` (substrate) rather than
under ``modules/content/`` precisely so the two ``services/`` callers can use
it without a module importing backwards across the boundary.

Why the distinction matters to a caller: an OCR rejection is a **verdict**,
not a window. The ordinary render failures this code retries — image-gen
restarting for a VRAM reclaim, a GPU lock timeout — clear in seconds, so a
retry recovers them. A rejection does not clear: the server already re-rolled
the seed ``image_ocr_gate_max_attempts`` times before giving up, so a client
retry just buys another full set of re-rolls for the same verdict. Callers
must treat :data:`OCR_GATE_REJECTED_STATUS` as terminal and take their
no-image-gen-image path.

Historical note (poindexter#1004): before enforcement the server returned the
leaking image and set ``ocr_gate_passed=false`` on the response — a field no
caller read. 67 of 693 renders over four weeks failed the gate and shipped
anyway, one of them a hero with "Poindexter Philosophy" burned across the top.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: HTTP status the image-gen server returns when a render is blocked by the
#: gate. 422 rather than 5xx: the render itself succeeded, its *content* is
#: unacceptable, and the request is not worth repeating unchanged.
OCR_GATE_REJECTED_STATUS = 422

#: ``detail.error`` marker on the rejection body. Checked (rather than
#: assuming every 422 is a gate rejection) so an unrelated future 422 from
#: this endpoint can't be silently read as a text-leak verdict.
OCR_GATE_REJECTED_ERROR = "ocr_gate_rejected"


def is_ocr_gate_rejection(status_code: int, body: Any) -> bool:
    """Whether this ``/generate`` response is an OCR-gate content rejection.

    ``body`` is the parsed JSON response (or anything at all — a non-dict,
    a parse failure sentinel, ``None``); only the documented shape matches.
    """
    if status_code != OCR_GATE_REJECTED_STATUS:
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    return isinstance(detail, dict) and detail.get("error") == OCR_GATE_REJECTED_ERROR


def describe_ocr_gate_rejection(body: Any) -> str:
    """One-line, log-safe summary of a rejection body.

    Falls back to a generic phrase rather than raising or returning ``''``:
    this only ever feeds an operator-facing log line, and a malformed body
    still needs to say *something* about why an image went missing.
    """
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return "OCR text-leakage gate rejected the render (no detail returned)"
    status = detail.get("ocr_gate_status", "?")
    chars = detail.get("ocr_text_chars")
    threshold = detail.get("threshold", "?")
    attempts = detail.get("ocr_gate_attempts", "?")
    return (
        f"OCR text-leakage gate rejected the render "
        f"(status={status}, chars={'unavailable' if chars is None else chars}, "
        f"threshold={threshold}, server attempts={attempts})"
    )


def safe_json(resp: Any) -> Any:
    """``resp.json()`` that returns ``None`` instead of raising.

    A rejection body is only ever used to explain a failure that has already
    happened, so a malformed/empty body must degrade to "no detail" rather
    than replace the real outcome with a JSON parse error — the caller still
    has to take its no-image path either way.

    Broad on purpose (the response object is whatever the HTTP client hands
    back), but not silent: an image-gen response that won't parse means the
    caller is about to report a render failure it can't explain, and that is
    worth an operator seeing.
    """
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — degraded diagnostics, logged below
        logger.warning(
            "[OCR-GATE] could not parse image-gen response body (%s: %s) — "
            "the render failure below will be reported without detail",
            type(exc).__name__, exc,
        )
        return None


__all__ = [
    "OCR_GATE_REJECTED_ERROR",
    "OCR_GATE_REJECTED_STATUS",
    "describe_ocr_gate_rejection",
    "is_ocr_gate_rejection",
    "safe_json",
]
