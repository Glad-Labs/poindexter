"""Contract test for the hygiene-summary model baseline seeds.

Pins the GPU/VRAM fix (2026-06-21): the ``embedding_collapse_summary_model``
and ``memory_compression_summary_model`` fallbacks had drifted to the ~17GB
writer-class model (``gemma-4-31B-it-qat``), so the offline collapse/retention
hygiene-summary jobs loaded the heavyweight writer into VRAM, pegging the
RTX 5090 at ~24GB even when no content task was running — despite their own
seed descriptions calling for a smaller model.

A genuinely-cheap hygiene model (e.g. ``phi4:14b``, ~8GB) keeps background
work off the writer-grade model; the 18GB writer only loads for real content
generation (``pipeline_writer_model``). The ``cost_tier.*`` tier indirection
that previously fronted these jobs was removed (PR #1907) — each job now reads
its own per-step ``*_model`` pin.

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
    """Hygiene-summary models must not be the writer-grade model.

    The bug: both seeded ``ollama/gemma-4-31B-it-qat`` (the 17GB writer), so
    the offline hygiene summaries loaded the writer-grade model and pegged
    VRAM. They must point at a genuinely smaller model.
    """
    value = _seed_value(baseline_seeds_text, key)
    assert value is not None, f"{key} seed row missing from baseline"
    assert value not in _WRITER_CLASS_MODELS, (
        f"{key} seeds the writer-class model {value!r}. Background/advisory "
        "callers would load the ~17GB writer into VRAM, pegging the GPU. Point "
        "it at a genuinely smaller model (e.g. ollama/phi4:14b, ~8GB)."
    )
