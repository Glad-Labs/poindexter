"""Tolerant JSON-object extraction from LLM output.

One canonical implementation of the "find the ``{...}`` the model was asked
for" ladder: direct parse → fenced-block parse → first-brace-block parse →
(opt-in) truncated-object salvage. Anything a reasoning model emits *outside*
the object (deliberation, prose, markdown fences) is discarded by
construction — the leak-proof-title lesson (#1280/#1821).

``services.title_generation`` and the SEO/QA atoms delegate here; the video
shot-list parsers keep their own array-shaped variants (different grammar).

**The salvage rung (Glad-Labs/poindexter#926) is opt-in and default OFF.**
A local model asked for a long structured object sometimes emits valid JSON
and then *derails mid-object* into prose — Langfuse traces of
``topic_ranking``'s ranking call show 10 of 19 real calls over 14 days ending
that way, e.g. a clean scored object followed by "…Let me re-evaluate the
candidates…" and then newline padding (one runaway reached 18,784 chars). The
object never closes, so every rung above fails and the caller loses *all* the
entries the model had already scored correctly.

Salvage recovers the complete top-level entries that precede the derail. It is
opt-in rather than a free upgrade because a partial object is not universally
safe: a QA rail handed a verdict missing its score field could read the gap as
a pass, which is exactly the fail-open shape ``reference_qa_rail_fail_open_pattern``
forbids. Callers whose entries are independent (a map of id → score) opt in;
callers parsing a single fixed-shape record should not.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
_BRACE_RE = re.compile(r"\{[\s\S]*\}")


def salvage_truncated_object(text: str) -> dict[str, Any] | None:
    """Recover the complete top-level entries of an unterminated JSON object.

    Scans from the first ``{``, tracking string state (with escapes) and brace
    depth, and remembers every comma sitting at depth 1 — each is a safe cut
    point *between* whole top-level entries. If the object never closes, the
    text is cut at the last such comma and closed with ``}``.

    Returns ``None`` when nothing can be recovered: no opening brace, a derail
    inside the very first entry (no depth-1 comma yet), or a repaired string
    that still does not parse. Never returns a partially-written entry — the
    cut is always at an entry boundary, so every key in the result carries the
    value the model actually emitted for it.
    """
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    last_safe_comma = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # The object closed on its own — not a truncation. Whatever
                # follows is trailing prose the rungs above already handle.
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
        elif ch == "," and depth == 1:
            last_safe_comma = i

    if last_safe_comma == -1:
        # Derailed before completing a single top-level entry.
        return None
    try:
        parsed = json.loads(text[start:last_safe_comma] + "}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_json_object(
    text: str, *, salvage_truncated: bool = False,
) -> dict[str, Any] | None:
    """Extract a JSON object from ``text``, tolerating markdown code fences
    and leading/trailing prose the model may emit around the object.

    Returns the parsed ``dict`` or ``None`` when no JSON object can be
    recovered — callers treat ``None`` as "no usable answer" and take their
    own degradation path (fallback derivation, H1/topic fallback, or a
    ``qa_rail_degraded`` finding).

    Set ``salvage_truncated=True`` to add a final rung that recovers the
    complete entries of an object the model never closed. Only safe for
    callers whose top-level entries are independent — see the module
    docstring.
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
                # silent-ok: the brace substring was not valid JSON either;
                # move to the next chunk, then to the salvage rung (when
                # enabled), and return None if everything is exhausted.
                pass
    if salvage_truncated:
        for chunk in (candidate, text):
            salvaged = salvage_truncated_object(chunk)
            if salvaged is not None:
                return salvaged
    return None


__all__ = ["extract_json_object", "salvage_truncated_object"]
