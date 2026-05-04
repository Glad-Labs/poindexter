"""``atoms.run_validators`` — programmatic content validator atom.

Phase 3 of the dynamic-pipeline-composition spec. Wraps the existing
:func:`services.content_validator.validate_content` (regex + heuristic
quality rules, no LLM) so the architect-LLM can drop a programmatic
quality gate at any point in a composed graph.

Design notes (per ``feedback_design_for_llm_consumers``):

INPUTS / OUTPUTS are contract-shaped so the architect can chain
this atom without ambiguity:

- Reads ``content`` (REQUIRED), optionally ``title`` / ``topic`` /
  ``tags`` / ``niche`` from state.
- Writes ``validator_issues`` (list[dict] with severity/category/
  description/line_number) and ``validator_passed`` (bool) into
  state. Other atoms (atoms.aggregate_reviews, the rewrite loop,
  finalize_task) read these.
- Halts the graph when ``halt_on_critical=True`` (default) AND any
  critical issue is found. Operators can disable per-task to run
  the validator as advisory-only by passing
  ``halt_on_critical=False``.

Why a separate atom from atoms.review_with_critic:

The validator is deterministic, fast, free, and catches the
fabrication patterns regex-able without an LLM (fake names, fake
stats, made-up Glad Labs claims). Critics are LLM-priced and slower
but catch nuance regex can't. Composing them as separate atoms lets
the architect put validators FIRST (cheap halt) and critics SECOND
(expensive nuance) in production graphs — exactly the layering the
existing cross_model_qa stage uses internally, now exposed as
LangGraph nodes.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#362.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)


ATOM_META = AtomMeta(
    name="atoms.run_validators",
    type="atom",
    version="1.0.0",
    description=(
        "Run programmatic ContentValidator rules (regex + heuristics, "
        "NO LLM) against state.content. Surfaces issues by severity "
        "(critical/warning) + category. Halts on critical when "
        "halt_on_critical=true (default)."
    ),
    inputs=(
        FieldSpec(
            name="content", type="str",
            description="The draft text to validate.",
            required=True,
        ),
        FieldSpec(
            name="title", type="str",
            description="Headline — used in fabrication-pattern matching.",
            required=False,
        ),
        FieldSpec(
            name="topic", type="str",
            description="Topic context — used in topic-mismatch detection.",
            required=False,
        ),
        FieldSpec(
            name="tags", type="list[str]",
            description="Topic tags — strengthens topic-mismatch heuristics.",
            required=False,
        ),
        FieldSpec(
            name="niche", type="str",
            description=(
                "Niche slug. Validators with applies_to_niches "
                "scoping skip rules outside this niche."
            ),
            required=False,
        ),
        FieldSpec(
            name="halt_on_critical", type="bool",
            description=(
                "When true (default), sets _halt=True if any critical "
                "issue is found. Set false to run as advisory-only."
            ),
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="validator_passed", type="bool",
            description="True when no critical issues found.",
        ),
        FieldSpec(
            name="validator_issues", type="list[dict]",
            description=(
                "List of {severity, category, description, matched_text, "
                "line_number} per issue."
            ),
        ),
        FieldSpec(
            name="validator_critical_count", type="int",
            description="Count of critical-severity issues.",
        ),
        FieldSpec(
            name="validator_warning_count", type="int",
            description="Count of warning-severity issues.",
        ),
        FieldSpec(
            name="_halt", type="bool",
            description="Set when critical found and halt_on_critical=true.",
        ),
    ),
    requires=("content",),
    produces=(
        "validator_passed", "validator_issues",
        "validator_critical_count", "validator_warning_count",
    ),
    capability_tier=None,  # programmatic — no LLM tier
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    fallback=(),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Atom entry point.

    Calls into the existing content_validator without modification.
    Maps the dataclass result back to plain dicts so downstream atoms
    (and the LLM critics) read consistent JSON shapes.
    """
    from services.content_validator import validate_content

    content = (state.get("content") or "").strip()
    if not content:
        logger.info("[atoms.run_validators] empty content — skipping")
        return {
            "validator_passed": True,
            "validator_issues": [],
            "validator_critical_count": 0,
            "validator_warning_count": 0,
        }

    title = state.get("title") or ""
    topic = state.get("topic") or ""
    tags = state.get("tags") or []
    niche = state.get("niche")
    halt_on_critical = state.get("halt_on_critical")
    if halt_on_critical is None:
        halt_on_critical = True

    try:
        result = validate_content(
            title=title, content=content, topic=topic,
            tags=list(tags) if isinstance(tags, list) else [],
            niche=niche,
        )
    except Exception as exc:
        logger.exception("[atoms.run_validators] validate_content raised: %s", exc)
        return {
            "_halt": True,
            "_halt_reason": f"validator crashed: {type(exc).__name__}: {exc}",
            "validator_passed": False,
            "validator_issues": [],
            "validator_critical_count": 0,
            "validator_warning_count": 0,
        }

    issues_dicts = [
        {
            "severity": i.severity,
            "category": i.category,
            "description": i.description,
            "matched_text": i.matched_text,
            "line_number": i.line_number,
        }
        for i in result.issues
    ]

    out: dict[str, Any] = {
        "validator_passed": bool(result.passed),
        "validator_issues": issues_dicts,
        "validator_critical_count": result.critical_count,
        "validator_warning_count": result.warning_count,
    }
    if halt_on_critical and result.critical_count > 0:
        out["_halt"] = True
        out["_halt_reason"] = (
            f"validator found {result.critical_count} critical issue(s) — "
            f"first: {result.issues[0].description if result.issues else '?'}"
        )
    return out


__all__ = ["ATOM_META", "run"]
