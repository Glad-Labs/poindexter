"""Regression guard for the 26 zero-reader orphan settings retired 2026-07-11.

``ProbeZeroReaderSettingsJob`` flagged 50 app_settings keys with NULL
``last_read_at`` past the grace window. A full-repo grep confirmed 26 of them
are read by nothing (superseded token/QA/content/image/cloud knobs + unwired
feature flags). Migration ``20260711_013742_drop_zero_reader_orphan_settings``
deletes them from existing installs, and all three every-boot / drift-checked
seed sources no longer emit them.

This test pins every scrub surface so a future baseline regen (or a brain-seed
edit) can't silently re-introduce a dead row:

  * absent from ``0000_baseline.seeds.sql`` (re-applied every boot),
  * absent from ``brain/seed_app_settings.json`` (brain first-boot seed),
  * absent from ``settings_defaults.DEFAULTS`` (``seed_all_defaults`` every boot),
  * the migration's ``_DEAD_KEYS`` is exactly that set, and
  * the live blind-spot / intentional-keep keys that these sat among are
    untouched (fat-finger floor).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]  # .../<repo>/src/cofounder_agent/tests/unit/services/migrations
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "services" / "migrations"
_DEFAULTS_PY = _REPO / "src" / "cofounder_agent" / "services" / "settings_defaults.py"
_BRAIN_SEED = _REPO / "brain" / "seed_app_settings.json"

_DEAD_KEYS = (
    "max_tokens_per_request",
    "max_tokens_per_task",
    "max_tokens_default",
    "qa_workflow_blog_content",
    "qa_workflow_premium_content",
    "qa_workflow_quick_check",
    "qa_overall_score_threshold",
    "qa_critical_dimension_floor",
    "ollama_electricity_cost_per_1k_tokens",
    "task_sweep_interval_seconds",
    "max_task_retries",
    "content_max_refinement_attempts",
    "content_temperature",
    "content_target_word_count",
    "content_min_word_count",
    "content_quality_minimum",
    "content_weekly_cap",
    "image_primary_source",
    "image_style_default",
    "enable_featured_image",
    "enable_mcp_server",
    "enable_memory_system",
    "enable_training_capture",
    "cloud_api_mode",
    "cloud_api_daily_limit",
    "hardware_cost_total",
)

# Verified-live keys the same probe flags but that ARE read (blind-spots) or are
# intentional operator config — must stay seeded.
_KEEP_KEYS = (
    "auto_publish_threshold",
    "pipeline_writer_model",
    "pipeline_critic_model",
    "pipeline_fallback_model",
    "qa_final_score_threshold",
    "qa_validator_weight",
    "qa_critic_weight",
    "qa_temperature",
    "stale_task_timeout_minutes",
    "api_base_url",
    "cost_alert_threshold_pct",
    "grafana_url",
    "cloudinary_cloud_name",
    "newsletter_from_email",
    "openclaw_webhook_url",
    "discord_ops_channel_id",  # pinned blank by the operator-leak guard
    "privacy_email",
    "gpu_model",
    "social_x_url",
    "social_linkedin_url",
    "telegram_alerts_enabled",  # unenforced control, left in place
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


@pytest.fixture(scope="module")
def migration_module():
    path = _MIGRATIONS_DIR / "20260711_013742_drop_zero_reader_orphan_settings.py"
    spec = importlib.util.spec_from_file_location("_drop_zero_reader_orphan_settings", path)
    assert spec and spec.loader, "could not load the drop_zero_reader_orphan_settings migration"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seeds_key(seeds_text: str, key: str) -> bool:
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    """Retired keys must not be re-seeded in the baseline — fresh installs stay clean."""
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is back in 0000_baseline.seeds.sql. It has no reader "
        "(zero-reader orphan retired 2026-07-11); leave it out of the seed."
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


@pytest.mark.parametrize("key", _KEEP_KEYS)
def test_live_keep_keys_still_seeded(baseline_seeds_text: str, key: str) -> None:
    """Fat-finger floor: the verified-live / intentional-keep keys stay seeded.

    Guards against a range delete taking out a real blind-spot key
    (``auto_publish_threshold`` read by ``auto_publish``, ``pipeline_writer_model``
    by the writer resolvers, ``stale_task_timeout_minutes`` by the brain sweep)
    or an intentional operator-config row (``discord_ops_channel_id`` pinned by
    the operator-leak guard).
    """
    assert _seeds_key(baseline_seeds_text, key), (
        f"live/keep key {key!r} was lost from 0000_baseline.seeds.sql"
    )


def test_migration_targets_exactly_the_dead_keys(migration_module) -> None:
    """The delete set and this test's expectation stay in lockstep."""
    assert tuple(migration_module._DEAD_KEYS) == _DEAD_KEYS


def test_no_overlap_between_dead_and_keep() -> None:
    """A key can't be both retired and kept — catches a classification typo."""
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_exposes_runner_interface(migration_module) -> None:
    assert callable(migration_module.up)
    assert callable(migration_module.down)
