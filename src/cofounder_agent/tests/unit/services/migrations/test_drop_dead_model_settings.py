"""Regression guard for the five orphaned model settings retired 2026-06-23.

``pipeline_research_model`` / ``pipeline_refinement_model`` /
``pipeline_social_model`` / ``model_role_factchecker`` / ``default_model_tier``
were set in the seed but read by no production code path (vestiges of the
deleted 6-stage StageRunner flow, the unread ``model_role_*`` scheme, and a
global cost-tier knob that was never wired — declined in favour of granular
per-step ``*_model`` pins).

Retiring them takes two independent steps, one guarded by each test below:

* **Fresh installs** stay clean because the keys are absent from
  ``0000_baseline.seeds.sql`` — a future baseline regen must not silently
  re-introduce them (``test_dead_key_absent_from_baseline_seeds``).
* **Existing installs (prod)** keep the rows until a forward-only migration
  DELETEs them. The 2026-06-23 seed removal was folded into the Phase G
  baseline squash, which only does ``CREATE ... IF NOT EXISTS`` /
  ``INSERT ... ON CONFLICT DO NOTHING`` — it can add a row but never drop one
  prod already carries — so a timestamped ``DELETE FROM app_settings``
  migration is what actually removes them
  (``test_delete_migration_exists_for_dead_keys``).
"""

from __future__ import annotations

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


def _dead_key_delete_migrations() -> list[tuple[str, str]]:
    """``(filename, source)`` for every non-baseline migration whose body
    DELETEs at least one of the dead keys from ``app_settings``.

    The squashed baseline can only ``CREATE ... IF NOT EXISTS`` /
    ``INSERT ... ON CONFLICT DO NOTHING`` (add, never drop), so a forward-only
    timestamped migration is the only thing that removes a row an existing
    install already carries.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        if path.name in {"0000_baseline.py", "__init__.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "DELETE FROM app_settings" in text and any(k in text for k in _DEAD_KEYS):
            found.append((path.name, text))
    return found


def test_delete_migration_exists_for_dead_keys() -> None:
    """A forward-only migration must DELETE the dead rows from existing installs.

    Absence from the seed (guarded above) only keeps *fresh* installs clean;
    prod keeps the rows the squashed baseline cannot drop until a timestamped
    ``DELETE FROM app_settings`` migration removes them.
    """
    migrations = _dead_key_delete_migrations()
    assert migrations, (
        "No forward-only migration DELETEs the retired model keys from "
        "app_settings. Fresh installs are clean (keys absent from the seed), "
        "but existing installs (prod) keep the rows forever without one. Add it "
        'with: python scripts/new-migration.py "drop dead model app_settings"'
    )
    covered = {key for _, text in migrations for key in _DEAD_KEYS if key in text}
    missing = sorted(set(_DEAD_KEYS) - covered)
    assert not missing, (
        f"a delete migration exists but omits dead keys {missing} — the DELETE "
        "must cover all five retired keys"
    )
