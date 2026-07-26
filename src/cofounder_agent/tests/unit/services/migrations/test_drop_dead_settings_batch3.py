"""Regression guard for the 24 zero-reader settings retired 2026-07-26 (batch 3).

Sibling to ``test_drop_zero_reader_orphans_batch2.py`` and
``test_drop_dead_cost_budget_settings.py``. Sourced from the sweep in
Glad-Labs/poindexter#915, which combined two detectors — the static corpus scan
in ``scripts/ci/settings_audit.py`` and the ``last_read_at`` read-telemetry
(#756) — because **neither is trustworthy alone**. Of 107 keys that both
detectors called dead, 65 were live:

  * ``findings.<kind>.*`` (59) are read by bulk raw SQL
    (``findings_alert_router`` does ``WHERE key LIKE 'findings.%.%'``), which
    never stamps ``last_read_at`` and defeats literal matching.
  * ``offsite_backup_*`` (5) are read by **shell** (``scripts/backup-offsite/
    run.sh``), a file type the audit's ``CODE_EXTS`` does not scan.
  * ``memory_stale_threshold_seconds_collapse_job`` is built as
    ``f"memory_stale_threshold_seconds_{writer}"``; the audit's prefix
    heuristic splits on the last ``_`` so a two-word suffix evades it.

Every key retired here therefore has a **named replacement**, not merely a
failed grep (per ``dont-delete-valuable-unfinished``: unwired != superseded):

  * ``embedding_retention_days.*`` -> ``retention_policies`` rows
    ``embeddings.ttl_prune.*`` (``ttl_days`` column).
  * ``embedding_collapse_*`` -> ``retention_policies`` rows
    ``embeddings.collapse.*``; the handler
    (``integrations/handlers/retention_embeddings_collapse.py``) reads
    ``age_days`` / ``cluster_size`` / ``source_table`` / ``summary_timeout_s``
    / ``summary_model`` from the row ``config`` JSONB, and ``enabled`` from the
    row column. ``plugins/registry.py`` records the 2026-06-24 job retirement.
  * ``topic_discovery_*`` -> the per-niche schema from #278: ``niches.active``,
    ``niches.discovery_cadence_minute_floor``, ``niche_sources``,
    ``discovery_runs``.
  * ``brand_keywords`` -> nothing reads it; its value is the empty-string unset
    sentinel.
  * ``plugin.job.probe_prompt_catalog_drift`` -> job deleted; prompt
    source-of-truth moved to the SKILL.md packs (#825).

Only 5 of the 24 were seeded (the rest were prod-only rows seeded by nothing);
those 5 are removed from ``0000_baseline.seeds.sql`` by this change, since a DB
delete alone loses to the next boot's ``INSERT ... ON CONFLICT DO NOTHING``.

**The keep-list is the point.** ``topic_discovery_length_distribution`` is LIVE
(read via ``services/topic_length.py``) and was seeded on the line directly
above the three dead ``topic_discovery_*`` rows. ``topic_discovery_manual_trigger``
is a documented operator affordance, and ``embedding_collapse_summary_model`` is
pinned by its own test — both deliberately survive this batch. A prefix- or
block-based removal would have taken all three out with the fossils.
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
    # -> retention_policies rows embeddings.ttl_prune.* (ttl_days)
    "embedding_retention_days.audit",
    "embedding_retention_days.brain",
    "embedding_retention_days.claude_sessions",
    "embedding_retention_days.issues",
    "embedding_retention_days.memory",
    "embedding_retention_days.posts",
    # -> retention_policies rows embeddings.collapse.* (config JSONB + enabled)
    "embedding_collapse_age_days",
    "embedding_collapse_cluster_size",
    "embedding_collapse_source_tables",
    "embedding_collapse_summary_timeout_seconds",
    "embedding_collapse_enabled",
    "embedding_collapse_summary_provider",
    # -> per-niche schema (#278): niches / niche_sources / discovery_runs
    "topic_discovery_auto_enabled",
    "topic_discovery_category_searches",
    "topic_discovery_ideation_lookback_days",
    "topic_discovery_min_cooldown_seconds",
    "topic_discovery_news_patterns",
    "topic_discovery_queue_low_threshold",
    "topic_discovery_rejection_streak",
    "topic_discovery_stale_hours",
    "topic_discovery_streak_window_hours",
    "topic_discovery_style_distribution",
    # no reader; value is the empty-string unset sentinel
    "brand_keywords",
    # job deleted; prompt SoT moved to the SKILL.md packs (#825)
    "plugin.job.probe_prompt_catalog_drift",
)

# Live neighbours a prefix/block-based removal would destroy. The first is the
# sharp one: it was seeded on the line immediately above the dead
# topic_discovery_* rows and differs from `topic_discovery_style_distribution`
# by a single word.
_KEEP_SEEDED_KEYS = (
    "topic_discovery_length_distribution",
    "embed_model",
    "electricity_rate_kwh",
)

# Live keys in the same families that this batch deliberately does NOT retire.
_KEEP_UNRETIRED_KEYS = (
    "topic_discovery_manual_trigger",
    "embedding_collapse_summary_model",
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
        "(zero-reader orphan retired 2026-07-26, batch 3; see poindexter#915 "
        "for the named replacement). Leave it out."
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
def test_live_neighbour_keys_still_seeded(baseline_seeds_text: str, key: str) -> None:
    assert _seeds_key(baseline_seeds_text, key), (
        f"live neighbour {key!r} was lost from 0000_baseline.seeds.sql — a "
        "prefix- or block-based removal of the batch-3 fossils takes live keys "
        "with it. Retire keys by exact name only."
    )


@pytest.mark.parametrize("key", _KEEP_UNRETIRED_KEYS)
def test_deliberately_unretired_keys_not_in_dead_list(key: str) -> None:
    assert key not in _DEAD_KEYS, (
        f"{key!r} was deliberately excluded from batch 3 (documented operator "
        "affordance / test-pinned). Retiring it needs its own verification."
    )


def test_no_overlap_between_dead_and_keep() -> None:
    keep = set(_KEEP_SEEDED_KEYS) | set(_KEEP_UNRETIRED_KEYS)
    assert not (set(_DEAD_KEYS) & keep)
