"""qa.self_claim — deterministic our-own-system claim verification (#1007).

The three prod instances this rail exists for (2026-08-09, two drafts at
Q94–95): an invented retrieval mechanism, invented quality scores ("a Q of
85 or 87" when real scores are 70 and 94–98), and a version number two
releases stale. The deterministic layers cover instances 2 and 3 outright;
the acceptance regressions are equally load-bearing — another product's
version numbers and claim-free dev-diary prose must NOT fire.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.modules.content.atoms import qa_self_claim as atom

pytestmark = pytest.mark.unit


def _sc(**over: str) -> Any:
    values = {
        "qa_self_claim_enabled": "true",
        "qa_self_claim_product_names": "poindexter",
        "site_name": "Glad Labs",
        "qa_self_claim_offender_penalty": "25",
    }
    values.update(over)
    return SimpleNamespace(get=lambda key, default="": values.get(key, default))


def _pool(qscores: list[int] | None = None, keys: list[str] | None = None):
    conn = AsyncMock()

    async def _fetch(query, *args):
        if "quality_score" in query:
            return [{"q": q} for q in (qscores or [])]
        return [{"key": k} for k in (keys or [])]

    conn.fetch = AsyncMock(side_effect=_fetch)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _patch_gates(monkeypatch):
    async def _states(_qa):
        return {"self_claim": (True, False)}

    monkeypatch.setattr(atom, "resolve_gate_states", _states)
    monkeypatch.setattr(
        "poindexter.modules.content.multi_model_qa.MultiModelQA.__init__",
        lambda self, **kw: None,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestSelfReferenceGate:
    def test_product_name_in_topic_fires(self):
        assert atom.is_self_referential("body", "Why Poindexter uses atoms", ["poindexter"])

    def test_first_person_system_prose_fires(self):
        assert atom.is_self_referential(
            "Last week we rewired the pipeline to defer rejects.", "", ["zzz"],
        )

    def test_unrelated_product_review_does_not_fire(self):
        assert not atom.is_self_referential(
            "Next.js 16 shipped the app router. It changed everything for React.",
            "Next.js review", ["poindexter", "glad labs"],
        )


class TestVersionClaims:
    def test_our_stale_version_is_extracted(self):
        content = "Poindexter is currently at release v0.116.0 and climbing."
        assert atom.extract_our_version_claims(content, ["poindexter"]) == ["0.116.0"]

    def test_another_products_version_is_out_of_scope(self):
        """The acceptance regression: a post citing someone else's release
        must never be judged against OUR version."""
        content = (
            "The library shipped Next.js version 15.3.1 with turbopack "
            "defaults, a notable release for the ecosystem."
        )
        assert atom.extract_our_version_claims(content, ["poindexter"]) == []

    def test_we_context_counts_as_ours(self):
        content = "We tagged release v0.99.0 last night after the fix landed."
        assert atom.extract_our_version_claims(content, ["poindexter"]) == ["0.99.0"]


class TestQscoreClaims:
    def test_issue_instance_two_shapes_extract(self):
        content = "you'll see entries sitting at a Q of 85 or a quality score of 87"
        assert atom.extract_qscore_claims(content) == [85, 87]

    def test_invented_scores_flag_against_real_distribution(self):
        offenders = atom.check_qscores_against([85, 87], {70, 94, 95, 96, 97, 98})
        assert len(offenders) == 2
        assert "Q85" in offenders[0]

    def test_real_scores_within_tolerance_pass(self):
        assert atom.check_qscores_against([94, 71], {70, 94, 98}) == []

    def test_empty_distribution_never_flags(self):
        """No real data → no verdict (a fresh install must not flag)."""
        assert atom.check_qscores_against([85], set()) == []


class TestSettingsTokens:
    def test_settings_shaped_tokens_extract(self):
        content = "flip `rag_rerank_enabled` and tune `qa_final_score_threshold`."
        assert atom.extract_settings_tokens(content) == [
            "rag_rerank_enabled", "qa_final_score_threshold",
        ]

    def test_ordinary_code_identifiers_are_out_of_scope(self):
        content = "call `resolve_gate_states` and read `pipeline_tasks` rows"
        assert atom.extract_settings_tokens(content) == []


class TestPaths:
    def test_real_package_path_passes(self):
        assert atom.check_paths(["services/rag_engine.py"]) == []

    def test_canonical_package_path_passes(self):
        assert atom.check_paths(["poindexter/services/rag_engine.py"]) == []

    def test_invented_path_flags(self):
        offenders = atom.check_paths(["services/entity_overlap_check.py"])
        assert len(offenders) == 1
        assert "entity_overlap_check" in offenders[0]


def test_current_package_version_reads_pyproject():
    v = atom.current_package_version()
    assert v and v.count(".") == 2


# ---------------------------------------------------------------------------
# run() — the atom end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRun:
    async def test_not_self_referential_returns_nothing(self, monkeypatch):
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": "A deep dive into PostgreSQL vacuum internals.",
            "topic": "PostgreSQL",
            "site_config": _sc(),
        })
        assert out == {}

    async def test_claim_free_self_prose_appends_no_review(self, monkeypatch):
        """Issue acceptance: dev-diary-shaped prose ABOUT the pipeline with
        nothing falsifiable must not fire — and must not emit a vacuous 100."""
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": "This week we tuned our pipeline's pacing and mood.",
            "topic": "dev diary",
            "site_config": _sc(),
        })
        assert out == {}

    async def test_stale_version_claim_is_an_offender(self, monkeypatch):
        _patch_gates(monkeypatch)
        monkeypatch.setattr(atom, "current_package_version", lambda root=None: "0.119.0")
        out = await atom.run({
            "content": "Poindexter is currently at release v0.116.0.",
            "topic": "The Poindexter Philosophy",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["reviewer"] == "self_claim"
        # Advisory-first: the gate config flips approved back to True and
        # tags the feedback — the OFFENDER is the signal, the veto is not.
        assert review["advisory"] is True
        assert review["feedback"].startswith("[advisory]")
        assert "v0.116.0" in review["feedback"]
        assert "0.119.0" in review["feedback"]

    async def test_current_version_claim_passes_with_review(self, monkeypatch):
        _patch_gates(monkeypatch)
        monkeypatch.setattr(atom, "current_package_version", lambda root=None: "0.119.0")
        out = await atom.run({
            "content": "Poindexter is currently at release v0.119.0.",
            "topic": "release notes",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is True
        assert review["score"] == 100.0

    async def test_issue_instance_invented_qscores_flag(self, monkeypatch):
        """Instance 2 verbatim: 'a Q of 85 or 87' against a queue whose real
        scores are 70 and 94–98."""
        _patch_gates(monkeypatch)
        monkeypatch.setattr(atom, "current_package_version", lambda root=None: None)
        out = await atom.run({
            "content": (
                "Open our queue and you'll see entries sitting at a Q of 85 "
                "or a quality score of 87 before they're approved."
            ),
            "topic": "The Poindexter Philosophy",
            "site_config": _sc(),
            "database_service": SimpleNamespace(
                pool=_pool(qscores=[70, 94, 95, 96, 97, 98]),
            ),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["advisory"] is True
        assert review["feedback"].count("quality-score claim") == 2
        # 2 offenders x 25 penalty
        assert review["score"] == 50.0

    async def test_nonexistent_settings_key_flags(self, monkeypatch):
        _patch_gates(monkeypatch)
        monkeypatch.setattr(atom, "current_package_version", lambda root=None: None)
        out = await atom.run({
            "content": "We shipped it behind `retrieval_overlap_check_enabled` in our pipeline.",
            "topic": "poindexter internals",
            "site_config": _sc(),
            "database_service": SimpleNamespace(pool=_pool(keys=[])),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["advisory"] is True
        assert "retrieval_overlap_check_enabled" in review["feedback"]

    async def test_master_switch_off_is_silent(self, monkeypatch):
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": "Poindexter is currently at release v0.0.1.",
            "topic": "poindexter",
            "site_config": _sc(qa_self_claim_enabled="false"),
        })
        assert out == {}

    async def test_db_failure_skips_db_layers_never_fakes(self, monkeypatch):
        """A dead pool drops layers 2–3 (reduced coverage); the version layer
        still verifies, and nothing invents a verdict for the skipped ones."""
        _patch_gates(monkeypatch)
        monkeypatch.setattr(atom, "current_package_version", lambda root=None: "0.119.0")
        bad_pool = MagicMock()
        bad_pool.acquire = MagicMock(side_effect=RuntimeError("pool dead"))
        out = await atom.run({
            "content": (
                "Poindexter is currently at release v0.116.0, with entries "
                "at a Q of 85 in our queue."
            ),
            "topic": "poindexter",
            "site_config": _sc(),
            "database_service": SimpleNamespace(pool=bad_pool),
        })
        (review,) = out["qa_rail_reviews"]
        # Only the version offender — the Q-claim was skipped, not judged.
        assert review["feedback"].count("version claim") == 1
        assert "quality-score claim" not in review["feedback"]


# ---------------------------------------------------------------------------
# layers 5-6: named capabilities + install specs vs the operating record
# ---------------------------------------------------------------------------

from poindexter.services.operating_record import GpuFact, OperatingRecord  # noqa: E402


def _record(**over):
    base = dict(
        known_names=frozenset({"ollama", "poindexter", "google search console", "claude"}),
        ram_gb=60.5,
        gpus=(GpuFact("rtx 5090", 32.0), GpuFact("rtx 3090", 24.0)),
    )
    base.update(over)
    return OperatingRecord(**base)


class TestCapabilityClaims:
    def test_run_class_verbs_extract_capitalised_names(self):
        text = ("We also run Jettison, which takes a single sentence and turns it into assets. "
                "We use Google Search Console data. We rely on Ollama on GPU 0. "
                "Our Poindexter pipeline is the thing.")
        assert atom.extract_capability_claims(text) == ["Jettison", "Google Search Console", "Ollama"]

    def test_build_class_verbs_and_lowercase_are_not_extracted(self):
        assert atom.extract_capability_claims("We built Presenter Personas last week and we use a small script.") == []

    def test_unknown_name_is_an_offender_known_ones_are_not(self):
        offenders = atom.check_capabilities(["Jettison", "Ollama", "Claude Sonnet"], _record())
        assert len(offenders) == 1 and '"Jettison"' in offenders[0]


class TestInstallSpecs:
    def test_specs_only_count_in_our_context(self):
        ours = "Our Poindexter box is a self-hosted RTX 5090 with 128GB of system RAM and 32GB of VRAM."
        theirs = "The reviewer tested on an RTX 4090 with 24GB of VRAM and 64GB of system RAM."
        assert atom.extract_install_specs(ours, ["poindexter"]) == [("ram_gb", "128"), ("vram_gb", "32"), ("gpu", "RTX 5090")]
        assert atom.extract_install_specs(theirs, ["poindexter"]) == []

    def test_checks_against_the_record(self):
        offenders, checked = atom.check_install_specs(
            [("ram_gb", "128"), ("ram_gb", "64"), ("vram_gb", "32"), ("vram_gb", "56"), ("vram_gb", "40"),
             ("gpu", "RTX 5090"), ("gpu", "RTX 4090")], _record(),
        )
        assert checked is True
        assert any("128GB of system RAM" in o for o in offenders)
        assert any("40GB of VRAM" in o for o in offenders)
        assert any("RTX 4090" in o for o in offenders)
        assert not any(o.startswith(("claims 64GB", "claims 32GB", "claims 56GB", "names a RTX 5090")) for o in offenders)

    def test_unknown_facts_are_skipped_not_judged(self):
        offenders, checked = atom.check_install_specs([("gpu", "RTX 4090"), ("vram_gb", "48")], _record(gpus=()))
        assert offenders == [] and checked is False


def _patch_required_gate(monkeypatch):
    """qa_gates.self_claim.required_to_pass=true — the posture since migration
    20260915_014128: a failing review stays approved=False and vetoes."""
    async def _states(_qa):
        return {"self_claim": (True, True)}
    monkeypatch.setattr(atom, "resolve_gate_states", _states)
    monkeypatch.setattr(
        "poindexter.modules.content.multi_model_qa.MultiModelQA.__init__",
        lambda self, **kw: None,
    )


class TestRunWithOperatingRecord:
    @pytest.mark.asyncio
    async def test_jettison_vetoes_when_the_gate_is_required(self, monkeypatch):
        _patch_required_gate(monkeypatch)
        monkeypatch.setattr(
            "poindexter.services.operating_record.load_operating_record",
            AsyncMock(return_value=_record()),
        )
        content = ("At Glad Labs, our content pipeline runs on Poindexter. "
                   "We also run Jettison, which takes a single sentence and turns it into campaign assets.")
        out = await atom.run({"content": content, "topic": "zero-click", "site_config": _sc()})
        review = out["qa_rail_reviews"][0]
        assert review["approved"] is False and "Jettison" in review["feedback"]
        assert review.get("advisory") is False

    @pytest.mark.asyncio
    async def test_jettison_is_advisory_when_the_gate_is_demoted(self, monkeypatch):
        _patch_gates(monkeypatch)  # required_to_pass=false (the poindexter#454 lever)
        monkeypatch.setattr(
            "poindexter.services.operating_record.load_operating_record",
            AsyncMock(return_value=_record()),
        )
        content = "Our Poindexter pipeline is fine. We also run Jettison for campaigns."
        out = await atom.run({"content": content, "topic": "t", "site_config": _sc()})
        review = out["qa_rail_reviews"][0]
        assert review["advisory"] is True and "Jettison" in review["feedback"]

    @pytest.mark.asyncio
    async def test_true_capabilities_and_specs_pass(self, monkeypatch):
        _patch_gates(monkeypatch)
        monkeypatch.setattr(
            "poindexter.services.operating_record.load_operating_record",
            AsyncMock(return_value=_record()),
        )
        content = ("Our Poindexter pipeline runs on a self-hosted RTX 5090 with 64GB of system RAM, "
                   "and we rely on Ollama for local inference.")
        out = await atom.run({"content": content, "topic": "local llms", "site_config": _sc()})
        review = out["qa_rail_reviews"][0]
        assert review["approved"] is True and review["score"] == 100.0

    @pytest.mark.asyncio
    async def test_record_failure_skips_the_layers_without_a_verdict(self, monkeypatch):
        _patch_gates(monkeypatch)
        monkeypatch.setattr(
            "poindexter.services.operating_record.load_operating_record",
            AsyncMock(side_effect=RuntimeError("no host")),
        )
        content = "Our Poindexter pipeline runs on Poindexter. We also run Jettison for campaigns."
        out = await atom.run({"content": content, "topic": "t", "site_config": _sc()})
        assert out == {}  # nothing checkable → no review, never a fake pass
