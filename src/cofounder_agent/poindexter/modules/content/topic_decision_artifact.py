"""The artifact an operator reviews at the topic-decision gate (#146).

``services.topic_proposal_service`` pauses a proposed topic at the
``topic_decision`` approval gate (when ``pipeline_gate_topic_decision`` is
on) with this artifact, reached through ``modules.content.api``.

This module was ``stages/topic_decision_gate.py``, which also held a
``TopicDecisionGateStage`` wrapper for the StageRunner. The StageRunner
was deleted in May 2026 and the Stage was never registered as an entry
point or placed in a graph, so the wrapper was removed (poindexter#643)
and the live builder moved here, out of the retiring stages layer.

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

When ``research_context`` isn't available yet (the gate runs before
research), ``research_summary`` is omitted so the operator can approve a
topic before any LLM cycles are spent.
"""

from __future__ import annotations

import re
from typing import Any

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

    Pure function. Missing fields are handled gracefully:

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


__all__ = [
    "build_topic_decision_artifact",
    "_summarize_research",
]
