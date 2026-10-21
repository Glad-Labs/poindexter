"""``latest_qa_pass``: one task's latest QA pass, ordered for reading."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from poindexter.services.task_qa_results import latest_qa_pass


def _row(details):
    return {"details": details, "timestamp": datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)}


def _details(**over):
    base = {
        "approved": False,
        "final_score": 68.8,
        "approval_threshold": 80,
        "reviewer_count": 4,
        "gating_rail_count": 2,
        "advisory_rail_count": 2,
        "qa_all_rail_score": 74.0,
        "terminal": True,
        "qa_rewrite_attempts": 1,
        "reviews": [
            {"reviewer": "zeta_rail", "score": 100, "approved": True, "advisory": False},
            {"reviewer": "deepeval_g_eval", "score": 100, "approved": True, "advisory": True,
             "feedback_preview": "[advisory] (passed, not required_to_pass) fine"},
            {"reviewer": "citation_verifier", "score": 0, "approved": False, "advisory": False},
            {"reviewer": "person_mention", "score": 75, "approved": True, "advisory": True,
             "feedback_preview": "[advisory] (failed, not required_to_pass) names Matt"},
            {"reviewer": "a_web_factcheck", "score": 0, "approved": True, "advisory": False,
             "not_applicable": True},
            {"reviewer": "b_freshness", "score": 0, "approved": True, "advisory": False},
        ],
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_none_when_the_task_has_no_qa_pass():
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    assert await latest_qa_pass(pool, "abc") is None


@pytest.mark.asyncio
async def test_reads_the_newest_row_for_the_task():
    pool = AsyncMock()
    pool.fetchrow.return_value = _row(_details())
    await latest_qa_pass(pool, "task-1")
    sql, task_id = pool.fetchrow.call_args.args
    assert "event_type = 'qa_pass_completed'" in sql
    assert "ORDER BY \"timestamp\" DESC" in sql and "LIMIT 1" in sql
    assert task_id == "task-1"


@pytest.mark.asyncio
async def test_summary_and_reading_order():
    pool = AsyncMock()
    pool.fetchrow.return_value = _row(json.dumps(_details()))  # asyncpg may hand back text
    qa = await latest_qa_pass(pool, "task-1")
    assert qa is not None
    assert qa["approved"] is False
    assert qa["final_score"] == 68.8 and qa["approval_threshold"] == 80
    assert qa["gating_rail_count"] == 2 and qa["advisory_rail_count"] == 2
    assert qa["recorded_at"].startswith("2026-10-21T12:00")
    # Blocking checks first: failures, passes, then the ones with nothing to
    # judge (flagged, or an approved 0 on a row from before the flag). Then
    # advisory, the failed one (recorded approved, flagged in its note) first.
    assert [r["reviewer"] for r in qa["reviews"]] == [
        "citation_verifier", "zeta_rail", "a_web_factcheck", "b_freshness",
        "person_mention", "deepeval_g_eval",
    ]
    assert qa["reviews"][2]["not_applicable"] is True
    assert qa["reviews"][3]["not_applicable"] is None


@pytest.mark.asyncio
async def test_a_row_of_the_wrong_shape_fails_loudly():
    pool = AsyncMock()
    pool.fetchrow.return_value = _row({"reviews": "not a list"})
    with pytest.raises(ValidationError):
        await latest_qa_pass(pool, "task-1")
