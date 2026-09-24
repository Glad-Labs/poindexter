"""Backcompat shim — the OCR-gate vocabulary moved to ``image_text_scan``.

This module used to hold the whole client side of the image-gen server's
text-leakage gate: three helpers for reading its HTTP 422. Despite the name it
never contained a scanner — the scan lived inside ``scripts/image-gen-server.py``,
welded to one renderer, which is why the featured fan-out's ComfyUI candidates
were never held to the no-text rule at all.

The vocabulary now lives in :mod:`poindexter.services.image_text_scan` next to
the backend-agnostic scanner. The contracts carried over verbatim; read that
module's docstring for them — in particular, an OCR rejection is a **verdict,
not a window**: callers treat :data:`OCR_GATE_REJECTED_STATUS` as terminal and
take their no-image path, because the server already re-rolled the seed
``image_ocr_gate_max_attempts`` times.

New code imports from ``image_text_scan``. These names stay re-exported so an
existing import (or an out-of-tree plugin) keeps working.
"""
from __future__ import annotations

from poindexter.services.image_text_scan import (
    OCR_GATE_REJECTED_ERROR,
    OCR_GATE_REJECTED_STATUS,
    describe_ocr_gate_rejection,
    is_ocr_gate_rejection,
    safe_json,
)

__all__ = [
    "OCR_GATE_REJECTED_ERROR",
    "OCR_GATE_REJECTED_STATUS",
    "describe_ocr_gate_rejection",
    "is_ocr_gate_rejection",
    "safe_json",
]
