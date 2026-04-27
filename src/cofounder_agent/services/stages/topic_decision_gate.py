"""TopicDecisionGateStage — topic-decision HITL gate (#146).

Wires the generic :class:`services.stages.approval_gate.ApprovalGateStage`
to a topic-decision-shaped artifact and registers itself under the stable
Stage name ``"topic_decision_gate"`` so the StageRunner can pick it up
from the configured order list.

The Stage is **inert by default** — the underlying ApprovalGateStage
checks ``pipeline_gate_topic_decision`` in app_settings and returns a
passthrough StageResult when it isn't ``"on"``. Adding this Stage to the
default pipeline order doesn't change behaviour for existing operators
until they explicitly flip the gate.

Artifact shape (per #146):

.. code:: python

    {
        "topic": "...",
        "primary_keyword": "...",
        "tags": ["...", ...],
        "category_suggestion": "...",
        "source": "anticipation_engine" | "manual" | ...,
        "research_summary": "<= ~200 words from research_context, or omitted",
        "score_signals": {
            "novelty": float | None,
            "internal_link_potential": float | None,
            "category_balance": float | str | None,
        },
    }

When ``research_context`` isn't on the context yet (i.e. the gate is
running before research) the ``research_summary`` field is omitted so
the operator can still approve a topic ahead of any LLM cycles burning.

Why a wrapper Stage rather than registering ApprovalGateStage twice
with different config: the Stage Runner keys stages by their ``.name``
attribute. Registering ``ApprovalGateStage`` directly only gives one
``"approval_gate"`` slot, which we'd burn on the first gate that
wants it. A thin per-gate wrapper Class with a stable ``.name`` keeps
multiple gates additive — ``"preview_approval_gate"``,
``"final_media_gate"``, etc. follow the same pattern when they ship.
"""

from __future__ import annotations

import re
from typing import Any

from plugins.stage import StageResult
from services.logger_config import get_logger
from services.stages.approval_gate import ApprovalGateStage

logger = get_logger(__name__)


# Word-count budget for the research summary embedded in the artifact.
# The operator scans the artifact on Telegram + the CLI; longer than
# ~200 words and the Telegram preview gets truncated and the CLI table
# row becomes a wall of text. Tunable via app_settings should the
# operator want a deeper preview later.
_RESEARCH_SUMMARY_MAX_WORDS = 200


def _summarize_research(research_context: Any) -> str:
    """Truncate ``research_context`` to roughly 200 words.

    Accepts the raw research-context blob in whatever shape the
    pipeline happens to surface it (``str``, ``None``, or a structured
    dict from ``research_service`` follow-ups). Returns a clean string
    with whitespace collapsed and a trailing ``"..."`` when the source
    overshoots the budget. Empty / unusable input → empty string so
    the artifact builder can decide whether to omit the field.
    """
    if not research_context:
        return ""

    if isinstance(research_context, dict):
        # Common research-service shapes: {"summary": "..."} or
        # {"text": "..."} or {"sources": [...]}; fall back to a stringy
        # rendering when nothing obvious is there. Avoid leaking raw
        # source URLs into the artifact — those go in the dashboard,
        # not in the operator preview.
        text = (
            research_context.get("summary")
            or research_context.get("text")
            or research_context.get("research_summary")
            or ""
        )
        if not text and isinstance(research_context.get("sources"), list):
            # Last resort: join source titles for at least *some* signal.
            titles = [
                str(s.get("title") or "").strip()
                for s in research_context["sources"]
                if isinstance(s, dict)
            ]
            text = " — ".join(t for t in titles if t)
        research_context = text

    if not isinstance(research_context, str):
        research_context = str(research_context)

    # Collapse whitespace runs so the word-count is deterministic and
    # the Telegram preview doesn't have stray newlines mid-paragraph.
    cleaned = re.sub(r"\s+", " ", research_context).strip()
    if not cleaned:
        return ""

    words = cleaned.split(" ")
    if len(words) <= _RESEARCH_SUMMARY_MAX_WORDS:
        return cleaned
    return " ".join(words[:_RESEARCH_SUMMARY_MAX_WORDS]).rstrip(",.;:") + "..."


def _coerce_tags(value: Any) -> list[str]:
    """Tags arrive as list, comma-string, or None depending on caller."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(t).strip() for t in value if str(t).strip()]
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(value).strip()] if str(value).strip() else []


def build_topic_decision_artifact(context: dict[str, Any]) -> dict[str, Any]:
    """Build the artifact dict the operator reviews for the topic-decision gate.

    Pure function so the unit tests can call it directly without
    instantiating the Stage. Missing fields are handled gracefully:

    - ``primary_keyword`` falls back to the first tag, then to ``""``.
    - ``tags`` defaults to ``[]``.
    - ``category_suggestion`` falls back to the empty string.
    - ``source`` defaults to ``"anticipation_engine"`` (the system's
      most common origin for auto-discovered topics).
    - ``research_summary`` is OMITTED when there's no research context
      yet — the operator should be able to approve a topic up-front
      without research having run.

    The score_signals subdict is always present so the operator UI can
    rely on the key existing; values default to ``None`` when the
    upstream signal hasn't been computed yet.
    """
    tags = _coerce_tags(context.get("tags"))
    primary_keyword = (
        context.get("primary_keyword")
        or (tags[0] if tags else "")
        or ""
    )

    artifact: dict[str, Any] = {
        "topic": context.get("topic", "") or "",
        "primary_keyword": primary_keyword,
        "tags": tags,
        "category_suggestion": context.get("category", "") or "",
        "source": context.get("topic_source", "anticipation_engine") or "anticipation_engine",
        "score_signals": {
            "novelty": context.get("novelty_score"),
            "internal_link_potential": context.get("internal_link_score"),
            "category_balance": context.get("category_balance"),
        },
    }

    research_summary = _summarize_research(context.get("research_context"))
    if research_summary:
        # Omit the field entirely when empty — keeps the operator UI
        # from rendering a "Research summary: (empty)" placeholder.
        artifact["research_summary"] = research_summary

    return artifact


class TopicDecisionGateStage:
    """Topic-decision HITL gate Stage.

    Thin wrapper around :class:`ApprovalGateStage` that bakes in:

    - ``gate_name = "topic_decision"``
    - the topic-shaped ``artifact_fn``
    - the stable Stage name ``"topic_decision_gate"`` (distinct from
      the inner Stage's ``"approval_gate"`` name so the runner can
      register both side-by-side and route by order).

    The wrapper preserves the inert-by-default behaviour: when
    ``pipeline_gate_topic_decision`` is unset / off, the inner Stage's
    own enable check returns a passthrough StageResult and the pipeline
    runs unchanged.
    """

    name = "topic_decision_gate"
    description = "Pause the pipeline pending human approval of a proposed topic"
    timeout_seconds = 30
    halts_on_failure = True

    def __init__(self) -> None:
        # Reuse the generic gate Stage — single instance per wrapper is
        # fine since ApprovalGateStage holds no per-call state.
        self._inner = ApprovalGateStage()

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        # Operators can override the gate slug or the artifact_fn via
        # PluginConfig if they really want to, but the defaults are
        # what #146 ships. Per-stage PluginConfig still threads through
        # so ``halts_on_failure`` / ``timeout_seconds`` overrides work.
        merged_config: dict[str, Any] = {
            "gate_name": "topic_decision",
            "artifact_fn": build_topic_decision_artifact,
        }
        if config:
            # Caller-supplied config wins for everything except
            # gate_name — locking that down here means a misconfigured
            # PluginConfig can't accidentally point this Stage at a
            # different gate.
            for k, v in config.items():
                if k == "gate_name":
                    continue
                merged_config[k] = v

        return await self._inner.execute(context, merged_config)


__all__ = [
    "TopicDecisionGateStage",
    "build_topic_decision_artifact",
    "_summarize_research",
]
