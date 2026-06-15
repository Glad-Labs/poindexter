"""Unit test for the _derive_categories helper in the promote migration.

Loaded via spec_from_file_location because the migration's module name starts
with a digit (not importable as a normal dotted submodule).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import services

_MIG = (
    Path(services.__file__).resolve().parent
    / "migrations"
    / "20260615_033048_promote_niche_sources_to_niche_bound_topic_taps.py"
)
_spec = importlib.util.spec_from_file_location("_promote_topic_taps_mig", _MIG)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_web_search_matches_bank_keys_by_slug_and_tags():
    # 'gaming' slug token matches bank key 'gaming'; 'hardware' tag matches 'hardware'.
    cats = _mod._derive_categories("web_search", "gaming", ["pc hardware"])
    assert cats == ["gaming", "hardware"]


def test_ai_ml_niche_has_no_bank_match_falls_through_to_tags():
    # No bank key for ai/ml -> empty -> web_search uses the tag-derived path.
    assert _mod._derive_categories("web_search", "ai-ml", ["llms", "agents"]) == []


def test_non_web_search_sources_get_no_categories():
    assert _mod._derive_categories("hackernews", "gaming", ["esports"]) == []
