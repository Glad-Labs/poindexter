"""
Unit tests for routes/approval_routes.py.

Tests cover:
- POST /api/tasks/{task_id}/reject        — reject_task
- GET  /api/tasks/pending-approval        — get_pending_approvals

NOTE: POST /api/tasks/{task_id}/approve is tested in test_task_publishing_routes.py
(the approve endpoint was moved there per issue #1335).
Bulk approve, bulk reject, and approval-status endpoints have been removed.

Auth and DB are overridden so no real I/O occurs.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.approval_routes import router
from tests.unit.routes.conftest import make_mock_db
from utils.route_utils import get_database_dependency


def _set_pool(mock_db, fetch_rows):
    """Attach a fake asyncpg pool so reject_task's ambiguity probe can run its
    ``task_id::text LIKE $1 || '%'`` lookup on the not-found path.

    ``conn.fetch`` returns ``fetch_rows`` (each ``{"id": <full task_id>}``).
    Only consulted when ``get_task`` returns None — a found task never touches
    the pool.
    """
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_rows)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=cm)
    mock_db.pool = pool
    return pool, conn

# ---------------------------------------------------------------------------
# App / client factory
# ---------------------------------------------------------------------------


def _build_app(mock_db=None) -> FastAPI:
    if mock_db is None:
        mock_db = make_mock_db()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


AWAITING_TASK = {
    "id": "task-001",
    "task_id": "task-001",
    "task_type": "blog_post",
    "status": "awaiting_approval",
    "topic": "AI Trends",
    "task_name": "Blog: AI Trends",
    "quality_score": 85.0,
    "metadata": {},
}

APPROVED_TASK = {
    **AWAITING_TASK,
    "status": "approved",
}

REJECTED_RETRY_TASK = {
    **AWAITING_TASK,
    "status": "rejected_retry",
}

REJECTED_FINAL_TASK = {
    **AWAITING_TASK,
    "status": "rejected_final",
}

FINAL_BODY = {
    "reason": "Duplicate topic",
    "feedback": "Topic already covered — close it out",
    "allow_revisions": False,
}

RETRY_BODY = {
    "reason": "Weak draft",
    "feedback": "Regenerate with more depth",
    "allow_revisions": True,
}


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/reject
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectTask:
    def test_reject_awaiting_task_returns_200(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/task-001/reject",
            json={
                "reason": "Content quality",
                "feedback": "Needs more depth",
                "allow_revisions": True,
            },
        )
        assert resp.status_code == 200

    def test_reject_with_revisions_sets_failed_revisions_status(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/task-001/reject",
            json={
                "reason": "Poor quality",
                "feedback": "Too short",
                "allow_revisions": True,
            },
        )
        data = resp.json()
        assert data["status"] == "rejected_retry"

    def test_reject_without_revisions_sets_rejected_final_status(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/task-001/reject",
            json={
                "reason": "Off-topic",
                "feedback": "Not relevant",
                "allow_revisions": False,
            },
        )
        data = resp.json()
        assert data["status"] == "rejected_final"

    def test_reject_clears_qa_approved_snapshot_marker(self):
        """Operator rejection is the explicit 'I don't want this content'
        signal — it must clear the pipeline_versions qa_approved_snapshot
        marker so the keep-best guard / stale-sweep promote bucket never
        resurrect the rejected draft on a later re-run."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        cleared: list[str] = []

        class _FakePipelineDB:
            def __init__(self, pool): ...

            async def clear_qa_approved_snapshot(self, task_id):
                cleared.append(task_id)

        with patch("services.pipeline_db.PipelineDB", _FakePipelineDB):
            resp = client.post(
                "/api/tasks/task-001/reject",
                json={
                    "reason": "Off voice",
                    "feedback": "Rewrite it",
                    "allow_revisions": True,
                },
            )
        assert resp.status_code == 200
        assert cleared == ["task-001"]

    def test_reject_nonexistent_task_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        # get_task None → the ambiguity probe runs; a prefix matching nothing
        # 404s the same as before.
        _set_pool(mock_db, fetch_rows=[])
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/ghost-task/reject",
            json={"reason": "x", "feedback": "y"},
        )
        assert resp.status_code == 404

    def test_reject_ambiguous_prefix_returns_409(self):
        """An ambiguous prefix paste returns 409 (use a longer prefix), not a
        misleading 404. get_task collapses ambiguous prefixes to None; the
        probe on the not-found path re-detects the ambiguity."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        _set_pool(
            mock_db,
            fetch_rows=[
                {"id": "550e8400-e29b-41d4-a716-446655440000"},
                {"id": "550e8400-e29b-41d4-a716-4466554400ff"},
            ],
        )
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/550e8400/reject",
            json={"reason": "dupe", "feedback": "ambiguous"},
        )
        assert resp.status_code == 409
        assert "Ambiguous" in resp.json()["detail"]
        mock_db.update_task.assert_not_called()

    def test_reject_task_wrong_status_returns_409(self):
        """Wrong-state reject now returns 409 Conflict (poindexter#743)."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value={**AWAITING_TASK, "status": "pending"})
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/task-001/reject",
            json={"reason": "x", "feedback": "y"},
        )
        assert resp.status_code == 409

    def test_reject_missing_required_fields_returns_422(self):
        client = TestClient(_build_app())
        # "reason" is required
        resp = client.post("/api/tasks/task-001/reject", json={"feedback": "only feedback"})
        assert resp.status_code == 422

    def test_db_update_called_on_rejection(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        client.post(
            "/api/tasks/task-001/reject",
            json={"reason": "Quality", "feedback": "Need improvement"},
        )

        assert mock_db.update_task.called
        call_args = mock_db.update_task.call_args
        updates = call_args[0][1]
        assert updates["approval_status"] == "rejected"
        # The CLI has always documented --feedback as landing on
        # error_message; since the 2026-07-24 finalize fix the route
        # actually writes it.
        assert updates["error_message"] == "Rejected (rejected_retry): Need improvement"


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/reject — finalize escalation (2026-07-24)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectFinalizeEscalation:
    """rejected_retry → rejected_final via a second reject call.

    Contract (2026-07-24 — operator rejected a duplicate topic with the CLI
    default --retry, then couldn't finalize it without a manual DB UPDATE):

    - rejected_retry  + allow_revisions=false → 200, rejected_final (NEW)
    - rejected_final  + allow_revisions=false → 200, idempotent re-finalize (NEW)
    - rejected_retry|rejected_final + allow_revisions=true → 409 (forbidden:
      re-queueing is the regen flow's seam, not this endpoint's)
    - escalation lost the race with the regen claim (guarded CAS returns
      None) → 409
    - every other non-awaiting status → 409, unchanged
    """

    def test_finalize_rejected_retry_returns_200_rejected_final(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=REJECTED_RETRY_TASK)
        mock_db.update_task_status_guarded = AsyncMock(return_value="rejected_retry")
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/task-001/reject", json=FINAL_BODY)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected_final"
        assert data["previous_status"] == "rejected_retry"

        # Status flip went through the guarded CAS, not a blind update.
        guard_call = mock_db.update_task_status_guarded.await_args
        assert guard_call.args[:2] == ("task-001", "rejected_final")
        assert guard_call.kwargs["allowed_from"] == (
            "rejected_retry", "rejected_final",
        )
        assert guard_call.kwargs["error_message"].startswith(
            "Rejected (rejected_final):"
        )

        # The enrichment write carries feedback/metadata but never status —
        # only the CAS may transition the row.
        updates = mock_db.update_task.call_args[0][1]
        assert "status" not in updates
        assert updates["approval_status"] == "rejected"
        assert updates["human_feedback"] == FINAL_BODY["feedback"]
        assert updates["metadata"]["finalized_from_status"] == "rejected_retry"

    def test_finalize_rejected_final_is_idempotent(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=REJECTED_FINAL_TASK)
        mock_db.update_task_status_guarded = AsyncMock(return_value="rejected_final")
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/task-001/reject", json=FINAL_BODY)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected_final"
        assert data["previous_status"] == "rejected_final"

    @pytest.mark.parametrize("task", [REJECTED_RETRY_TASK, REJECTED_FINAL_TASK])
    def test_retry_reject_on_already_rejected_task_still_409(self, task):
        """Downgrading / re-queueing an already-rejected task via reject
        stays forbidden — only the terminal finalize is permitted."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task)
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/task-001/reject", json=RETRY_BODY)

        assert resp.status_code == 409
        assert "--final" in resp.json()["detail"]
        mock_db.update_task.assert_not_called()
        mock_db.update_task_status_guarded.assert_not_called()

    def test_finalize_race_with_regen_claim_returns_409(self):
        """The regen flow claimed the row (rejected_retry → in_progress)
        between the route's read and the CAS — the guard returns None and
        the route must 409, not report a finalize that never landed."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=REJECTED_RETRY_TASK)
        mock_db.update_task_status_guarded = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/task-001/reject", json=FINAL_BODY)

        assert resp.status_code == 409
        assert "regeneration" in resp.json()["detail"]
        mock_db.update_task.assert_not_called()

    def test_finalize_skips_double_count_outcome_side_effects(self):
        """The first reject already nudged variant weights (EWMA) and flipped
        model_performance for this human decision — the escalation must not
        fire them again."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=REJECTED_RETRY_TASK)
        mock_db.update_task_status_guarded = AsyncMock(return_value="rejected_retry")
        client = TestClient(_build_app(mock_db))

        with patch(
            "services.router_outcome_feedback.record_task_outcome",
            new=AsyncMock(),
        ) as mock_outcome:
            resp = client.post("/api/tasks/task-001/reject", json=FINAL_BODY)

        assert resp.status_code == 200
        mock_outcome.assert_not_awaited()
        mock_db.mark_model_performance_outcome.assert_not_called()

    def test_normal_reject_still_fires_outcome_side_effects(self):
        """Companion to the skip test: the awaiting_approval path keeps the
        #361 outcome feedback loop."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        with patch(
            "services.router_outcome_feedback.record_task_outcome",
            new=AsyncMock(),
        ) as mock_outcome:
            resp = client.post("/api/tasks/task-001/reject", json=RETRY_BODY)

        assert resp.status_code == 200
        mock_outcome.assert_awaited_once()
        mock_db.mark_model_performance_outcome.assert_called_once()
        # And the plain path never touches the guarded CAS.
        mock_db.update_task_status_guarded.assert_not_called()

    def test_finalize_writes_rejected_final_gate_history_row(self):
        """pipeline_gate_history must gain a fresh rejected_final row so the
        content_tasks view's latest-row subqueries agree with the new task
        status; the metadata records the escalation provenance."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=REJECTED_RETRY_TASK)
        mock_db.update_task_status_guarded = AsyncMock(return_value="rejected_retry")
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/task-001/reject", json=FINAL_BODY)

        assert resp.status_code == 200
        insert_args = mock_db.pool.execute.await_args.args
        assert "pipeline_gate_history" in insert_args[0]
        assert insert_args[3] == "rejected_final"  # event_kind
        import json as _json

        gate_meta = _json.loads(insert_args[6])
        assert gate_meta["escalated"] is True
        assert gate_meta["previous_status"] == "rejected_retry"

    def test_finalize_writes_audit_log_event(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=REJECTED_RETRY_TASK)
        mock_db.update_task_status_guarded = AsyncMock(return_value="rejected_retry")
        client = TestClient(_build_app(mock_db))

        with patch("routes.approval_routes.audit_log_bg") as mock_audit:
            resp = client.post("/api/tasks/task-001/reject", json=FINAL_BODY)

        assert resp.status_code == 200
        assert mock_audit.call_count == 1
        kwargs = mock_audit.call_args.kwargs
        assert kwargs["event_type"] == "approval_gate_rejected"
        assert kwargs["details"]["escalated"] is True
        assert kwargs["details"]["new_status"] == "rejected_final"
        assert kwargs["details"]["previous_status"] == "rejected_retry"

    @pytest.mark.parametrize(
        "status", ["pending", "in_progress", "approved", "published", "failed"],
    )
    @pytest.mark.parametrize("body", [FINAL_BODY, RETRY_BODY])
    def test_other_statuses_still_409_for_both_reject_shapes(self, status, body):
        """The escalation only opens rejected_retry|rejected_final →
        rejected_final. Every other non-awaiting status keeps the 409 for
        BOTH allow_revisions shapes (poindexter#743 contract)."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value={**AWAITING_TASK, "status": status})
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/task-001/reject", json=body)

        assert resp.status_code == 409
        mock_db.update_task.assert_not_called()
        mock_db.update_task_status_guarded.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/unapprove
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnapproveTask:
    def test_unapprove_approved_task_returns_200_default_target(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=APPROVED_TASK)
        client = TestClient(_build_app(mock_db))

        with patch(
            "services.publish_service.unapprove_task",
            new=AsyncMock(return_value={
                "ok": True, "new_status": "awaiting_approval",
                "posts_row_removed": True, "reason": None,
            }),
        ):
            resp = client.post("/api/tasks/task-001/unapprove", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "awaiting_approval"
        assert data["previous_status"] == "approved"
        assert data["posts_row_removed"] is True

    def test_unapprove_to_rejected_final_returns_200_and_forwards_args(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=APPROVED_TASK)
        client = TestClient(_build_app(mock_db))

        with patch(
            "services.publish_service.unapprove_task",
            new=AsyncMock(return_value={
                "ok": True, "new_status": "rejected_final",
                "posts_row_removed": False, "reason": None,
            }),
        ) as mock_unapprove:
            resp = client.post(
                "/api/tasks/task-001/unapprove",
                json={"to": "rejected_final", "feedback": "Off-topic, don't regen"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected_final"
        assert mock_unapprove.await_args.kwargs["target_status"] == "rejected_final"
        assert mock_unapprove.await_args.kwargs["feedback"] == "Off-topic, don't regen"

    def test_unapprove_rejected_target_without_feedback_returns_422(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=APPROVED_TASK)
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/task-001/unapprove", json={"to": "rejected_final"},
        )
        assert resp.status_code == 422

    def test_unapprove_wrong_status_returns_409(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/task-001/unapprove", json={})
        assert resp.status_code == 409

    def test_unapprove_nonexistent_task_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        _set_pool(mock_db, fetch_rows=[])
        client = TestClient(_build_app(mock_db))

        resp = client.post("/api/tasks/ghost-task/unapprove", json={})
        assert resp.status_code == 404

    def test_unapprove_race_condition_reports_409(self):
        """The service function's own idempotency guard caught a race (task
        stopped being 'approved' between the route's check and the revert)."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=APPROVED_TASK)
        client = TestClient(_build_app(mock_db))

        with patch(
            "services.publish_service.unapprove_task",
            new=AsyncMock(return_value={
                "ok": False, "new_status": "awaiting_approval",
                "posts_row_removed": False, "reason": "not_approved",
            }),
        ):
            resp = client.post("/api/tasks/task-001/unapprove", json={})

        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /api/tasks/pending-approval
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPendingApprovals:
    def test_returns_200_with_empty_list(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        # Canonical offset envelope (poindexter#745): items, not the legacy
        # tasks key; the redundant count (= len(items)) was dropped.
        assert data["items"] == []
        assert "tasks" not in data
        assert "count" not in data

    def test_returns_pending_tasks(self):
        pending_task = {
            "task_id": "p1",
            "id": "p1",
            "task_type": "blog_post",
            "status": "awaiting_approval",
            "topic": "Blockchain",
            "title": "Blockchain Blog",
            "task_name": "Blockchain Blog",
            "quality_score": 80,
            "content": "Some content here",
            "featured_image_url": None,
            "task_metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([pending_task], 1))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["topic"] == "Blockchain"

    def test_qa_feedback_rides_on_pending_items(self):
        """The approval card's QA REVIEW section reads qa_feedback straight
        off the pending row (2026-08-20) — absent means a null, not a 500."""
        row = {
            "task_id": "p2", "id": "p2", "task_type": "blog_post",
            "status": "awaiting_approval", "topic": "T", "title": "T",
            "quality_score": 99, "content": "c", "featured_image_url": None,
            "task_metadata": {"qa_flagged": True, "qa_vetoed_by": ["programmatic_validator"]},
            "qa_feedback": "Final score: 99/100\n- programmatic_validator [programmatic] 0/100 FAIL: boom",
            "created_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([row], 1))
        client = TestClient(_build_app(mock_db))
        item = client.get("/api/tasks/pending-approval").json()["items"][0]
        assert item["qa_feedback"].startswith("Final score: 99/100")
        assert item["metadata"]["qa_vetoed_by"] == ["programmatic_validator"]

    def test_pagination_defaults_are_correct(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/pending-approval")
        data = resp.json()
        assert data["limit"] == 20
        assert data["offset"] == 0

    def test_passes_status_filter_to_db(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        client.get("/api/tasks/pending-approval")
        call_kwargs = mock_db.get_tasks_paginated.call_args.kwargs
        assert call_kwargs["status"] == "awaiting_approval"

    def test_invalid_limit_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks/pending-approval?limit=999")
        assert resp.status_code == 422

    def test_db_failure_fails_loud_not_empty_200(self):
        """poindexter#744 — a DB failure must surface as a 5xx, never a 200
        with an empty queue. This is the primary HITL surface: 'database down'
        must not be indistinguishable from 'healthy and nothing pending', or
        work silently piles up unreviewed."""
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )
        client = TestClient(_build_app(mock_db), raise_server_exceptions=False)

        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code >= 500


# ---------------------------------------------------------------------------
# Integration-level approval lifecycle tests (Issue #560)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalLifecycle:
    """Multi-step approval lifecycle — simulates the real approval flow."""

    def test_feedback_field_persisted_in_db_update(self):
        """Reject: feedback text must be included in the DB update call."""
        feedback_text = "The content was off-brand and too short."
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        client.post(
            "/api/tasks/task-001/reject",
            json={"reason": "Quality", "feedback": feedback_text},
        )

        call_args = mock_db.update_task.call_args
        updates = call_args[0][1]
        metadata = updates.get("metadata") or updates.get("task_metadata") or {}
        stored_feedback = (
            updates.get("rejection_feedback")
            or updates.get("feedback")
            or metadata.get("rejection_feedback")
            or metadata.get("feedback")
        )
        assert stored_feedback == feedback_text

    def test_unauthenticated_pending_approvals_returns_401_or_403(self):
        """Without auth override, pending-approval listing should be rejected."""
        mock_db = make_mock_db()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: mock_db
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code in (401, 403, 422)
