"""qa.rewrite — one bounded revision pass for a critic-vetoed draft.

Part of the canonical_blog QA rescue cycle. When qa.aggregate defers a
RESCUABLE reject (a soft LLM-critic veto, or a below-threshold score with no
hard veto — see _qa_rail_common.is_rescuable_reject; NEVER a fabrication or
gate veto), it emits ``_goto="qa_rewrite"`` and the branch router routes here
instead of halting.

This atom:
  1. Reads ``content`` + the failing critic feedback from ``qa_rail_reviews``.
  2. Calls the writer model with a targeted "revise to fix these issues" prompt.
  3. Returns the revised ``content``, increments ``qa_rewrite_attempts``, and
     emits the ``qa_rail_reviews`` reset sentinel ``[{"__reset__": True}]`` so
     the second QA pass starts from an empty review list (the _merge_rail_reviews
     reducer honors the sentinel). Without the reset, the stale first-pass veto
     would carry over and guarantee a re-reject.

A ``loop``-flagged edge (qa_rewrite -> qa_programmatic) re-runs the whole QA
block. The bound is the durable ``qa_rewrite_attempts`` counter: qa.aggregate
only rescues while ``attempts < qa_rewrite_max_attempts`` (default 1), so the
cycle runs at most N times — even across a kill-and-resume, because the counter
lives in the LangGraph postgres checkpoint.

Degrade-to-reject: if the writer errors or returns empty, keep the prior
content unchanged (omit the ``content`` key) and STILL increment the counter —
the next qa.aggregate pass sees ``attempts == max``, declines to rescue, and
the original reject stands. A finding is emitted for observability. The loop
always terminates.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.rewrite",
    type="atom",
    version="1.0.0",
    description=(
        "One bounded revision pass for a critic-vetoed draft; resets the QA "
        "review channel and increments the durable rescue-attempt counter."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="the vetoed draft"),
        FieldSpec(name="qa_rail_reviews", type="list[dict]",
                  description="failing reviews — source of the critic feedback"),
        FieldSpec(name="qa_rewrite_attempts", type="int",
                  description="prior rescue attempts"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="revised draft"),
        FieldSpec(name="qa_rewrite_attempts", type="int",
                  description="incremented attempt counter"),
        FieldSpec(name="qa_rail_reviews", type="list[dict]",
                  description="reset sentinel clearing stale first-pass reviews"),
    ),
    requires=("content", "qa_rail_reviews", "qa_rewrite_attempts"),
    produces=("content", "qa_rewrite_attempts", "qa_rail_reviews"),
    capability_tier="standard",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    parallelizable=False,
)

# Reset sentinel for the qa_rail_reviews reducer (services.template_runner.
# _merge_rail_reviews). Emitting this clears the stale first-pass reviews so
# the second QA pass scores the revised draft from scratch.
_REVIEW_RESET = [{"__reset__": True}]

_REVISE_PROMPT_KEY = "atoms.qa_rewrite.revise_prompt"

_REVISE_PROMPT_FALLBACK = """\
You are revising a draft article that an editorial critic flagged for specific, \
fixable issues. Apply ONLY the fixes the critic asked for. Preserve the \
article's structure, headings, length, links, citations, and voice. Do not add \
new sections or remove existing ones unless a fix requires it. Return the \
COMPLETE revised article in Markdown — body only, no preamble, no commentary, \
no JSON envelope.

CRITIC FEEDBACK TO ADDRESS:
{feedback}

ORIGINAL DRAFT:
{content}
"""


def _resolve_revise_prompt(*, content: str, feedback: str) -> str:
    """Pull the revise prompt via UnifiedPromptManager (Langfuse/DB override
    surface), falling back to the inline constant. Mirrors review_with_critic.
    Per feedback_prompts_must_be_db_configurable."""
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _REVISE_PROMPT_KEY, content=content, feedback=feedback,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[qa.rewrite] prompt lookup for %r failed (%s) — inline fallback",
            _REVISE_PROMPT_KEY, exc,
        )
        return _REVISE_PROMPT_FALLBACK.format(content=content, feedback=feedback)


def _failing_critic_feedback(reviews: list[dict[str, Any]]) -> str:
    """Collect the actionable feedback: non-advisory FAILING reviews only.
    Advisory rails and passing reviews carry no veto to fix."""
    notes = [
        str(r.get("feedback") or "").strip()
        for r in reviews
        if not r.get("approved")
        and not r.get("advisory")
        and str(r.get("feedback") or "").strip()
    ]
    if not notes:
        return "- (no specific feedback; tighten weak claims and improve clarity)"
    return "\n".join(f"- {n}" for n in notes)


def _emit_empty_finding(model: str) -> None:
    """Best-effort observability when the revise call yields nothing usable."""
    try:
        from utils.findings import emit_finding
        emit_finding(
            source="modules.content.atoms.qa_rewrite",
            kind="qa_rewrite_empty_revision",
            title=f"QA rescue revise model {model!r} returned empty — reject stands",
            body=(
                f"qa.rewrite called the writer ({model!r}) to revise a "
                f"critic-vetoed draft but got empty/failed output. The prior "
                f"draft was kept and the attempt counter burned, so the next "
                f"qa.aggregate pass declines to rescue and the original reject "
                f"stands. Verify writer-model health if this recurs."
            ),
            severity="warn",
            dedup_key=f"qa_rewrite_empty_revision:{model}",
            extra={"model": model},
        )
    except Exception:  # noqa: BLE001 — finding emission must never raise here
        # silent-ok: emitting the observability finding is itself best-effort
        pass


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from services.llm_text import ollama_chat_text, resolve_local_model

    content = (state.get("content") or "").strip()
    attempts = int(state.get("qa_rewrite_attempts") or 0)
    site_config = state.get("site_config")

    # Degrade-to-reject guard: nothing to work with → burn the attempt so the
    # loop terminates and the original reject stands. Reset reviews so the
    # re-run (it won't rescue again, but a re-run path stays clean) is empty.
    if not content or site_config is None:
        return {
            "qa_rewrite_attempts": attempts + 1,
            "qa_rail_reviews": list(_REVIEW_RESET),
        }

    reviews = state.get("qa_rail_reviews") or []
    feedback = _failing_critic_feedback(reviews)
    pool = getattr(state.get("database_service"), "pool", None)
    task_id = state.get("task_id")
    # model=None chains pipeline_writer_model → cost_tier.standard.model.
    model = resolve_local_model(model=None, site_config=site_config)
    revise_prompt = _resolve_revise_prompt(content=content, feedback=feedback)

    revised = ""
    try:
        raw = await ollama_chat_text(
            revise_prompt,
            model=model,
            site_config=site_config,
            pool=pool,
            # Reuse the orphaned cross_model_qa timeout setting (240s default).
            timeout_setting="content_router_qa_rewrite_timeout_seconds",
            timeout_default=240.0,
            task_id=task_id,
            phase="qa_rewrite",
        )
        revised = (raw or "").strip()
    except Exception as exc:  # noqa: BLE001 — a failed revise must not crash the graph
        logger.warning(
            "[qa.rewrite] revise call failed (%s) — keeping prior draft", exc,
        )
        revised = ""

    if not revised:
        _emit_empty_finding(model)
        return {
            "qa_rewrite_attempts": attempts + 1,
            "qa_rail_reviews": list(_REVIEW_RESET),
        }

    logger.info(
        "[qa.rewrite] revised draft for task=%s (attempt %d) — %d chars",
        str(task_id or "?")[:8], attempts + 1, len(revised),
    )
    return {
        "content": revised,
        "qa_rewrite_attempts": attempts + 1,
        "qa_rail_reviews": list(_REVIEW_RESET),
        # The revised draft is fresh — clear the #661 known_wrong_fact flag so
        # the second qa.programmatic pass re-derives it from the new content.
        "qa_known_wrong_fact_only": False,
    }


__all__ = ["ATOM_META", "run"]
