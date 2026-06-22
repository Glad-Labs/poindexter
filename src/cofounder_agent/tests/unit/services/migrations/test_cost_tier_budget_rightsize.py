"""Contract test for the cost_tier.budget.model + hygiene-summary baseline seeds.

Pins the GPU/VRAM fix (2026-06-21): ``cost_tier.budget.model`` had drifted to
the ~17GB writer-class model (``gemma-4-31B-it-qat``), collapsing the tier
ladder so ``budget`` ≈ ``standard``. Every ``budget`` caller — the DeepEval +
Ragas advisory judges, ``image_decision_agent``, and the collapse/retention
hygiene summary jobs — therefore loaded the heavyweight writer into VRAM,
pegging the RTX 5090 at ~24GB even when no content task was running. The
``embedding_collapse_summary_model`` / ``memory_compression_summary_model``
fallbacks had the same drift, despite their own seed descriptions calling for
a smaller model.

A genuinely-cheap budget tier (e.g. ``phi4:14b``, ~8GB) keeps background work
off the writer-grade model; the 18GB writer only loads for real content
generation (``pipeline_writer_model``).

Why a contract test: the baseline gets regenerated periodically, and a stale
source could re-introduce the 18GB model. Operators can still tune the live
value (``ON CONFLICT DO NOTHING`` preserves runtime overrides); this only pins
the shipped default.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ~17-19GB content/writer-class models that must NOT back a cheap budget /
# hygiene job — loading any of these for background work pegs VRAM.
_WRITER_CLASS_MODELS = {
    "ollama/gemma-4-31B-it-qat:latest",
    "ollama/gemma4:31b",
    "ollama/glm-4.7-5090:latest",
    "ollama/qwen3.6:latest",
}

# Seed keys whose callers are advisory / hygiene / offline — they must run on
# a sub-writer-size model.
_CHEAP_JOB_MODEL_KEYS = (
    "cost_tier.budget.model",
    "embedding_collapse_summary_model",
    "memory_compression_summary_model",
)


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


def _seed_value(seeds_text: str, key: str) -> str | None:
    """Extract the seeded value for ``key`` from the baseline SQL."""
    match = re.search(rf"VALUES \('{re.escape(key)}', '([^']+)'", seeds_text)
    return match.group(1) if match else None


@pytest.mark.parametrize("key", _CHEAP_JOB_MODEL_KEYS)
def test_cheap_job_model_is_not_writer_class(baseline_seeds_text: str, key: str) -> None:
    """budget tier + hygiene-summary models must not be the writer-grade model.

    The bug: all three seeded ``ollama/gemma-4-31B-it-qat`` (the 17GB writer),
    so advisory judges + hygiene summaries loaded the writer-grade model and
    pegged VRAM. They must point at a genuinely smaller model.
    """
    value = _seed_value(baseline_seeds_text, key)
    assert value is not None, f"{key} seed row missing from baseline"
    assert value not in _WRITER_CLASS_MODELS, (
        f"{key} seeds the writer-class model {value!r}. Background/advisory "
        "callers would load the ~17GB writer into VRAM, pegging the GPU. Point "
        "it at a genuinely smaller model (e.g. ollama/phi4:14b, ~8GB)."
    )


def test_budget_tier_differs_from_standard(baseline_seeds_text: str) -> None:
    """The tier ladder must be monotonic — budget genuinely cheaper than standard."""
    budget = _seed_value(baseline_seeds_text, "cost_tier.budget.model")
    standard = _seed_value(baseline_seeds_text, "cost_tier.standard.model")
    assert budget and standard, "cost_tier budget/standard seed rows missing"
    assert budget != standard, (
        f"cost_tier.budget.model ({budget!r}) must differ from "
        f"cost_tier.standard.model ({standard!r}) — a collapsed ladder means "
        "'budget' callers pay standard-tier VRAM cost."
    )
