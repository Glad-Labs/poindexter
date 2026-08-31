"""Tests for ``scripts/sync-claude-md-stats.py``'s README half.

The script already owned CLAUDE.md's repo-derived counts; it grew the root
README's test-count claims when those were found stale (the shields.io
badge read ``tests-11,400+`` on 2026-08-31 against ~17,200 real test
functions — hand-typed, correct on the day, watched by nothing).

Covered here: the static test-function counter, the canonical_blog graph
shape reader, and the six README rewrites (tests badge, Key-features row,
Project-status bullet, node count, and the two QA-rail counts). The
CLAUDE.md half's anchors are covered by ``test_claude_md_anchor_lint.py``.

The hyphenated filename is not a legal module name, so it loads via
importlib the same way the anchor lint does.
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load(script_rel: str, name: str):
    script = _repo_root() / script_rel
    spec = spec_from_file_location(name, script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SYNC = _load("scripts/sync-claude-md-stats.py", "sync_claude_md_stats_readme_t")

# The three shapes the test count appears in, reproduced verbatim from README
# plus decoy numbers that MUST survive untouched — a bare ``\d+`` pattern
# would eat the release badge and the Python version.
SAMPLE = (
    "[![Release](https://img.shields.io/github/v/release/Glad-Labs/poindexter)]"
    "(https://github.com/Glad-Labs/poindexter/releases)\n"
    "[![Tests](https://img.shields.io/badge/tests-11%2C400%2B-brightgreen)]"
    "(https://github.com/Glad-Labs/poindexter/actions)\n"
    "| **OAuth 2.1 throughout**     | Every consumer mints scoped JWTs.  |\n"
    "| **11,400+ tests**            | Unit coverage across all services. |\n"
    "- Python 3.13+ and 8 GB+ VRAM.\n"
    "- 11,400+ unit tests passing in CI on every push, plus link-rot CI.\n"
    "…then makes each one survive 13 QA rails — cross-model LLM critics, "
    "DeepEval and Ragas evaluations, and vision QA on every image.\n"
    "A declarative LangGraph DAG stored in the database — 44 nodes covering "
    "research, writing, image generation, the 13 QA rails, SEO, and publish.\n"
)

FRESH: OrderedDict[str, int | str] = OrderedDict([
    ("service_py_files", 470),
    ("test_files", 1108),
    ("test_functions", 17_238),
    ("grafana_dashboards", 12),
    ("canonical_blog_nodes", 46),
    ("canonical_blog_qa_rails", 14),
])


class TestCountTestFunctions:
    """Counting by filename undercounts the suite by ~15x; counting ``def
    test_`` only at column 0 undercounts it too, because most tests live
    inside classes and their def is indented."""

    def test_counts_indented_and_async_defs(self, tmp_path):
        f = tmp_path / "test_x.py"
        f.write_text(
            "def test_top_level():\n    pass\n\n"
            "class TestGroup:\n"
            "    def test_indented(self):\n        pass\n\n"
            "    async def test_async_indented(self):\n        pass\n",
            encoding="utf-8",
        )
        assert SYNC._count_test_functions([f]) == 3

    def test_ignores_non_test_defs_and_prose_mentions(self, tmp_path):
        f = tmp_path / "test_y.py"
        f.write_text(
            '"""A docstring that says def test_ but declares nothing."""\n'
            "def helper():\n    pass\n"
            "def testing_not_a_test():\n    pass\n"
            "def test_real():\n    pass\n",
            encoding="utf-8",
        )
        assert SYNC._count_test_functions([f]) == 1

    def test_sums_across_files(self, tmp_path):
        a, b = tmp_path / "test_a.py", tmp_path / "test_b.py"
        a.write_text("def test_one():\n    pass\n", encoding="utf-8")
        b.write_text(
            "def test_two():\n    pass\ndef test_three():\n    pass\n",
            encoding="utf-8",
        )
        assert SYNC._count_test_functions([a, b]) == 3

    def test_real_repo_count_is_in_the_right_order_of_magnitude(self):
        """Guards a regex regression that would silently return ~0 and floor
        the public badge to "0+"."""
        stats = SYNC.collect_stats()
        assert stats["test_functions"] > 10 * stats["test_files"]


class TestLoadCanonicalBlogSpec:
    """The spec is READ, not imported: this script runs on a bare
    `setup-python` step with no `pip install`, so importing the module would
    drag the backend's dependency tree into a job that has none."""

    def test_reads_the_real_spec(self):
        spec = SYNC._load_canonical_blog_spec()
        assert spec["name"] == "canonical_blog"
        assert spec["entry"] == "verify_task"
        assert len(spec["nodes"]) > 30

    def test_ast_parse_agrees_with_actually_importing_the_module(self):
        """Cross-check the stdlib path against the real object. If the spec
        ever grows a non-literal construct, this and the parser must not
        disagree silently."""
        from services.canonical_blog_spec import (  # noqa: PLC0415
            CANONICAL_BLOG_GRAPH_DEF as real,
        )

        parsed = SYNC._load_canonical_blog_spec()
        assert len(parsed["nodes"]) == len(real["nodes"])
        assert [n["atom"] for n in parsed["nodes"]] == [n["atom"] for n in real["nodes"]]

    def test_missing_assignment_raises_rather_than_defaulting(self, tmp_path, monkeypatch):
        """A spec that cannot be read must never become "0 nodes covering" in
        the public README — red CI is the correct outcome."""
        fake_root = tmp_path
        (fake_root / "src/cofounder_agent/services").mkdir(parents=True)
        (fake_root / "src/cofounder_agent/services/canonical_blog_spec.py").write_text(
            "SOMETHING_ELSE = {}\n", encoding="utf-8"
        )
        monkeypatch.setattr(SYNC, "ROOT", fake_root)
        with pytest.raises(ValueError, match="CANONICAL_BLOG_GRAPH_DEF"):
            SYNC._load_canonical_blog_spec()

    def test_non_graph_value_raises(self, tmp_path, monkeypatch):
        fake_root = tmp_path
        (fake_root / "src/cofounder_agent/services").mkdir(parents=True)
        (fake_root / "src/cofounder_agent/services/canonical_blog_spec.py").write_text(
            "CANONICAL_BLOG_GRAPH_DEF = {'name': 'x'}\n", encoding="utf-8"
        )
        monkeypatch.setattr(SYNC, "ROOT", fake_root)
        with pytest.raises(ValueError, match="not a graph spec"):
            SYNC._load_canonical_blog_spec()


class TestCountQaRails:
    """qa.aggregate is the gate DECISION and qa.rewrite is the rescue
    revision. Counting either as a "rail the draft must survive" would
    overstate the gauntlet by two."""

    def test_excludes_the_aggregator_and_the_rescue_rewrite(self):
        spec = {"nodes": [
            {"id": "a", "atom": "qa.programmatic"},
            {"id": "b", "atom": "qa.critic"},
            {"id": "c", "atom": "qa.aggregate"},
            {"id": "d", "atom": "qa.rewrite"},
            {"id": "e", "atom": "content.generate_draft"},
        ]}
        assert SYNC._count_qa_rails(spec) == 2

    def test_real_spec_matches_the_documented_rail_count(self):
        """CLAUDE.md says canonical_blog wires 14 rail atoms; the README
        sentence this script writes has to agree with that."""
        spec = SYNC._load_canonical_blog_spec()
        qa_nodes = [n for n in spec["nodes"] if n["atom"].startswith("qa.")]
        assert SYNC._count_qa_rails(spec) == len(qa_nodes) - 2

    def test_a_spec_with_no_qa_nodes_counts_zero_rather_than_crashing(self):
        assert SYNC._count_qa_rails({"nodes": [{"id": "a", "atom": "stage.x"}]}) == 0


class TestApplyToReadme:
    def test_rewrites_all_three_test_claims_floored(self):
        new, changes = SYNC.apply_to_readme(FRESH, text=SAMPLE)

        # shields.io needs the comma and plus percent-encoded
        assert "tests-17%2C000%2B-brightgreen" in new
        assert "| **17,000+ tests**" in new
        assert "17,000+ unit tests passing in CI" in new
        assert len(changes) == 6
        assert not any(c.startswith("WARNING:") for c in changes)

    def test_rewrites_the_graph_shape_exactly_not_floored(self):
        """A structural fact reads worse rounded: "40+ nodes" is vaguer AND
        less true than "46 nodes"."""
        new, _ = SYNC.apply_to_readme(FRESH, text=SAMPLE)
        assert "46 nodes covering" in new
        assert "survive 14 QA rails" in new
        assert "the 14 QA rails" in new
        for floored_shape in ("40+ nodes", "10+ QA rails", "46+ nodes"):
            assert floored_shape not in new

    def test_the_two_qa_rail_sentences_are_separate_anchors(self):
        """Both say "N QA rails" but only one says "survive". A single greedy
        pattern would rewrite one and leave the other frozen."""
        new, changes = SYNC.apply_to_readme(FRESH, text=SAMPLE)
        assert new.count("14 QA rails") == 2
        names = {c.split(" ->")[0] for c in changes}
        assert {"qa_rails_curation", "qa_rails_dag"} <= names

    def test_leaves_unrelated_numbers_alone(self):
        new, _ = SYNC.apply_to_readme(FRESH, text=SAMPLE)
        assert "img.shields.io/github/v/release/Glad-Labs/poindexter" in new
        assert "Python 3.13+ and 8 GB+ VRAM." in new
        assert "**OAuth 2.1 throughout**" in new

    def test_claim_is_floored_not_exact(self):
        """17,238 must publish as "17,000+", never as the exact count: the
        claim has to stay true if the sync ever stops running."""
        new, _ = SYNC.apply_to_readme(FRESH, text=SAMPLE)
        assert "17,238" not in new
        assert "17,000+" in new

    def test_is_idempotent_on_already_fresh_text(self):
        once, _ = SYNC.apply_to_readme(FRESH, text=SAMPLE)
        twice, changes = SYNC.apply_to_readme(FRESH, text=once)
        assert twice == once
        assert changes == []

    def test_dead_anchor_is_reported_not_silently_skipped(self):
        """A copy edit that drops "unit tests passing in CI" must surface,
        not freeze the bullet at whatever it last said (#2832)."""
        reworded = SAMPLE.replace(
            "11,400+ unit tests passing in CI", "a large suite running in CI"
        )
        _, changes = SYNC.apply_to_readme(FRESH, text=reworded)
        warnings = [c for c in changes if c.startswith("WARNING:")]
        assert len(warnings) == 1
        assert "test_functions_status_bullet" in warnings[0]

    def test_every_anchor_matches_the_real_checked_in_readme(self):
        """Acceptance: the patterns work against the file they ship for."""
        _, changes = SYNC.apply_to_readme(SYNC.collect_stats())
        assert not [c for c in changes if c.startswith("WARNING:")]
