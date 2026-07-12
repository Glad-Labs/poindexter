"""Contract test for the hygiene-summary model baseline seeds.

Pins the GPU/VRAM fix (2026-06-21): ``memory_compression_summary_model``
(used by ``retention_summarize_to_table``) must not drift to the ~17GB
writer-class model. The offline collapse handler previously read
``embedding_collapse_summary_model`` from app_settings, but that key was
retired when the job was folded into the ``embeddings_collapse`` retention
handler (2026-06-24); the collapse handler now reads ``summary_model`` from
its ``retention_policies.config`` JSONB.

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
    "ollama/glm-4.7-5090:latest",
    "ollama/qwen3.6:latest",
}

# app_settings keys whose callers are advisory / hygiene / offline — they must
# run on a sub-writer-size model.
_CHEAP_JOB_MODEL_KEYS = (
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
    """Hygiene-summary app_settings must not be the writer-grade model."""
    value = _seed_value(baseline_seeds_text, key)
    assert value is not None, f"{key} seed row missing from baseline"
    assert value not in _WRITER_CLASS_MODELS, (
        f"{key} seeds the writer-class model {value!r}. Background/advisory "
        "callers would load the ~17GB writer into VRAM, pegging the GPU. Point "
        "it at a genuinely smaller model (e.g. ollama/phi4:14b, ~8GB)."
    )
