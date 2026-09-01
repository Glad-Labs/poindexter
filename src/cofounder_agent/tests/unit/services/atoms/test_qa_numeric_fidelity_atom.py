"""qa.numeric_fidelity — arithmetic source-grounding rail.

The catch this rail exists for (measured over 40 published posts, 2026-09-01):
a draft whose HEADLINE statistic — "110,000 papers" — appears nowhere in the
research corpus it was written from, waved through by every other rail.

The skip regressions are equally load-bearing. This rail must append NO review
rather than a fake verdict when it has nothing to judge: no research corpus
(42% of canonical_blog runs), a corpus too thin to mean anything, or a draft
with no attributed numbers at all. A rail that scores 100 on absent evidence
is worse than an absent rail, because the dashboard reads it as coverage.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms import qa_numeric_fidelity as atom

pytestmark = pytest.mark.unit


def _sc(**over: str) -> Any:
    values = {
        "qa_numeric_fidelity_enabled": "true",
        "qa_numeric_fidelity_offender_penalty": "25",
        "qa_numeric_fidelity_min_corpus_numbers": "3",
        "qa_numeric_fidelity_allow_derived": "true",
        "qa_numeric_fidelity_units": "",
        "qa_numeric_fidelity_attribution_markers": "",
    }
    values.update(over)
    return SimpleNamespace(get=lambda key, default="": values.get(key, default))


def _pool():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _patch_gates(monkeypatch, required: bool = False):
    async def _states(_qa):
        return {"numeric_fidelity": (True, required)}

    monkeypatch.setattr(atom, "resolve_gate_states", _states)
    monkeypatch.setattr(
        "modules.content.multi_model_qa.MultiModelQA.__init__",
        lambda self, **kw: None,
    )


def _state(content: str, corpus: str, **over: Any) -> dict[str, Any]:
    st: dict[str, Any] = {
        "content": content,
        "research_context": corpus,
        "site_config": _sc(),
        "pool": _pool(),
    }
    st.update(over)
    return st


class TestTheCatch:
    async def test_unsourced_headline_statistic_is_flagged(self, monkeypatch):
        _patch_gates(monkeypatch)
        out = await atom.run(_state(
            "According to the index, the archive covers 110,000 papers.",
            "the index holds 47 entries, 98 datasets and 26 models",
        ))
        review = out["qa_rail_reviews"][0]
        assert review["reviewer"] == "numeric_fidelity"
        assert review["score"] == 75.0          # 100 - one 25-point offender
        assert "110,000" in review["feedback"]

    async def test_a_sourced_figure_passes(self, monkeypatch):
        _patch_gates(monkeypatch)
        out = await atom.run(_state(
            "The survey found 53.7% admiration and 23,262 respondents.",
            "admiration 53.7 percent across 23,262 developers, 8 cohorts",
        ))
        review = out["qa_rail_reviews"][0]
        assert review["approved"] is True
        assert review["score"] == 100.0

    async def test_unattributed_numbers_never_fail_the_rail(self, monkeypatch):
        """An unattributed number is the author's framing, not a promise about
        someone else's data — scoring them flagged 33% of real prose."""
        _patch_gates(monkeypatch)
        out = await atom.run(_state(
            "We shipped 900 posts and ran 42 sweeps.",
            "unrelated corpus 1 2 3 4 5",
        ))
        assert out == {}


class TestSkipsRatherThanGuesses:
    async def test_no_research_context_appends_no_review(self, monkeypatch):
        """42% of canonical_blog runs. Verifying against nothing would either
        pass everything or fail everything; both are lies."""
        _patch_gates(monkeypatch)
        assert await atom.run(_state("The study found 90%.", "")) == {}

    async def test_thin_corpus_appends_no_review(self, monkeypatch):
        """With two numbers in scope, a miss says more about the research than
        about the draft."""
        _patch_gates(monkeypatch)
        out = await atom.run(_state("The study found 90%.", "only 1 and 2"))
        assert out == {}

    async def test_no_attributed_claims_appends_no_review(self, monkeypatch):
        _patch_gates(monkeypatch)
        out = await atom.run(_state("Prose with no numbers at all.", "1 2 3 4 5 6"))
        assert out == {}

    async def test_missing_content_or_site_config_is_safe(self):
        assert await atom.run({"content": "", "site_config": _sc()}) == {}
        assert await atom.run({"content": "x", "site_config": None}) == {}

    async def test_master_switch_off_short_circuits(self, monkeypatch):
        _patch_gates(monkeypatch)
        st = _state("The study found 90%.", "1 2 3 4 5")
        st["site_config"] = _sc(qa_numeric_fidelity_enabled="false")
        assert await atom.run(st) == {}

    async def test_a_crashed_check_emits_nothing_rather_than_a_verdict(self, monkeypatch):
        """Advisory rail: a bug of ours must not penalise the post, and must
        not manufacture a pass either. Absence shows on the dashboard."""
        _patch_gates(monkeypatch)
        monkeypatch.setattr(
            "services.numeric_fidelity.verify",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        out = await atom.run(_state("The study found 90%.", "1 2 3 4 5 6"))
        assert out == {}


class TestAdvisoryPosture:
    async def test_advisory_gate_config_neutralises_the_veto(self, monkeypatch):
        _patch_gates(monkeypatch, required=False)
        out = await atom.run(_state(
            "According to the index, 110,000 papers.",
            "47 entries, 98 datasets, 26 models",
        ))
        review = out["qa_rail_reviews"][0]
        assert review["advisory"] is True
        assert review["approved"] is True          # veto bit neutralised
        assert "[advisory]" in review["feedback"]

    async def test_graduation_restores_the_veto(self, monkeypatch):
        """required_to_pass=true is the operator's graduation lever."""
        _patch_gates(monkeypatch, required=True)
        out = await atom.run(_state(
            "According to the index, 110,000 papers.",
            "47 entries, 98 datasets, 26 models",
        ))
        review = out["qa_rail_reviews"][0]
        assert review["advisory"] is False
        assert review["approved"] is False

    async def test_provider_is_programmatic(self, monkeypatch):
        """Weights the rail at validator_weight in _qa_rail_common."""
        _patch_gates(monkeypatch)
        out = await atom.run(_state(
            "The survey found 90%.", "90 percent of 5 things, 1 2 3",
        ))
        assert out["qa_rail_reviews"][0]["provider"] == "programmatic"


class TestConfigIsDbDriven:
    async def test_penalty_is_tunable(self, monkeypatch):
        _patch_gates(monkeypatch)
        st = _state(
            "According to the index, 110,000 papers.",
            "47 entries, 98 datasets, 26 models",
        )
        st["site_config"] = _sc(qa_numeric_fidelity_offender_penalty="40")
        out = await atom.run(st)
        assert out["qa_rail_reviews"][0]["score"] == 60.0

    async def test_custom_attribution_markers_replace_the_defaults(self, monkeypatch):
        _patch_gates(monkeypatch)
        st = _state("Per our telemetry, 110,000 papers.", "47 entries, 98 sets, 26 models")
        st["site_config"] = _sc(qa_numeric_fidelity_attribution_markers="per our telemetry")
        out = await atom.run(st)
        assert "110,000" in out["qa_rail_reviews"][0]["feedback"]


class TestAtomContract:
    def test_declares_no_llm_tier_and_is_free(self):
        assert atom.ATOM_META.capability_tier is None
        assert atom.ATOM_META.cost_class == "free"

    def test_declares_its_channels(self):
        assert atom.ATOM_META.requires == ("content",)
        assert atom.ATOM_META.produces == ("qa_rail_reviews",)

    def test_imports_no_sibling_atom_or_stage(self):
        """Atom independence: an atom may import services/_helpers, never a
        sibling atom or a stage, or the graph stops being the whole truth."""
        import inspect

        src = inspect.getsource(atom)
        assert "modules.content.stages" not in src
        for line in src.splitlines():
            if line.startswith(("import ", "from ")) and "atoms." in line:
                assert "atoms._" in line, f"sibling-atom import: {line}"
