"""Contract test: the baseline seeds the noisy LLM-judge QA rails as advisory.

Self-heal-before-paging (#1868, qa-self-heal §6): the DeepEval / Ragas LLM-judge
rails are noisy — in the originating 30-day review, their false-positives were a
meaningful share of the ~60-70% of finished drafts the gates discarded (avg
rejected score 79). A hard veto from any one of them throws away a complete draft
over a single LLM judge's call. The two deterministic hard gates that remain
(``programmatic_validator`` + ``llm_critic``) and the operator are the real
publication gate.

``20260607_182804_graduate_eval_rails_to_required`` (#454) had graduated four of
these rails to hard gates; the demotions back to advisory landed in two waves:

  - ``deepeval_faithfulness`` — demoted by the standalone #1868 migration
    ``20260622_201603_demote_deepeval_faithfulness_to_advisory``, which the
    **Phase F squash folds away** (the baseline now seeds it advisory directly,
    the migration deleted as redundant).
  - ``deepeval_g_eval`` + ``ragas_eval`` — already advisory on prod via operator
    runtime tuning (no migration backed it). The operator chose to align the
    shipped baseline with prod rather than ship them hard to fresh OSS installs
    (where the same draft-discarding behaviour would recur). Folded into the
    baseline as part of this squash.

This guard pins all three so a future baseline regeneration (Phase G+) or a
hand-edit can't silently re-graduate any of them back to a hard gate and
resurrect the draft-discarding behaviour.

Out of scope: ``deepeval_brand_fabrication`` stays a hard gate (prod runs it
hard) — it is a different, deterministic-pattern-backed rail, and its hardness is
a separate decision, so it is intentionally NOT pinned here. Operators can still
tune any live value (``ON CONFLICT (id) DO NOTHING`` preserves runtime
overrides — this only pins the shipped default).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# The noisy LLM-judge rails that must ship advisory (required_to_pass=false).
_ADVISORY_JUDGE_RAILS = ("deepeval_faithfulness", "deepeval_g_eval", "ragas_eval")


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


def _gate_required_to_pass(seeds_text: str, gate_name: str) -> str | None:
    """Extract the ``required_to_pass`` literal for a ``qa_gates`` seed row.

    Row shape: ``VALUES ('<uuid>', '<name>', '<stage>', <order>, '<reviewer>',
    <required_to_pass>, <enabled>, ...)`` — required_to_pass is the 6th value.
    """
    match = re.search(
        rf"INSERT INTO qa_gates [^;]*?VALUES \('[0-9a-fA-F-]+', "
        rf"'{re.escape(gate_name)}', '[^']*', \d+, '[^']*', (true|false),",
        seeds_text,
    )
    return match.group(1) if match else None


@pytest.mark.parametrize("rail", _ADVISORY_JUDGE_RAILS)
def test_noisy_judge_rail_seeded_advisory(baseline_seeds_text: str, rail: str) -> None:
    """Each noisy LLM-judge rail must seed advisory (required_to_pass=false)."""
    value = _gate_required_to_pass(baseline_seeds_text, rail)
    assert value is not None, f"{rail} qa_gates seed row missing from the baseline"
    assert value == "false", (
        f"{rail} must seed as advisory (required_to_pass=false). Found "
        f"required_to_pass={value!r} — a hard LLM-judge gate discards finished "
        "drafts over a single noisy judge call (the behaviour #1868 + the "
        "g_eval/ragas demotion removed). If a baseline regeneration re-graduated "
        "it, re-apply the demotion to the seed."
    )


def test_brand_fabrication_left_as_hard_gate(baseline_seeds_text: str) -> None:
    """Scope guard: the demotion is the three noisy LLM judges only.

    ``deepeval_brand_fabrication`` is a separate, pattern-backed rail whose
    hardness is decided elsewhere — this test documents that the advisory
    demotion deliberately did NOT touch it, so a future change to brand_fabrication
    is a conscious edit, not an accidental side-effect of this guard.
    """
    value = _gate_required_to_pass(baseline_seeds_text, "deepeval_brand_fabrication")
    assert value is not None, "deepeval_brand_fabrication seed row missing from baseline"
    assert value == "true", (
        "deepeval_brand_fabrication is expected to ship as a hard gate "
        f"(required_to_pass=true); found {value!r}. If this was a deliberate "
        "demotion, update _ADVISORY_JUDGE_RAILS and this guard together."
    )
