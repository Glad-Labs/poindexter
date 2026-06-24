"""Regression guard for the five orphaned model settings retired 2026-06-23.

``pipeline_research_model`` / ``pipeline_refinement_model`` /
``pipeline_social_model`` / ``model_role_factchecker`` / ``default_model_tier``
were set in the seed but read by no production code path (vestiges of the
deleted 6-stage StageRunner flow, the unread ``model_role_*`` scheme, and a
global cost-tier knob that was never wired — declined in favour of granular
per-step ``*_model`` pins). Migration
``20260623_202131_drop_dead_model_settings`` deletes them from existing installs
and the baseline seed no longer emits them.

This test pins both halves so a future baseline regen can't silently
re-introduce the dead rows:

  * the five keys are absent from ``0000_baseline.seeds.sql`` (fresh installs
    never get them), and
  * the migration's ``_DEAD_KEYS`` is exactly that set (the delete and the seed
    stay in lockstep).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "services" / "migrations"
_DEAD_KEYS = (
    "pipeline_research_model",
    "pipeline_refinement_model",
    "pipeline_social_model",
    "model_role_factchecker",
    "default_model_tier",
)


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (_MIGRATIONS_DIR / "0000_baseline.seeds.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def migration_module():
    path = _MIGRATIONS_DIR / "20260623_202131_drop_dead_model_settings.py"
    spec = importlib.util.spec_from_file_location("_drop_dead_model_settings", path)
    assert spec and spec.loader, "could not load the drop_dead_model_settings migration"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seeds_key(seeds_text: str, key: str) -> bool:
    """True when ``key`` is INSERTed by the baseline seed SQL."""
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    """The retired keys must not be re-seeded — fresh installs stay clean."""
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is back in 0000_baseline.seeds.sql. It has no production "
        "reader (vestige of the deleted StageRunner stages / model_role_* "
        "scheme / unwired global cost-tier knob); leave it out of the seed."
    )


def test_live_neighbour_keys_still_seeded(baseline_seeds_text: str) -> None:
    """Sanity floor: the live keys these dead ones sat next to are untouched.

    Guards against a fat-fingered range delete taking out a real, read key
    (``pipeline_writer_model`` / ``pipeline_critic_model`` / ``pipeline_fallback_model``
    are all read by the writer + critic resolvers; ``model_role_image_decision``
    is read by ``image_decision_agent``).
    """
    for key in (
        "pipeline_writer_model",
        "pipeline_critic_model",
        "pipeline_fallback_model",
        "model_role_image_decision",
    ):
        assert _seeds_key(baseline_seeds_text, key), f"live key {key!r} lost from seed"


def test_migration_targets_exactly_the_dead_keys(migration_module) -> None:
    """The delete set and this test's expectation stay in lockstep."""
    assert tuple(migration_module._DEAD_KEYS) == _DEAD_KEYS


def test_migration_exposes_runner_interface(migration_module) -> None:
    assert callable(migration_module.up)
    assert callable(migration_module.down)
