"""Shared target-length picker (#542).

Both the topic-discovery auto-queue path and ``topic_proposal_service``
(manual / URL-seed) vary post length through this one weighted picker so
the queue isn't dominated by a single length. Customers tune the mix via
``app_settings.topic_discovery_length_distribution`` as JSON, e.g.
``[[400, 800, 0.25], [800, 1200, 0.35], [1500, 2000, 0.25], [2500, 3500, 0.15]]``.

Extracted verbatim out of ``services.topic_discovery`` so the picker no
longer lives in the legacy discovery dispatcher (which is being retired —
``project_topic_discovery_consolidation``). ``topic_discovery`` re-exports
these names for backward compatibility until that module is deleted.
"""

from __future__ import annotations

import json
import random
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# Default distribution (journalism-style spread, #542): a true short-form
# bucket (news brief / explainer, ~400-800 words) is first-class, and weight
# is spread across four buckets instead of clustering 60% on a single
# 800-1200 band. This makes length variation visible across a day's output
# instead of every post landing near ~1000 words.
DEFAULT_LENGTH_WEIGHTS: list[tuple[int, int, float]] = [
    (400, 800, 0.25),    # Short reads — news brief / quick explainer (2-3 min)
    (800, 1200, 0.30),   # Standard reads (3-5 min)
    (1500, 2000, 0.25),  # Medium features (6-8 min)
    (2500, 3500, 0.20),  # Deep dives (10-15 min)
]


def resolve_length_weights(
    site_config: Any,
) -> list[tuple[int, int, float]]:
    """Resolve the (lo, hi, weight) length buckets from app_settings.

    Reads ``topic_discovery_length_distribution`` off the supplied
    ``site_config`` (any object exposing ``.get(key, default)``). Falls
    back to :data:`DEFAULT_LENGTH_WEIGHTS` when the key is unset or the
    JSON is malformed (logs a WARNING — no silent swallow).
    """
    weights = list(DEFAULT_LENGTH_WEIGHTS)
    raw = ""
    if site_config is not None:
        try:
            raw = site_config.get("topic_discovery_length_distribution", "")
        except Exception:  # pragma: no cover - defensive
            raw = ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                weights = [
                    (int(lo), int(hi), float(w)) for lo, hi, w in parsed
                ]
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[TOPIC_LENGTH] topic_discovery_length_distribution "
                "invalid JSON, using defaults: %s", exc,
            )
    return weights


def pick_target_length(site_config: Any) -> int:
    """Pick a target word count via the weighted, DB-configurable picker.

    Shared by ``topic_discovery`` and ``topic_proposal_service`` (#542)
    so every task-creation path that doesn't get an explicit length still
    varies output length the same way. Weights come from app_settings via
    :func:`resolve_length_weights`.
    """
    weights = resolve_length_weights(site_config)
    r = random.random()
    cumulative = 0.0
    for lo, hi, weight in weights:
        cumulative += weight
        if r <= cumulative:
            return random.randint(lo, hi)
    # Fallback: weights summed to < 1.0 (operator misconfigured) — use the
    # last bucket's range so we still vary rather than pinning a constant.
    lo, hi, _ = weights[-1]
    return random.randint(lo, hi)
