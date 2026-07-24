"""Tolerant JSON-object extraction from LLM output.

One canonical implementation of the "find the ``{...}`` the model was asked
for" ladder: direct parse → fenced-block parse → first-brace-block parse.
Anything a reasoning model emits *outside* the object (deliberation, prose,
markdown fences) is discarded by construction — the leak-proof-title lesson
(#1280/#1821).

``services.title_generation`` and the SEO/QA atoms delegate here; the video
shot-list parsers keep their own array-shaped variants (different grammar).
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from ``text``, tolerating markdown code fences
    and leading/trailing prose the model may emit around the object.

    Returns the parsed ``dict`` or ``None`` when no JSON object can be
    recovered — callers treat ``None`` as "no usable answer" and take their
    own degradation path (fallback derivation, H1/topic fallback, or a
    ``qa_rail_degraded`` finding).
    """
    if not text:
        return None
    fence_match = _FENCE_RE.search(text)
    candidate = fence_match.group(1) if fence_match else text
    for chunk in (candidate, text):
        chunk = chunk.strip()
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            # silent-ok: a failed parse IS the control flow — this chunk was
            # not whole-string JSON, so fall through to the brace-substring
            # rung. Exhausting every rung returns None (caller degrades).
            pass
        brace_match = _BRACE_RE.search(chunk)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                # silent-ok: last rung of the ladder — the brace substring
                # was not valid JSON either; move to the next chunk or
                # return None if exhausted.
                pass
    return None


__all__ = ["extract_json_object"]
