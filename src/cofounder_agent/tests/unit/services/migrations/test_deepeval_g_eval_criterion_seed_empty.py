"""Contract tests for the deepeval-g-eval-criterion seed-masking fix (poindexter#830).

#829 made ``qa.deepeval_g_eval_criterion`` a SKILL.md catalog default. These
tests pin that the baseline no longer re-masks it via a non-empty
``app_settings.deepeval_g_eval_criterion`` seed, and that the one-shot
convergence migration targets the exact historical value prod carries (so it
actually fires there rather than silently no-opping).

Why contract tests: the baseline is regenerated periodically; a stale source
could re-bake the full rubric into the seed and silently shadow the catalog
again (``multi_model_qa`` uses any non-empty setting value). ``ON CONFLICT DO
NOTHING`` preserves live operator overrides — this only pins the shipped
default to the empty ("use the catalog") sentinel.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_SVC = Path(__file__).resolve().parents[4] / "services"
_BASELINE_SEEDS = _SVC / "migrations" / "0000_baseline.seeds.sql"
_CONVERGENCE_MIGRATION = (
    _SVC
    / "migrations"
    / "20260704_051500_empty_deepeval_g_eval_criterion_seed_so_skill_is_authoritative.py"
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


def test_convergence_migration_targets_current_default() -> None:
    """The convergence migration's frozen historical value must equal today's
    ``_DEFAULT_G_EVAL_CRITERION`` — that's the value seeded installs carry, so
    equality is what makes the UPDATE actually fire on prod (rather than
    no-op). If the constant is intentionally changed before prod converges,
    drop this coupling; the migration always targets the ORIGINAL default."""
    from services import deepeval_rails

    spec = importlib.util.spec_from_file_location(
        "_mig_empty_g_eval_criterion", _CONVERGENCE_MIGRATION,
    )
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    assert mig._HISTORICAL_DEFAULT == deepeval_rails._DEFAULT_G_EVAL_CRITERION, (
        "convergence migration targets a value that no longer matches the "
        "shipped default — it would no-op on prod and leave the seed masking "
        "the catalog."
    )
