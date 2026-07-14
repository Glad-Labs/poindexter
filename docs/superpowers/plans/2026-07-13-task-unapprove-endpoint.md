# Task Unapprove Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (this repo disables subagent/Task-tool dispatch — see `feedback_no_subagent_delegation` — so do NOT use subagent-driven-development). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an operator a supported way to undo an accidental `approve` click on a `pipeline_tasks` row that hasn't been published yet — a CLI command (primary), REST endpoint, and MCP tool that revert `approved` back to `awaiting_approval` (default) or straight to `rejected_retry`/`rejected_final`.

**Architecture:** A new service function (`unapprove_task`, `services/publish_service.py`) does the actual DB work in one transaction — flip `pipeline_tasks.status`, clear `scheduled_at`, delete any staged `posts` row — plus the same best-effort audit-trail writes the existing `reject_task` makes. A thin new route (`POST /api/tasks/{task_id}/unapprove`, `routes/approval_routes.py`) validates and delegates to it. The CLI (`poindexter tasks unapprove`) and an MCP tool (`unapprove_post`) are both thin HTTP wrappers over that route.

**Tech Stack:** FastAPI + asyncpg (backend), Click (CLI), FastMCP (MCP server), pytest + pytest-asyncio (tests).

## Global Constraints

- No inline SQL in `routes/`, `poindexter/cli/`, or `mcp-server/` — the CI `adapter_purity_lint.py` ratchet fails on new violations. All raw SQL lives in `unapprove_task` (`services/publish_service.py`).
- Do not modify `reject_task` (`approval_routes.py`) or `approve_task` (`task_publishing_routes.py`) — new code only, zero regression risk to their existing tests.
- Feedback is required when reverting to `rejected_retry`/`rejected_final`, optional (defaults to an honest "Unapproved by operator" note, never a fabricated reason) when reverting to `awaiting_approval`.
- Mirror `reject_task`'s exact audit-trail calls: `clear_qa_approved_snapshot`, a `pipeline_gate_history` insert, `record_task_outcome(decision="rejected")`, `mark_model_performance_outcome(human_approved=False)` — all best-effort (individually try/excepted; a failure never masks a successful status revert).
- Design reference: `docs/superpowers/specs/2026-07-13-task-unapprove-endpoint-design.md`.

---

### Task 1: Service function — `unapprove_task`

**Files:**

- Modify: `src/cofounder_agent/services/publish_service.py` (append after `unpublish_post`, which ends at line 2363)
- Test: `src/cofounder_agent/tests/unit/services/test_publish_service_unapprove.py` (create)

**Interfaces:**

- Produces: `async def unapprove_task(pool: Any, task_id: str, *, target_status: str = "awaiting_approval", feedback: str | None = None, reviewer_id: str | None = None) -> dict[str, Any]` returning `{"ok": bool, "new_status": str, "posts_row_removed": bool, "reason": str | None}`.

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/test_publish_service_unapprove.py`:

```python
"""Unit tests for services.publish_service.unapprove_task — undoes an
operator approval that hasn't been published yet.

Reverses ``approve_task``'s stage_only side effect: flips
``pipeline_tasks.status`` from ``approved`` back to ``awaiting_approval``
(or ``rejected_retry`` / ``rejected_final``), clears ``scheduled_at``, and
deletes any staged ``posts`` row the approve step created (never published,
so nothing external to retire — the inverse of ``publish_post_from_task``'s
``stage_only=True`` branch).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool(*, update_result="UPDATE 1", delete_result="DELETE 1"):
    """Build a pool whose conn yields ``update_result``/``delete_result`` for
    the transaction's two statements; ``pool.execute`` (used directly, same
    idiom as ``reject_task``) serves the best-effort gate_history insert.
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=[update_result, delete_result])
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_cm)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool, conn


@pytest.mark.unit
class TestUnapproveTask:
    @pytest.mark.asyncio
    async def test_reverts_to_awaiting_approval_and_removes_staged_post(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 1")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ) as mock_rfb:
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            result = await unapprove_task(pool, "task-1")

        assert result == {
            "ok": True,
            "new_status": "awaiting_approval",
            "posts_row_removed": True,
            "reason": None,
        }
        # UPDATE pipeline_tasks + DELETE posts, both inside the transaction.
        assert conn.execute.await_count == 2
        update_args = conn.execute.await_args_list[0].args
        assert update_args[1] == "task-1"
        assert update_args[2] == "awaiting_approval"
        delete_args = conn.execute.await_args_list[1].args
        assert delete_args[1] == "task-1"
        # gate_history insert runs via pool.execute (reject_task's own idiom).
        pool.execute.assert_awaited_once()
        mock_pdb_cls.return_value.clear_qa_approved_snapshot.assert_awaited_once_with("task-1")
        mock_rfb.assert_awaited_once()
        assert mock_rfb.await_args.kwargs["decision"] == "rejected"
        assert mock_rfb.await_args.kwargs["task_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_reverts_to_rejected_final_with_no_staged_post(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 0")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ):
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            result = await unapprove_task(
                pool, "task-1", target_status="rejected_final", feedback="Off-topic",
            )

        assert result["ok"] is True
        assert result["new_status"] == "rejected_final"
        assert result["posts_row_removed"] is False

    @pytest.mark.asyncio
    async def test_reverts_to_rejected_retry(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 1")
        with patch("services.pipeline_db.PipelineDB") as mock_pdb_cls, patch(
            "services.router_outcome_feedback.record_task_outcome", new=AsyncMock()
        ):
            mock_pdb_cls.return_value.clear_qa_approved_snapshot = AsyncMock()
            result = await unapprove_task(
                pool, "task-1", target_status="rejected_retry", feedback="Needs a rewrite",
            )

        assert result["new_status"] == "rejected_retry"

    @pytest.mark.asyncio
    async def test_not_currently_approved_is_idempotent_noop(self):
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 0")
        result = await unapprove_task(pool, "task-1")

        assert result == {
            "ok": False,
            "new_status": "awaiting_approval",
            "posts_row_removed": False,
            "reason": "not_approved",
        }
        # Never reached the posts DELETE or any audit write.
        assert conn.execute.await_count == 1
        pool.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_audit_trail_failures_do_not_mask_successful_revert(self):
        """Best-effort writes must never raise past unapprove_task — the
        status flip already committed and must be reported as such."""
        from services.publish_service import unapprove_task

        pool, conn = _make_pool(update_result="UPDATE 1", delete_result="DELETE 1")
        pool.execute = AsyncMock(side_effect=RuntimeError("gate_history boom"))
        with patch(
            "services.pipeline_db.PipelineDB", side_effect=RuntimeError("snapshot boom"),
        ), patch(
            "services.router_outcome_feedback.record_task_outcome",
            new=AsyncMock(side_effect=RuntimeError("outcome boom")),
        ):
            result = await unapprove_task(pool, "task-1")

        assert result["ok"] is True
        assert result["new_status"] == "awaiting_approval"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_publish_service_unapprove.py -v`
Expected: FAIL — `ImportError: cannot import name 'unapprove_task' from 'services.publish_service'`

- [ ] **Step 3: Implement `unapprove_task`**

Append to `src/cofounder_agent/services/publish_service.py` (after line 2363, the end of `unpublish_post`):

```python


async def unapprove_task(
    pool: Any,
    task_id: str,
    *,
    target_status: str = "awaiting_approval",
    feedback: str | None = None,
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    """Undo an operator approval that hasn't been published yet.

    Reverses ``approve_task``'s ``stage_only`` side effect: flips
    ``pipeline_tasks.status`` from ``approved`` to ``target_status``
    (``awaiting_approval`` | ``rejected_retry`` | ``rejected_final``),
    clears any ``scheduled_at`` slot, and deletes the staged ``posts`` row
    (created at ``status='approved'``, never published — nothing external
    to retire, unlike ``unpublish_post``). Idempotent no-op
    (``ok=False``) if the task isn't currently 'approved'.

    The primary status/posts revert is one transaction (must not partially
    apply); the audit-trail writes below are best-effort, individually
    try/excepted, exactly like ``routes.approval_routes.reject_task`` — a
    logging hiccup must never mask a status flip that already succeeded.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            update_result = await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET status = $2, scheduled_at = NULL, updated_at = NOW()
                 WHERE task_id = $1 AND status = 'approved'
                """,
                task_id, target_status,
            )
            if not update_result.startswith("UPDATE 1"):
                logger.info(
                    "[publish_service] unapprove_task: task %s not currently "
                    "'approved' (%s) — no-op", task_id, update_result,
                )
                return {
                    "ok": False,
                    "new_status": target_status,
                    "posts_row_removed": False,
                    "reason": "not_approved",
                }

            delete_result = await conn.execute(
                """
                DELETE FROM posts
                 WHERE metadata ->> 'pipeline_task_id' = $1
                   AND status = 'approved'
                """,
                task_id,
            )

    posts_removed = not delete_result.endswith(" 0")
    logger.info(
        "[publish_service] unapprove_task: task %s approved -> %s "
        "(staged post removed: %s)", task_id, target_status, posts_removed,
    )

    # Best-effort audit trail — mirrors reject_task exactly. event_kind
    # reuses the existing 'rejected' taxonomy (the content_tasks view's
    # scalar subqueries only recognize 'approved'/'rejected'/'rejected_retry'
    # /'rejected_final'; a novel 'unapproved' value would be silently
    # invisible to it) even for the plain awaiting_approval revert — the
    # approval WAS reversed, which the audit trail should reflect.
    event_kind = target_status if target_status != "awaiting_approval" else "rejected"
    actor = reviewer_id or "operator"

    try:
        from services.pipeline_db import PipelineDB

        await PipelineDB(pool).clear_qa_approved_snapshot(task_id)
    except Exception as marker_err:  # noqa: BLE001
        logger.warning(
            "[unapprove_task] clear_qa_approved_snapshot failed for %s: %s",
            task_id, marker_err,
        )

    try:
        await pool.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, actor, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            task_id,
            "final_approval",
            event_kind,
            feedback,
            actor,
            json.dumps(
                {"reviewer": actor, "decision": event_kind, "reversed_from": "approved"},
                default=str,
            ),
        )
    except Exception as review_err:  # noqa: BLE001
        logger.warning(
            "[unapprove_task] pipeline_gate_history write failed for %s: %s",
            task_id, review_err,
        )

    try:
        from services.router_outcome_feedback import record_task_outcome

        await record_task_outcome(pool=pool, task_id=task_id, decision="rejected")
    except Exception as rfb_err:  # noqa: BLE001
        logger.debug("[unapprove_task] router outcome feedback failed: %s", rfb_err)

    return {
        "ok": True,
        "new_status": target_status,
        "posts_row_removed": posts_removed,
        "reason": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_publish_service_unapprove.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/publish_service.py src/cofounder_agent/tests/unit/services/test_publish_service_unapprove.py
git commit -m "feat(publish-service): add unapprove_task to revert an unpublished approval"
```

---

### Task 2: Route — `POST /api/tasks/{task_id}/unapprove`

**Files:**

- Modify: `src/cofounder_agent/routes/approval_routes.py` (add import + schema + route after `reject_task`, which ends at line 264, before `get_pending_approvals` at line 267)
- Test: `src/cofounder_agent/tests/unit/routes/test_approval_routes.py` (add a new test class after `TestRejectTask`, before `TestGetPendingApprovals`)

**Interfaces:**

- Consumes: `unapprove_task(pool, task_id, *, target_status="awaiting_approval", feedback=None, reviewer_id=None) -> dict[str, Any]` (Task 1).
- Produces: `POST /api/tasks/{task_id}/unapprove` — request `{"to": "awaiting_approval"|"rejected_retry"|"rejected_final", "feedback": str | None}`, response `{"task_id", "status", "previous_status", "posts_row_removed", "message"}`.

- [ ] **Step 1: Write the failing tests**

In `src/cofounder_agent/tests/unit/routes/test_approval_routes.py`, insert this new class right after the `TestRejectTask` class ends (after the blank lines following `test_db_update_called_on_rejection`, before the `# GET /api/tasks/pending-approval` section comment):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_approval_routes.py::TestUnapproveTask -v`
Expected: FAIL — 404 for every case (no `/unapprove` route registered yet)

- [ ] **Step 3: Implement the route**

In `src/cofounder_agent/routes/approval_routes.py`, change the typing import (line 17) from:

```python
from typing import Any
```

to:

```python
from typing import Any, Literal
```

Then insert this immediately after `reject_task` ends (after line 264, i.e. right before the `@router.get("/pending-approval", ...)` block):

```python
class UnapproveRequest(BaseModel):
    """Request body for ``POST /{task_id}/unapprove``."""

    to: Literal["awaiting_approval", "rejected_retry", "rejected_final"] = "awaiting_approval"
    feedback: str | None = None


@router.post(
    "/{task_id}/unapprove",
    summary="Revert an approved-but-unpublished task",
    response_model=dict[str, Any],
    status_code=200,
)
async def unapprove_task_route(
    task_id: str,
    request: UnapproveRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Undo an operator approval that hasn't been published yet.

    Only tasks with status 'approved' can be unapproved — approve only
    stages a task (see ``feedback_approve_does_not_mean_publish``), so this
    is always safe. Reverts to 'awaiting_approval' by default (back in the
    review queue, content unchanged), or straight to 'rejected_retry' /
    'rejected_final' via ``to``. Also un-stages any linked draft ``posts``
    row and clears any ``scheduled_at`` slot the approval created.
    """
    try:
        operator = get_operator_identity()

        task = await db_service.get_task(task_id)
        if not task:
            await resolve_task_id_prefix(db_service.pool, task_id)
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        full_task_id = str(task.get("task_id") or task.get("id") or task_id)

        current_status = task.get("status")
        if current_status != "approved":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot unapprove task with status '{current_status}' — expected 'approved'",
            )

        if request.to != "awaiting_approval" and not request.feedback:
            raise HTTPException(
                status_code=422,
                detail="feedback is required when 'to' targets a rejected_* status",
            )

        from services.publish_service import unapprove_task

        result = await unapprove_task(
            db_service.pool,
            full_task_id,
            target_status=request.to,
            feedback=request.feedback,
            reviewer_id=operator.get("id"),
        )

        if not result["ok"]:
            raise HTTPException(
                status_code=409,
                detail=f"Task {full_task_id} was no longer 'approved' when the revert ran",
            )

        try:
            await db_service.mark_model_performance_outcome(
                full_task_id, human_approved=False,
            )
        except Exception as mp_err:  # noqa: BLE001
            logger.debug(
                "[unapprove_task_route] mark_model_performance_outcome failed: %s", mp_err,
            )

        logger.info(
            "Task %s unapproved by %s -> %s",
            full_task_id, operator["id"], result["new_status"],
        )

        return {
            "task_id": full_task_id,
            "status": result["new_status"],
            "previous_status": "approved",
            "posts_row_removed": result["posts_row_removed"],
            "message": f"Task reverted to {result['new_status']}.",
        }

    except HTTPException:
        raise
    except AppError:
        raise
    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error("Failed to unapprove task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to unapprove task",
        ) from e


```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/routes/test_approval_routes.py -v`
Expected: PASS (all tests in the file, including the existing `TestRejectTask` / `TestGetPendingApprovals` classes — confirms no regression)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/routes/approval_routes.py src/cofounder_agent/tests/unit/routes/test_approval_routes.py
git commit -m "feat(approval-routes): add POST /{task_id}/unapprove"
```

---

### Task 3: CLI — `poindexter tasks unapprove`

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/tasks.py` (add command after `tasks_publish`, which ends at line 314, before the `# bulk-ops` section comment at line 317)
- Test: `src/cofounder_agent/tests/unit/cli/test_tasks_unapprove_cli.py` (create)

**Interfaces:**

- Consumes: `POST /api/tasks/{task_id}/unapprove` (Task 2) via the existing `_post_action(task_id, action, payload)` helper already defined in this file.
- Produces: `poindexter tasks unapprove <task_id> [--to awaiting_approval|rejected_retry|rejected_final] [--feedback TEXT]`.

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/cli/test_tasks_unapprove_cli.py`:

```python
"""CLI wiring for ``poindexter tasks unapprove`` — undo an accidental
approve on a task that hasn't published yet.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.tasks import tasks_group


@pytest.fixture
def runner():
    return CliRunner()


def _fake_client(result):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = MagicMock()
    client.json_or_raise.return_value = result
    return client


def test_unapprove_default_posts_awaiting_approval_target(runner):
    client = _fake_client({
        "task_id": "abc123", "status": "awaiting_approval",
        "previous_status": "approved", "posts_row_removed": True,
    })
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = runner.invoke(tasks_group, ["unapprove", "abc123"])

    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert args[0] == "/api/tasks/abc123/unapprove"
    assert kwargs["json"] == {"to": "awaiting_approval", "feedback": "Unapproved by operator"}
    assert "awaiting_approval" in result.output


def test_unapprove_to_rejected_final_sends_given_feedback(runner):
    client = _fake_client({
        "task_id": "abc123", "status": "rejected_final",
        "previous_status": "approved", "posts_row_removed": False,
    })
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = runner.invoke(
            tasks_group,
            ["unapprove", "abc123", "--to", "rejected_final", "--feedback", "Off-topic"],
        )

    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert kwargs["json"] == {"to": "rejected_final", "feedback": "Off-topic"}
    assert "rejected_final" in result.output


def test_unapprove_to_rejected_without_feedback_errors_before_network_call(runner):
    with patch("poindexter.cli.tasks.WorkerClient") as mock_client_cls:
        result = runner.invoke(
            tasks_group, ["unapprove", "abc123", "--to", "rejected_retry"],
        )

    assert result.exit_code == 2
    assert "--feedback is required" in result.output
    mock_client_cls.assert_not_called()


def test_unapprove_invalid_to_choice_rejected_by_click(runner):
    result = runner.invoke(tasks_group, ["unapprove", "abc123", "--to", "bogus"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_tasks_unapprove_cli.py -v`
Expected: FAIL — `Error: No such command 'unapprove'` (exit_code 2, "unknown command")

- [ ] **Step 3: Implement the CLI command**

In `src/cofounder_agent/poindexter/cli/tasks.py`, insert this immediately after the `tasks_publish` function (after line 314, before the `# bulk-ops` section comment on line 317):

```python
@tasks_group.command("unapprove")
@click.argument("task_id")
@click.option(
    "--to",
    type=click.Choice(
        ["awaiting_approval", "rejected_retry", "rejected_final"], case_sensitive=False,
    ),
    default="awaiting_approval",
    show_default=True,
    help=(
        "Target status. 'awaiting_approval' puts the task back in the review "
        "queue unchanged (undo an accidental approve click). 'rejected_retry' "
        "/ 'rejected_final' rejects it outright in one step."
    ),
)
@click.option(
    "--feedback",
    default="",
    help="Reason, surfaced on the audit log. Required when --to targets a rejected_* status.",
)
def tasks_unapprove(task_id: str, to: str, feedback: str) -> None:
    """Revert an approved-but-not-yet-published task (full id or 8-char prefix).

    Approve only stages a task — it hasn't published — so this is always
    safe. Also un-stages any linked draft post and clears any scheduled
    publish slot the approval created.
    """
    if to != "awaiting_approval" and not feedback:
        click.echo(
            "Error: --feedback is required when --to targets a rejected_* status.",
            err=True,
        )
        sys.exit(2)
    payload: dict[str, Any] = {
        "to": to,
        "feedback": feedback or "Unapproved by operator",
    }
    try:
        t = _post_action(task_id, "unapprove", payload)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Reverted: {task_id}  status={t.get('status', '?')}", fg="yellow")


```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_tasks_unapprove_cli.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/tasks.py src/cofounder_agent/tests/unit/cli/test_tasks_unapprove_cli.py
git commit -m "feat(cli): add poindexter tasks unapprove"
```

---

### Task 4: MCP tool — `unapprove_post`

**Files:**

- Modify: `mcp-server/server.py` (add tool after `reject_post`, which ends at line 453, before `publish_post` at line 457)
- Test: `mcp-server/tests/test_unapprove_post.py` (create)

**Interfaces:**

- Consumes: `POST /api/tasks/{task_id}/unapprove` (Task 2) via the existing `_api(method, path, data=None, *, timeout=15.0)` helper and `_resolve_task_id(task_id) -> str` helper already defined in `server.py`.
- Produces: `unapprove_post(task_id: str, to: str = "awaiting_approval", feedback: str = "") -> str` MCP tool.

- [ ] **Step 1: Write the failing tests**

Create `mcp-server/tests/test_unapprove_post.py`:

```python
"""Tests for the ``unapprove_post`` MCP tool — undo an accidental approve on
a task that hasn't published yet.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import server  # noqa: E402


@pytest.mark.asyncio
async def test_unapprove_post_default_target():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api", AsyncMock(return_value={"status": "awaiting_approval"}),
        ) as api,
    ):
        out = await server.unapprove_post("abc1")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/unapprove",
        data={"to": "awaiting_approval", "feedback": None},
    )
    assert "awaiting_approval" in out


@pytest.mark.asyncio
async def test_unapprove_post_to_rejected_final_with_feedback():
    with (
        patch.object(server, "_resolve_task_id", AsyncMock(return_value="full")),
        patch.object(
            server, "_api", AsyncMock(return_value={"status": "rejected_final"}),
        ) as api,
    ):
        out = await server.unapprove_post("abc1", to="rejected_final", feedback="Off-topic")
    api.assert_awaited_once_with(
        "POST", "/api/tasks/full/unapprove",
        data={"to": "rejected_final", "feedback": "Off-topic"},
    )
    assert "rejected_final" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mcp-server && python -m pytest tests/test_unapprove_post.py -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'unapprove_post'`

- [ ] **Step 3: Implement the MCP tool**

In `mcp-server/server.py`, insert this immediately after `reject_post` ends (after line 453, before the `@mcp.tool()` / `async def publish_post` block on line 456):

```python
@mcp.tool()
async def unapprove_post(
    task_id: str, to: str = "awaiting_approval", feedback: str = "",
) -> str:
    """Revert an approved-but-not-yet-published task (undo an accidental approve).

    ``to`` is "awaiting_approval" (default — puts it back in the review
    queue, content unchanged), "rejected_retry", or "rejected_final".
    Feedback is required by the worker API when rejecting outright.
    """
    full_id = await _resolve_task_id(task_id)
    result = await _api("POST", f"/api/tasks/{full_id}/unapprove", data={
        "to": to,
        "feedback": feedback or None,
    })
    status = result.get("status", result.get("error", "?"))
    return f"Reverted: {status}"


```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp-server && python -m pytest tests/test_unapprove_post.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp-server/server.py mcp-server/tests/test_unapprove_post.py
git commit -m "feat(mcp): add unapprove_post tool"
```

---

### Task 5: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend unit suite to confirm no regressions**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/ -q`
Expected: all pass (same pass count as the pre-change baseline plus the ~11 new tests added across Tasks 1-3; 0 failures, 0 new collection errors)

- [ ] **Step 2: Run the mcp-server suite**

Run: `cd mcp-server && python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 3: Run the adapter-purity lint to confirm no new inline SQL was introduced in routes/CLI/mcp-server**

Run: `python scripts/ci/adapter_purity_lint.py`
Expected: `adapter_purity_lint: clean — no new inline-SQL adapters (... ratchet only shrinks)`

- [ ] **Step 4: Manual sanity check of the CLI help text**

Run: `cd src/cofounder_agent && poetry run python -m poindexter tasks unapprove --help`
Expected: shows the `--to` choice option (with its three values) and `--feedback` option, matching the docstring written in Task 3.
