"""``atoms.review_with_critic`` — single-LLM critic review atom.

Phase 3 of the dynamic-pipeline-composition spec. Replaces one of the
several critic invocations bundled inside the legacy
:mod:`services.cross_model_qa` stage so the architect-LLM can compose
critic chains explicitly:

- "review with cheap_critic, then aggregate"
- "review with two budget_critics in parallel, aggregate, halt if
  any reject"

Granularity rationale: cross_model_qa today fans out to N critics in
one stage, which means the operator can't a/b test "what if I swap
critic 2 for a different model" without forking the stage. With this
atom, the architect can compose:

  draft_section → review_with_critic (model=A)
                → review_with_critic (model=B)
                → aggregate_reviews
                → halt_or_continue

…and the router can independently bias each critic slot from
capability_outcomes feedback.

Output shape:

The atom emits a ``Review`` dict (matches the existing
``services.cross_model_qa.Review`` shape so legacy code that downstream
reads ``state["qa_reviews"]`` still works once cross_model_qa is
atom-grain-refactored). Multiple invocations APPEND to ``qa_reviews``;
the aggregator atom reads the full list and decides pass/fail.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#362.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)


_REVIEW_SYSTEM_PROMPT = """\
You are an editorial critic reviewing a draft article for a tech audience.
Your job is to surface concrete, fixable issues — not to rewrite the post.

Score the draft 0-100 on these axes:

- factual_accuracy: are claims well-supported, not fabricated?
- voice: does the tone feel human / professional / non-spammy?
- clarity: is the writing easy to follow and free of fluff?
- structure: does the post have logical flow?

Then list up to 3 SPECIFIC issues (each one sentence) the writer
should fix, OR an empty list if the draft is good as-is.

Return ONLY a JSON object — no prose, no markdown fences:

{
  "factual_accuracy": <0-100>,
  "voice": <0-100>,
  "clarity": <0-100>,
  "structure": <0-100>,
  "overall": <0-100>,
  "verdict": "approve" | "revise" | "reject",
  "issues": ["<issue 1>", "<issue 2>", ...]
}

A draft passing all four axes at >=70 is "approve". 50-69 is "revise"
(specific fixes). <50 is "reject" (fundamental problems).
"""


ATOM_META = AtomMeta(
    name="atoms.review_with_critic",
    type="atom",
    version="1.0.0",
    description=(
        "Single-LLM critic review of a draft. Returns a Review dict with "
        "axis scores + verdict + specific issues. Append-friendly: each "
        "invocation pushes a Review onto state['qa_reviews']."
    ),
    inputs=(
        FieldSpec(
            name="content", type="str",
            description="The draft text to review.",
            required=True,
        ),
        FieldSpec(
            name="title", type="str",
            description="Headline — surfaced to the critic for context.",
            required=False,
        ),
        FieldSpec(
            name="critic_role", type="str",
            description=(
                "Optional label distinguishing this critic from siblings "
                "in the same template (e.g. 'voice_critic', 'fact_critic'). "
                "Stored on the Review for aggregation."
            ),
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="qa_reviews", type="list[Review]",
            description="Appended-to list of Review dicts (one per critic).",
        ),
    ),
    requires=("content",),
    produces=("qa_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(
        max_attempts=2, backoff_s=3.0,
        retry_on=("httpx.ReadTimeout", "httpx.ConnectError", "json.JSONDecodeError"),
    ),
    fallback=("cheap_critic", "budget_critic", "free_critic"),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Atom entry point.

    Reads ``state['content']`` (mandatory), optionally ``state['title']``
    and ``state['critic_role']``. Resolves the local model to use via
    ``critic_model_override`` on state, then ``pipeline_critic_model``
    setting, then writer-model fallback.

    Returns a state delta with ``qa_reviews`` set to the appended list
    (LangGraph state merge semantics will replace, not concat — so we
    return the full list including any prior reviews).
    """
    from services.llm_text import ollama_chat_text

    content = (state.get("content") or "").strip()
    if not content:
        logger.info("[atoms.review_with_critic] empty content — skipping")
        return {}

    title = (state.get("title") or "").strip()
    critic_role = (state.get("critic_role") or "").strip()

    # Model resolution: explicit > role-based setting > default critic > writer fallback
    from services.site_config import site_config
    model = (
        state.get("critic_model_override")
        or (
            site_config.get(f"pipeline_critic_model_{critic_role}")
            if critic_role else None
        )
        or site_config.get("pipeline_critic_model")
        or site_config.get("pipeline_writer_model", "glm-4.7-5090:latest")
        or "glm-4.7-5090:latest"
    )

    user_prompt = (
        f"TITLE: {title}\n\n" if title else ""
    ) + f"DRAFT:\n\n{content}"

    try:
        raw = await ollama_chat_text(
            user_prompt,
            model=model,
            timeout_setting="pipeline_critic_timeout_seconds",
            timeout_default=90.0,
            system=_REVIEW_SYSTEM_PROMPT,
        )
    except Exception as exc:
        logger.exception("[atoms.review_with_critic] ollama call failed: %s", exc)
        return {}

    review = _parse_review(raw)
    if review is None:
        logger.warning(
            "[atoms.review_with_critic] could not parse review JSON; raw: %s",
            raw[:200],
        )
        return {}

    review["critic_role"] = critic_role or "default"
    review["model_used"] = model
    review["raw_response"] = raw[:2000]

    prior = list(state.get("qa_reviews") or [])
    prior.append(review)
    return {"qa_reviews": prior}


def _parse_review(raw: str) -> dict[str, Any] | None:
    """Tolerantly extract the Review JSON from the model's output.

    Handles markdown-fence wrapping, leading/trailing prose, etc. —
    same forgiving-parser shape as the architect's spec parser.
    """
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    # Normalize the verdict field — some models emit lowercase, others
    # title case, others abbreviate.
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in ("approve", "revise", "reject"):
        # Infer from overall if missing.
        try:
            overall = float(obj.get("overall", 0))
        except (TypeError, ValueError):
            overall = 0.0
        verdict = (
            "approve" if overall >= 70 else "revise" if overall >= 50 else "reject"
        )
    obj["verdict"] = verdict
    return obj


__all__ = ["ATOM_META", "run"]
