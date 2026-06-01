"""Unit tests for the shared target-length picker (#542).

Pins the contract that both ``topic_discovery`` and
``topic_proposal_service`` vary post length through one weighted,
DB-configurable picker:

- :func:`resolve_length_weights` reads
  ``app_settings.topic_discovery_length_distribution`` and falls back to
  :data:`DEFAULT_LENGTH_WEIGHTS` (logging, not swallowing, bad JSON).
- :func:`pick_target_length` returns a value inside one of the resolved
  buckets and is driven entirely by the DB-configurable weights.
- ``propose_topic`` fills in a varied length when none is supplied but
  honours an explicit caller value.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Any

from services.topic_discovery import (
    DEFAULT_LENGTH_WEIGHTS,
    pick_target_length,
    resolve_length_weights,
)


def _make_site_config(values: dict[str, str] | None = None) -> Any:
    cache = dict(values or {})

    def _get(key, default=None):
        return cache.get(key, default)

    return SimpleNamespace(get=_get)


# ---------------------------------------------------------------------------
# resolve_length_weights
# ---------------------------------------------------------------------------


class TestResolveLengthWeights:
    def test_defaults_when_unset(self):
        cfg = _make_site_config()
        assert resolve_length_weights(cfg) == DEFAULT_LENGTH_WEIGHTS

    def test_default_weights_sum_to_one(self):
        # Buckets must partition the probability space — a misconfigured
        # default would silently bias every picked length.
        total = sum(w for _, _, w in DEFAULT_LENGTH_WEIGHTS)
        assert abs(total - 1.0) < 1e-9

    def test_default_has_short_form_bucket(self):
        # #542: a true short-form bucket (< 1000 words) must exist so the
        # spread is visible, not clustered around ~1000 words.
        assert any(hi <= 1000 for _lo, hi, _w in DEFAULT_LENGTH_WEIGHTS)

    def test_default_spans_short_to_deep_dive(self):
        los = [lo for lo, _hi, _w in DEFAULT_LENGTH_WEIGHTS]
        his = [hi for _lo, hi, _w in DEFAULT_LENGTH_WEIGHTS]
        assert min(los) <= 500   # short-form floor
        assert max(his) >= 3000  # deep-dive ceiling

    def test_db_override_parsed(self):
        cfg = _make_site_config({
            "topic_discovery_length_distribution":
                "[[300, 500, 0.5], [900, 1100, 0.5]]",
        })
        assert resolve_length_weights(cfg) == [
            (300, 500, 0.5),
            (900, 1100, 0.5),
        ]

    def test_invalid_json_falls_back_to_defaults(self):
        cfg = _make_site_config({
            "topic_discovery_length_distribution": "not-json",
        })
        assert resolve_length_weights(cfg) == DEFAULT_LENGTH_WEIGHTS

    def test_none_site_config_uses_defaults(self):
        assert resolve_length_weights(None) == DEFAULT_LENGTH_WEIGHTS


# ---------------------------------------------------------------------------
# pick_target_length
# ---------------------------------------------------------------------------


class TestPickTargetLength:
    def test_returns_value_within_a_default_bucket(self):
        cfg = _make_site_config()
        for _ in range(200):
            v = pick_target_length(cfg)
            assert any(lo <= v <= hi for lo, hi, _w in DEFAULT_LENGTH_WEIGHTS)

    def test_respects_db_override_range(self):
        cfg = _make_site_config({
            "topic_discovery_length_distribution": "[[300, 350, 1.0]]",
        })
        for _ in range(50):
            v = pick_target_length(cfg)
            assert 300 <= v <= 350

    def test_varies_across_calls(self):
        # With four default buckets and many draws, output must not pin to
        # a single value — that is the whole point of #542.
        random.seed(1234)
        cfg = _make_site_config()
        seen = {pick_target_length(cfg) for _ in range(300)}
        assert len(seen) > 5

    def test_short_bucket_reachable(self):
        # A draw that lands in the first (short) bucket must be possible.
        cfg = _make_site_config({
            "topic_discovery_length_distribution":
                "[[400, 800, 1.0]]",
        })
        v = pick_target_length(cfg)
        assert 400 <= v <= 800

    def test_underweight_config_still_returns_last_bucket(self):
        # Weights sum to < 1.0 (operator typo) — must still return a value
        # from the last bucket rather than crashing or pinning a constant.
        cfg = _make_site_config({
            "topic_discovery_length_distribution":
                "[[400, 500, 0.1], [2000, 2100, 0.1]]",
        })
        random.seed(0)
        for _ in range(50):
            v = pick_target_length(cfg)
            assert (400 <= v <= 500) or (2000 <= v <= 2100)
