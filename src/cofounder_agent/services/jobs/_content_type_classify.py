"""Pure helpers for the content-type classifier (no I/O — unit-testable).

Split out from ``classify_content_types.py`` so the label parsing + the
deterministic guardrail over the LLM's output can be tested without a pool or
an LLM. The ``_``-prefix keeps this a library, not a job the registry would
pick up.
"""
from __future__ import annotations

import json
from typing import Any


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
    ``["label", ...]``. Drops unknown/duplicate labels, clamps confidence to
    ``[0, 1]`` (default ``1.0`` when absent/non-numeric). Returns ``[]`` on any
    parse failure — the caller treats that as "no labels" (post stays
    unclassified; no silent default label per feedback_no_silent_defaults).
    """
    allowed_set = {a.lower() for a in allowed}
    try:
        data: Any = json.loads(raw_text)
    except (ValueError, TypeError):
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
