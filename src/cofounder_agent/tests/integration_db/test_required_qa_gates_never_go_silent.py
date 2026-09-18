"""A ``required_to_pass`` QA gate must never be satisfiable only by silence.

The gap (poindexter#1060): ``missing_required_gates`` fails closed on a
required+enabled gate with no matching review, because it cannot tell "the rail
crashed" from "the rail ran and had nothing to judge". Two rails graduated to
required on 2026-09-15 are *designed* to be silent on most drafts —
``qa.self_claim`` (spoke on 4 of 171 passes over 45 days) and ``qa.freshness``
(0 reviews, ever) — so from that day every clean post was vetoed by
``missing_required:self_claim, missing_required:freshness`` at scores of
95.5-97.6 against a threshold of 80, with no rail having objected to anything.
``auto_publish_gate`` recorded ``pass`` up to 09-07 and never again.

Nothing caught it because the bug is a PAIRING, and the two halves live apart:

* *this rail may be silent* is a fact about code, pinned by green unit tests
  that asserted ``await run(...) == {}`` — correct about the rail in isolation;
* *this gate is required* is a fact about a seeded ``qa_gates`` row, set by a
  migration whose own docstring argued the silence made the promotion **safe**.

Neither half looks wrong alone. This test is the join: it reads the REAL
seeded-and-migrated ``qa_gates`` and runs each required rail against a draft
that gives it nothing to judge, asserting a review comes back. The correct
answer is ``not_applicable_review`` — scoreless, so an evergreen post still
pays nothing for a rail it does not need, but PRESENT, so the required gate is
satisfied honestly rather than by absence.

Scope: the deterministic rails, i.e. those declaring ``capability_tier=None``.
A model-backed rail also goes silent — on timeout or an unparseable completion
— but that is the fail-open contract for a rail that COULD NOT run, already
covered by the poindexter#1012 retry and ``RERUNNABLE_GATES``, and it needs a
live model this tier does not have. The discriminator is the atom's own
metadata, so a new deterministic rail is covered the day it is written.
"""

from __future__ import annotations

import pytest

from poindexter.modules.content.atoms._qa_rail_common import missing_required_gates
from poindexter.services.atom_registry import discover, get_atom_callable, get_atom_meta

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

# gate name (qa_gates.name) -> the atom that produces its review. There is no
# derivable map — ``qa_gates_db_writer._REVIEWER_TO_GATE`` goes the other way,
# from reviewer to gate — so it is spelled out here and the coverage test below
# makes it impossible to forget: a newly-required gate that is missing from
# this dict FAILS rather than being silently skipped.
_GATE_TO_ATOM = {
    "citation_verifier": "qa.citations",
    "consistency": "qa.consistency",
    "content_originality": "qa.content_originality",
    "freshness": "qa.freshness",
    "llm_critic": "qa.critic",
    "numeric_fidelity": "qa.numeric_fidelity",
    "person_mention": "qa.person_mention",
    "programmatic_validator": "qa.programmatic",
    "ragas_eval": "qa.ragas",
    "self_claim": "qa.self_claim",
    "self_consistency": "qa.self_consistency",
    "title_coherence": "qa.title_coherence",
    "topic_delivery": "qa.topic_delivery",
    "unlinked_attribution": "qa.unlinked_attribution",
    "vision_gate": "qa.vision",
    "web_factcheck": "qa.web_factcheck",
}

# A draft engineered to give every deterministic rail nothing to judge: no
# claim about this system, no moment-anchoring phrase, no external URL, no
# named-source attribution, no attributed figure. This is the SHAPE of an
# ordinary evergreen post — the common case, not an edge case.
_NOTHING_TO_JUDGE = (
    "# Vacuum Internals\n\n"
    "Postgres reclaims dead tuples in the background. Understanding when it "
    "runs, and what it chooses to skip, explains most of the surprises people "
    "hit with table bloat on a busy write path.\n\n"
    "## Why bloat accumulates\n\n"
    "An update writes a new row version and leaves the old one behind. Until "
    "the old version is reclaimed it still occupies a page, so a table can "
    "grow even when the number of live rows does not.\n"
)


class _Config:
    """Minimal SiteConfig stand-in: DB-backed values are not what is under
    test here, so every rail sees its own defaults."""

    def __init__(self, pool) -> None:
        self._pool = pool

    def get(self, key: str, default=None):
        return default

    async def get_secret(self, key: str, default=None):
        return default


async def _required_enabled_gates(test_pool) -> list[str]:
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name FROM qa_gates "
            "WHERE required_to_pass IS TRUE AND enabled IS TRUE ORDER BY name"
        )
    return [r["name"] for r in rows]


def _deterministic(gate: str) -> bool:
    """Rails with no ``capability_tier`` need no model, so CI can run them."""
    atom = _GATE_TO_ATOM.get(gate)
    if atom is None:
        return False
    meta = get_atom_meta(atom)
    return meta is not None and meta.capability_tier is None


async def test_every_required_gate_maps_to_a_known_atom(test_pool):
    """The allowlist above cannot rot into a silent skip.

    Graduating a gate to ``required_to_pass`` without registering its atom
    here fails this test, which is the moment to ask the question the 09-15
    migrations did not: *what does this rail do when it has nothing to say?*
    """
    discover()
    required = await _required_enabled_gates(test_pool)
    assert required, "no required qa_gates found — the seed did not load"
    unmapped = [g for g in required if g not in _GATE_TO_ATOM]
    assert not unmapped, (
        f"required qa_gates with no atom registered in _GATE_TO_ATOM: {unmapped}. "
        "Add the mapping, then make sure the rail emits a not_applicable review "
        "when it has nothing to judge — a required rail that answers with "
        "silence hard-vetoes every clean post (poindexter#1060)."
    )


async def test_required_deterministic_rails_emit_on_a_nothing_to_judge_draft(
    test_pool,
):
    """The join that was missing: required gate x silent rail.

    Runs each required deterministic rail against an evergreen draft and
    asserts it produced a review. A rail that returns ``{}`` here is the exact
    defect — ``qa.aggregate`` would read it as an absent required gate and
    reject a post no rail objected to.
    """
    discover()
    required = await _required_enabled_gates(test_pool)
    exercised = [g for g in required if _deterministic(g)]
    assert exercised, (
        "no deterministic required rails to exercise — either the seed did not "
        "load or _GATE_TO_ATOM has drifted from the atom registry"
    )

    state = {
        "content": _NOTHING_TO_JUDGE,
        "title": "Vacuum Internals",
        "seo_title": "Vacuum Internals",
        "topic": "postgres vacuum internals",
        "research_context": "",
        "task_id": "00000000-0000-0000-0000-000000000000",
        "site_config": _Config(test_pool),
    }

    silent: list[str] = []
    reviews: list[dict] = []
    for gate in exercised:
        out = await get_atom_callable(_GATE_TO_ATOM[gate])(dict(state))
        rail_reviews = (out or {}).get("qa_rail_reviews") or []
        if not rail_reviews:
            silent.append(gate)
        reviews.extend(rail_reviews)

    assert not silent, (
        f"required rail(s) produced NO review for a draft with nothing to "
        f"judge: {silent}. Return not_applicable_review(...) instead of {{}} — "
        "scoreless, so it cannot inflate the mean, but present, so the gate is "
        "satisfied honestly rather than by absence (poindexter#1060)."
    )

    # …and the join itself: the aggregate's fail-closed guard must now be quiet.
    gate_states = dict.fromkeys(exercised, (True, True))
    assert missing_required_gates(reviews, gate_states) == []


async def test_a_genuinely_absent_rail_is_still_a_veto(test_pool):
    """The fix NARROWS the fail-closed guard; it must not remove it.

    A rail that could not run at all (crash, timeout, dead dependency) still
    has to veto — that is what stops a vacuous pass when QA did not happen.
    """
    required = await _required_enabled_gates(test_pool)
    gate_states = dict.fromkeys(required, (True, True))
    assert sorted(missing_required_gates([], gate_states)) == sorted(required)
