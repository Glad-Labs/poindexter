"""Pure helpers for the content-type classifier (no I/O — unit-testable).

Split out from ``classify_content_types.py`` so the label parsing + the
deterministic guardrail over the LLM's output can be tested without a pool or
an LLM. The ``_``-prefix keeps this a library, not a job the registry would
pick up.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Recover a JSON value from model output that wraps it in a ```json fence and/or
# a reasoning preamble. Mirrors title_generation._extract_json /
# seo_generate_all_metadata._extract_json (the substrate replicates this small
# helper rather than cross-importing). gemma-4-31B — the structured-extraction
# model — emits a long deliberation, then a fenced block, then a bare object;
# a plain json.loads of the whole string fails, which silently yielded zero
# labels across the whole published corpus until this recovery was added.
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


def _loads_lenient(raw_text: str) -> Any:
    """Parse a JSON dict/list from model output, tolerating a markdown code
    fence and/or leading/trailing prose. Returns the parsed value, or ``None``
    when nothing JSON-shaped can be recovered (caller treats that as "no
    labels")."""
    if not raw_text or not isinstance(raw_text, str):
        return None
    fence = _FENCE_RE.search(raw_text)
    # Prefer the fenced content; fall back to scanning the whole string.
    for chunk in (fence.group(1) if fence else None, raw_text):
        if chunk is None:
            continue
        chunk = chunk.strip()
        try:
            return json.loads(chunk)
        except (ValueError, TypeError):
            # silent-ok: this chunk isn't valid JSON as-is; fall through to the
            # {...}/[...] span scan below. Exhausting every strategy returns
            # None, which validate_labels treats as "no labels" (the post stays
            # unclassified and is retried next run — feedback_no_silent_defaults).
            pass
        # Discard a reasoning preamble by grabbing the first {...} or [...] span.
        for pattern in (_OBJECT_RE, _ARRAY_RE):
            match = pattern.search(chunk)
            if match:
                try:
                    return json.loads(match.group())
                except (ValueError, TypeError):
                    # silent-ok: this {...}/[...] slice isn't valid JSON; try the
                    # next pattern/chunk. Exhausting all returns None → no labels.
                    pass
    return None


def parse_labels_csv(raw: str) -> list[str]:
    """Parse a CSV label set (``content_type_labels``) into a normalized list.

    Strips, lowercases, de-dupes (order-preserving). Empty / whitespace-only /
    ``None`` yields ``[]``.
    """
    seen: dict[str, None] = {}
    for part in (raw or "").split(","):
        label = part.strip().lower()
        if label:
            seen.setdefault(label, None)
    return list(seen)


def validate_labels(raw_text: str, allowed: list[str]) -> list[tuple[str, float]]:
    """Parse LLM output → ``[(label, confidence)]`` keeping only allowed labels.

    Accepts either ``{"labels": [{"label", "confidence"}, ...]}`` or a bare
    ``["label", ...]``, tolerating a reasoning preamble and/or a ```json
    markdown fence around it (see :func:`_loads_lenient`). Drops
    unknown/duplicate labels, clamps confidence to ``[0, 1]`` (default ``1.0``
    when absent/non-numeric). Returns ``[]`` on any parse failure — the caller
    treats that as "no labels" (post stays unclassified; no silent default
    label per feedback_no_silent_defaults).
    """
    allowed_set = {a.lower() for a in allowed}
    data = _loads_lenient(raw_text)
    if data is None:
        return []

    items = data.get("labels", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label", "")).strip().lower()
            conf_raw = item.get("confidence", 1.0)
        else:
            label, conf_raw = str(item).strip().lower(), 1.0

        if label in allowed_set and label not in seen:
            try:
                conf = max(0.0, min(1.0, float(conf_raw)))
            except (ValueError, TypeError):
                conf = 1.0
            out.append((label, conf))
            seen.add(label)
    return out
