"""Regression guard for the six ``premium_*`` license settings retired 2026-08-24.

``poindexter premium`` (Lemon Squeezy license-key activation) stamped six
``premium_*`` keys into ``app_settings`` that no code ever read — the premium
prompt gating its seed description advertised died with the ``prompt_templates``
table (poindexter#47 Phase 2), and Pro is now delivered as GitHub collaborator
access via ``poindexter pro`` (glad-labs-stack#3216). Migration
``20260825_021743`` drops the rows on existing installs; the ``premium_active``
baseline seed was removed in the same commit.

This test pins every scrub surface so a future baseline regen (or a brain-seed
edit) can't silently re-introduce a dead row:

  * absent from ``0000_baseline.seeds.sql`` (re-applied every boot),
  * absent from ``brain/seed_app_settings.json`` (brain first-boot seed),
  * absent from ``settings_defaults.DEFAULTS`` (``seed_all_defaults`` every boot),
  * the migration's explicit key list matches exactly what ``premium.py``
    used to write (no LIKE-pattern widening), and
  * the ``premium``-substringed key that IS live — the
    ``plugin.llm_provider.primary.premium`` cost-tier row — stays seeded
    (fat-finger floor against a ``%premium%`` range delete).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]  # .../<repo>/src/cofounder_agent/tests/unit/services/migrations
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "services" / "migrations"
_DEFAULTS_PY = _REPO / "src" / "cofounder_agent" / "services" / "settings_defaults.py"
_BRAIN_SEED = _REPO / "brain" / "seed_app_settings.json"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260825_021743_retire_premium_license_settings_orphaned_by_the_pro_delivery_cutover.py"
)

_DEAD_KEYS = (
    "premium_license_key",
    "premium_instance_id",
    "premium_active",
    "premium_email",
    "premium_customer_name",
    "premium_validated_at",
)

# Live key sharing the "premium" substring — the LLM cost-tier router row.
_KEEP_KEY = "plugin.llm_provider.primary.premium"


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
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    """Retired keys must not be re-seeded in the baseline — fresh installs stay clean."""
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is back in 0000_baseline.seeds.sql. It has no reader "
        "(premium license flow retired 2026-08-24); leave it out of the seed."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    """The brain first-boot seed must not re-introduce a retired key either."""
    assert key not in brain_seed_keys, (
        f"{key!r} is back in brain/seed_app_settings.json — a fresh brain "
        "bootstrap would resurrect it. Remove it from the brain seed."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_defaults(defaults_keys: set[str], key: str) -> None:
    """settings_defaults.DEFAULTS re-seeds every boot — a retired key here resurrects."""
    assert key not in defaults_keys, (
        f"{key!r} is back in settings_defaults.DEFAULTS — seed_all_defaults would "
        "re-insert it on the next boot. Remove it from DEFAULTS."
    )


def test_migration_key_list_is_exactly_the_premium_writer_set() -> None:
    """The migration names the six keys premium.py wrote — no more, no less.

    An explicit tuple (no LIKE pattern) is the template convention
    (``20260809_020020``): it can never widen to a key that is still live.
    Parsing the file keeps this a pure-file test — importing the migrations
    package inside the docker image trips the ``parents[5]`` path-depth quirk.
    """
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ORPHANED_KEYS" for t in node.targets
        ):
            assert isinstance(node.value, ast.Tuple)
            keys = tuple(
                k.value for k in node.value.elts if isinstance(k, ast.Constant)
            )
            assert sorted(keys) == sorted(_DEAD_KEYS)
            return
    raise AssertionError("ORPHANED_KEYS tuple not found in the retire-premium migration")


def test_live_premium_substring_key_still_seeded(baseline_seeds_text: str) -> None:
    """Fat-finger floor: the cost-tier router key survives the scrub.

    ``plugin.llm_provider.primary.premium`` is the ``premium`` *cost tier*
    (free/budget/standard/premium) — unrelated to the license flow, read by
    ``services/llm_providers/dispatcher.get_provider`` on every premium-tier
    LLM call. A careless ``LIKE '%premium%'`` delete would take it out.
    """
    assert _seeds_key(baseline_seeds_text, _KEEP_KEY), (
        f"live key {_KEEP_KEY!r} was lost from 0000_baseline.seeds.sql"
    )
