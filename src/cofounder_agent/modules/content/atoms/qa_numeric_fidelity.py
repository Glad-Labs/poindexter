"""qa.numeric_fidelity — does every sourced number reconcile with the research?

The other anti-hallucination rails are judgements: a regex that recognises
fabrication shapes, or an LLM asked whether the draft is faithful. This one is
arithmetic. The writer is handed a research corpus; a number the draft
presents as **sourced fact** either reconciles with a number in that corpus or
it does not, and no opinion is involved.

Scope is narrow on purpose, and the boundary was measured rather than guessed.
Run over 40 real published posts:

- 80 checkable numbers extracted, **29 attributed** (presented as sourced
  fact), 27 of those reconciled — 26 exactly, 1 via a recorded derivation.
- 2 flagged. One was a genuine catch: a post whose *headline* statistic
  ("110,000 papers") appears nowhere in the research corpus it was written
  from. One was a false positive: "a 2,000-engineer org" — a hypothetical
  quantity sitting in a sentence that happened to contain "dataset".

An earlier version scored **every** checkable number against the corpus and
flagged 33%, all of them wrong (a hypothetical in quotes, a rhetorical "$10K
MRR" target, and a claim about our own repo — which is ``qa.self_claim``'s
corpus, not this one). Digit-bearing sentences in ordinary prose are mostly
years, image URLs and figures of speech. Hence the attribution gate: an
unattributed number is the author's framing, an attributed one is a promise
about someone else's data, and only the second kind can fail here.

**Known false-positive mode**, kept rather than over-tuned away: a hypothetical
quantity inside a sentence that carries an attribution word. Two examples do
not justify a heuristic that would cost real catches, and advisory-first means
an operator sees it before it can ever block a post.

A draft with no attributed numeric claims appends **no review at all** (same
contract as ``qa.self_claim``), and so does a task whose ``research_context``
is empty — 42% of canonical_blog runs, where there is simply no ground truth
to check against. A skipped rail is reduced coverage, never a fake verdict.

Advisory-first: seeded ``qa_gates.numeric_fidelity.required_to_pass=false`` so
it scores and surfaces offenders on the QA Rails dashboard without vetoing
until an operator graduates it. Master switch ``qa_numeric_fidelity_enabled``.

Chain position: after ``qa.citations``, before ``qa.unlinked_attribution`` —
beside the other source-grounding rails, and after citation repair has run.
"""

from __future__ import annotations

import logging
from typing import Any

from modules.content.atoms._pool import resolve_pool
from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.numeric_fidelity",
    type="atom",
    version="1.0.0",
    description=(
        "Deterministic check that every ATTRIBUTED number in the draft "
        "reconciles with a number in the research corpus (exact at the "
        "written precision, or a recorded derivation). No LLM. "
        "Advisory-first (DB-driven via qa_gates.numeric_fidelity)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(
            name="research_context",
            type="str",
            description="research corpus the writer was grounded on",
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="numeric fidelity review result",
        ),
    ),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,  # deterministic — no LLM tier
    cost_class="free",
    idempotent=True,
    side_effects=(),
    parallelizable=True,
)

_RAIL = "numeric_fidelity"
_DEFAULT_PENALTY = 25.0
_DEFAULT_MIN_CORPUS_NUMBERS = 3


def _cfg(site_config: Any, key: str, default: Any) -> Any:
    try:
        value = site_config.get(key, default)
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional tuning knob; the documented default keeps the
        # advisory rail running rather than silently disarming it.
        return default
    return default if value in (None, "") else value


def _csv(raw: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw]
    else:
        items = [p.strip() for p in str(raw or "").split(",")]
    cleaned = tuple(i for i in items if i)
    return cleaned or fallback


def _feedback(result: Any, penalty_count: int) -> str:
    if penalty_count == 0:
        return (
            f"all {result.checkable} attributed number(s) reconcile with the "
            f"research corpus ({result.corpus_numbers} source values)"
        )
    lines = [
        f"{penalty_count} attributed number(s) reconcile with no value in the "
        f"research corpus ({result.corpus_numbers} source values):",
    ]
    for verdict in result.unsupported[:5]:
        lines.append(f"- {verdict.claim.describe()}")
    return " ".join(lines)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    if str(_cfg(site_config, "qa_numeric_fidelity_enabled", "true")).lower() not in (
        "true", "1", "yes",
    ):
        return {}

    corpus = (state.get("research_context") or "").strip()
    if not corpus:
        # No ground truth on this task (42% of canonical_blog runs). Verifying
        # against nothing would either pass everything or fail everything;
        # both are lies. Append no review — the rail's ABSENCE from the pass is
        # the honest signal, and the QA Rails board tracks rail presence.
        logger.info("[qa.numeric_fidelity] no research_context — rail skipped")
        return {}

    from services.numeric_fidelity import (
        DEFAULT_ATTRIBUTION_MARKERS,
        DEFAULT_UNITS,
        verify,
    )

    try:
        penalty = float(_cfg(site_config, "qa_numeric_fidelity_offender_penalty", _DEFAULT_PENALTY))
    except (TypeError, ValueError):
        penalty = _DEFAULT_PENALTY
    try:
        min_corpus = int(_cfg(site_config, "qa_numeric_fidelity_min_corpus_numbers", _DEFAULT_MIN_CORPUS_NUMBERS))
    except (TypeError, ValueError):
        min_corpus = _DEFAULT_MIN_CORPUS_NUMBERS

    units = _csv(_cfg(site_config, "qa_numeric_fidelity_units", ""), DEFAULT_UNITS)
    markers = _csv(
        _cfg(site_config, "qa_numeric_fidelity_attribution_markers", ""),
        DEFAULT_ATTRIBUTION_MARKERS,
    )
    allow_derived = str(
        _cfg(site_config, "qa_numeric_fidelity_allow_derived", "false"),
    ).lower() in ("true", "1", "yes")

    from modules.content.multi_model_qa import MultiModelQA, ReviewerResult

    try:
        result = verify(
            content,
            [corpus],
            units=units,
            markers=markers,
            allow_derived=allow_derived,
        )
    except Exception as exc:  # noqa: BLE001
        # Fail SOFT, unlike qa.programmatic: this rail is advisory and a
        # crashed heuristic must not manufacture a verdict either way. A
        # failing-but-visible review would penalise the post for our bug, so
        # emit nothing and let the rail's absence show on the dashboard.
        logger.warning("[qa.numeric_fidelity] verify failed: %s: %s", type(exc).__name__, exc)
        return {}

    # A thin corpus makes "unsupported" meaningless — with three numbers in
    # scope, absence of a match says more about the research than the draft.
    if result.corpus_numbers < min_corpus or result.checkable == 0:
        logger.info(
            "[qa.numeric_fidelity] nothing to judge (corpus=%d, attributed=%d) — rail skipped",
            result.corpus_numbers, result.checkable,
        )
        return {}

    offenders = len(result.unsupported)
    review = ReviewerResult(
        reviewer=_RAIL,
        approved=offenders == 0,
        score=max(0.0, 100.0 - penalty * offenders),
        feedback=_feedback(result, offenders),
        provider="programmatic",
    )

    pool = resolve_pool(state, atom="qa.numeric_fidelity")
    qa = MultiModelQA(
        pool=pool,
        settings_service=state.get("settings_service"),
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, _RAIL)
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
