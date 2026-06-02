"""Gap analysis for the weekly issue-triage sweep.

Pure over a list of issue dicts (as returned by `gh issue list --json
number,title,labels,milestone,body`). For each issue it reports which of the
four triage axes are missing and pre-computes the one axis the sweep may apply
without judgment: the content-derived `type`. `area` is left to the reasoning
caller (apply-if-cited); `priority`/`milestone` are always surfaced.
"""
from __future__ import annotations

from typing import Any

from services.triage.derive_labels import derive_type

PRIORITIES = ("P0-critical", "P1-high", "P2-medium", "P3-low")
TYPES = ("bug", "feature", "enhancement", "improvement", "chore",
         "security", "tech-debt", "documentation", "question", "testing")
AREAS = ("backend", "frontend", "testing", "infra", "monitoring",
         "pipeline", "monetization")


def _names(issue: dict[str, Any]) -> set[str]:
    return {lbl.get("name") for lbl in (issue.get("labels") or [])}


def find_gaps(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one gap-report per issue that is missing ANY triage axis."""
    out: list[dict[str, Any]] = []
    for issue in issues:
        names = _names(issue)
        missing_priority = not (names & set(PRIORITIES))
        missing_type = not (names & set(TYPES))
        missing_area = not (names & set(AREAS))
        missing_milestone = not issue.get("milestone")
        if not (missing_priority or missing_type or missing_area or missing_milestone):
            continue
        out.append({
            "number": issue.get("number"),
            "title": issue.get("title", ""),
            "body_excerpt": (issue.get("body") or "")[:600],
            "missing_priority": missing_priority,
            "missing_type": missing_type,
            "missing_area": missing_area,
            "missing_milestone": missing_milestone,
            "derived_type": derive_type(issue.get("title", "")) if missing_type else None,
        })
    return out
