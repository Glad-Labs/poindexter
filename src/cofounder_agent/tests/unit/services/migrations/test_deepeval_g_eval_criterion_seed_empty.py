"""Contract test for the deepeval-g-eval-criterion baseline seed (poindexter#830).

#829 made ``qa.deepeval_g_eval_criterion`` a SKILL.md catalog default. This test
pins that the baseline no longer re-masks it via a non-empty
``app_settings.deepeval_g_eval_criterion`` seed.

Why a contract test: the baseline is regenerated periodically; a stale source
could re-bake the full rubric into the seed and silently shadow the catalog
again (``multi_model_qa`` uses any non-empty setting value). ``ON CONFLICT DO
NOTHING`` preserves live operator overrides — this only pins the shipped default
to the empty ("use the catalog") sentinel.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BASELINE_SEEDS = (
    Path(__file__).resolve().parents[4] / "services" / "migrations" / "0000_baseline.seeds.sql"
)


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return _BASELINE_SEEDS.read_text(encoding="utf-8")


def _seed_value(seeds_text: str, key: str) -> str | None:
    """Extract the seeded value for ``key`` — ``[^']*`` matches the empty
    sentinel (the hygiene-test helper uses ``[^']+`` and would miss it)."""
    match = re.search(rf"VALUES \('{re.escape(key)}', '([^']*)'", seeds_text)
    return match.group(1) if match else None


def test_baseline_seeds_criterion_empty(baseline_seeds_text: str) -> None:
    """Baseline must seed ``deepeval_g_eval_criterion`` empty so the SKILL.md
    catalog default is authoritative — a non-empty seed silently shadows it."""
    value = _seed_value(baseline_seeds_text, "deepeval_g_eval_criterion")
    assert value is not None, "deepeval_g_eval_criterion seed row missing from baseline"
    assert value == "", (
        "deepeval_g_eval_criterion is seeded non-empty "
        f"({value!r}) — multi_model_qa uses any non-empty setting value, so "
        "this re-masks the qa.deepeval_g_eval_criterion SKILL.md catalog "
        "default (poindexter#830). Seed '' (use-the-catalog sentinel) instead."
    )
