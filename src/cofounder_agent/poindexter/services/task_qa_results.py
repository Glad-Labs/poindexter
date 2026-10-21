"""A task's QA results: what each check said about its latest draft.

Every QA pass writes one ``qa_pass_completed`` row to ``audit_log`` with each
reviewer's verdict (``qa.aggregate`` and the dev_diary path, see
``services/audit_event_schemas.py``). The QA Rails board reads those rows in
aggregate; until 2026-10-21 nothing showed them for ONE task, so an operator
deciding on a draft saw a single quality score and not which check said what.
``poindexter tasks qa <id>`` and ``GET /api/tasks/{id}/qa`` read the latest row
through :func:`latest_qa_pass`.

The row is validated with the same model its producers write against, so a
shape change fails here loudly rather than printing a half-empty table.
"""

from __future__ import annotations

import json
from typing import Any

from poindexter.services.audit_event_schemas import QaPassCompletedDetails

_LATEST_PASS_SQL = """
    SELECT details, "timestamp"
      FROM audit_log
     WHERE event_type = 'qa_pass_completed'
       AND task_id = $1
     ORDER BY "timestamp" DESC
     LIMIT 1
"""


def _reading_rank(review: dict[str, Any]) -> int:
    """0 for a check that objected, 1 for a pass, 2 for one with nothing to judge.

    An advisory check that failed is sometimes recorded approved, with its note
    saying so, and it ranks with the objections. Rows from before
    ``not_applicable`` was stored read an approved 0 as nothing to judge.
    """
    note = str(review.get("feedback_preview") or "")
    if not review["approved"] or note.startswith("[advisory] (failed"):
        return 0
    na = review.get("not_applicable")
    if na is None:
        na = float(review.get("score") or 0) == 0
    return 2 if na else 1


async def latest_qa_pass(pool: Any, task_id: str) -> dict[str, Any] | None:
    """The task's most recent QA pass, or ``None`` when it has none yet.

    Shape: the summary fields (``approved``, ``final_score``,
    ``approval_threshold``, the rail counts), ``recorded_at``, and ``reviews``
    ordered for reading: the checks that can block a post first, then the
    advisory ones; within each, objections first and checks with nothing to
    judge last, then by name.
    """
    row = await pool.fetchrow(_LATEST_PASS_SQL, str(task_id))
    if row is None:
        return None
    raw = row["details"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    details = QaPassCompletedDetails.model_validate(raw)
    reviews = sorted(
        (r.model_dump() for r in details.reviews),
        key=lambda r: (bool(r["advisory"]), _reading_rank(r), str(r["reviewer"] or "")),
    )
    return {
        "task_id": str(task_id),
        "recorded_at": row["timestamp"].isoformat() if row["timestamp"] else None,
        "approved": details.approved,
        "final_score": details.final_score,
        "approval_threshold": details.approval_threshold,
        "terminal": details.terminal,
        "qa_rewrite_attempts": details.qa_rewrite_attempts,
        "gating_rail_count": details.gating_rail_count,
        "advisory_rail_count": details.advisory_rail_count,
        "qa_all_rail_score": details.qa_all_rail_score,
        "reviews": reviews,
    }


__all__ = ["latest_qa_pass"]
