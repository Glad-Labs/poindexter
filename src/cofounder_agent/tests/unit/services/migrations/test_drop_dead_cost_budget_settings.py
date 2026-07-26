"""Regression guard for the 3 pre-rename cost/budget orphan settings retired 2026-07-26.

Sibling to ``test_drop_zero_reader_orphans_batch2.py``. These three keys predate
the cost-guard rename that introduced the ``_usd``-suffixed keys, and a whole-word
grep across ``src/``, ``brain/``, ``scripts/``, ``mcp-server/`` and the Grafana
dashboards found **no reader** for any of them:

  * ``daily_budget_usd``    — superseded by ``cost_throttle_daily_budget_usd``.
  * ``daily_spend_limit``   — superseded by ``daily_spend_limit_usd``.
  * ``monthly_spend_limit`` — superseded by ``monthly_spend_limit_usd``.

The only code hits for these strings are **in-process field names**, not settings
reads: ``ThrottleDecision.daily_budget_usd`` and the ``get_state()`` dict key in
``services/spend_throttle.py`` (re-exported as a Prometheus gauge by
``services/metrics_exporter.py``) are populated from
``cost_throttle_daily_budget_usd``, never from the ``daily_budget_usd`` row.

Why a test and not just a DELETE: ``0000_baseline.seeds.sql`` re-applies on every
boot with ``INSERT ... ON CONFLICT DO NOTHING``, so deleting the rows without
removing the seed loses to the next startup. Only the baseline seeds carried
these three — ``settings_defaults.DEFAULTS`` and ``brain/seed_app_settings.json``
already had just the ``_usd`` variants — but all three sources are pinned here so
a baseline regen or brain-seed edit can't silently resurrect a dead row.

**Prefix hazard — the reason ``_KEEP_*`` exists.** ``daily_spend_limit`` is a
strict prefix of the live ``daily_spend_limit_usd``, and ``monthly_spend_limit``
of ``monthly_spend_limit_usd``. A substring- or prefix-matching removal would
take out the live cost-guard hard caps along with the fossils and silently
disable the API spend ceiling. The keep-lists below are the fat-finger floor
against exactly that.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "services" / "migrations"
_DEFAULTS_PY = _REPO / "src" / "cofounder_agent" / "services" / "settings_defaults.py"
_BRAIN_SEED = _REPO / "brain" / "seed_app_settings.json"

_DEAD_KEYS = (
    "daily_budget_usd",
    "daily_spend_limit",
    "monthly_spend_limit",
)

# Live keys the baseline still seeds — the cost-guard hard caps a prefix-match
# removal would destroy, plus their immediate seed-block neighbours.
_KEEP_SEEDED_KEYS = (
    "daily_spend_limit_usd",
    "monthly_spend_limit_usd",
    "cost_alert_threshold_pct",
    "electricity_rate_kwh",
    "daily_post_limit",
)

# Live keys carried by settings_defaults.DEFAULTS only (never baseline-seeded):
# the spend_throttle total-cost budgets that superseded ``daily_budget_usd``.
_KEEP_DEFAULTS_KEYS = (
    "daily_spend_limit_usd",
    "monthly_spend_limit_usd",
    "cost_throttle_daily_budget_usd",
    "cost_throttle_monthly_budget_usd",
)


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (_MIGRATIONS_DIR / "0000_baseline.seeds.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def brain_seed_keys() -> set[str]:
    data = json.loads(_BRAIN_SEED.read_text(encoding="utf-8"))
    return {s["key"] for s in data.get("settings", []) if isinstance(s, dict) and "key" in s}


@pytest.fixture(scope="module")
def defaults_keys() -> set[str]:
    tree = ast.parse(_DEFAULTS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        target_is_defaults = False
        value = None
        if isinstance(node, ast.Assign):
            target_is_defaults = any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets
            )
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_is_defaults = node.target.id == "DEFAULTS"
            value = node.value
        if target_is_defaults and isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    raise AssertionError("DEFAULTS dict not found in settings_defaults.py")


def _seeds_key(seeds_text: str, key: str) -> bool:
    """True when the baseline seeds an EXACT key (the quote+comma anchors the match)."""
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is back in 0000_baseline.seeds.sql — it has no reader "
        "(pre-rename cost/budget orphan retired 2026-07-26). Leave it out; the "
        f"live key is {key}_usd or its cost_throttle_* equivalent."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    assert key not in brain_seed_keys, (
        f"{key!r} is back in brain/seed_app_settings.json — a fresh brain "
        "bootstrap would resurrect it. Remove it from the brain seed."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_defaults(defaults_keys: set[str], key: str) -> None:
    assert key not in defaults_keys, (
        f"{key!r} is back in settings_defaults.DEFAULTS — seed_all_defaults would "
        "re-insert it on the next boot. Remove it from DEFAULTS."
    )


@pytest.mark.parametrize("key", _KEEP_SEEDED_KEYS)
def test_live_keep_keys_still_seeded(baseline_seeds_text: str, key: str) -> None:
    assert _seeds_key(baseline_seeds_text, key), (
        f"live/keep key {key!r} was lost from 0000_baseline.seeds.sql — a "
        "prefix-matching removal of the retired fossils takes the live cost-guard "
        "caps with it and silently disables the API spend ceiling."
    )


@pytest.mark.parametrize("key", _KEEP_DEFAULTS_KEYS)
def test_live_keep_keys_still_in_defaults(defaults_keys: set[str], key: str) -> None:
    assert key in defaults_keys, f"live/keep key {key!r} was lost from settings_defaults.DEFAULTS"


def test_no_overlap_between_dead_and_keep() -> None:
    keep = set(_KEEP_SEEDED_KEYS) | set(_KEEP_DEFAULTS_KEYS)
    assert not (set(_DEAD_KEYS) & keep)
