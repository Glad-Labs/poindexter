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
    async def test_not_self_referential_gets_a_scoreless_na_pass(self, monkeypatch):
        """The path that vetoed 6 of 6 clean posts at 95.5-97.6.

        self_claim emitted a review on 4 of 171 passes over 45 days. Once its
        gate went required_to_pass on 2026-09-15, the other 167 became
        ``missing_required:self_claim`` — a veto naming a rail that had
        objected to nothing (poindexter#1060). Scoreless, so an evergreen
        post still pays nothing for a rail it does not need.
        """
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": "A deep dive into PostgreSQL vacuum internals.",
            "topic": "PostgreSQL",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["reviewer"] == "self_claim"
        assert review["not_applicable"] is True
        assert review["approved"] is True and review["score"] == 0.0
        assert "no claims about this system" in review["feedback"]

    async def test_claim_free_self_prose_does_not_fire(self, monkeypatch):
        """Issue acceptance: dev-diary-shaped prose ABOUT the pipeline with
        nothing falsifiable must not fire — and must not emit a vacuous 100.

        ``not_applicable`` delivers both (it is dropped from the gating mean
        AND from ``qa_all_rail_score``) while still satisfying the required
        gate honestly instead of by absence.
        """
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": "This week we tuned our pipeline's pacing and mood.",
            "topic": "dev diary",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["not_applicable"] is True and review["score"] == 0.0
        assert "asserts nothing falsifiable" in review["feedback"]

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

    async def test_master_switch_off_is_na_not_silence(self, monkeypatch):
        # qa_self_claim_enabled says whether the rail RUNS; required_to_pass
        # says whether it GATES. Silence here lets the off switch hard-reject
        # every post, so the two levers would fight.
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": "Poindexter is currently at release v0.0.1.",
            "topic": "poindexter",
            "site_config": _sc(qa_self_claim_enabled="false"),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["not_applicable"] is True and review["approved"] is True
        assert "qa_self_claim_enabled=false" in review["feedback"]

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
        # Not a fake PASS and not silence either (poindexter#1060): self_claim
        # is required_to_pass, so `{}` would hard-veto a clean post over a
        # blipped record lookup — the #1012 failure mode. N/A is scoreless,
        # claims nothing, and names the gap.
        (review,) = out["qa_rail_reviews"]
        assert review["not_applicable"] is True and review["score"] == 0.0
        assert "reduced coverage" in review["feedback"]
        assert "operating record unavailable" in review["feedback"]


# ---------------------------------------------------------------------------
# Layer 7 — first-person biography (poindexter#1055)
#
# The draft that earned this layer (task 4a23f39e) reached awaiting_approval at
# Q94 with the critic scoring it 98 and no rail objecting, having invented the
# founder's childhood, a named 1997 side project with source filenames, and a
# Glad Labs teaching project. Excerpts below are from that draft.
# ---------------------------------------------------------------------------

_FABRICATION = (
    "My own dad ran almost the identical experiment on me around the same "
    "year, minus the flair of relocating to another chair. I was ten, maybe "
    "eleven. It was 1997 or 1998, the same stretch of my childhood when I was "
    "also teaching myself to program on a hand-me-down 486."
)


def test_biography_layer_catches_the_1055_fabrication():
    claims = atom.extract_biography_claims(_FABRICATION)
    kinds = {k for k, _ in claims}
    assert {"family", "childhood", "age"} <= kinds, kinds
    # With no declared founder facts every one of them is unsourced.
    assert len(atom.check_biography(claims, "")) == len(claims) >= 3


def test_research_context_cannot_ground_a_claim_about_the_author():
    """The #1055 trap: the task's corpus was an article about SOMEONE ELSE's
    father, so it was full of the word "dad". Grounding on the corpus would
    have passed the exact fabrication this layer exists to catch — only
    qa_self_claim_founder_facts may license a first-person claim, and
    check_biography takes no corpus argument at all."""
    claims = atom.extract_biography_claims("My dad taught me to debug by hand.")
    assert claims
    someone_elses_story = "the author's dad taught him chess on a rainy afternoon"
    assert atom.check_biography(claims, someone_elses_story)


def test_declared_founder_facts_ground_a_matching_claim():
    claims = atom.extract_biography_claims(
        "My dad taught me chess on a hand-me-down board in 1997."
    )
    facts = "matt's dad taught him chess on a hand-me-down board in 1997"
    assert atom.check_biography(claims, facts) == []


def test_a_single_shared_word_does_not_license_an_anecdote():
    """"dad" appearing in the declared facts must not ground an unrelated
    invented anecdote about one."""
    claims = atom.extract_biography_claims(
        "My dad shipped a genetic algorithm called ColorGA on a 486 in 1997."
    )
    assert atom.check_biography(claims, "matt's dad is a retired electrician")


@pytest.mark.parametrize(
    "text",
    [
        # Measured against 207 published posts (1.3M chars): zero fired.
        "We measured 10,240 MiB held ~6.5h after the last render.",
        "We shipped 30 PRs and 28 notable commits today.",
        "We run LiteLLM as the provider layer now.",
        "The model I was testing came out 18% faster on single-request latency.",
        "Throughput was 10x faster than the baseline; I was 30% off in my estimate.",
        # Third person is the correct way to retell a source's personal story.
        "The author's dad ran the identical experiment on him that year.",
        "His childhood was spent programming on a hand-me-down 486.",
    ],
)
def test_biography_layer_does_not_fire_on_ordinary_prose(text):
    assert atom.extract_biography_claims(text) == []


async def test_biography_runs_outside_the_self_reference_gate(monkeypatch):
    """An invented childhood is ungrounded whether or not the draft is about
    our own stack — the essay that earned the layer barely named the product."""
    _patch_gates(monkeypatch)
    topic = "a childhood story"
    assert not atom.is_self_referential(_FABRICATION, topic, ["poindexter"])

    result = await atom.run(
        {"content": _FABRICATION, "topic": topic, "site_config": _sc()}
    )
    reviews = result.get("qa_rail_reviews") or []
    assert len(reviews) == 1
    assert "first-person" in reviews[0]["feedback"]


def _patch_gates_required(monkeypatch):
    """qa_gates.self_claim.required_to_pass=True — the live prod shape since
    2026-09-15, where an offender really does veto."""
    async def _states(_qa):
        return {"self_claim": (True, True)}

    monkeypatch.setattr(atom, "resolve_gate_states", _states)
    monkeypatch.setattr(
        "poindexter.modules.content.multi_model_qa.MultiModelQA.__init__",
        lambda self, **kw: None,
    )


async def test_advisory_mode_names_the_claim_without_vetoing(monkeypatch):
    """Advisory offenders ride in their own bucket: they move the score and
    the operator's read, but never the pass/fail bit — so graduating the layer
    is a settings change, not a deploy."""
    _patch_gates_required(monkeypatch)
    result = await atom.run(
        {"content": _FABRICATION, "topic": "childhood", "site_config": _sc()}
    )
    review = result["qa_rail_reviews"][0]
    assert review["approved"] is True, "advisory offenders must not flip the veto"
    assert review["score"] < 100
    assert "advisory" in review["feedback"]
    assert "False self-claims" not in review["feedback"]


async def test_enforcing_mode_vetoes(monkeypatch):
    _patch_gates_required(monkeypatch)
    result = await atom.run(
        {
            "content": _FABRICATION,
            "topic": "childhood",
            "site_config": _sc(qa_self_claim_biography_mode="enforcing"),
        }
    )
    review = result["qa_rail_reviews"][0]
    assert review["approved"] is False
    assert review["feedback"].startswith("False self-claims")


async def test_off_mode_skips_the_layer(monkeypatch):
    _patch_gates(monkeypatch)
    result = await atom.run(
        {
            "content": _FABRICATION,
            "topic": "childhood",
            "site_config": _sc(qa_self_claim_biography_mode="off"),
        }
    )
    (review,) = result["qa_rail_reviews"]
    assert review["not_applicable"] is True and review["approved"] is True


async def test_declared_founder_facts_silence_the_layer_end_to_end(monkeypatch):
    _patch_gates(monkeypatch)
    result = await atom.run(
        {
            "content": "My dad taught me chess on a hand-me-down board in 1997.",
            "topic": "childhood",
            "site_config": _sc(
                qa_self_claim_founder_facts=(
                    "matt's dad taught him chess on a hand-me-down board in 1997"
                ),
            ),
        }
    )
    review = result["qa_rail_reviews"][0]
    # The rail RAN and the claim resolved, so it records an honest pass rather
    # than vanishing — the distinction poindexter#1051 is about.
    assert review["approved"] is True
    assert review["score"] == 100
    assert "first-person" not in review["feedback"]


# ---------------------------------------------------------------------------
# Layer 8 — conducted-experiment claims (poindexter#1050 / #1052)
#
# "We ran the audit properly. Ollama came out about 18% faster on
# single-request latency than vLLM" reached the queue at Q97.8 — twice, because
# the reject-with-retry regenerated the same story. No such audit happened.
#
# Neither neighbour owned it: qa.numeric_fidelity scores only numbers presented
# as SOURCED fact and defers our-own claims to this rail, while this rail did
# record-resolution and no record enumerates "experiments we ran".
#
# The GOOD cases below are real sentences from published posts. They are the
# reason the scope is narrow: three wider groundedness detectors were measured
# against the corpus and every one of them flagged these as fabrications.
# ---------------------------------------------------------------------------

_AUDIT_FABRICATION = (
    "We went through this exact decision internally. We ran the audit properly. "
    "Ollama came out about 18% faster on single-request latency than vLLM, "
    "against roughly 50 calls a day total."
)

# A corpus with none of the claimed figures in it.
_UNRELATED_CORPUS = (
    "Local inference tooling has matured through 2026. Practitioners weigh "
    "throughput against operational complexity, and the guides disagree about "
    "which layer belongs in a small deployment. Community discussion continues "
    "across forums and vendor documentation alike, with no consensus yet."
)


class TestExperimentClaimExtraction:
    def test_claim_and_figure_may_sit_in_adjacent_sentences(self):
        """The draft said "We ran the audit properly." and put the 18% in the
        NEXT sentence, which carries no first-person marker at all — a
        sentence-scoped detector misses it entirely."""
        claims = atom.extract_experiment_claims(_AUDIT_FABRICATION)
        assert claims
        figures = {f for _, figs in claims for f in figs}
        assert "18" in figures

    def test_named_experiment_fires_without_a_comparison_word(self):
        claims = atom.extract_experiment_claims(
            "We ran a bake-off across three providers. The winner finished in "
            "4.2 seconds."
        )
        assert claims

    def test_measuring_verb_alone_is_not_enough(self):
        """The weaker frame on its own matches ordinary dev-diary reporting.
        Measured: it fired on 38 of 41 first-person result sentences in the
        corpus, every one of them true."""
        assert atom.extract_experiment_claims(
            "Even after a successful render evening, we measured 10,240 MiB "
            "held ~6.5h after the last render."
        ) == []

    def test_one_fabrication_is_reported_once_despite_overlapping_windows(self):
        """Windows overlap, so a single invented benchmark sits inside several.
        Reporting each is not just noisy — every duplicate costs another
        penalty, so one claim could zero the score by itself."""
        text = (
            "My own dad ran almost the identical experiment on me around the same year. "
            "I was ten, maybe eleven. We ran the audit properly. Ollama came out about "
            "18% faster on single-request latency than vLLM, against roughly 50 calls a day."
        )
        assert len(atom.extract_experiment_claims(text)) == 1

    def test_distinct_experiments_with_different_figures_both_report(self):
        """De-dup is on the FIGURES, not on position — positional skipping was
        tried first and merged two genuinely separate experiments into one."""
        text = (
            "We ran a bake-off across three providers. The winner finished in 4.2 seconds. "
            + "Filler sentence about something unrelated entirely. " * 4
            + "Later we benchmarked the encoder and it came out 9.7x faster than the old one."
        )
        claims = atom.extract_experiment_claims(text)
        assert len(claims) == 2
        assert {f for _, figs in claims for f in figs} == {"4.2", "9.7"}

    def test_years_are_not_measurements(self):
        assert atom.extract_figures("we benchmarked it in 2026 and it was 2x faster") == ["2"]

    @pytest.mark.parametrize(
        "text",
        [
            # Every one of these is a real sentence from a published post.
            "We found that every CORS preflight OPTIONS request was returning a 500 (PR #3006).",
            "We run LiteLLM as the provider layer now, and we're working it toward the default path.",
            "Our stored post embeddings turned out to be built from the title plus roughly the first 500 characters.",
            "When we ran title-based similarity checks against a new draft, it scored a comfortable 0.55 against an existing post.",
            "We shipped 30 PRs and 28 notable commits today.",
            "GlitchTip issue 736 paged us 13 times over five days before we found the real fault line.",
            "On the ops side, we triaged a GlitchTip backlog of 4,252 events and found only about 9 needed human eyes.",
            # Uncheckable by construction, and deliberately out of scope: the
            # published corpus carries "We went through this exact realization
            # building our own content pipeline", which is fine.
            "We went through this exact realization building our own content pipeline.",
        ],
    )
    def test_true_first_person_prose_does_not_fire(self, text):
        assert atom.extract_experiment_claims(text) == []


class TestFigureReconciliation:
    def test_figure_present_in_corpus_is_grounded(self):
        assert atom.figure_in_corpus("18", "the vendor reports an 18% gap")

    def test_rounding_for_prose_is_not_fabrication(self):
        """Same rule as qa.numeric_fidelity: round(source, decimals_written)."""
        assert atom.figure_in_corpus("2.3", "measured at 2.34x throughput")

    def test_absent_figure_is_not_grounded(self):
        assert not atom.figure_in_corpus("18", "no such number appears here")

    def test_offender_names_the_missing_figures(self):
        claims = atom.extract_experiment_claims(_AUDIT_FABRICATION)
        offenders = atom.check_experiment_claims(claims, _UNRELATED_CORPUS)
        assert offenders and "18" in offenders[0]


class TestExperimentLayerThroughRun:
    async def test_fabricated_benchmark_is_reported(self, monkeypatch):
        _patch_gates_required(monkeypatch)
        out = await atom.run({
            "content": _AUDIT_FABRICATION,
            "topic": "local inference engines",
            "research_context": _UNRELATED_CORPUS,
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is True, "advisory by default"
        assert "conducting an experiment" in review["feedback"]

    async def test_enforcing_mode_vetoes(self, monkeypatch):
        _patch_gates_required(monkeypatch)
        out = await atom.run({
            "content": _AUDIT_FABRICATION,
            "topic": "local inference engines",
            "research_context": _UNRELATED_CORPUS,
            "site_config": _sc(qa_self_claim_experiment_mode="enforcing"),
        })
        assert out["qa_rail_reviews"][0]["approved"] is False

    async def test_a_thin_corpus_is_nothing_to_judge_not_all_fabricated(
        self, monkeypatch,
    ):
        """42% of runs carry no research_context. Without one every figure
        reads as invented, which would turn the rail into a generator of false
        vetoes on exactly the posts it has least evidence about."""
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": _AUDIT_FABRICATION,
            "topic": "local inference engines",
            "research_context": "",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["not_applicable"] is True and review["score"] == 0.0

    async def test_grounded_figures_do_not_fire(self, monkeypatch):
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": _AUDIT_FABRICATION,
            "topic": "local inference engines",
            "research_context": (
                "The published comparison puts the single-request latency gap at "
                "18% in favour of the lighter runtime, across a workload of "
                "roughly 50 calls a day in the deployments surveyed."
            ),
            "site_config": _sc(),
        })
        reviews = out.get("qa_rail_reviews") or []
        assert not reviews or "conducting an experiment" not in reviews[0]["feedback"]

    async def test_off_mode_skips_the_layer(self, monkeypatch):
        _patch_gates(monkeypatch)
        out = await atom.run({
            "content": _AUDIT_FABRICATION,
            "topic": "local inference engines",
            "research_context": _UNRELATED_CORPUS,
            "site_config": _sc(qa_self_claim_experiment_mode="off"),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["not_applicable"] is True and review["approved"] is True


# ---------------------------------------------------------------------------
# poindexter#1060 — a required rail may never answer with silence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rail_that_could_not_run_still_fails_closed(monkeypatch):
    # The N/A contract covers "ran, nothing to judge" ONLY. No draft at all is
    # a broken pipeline, and there the missing_required veto is correct.
    _patch_gates(monkeypatch)
    assert await atom.run({"content": "", "site_config": _sc()}) == {}
    assert await atom.run({"content": "x", "site_config": None}) == {}
