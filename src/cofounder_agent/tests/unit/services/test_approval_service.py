"""Characterization tests for ``services.approval_service`` (#622).

Pins the current behavior of the mid-pipeline HITL gate service BEFORE the
shared gate-machinery extraction, so the refactor is provably behavior-
preserving. There were no tests here previously — this also closes a coverage
gap on a core approval-path module.

Covers: artifact coercion, the exception hierarchy, gate-enable resolution,
and the approve / reject / show / pause gate-matching contracts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.approval_service as svc
from tests.unit.services._gate_fakes import FakeConn, FakePool, executed_sql

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _coerce_artifact
# ---------------------------------------------------------------------------


class TestCoerceArtifact:
    def test_none_returns_empty_dict(self):
        assert svc._coerce_artifact(None) == {}

    def test_dict_passthrough(self):
        d = {"title": "x"}
        assert svc._coerce_artifact(d) is d

    def test_json_string_parsed_to_dict(self):
        assert svc._coerce_artifact('{"a": 1}') == {"a": 1}

    def test_non_dict_json_wrapped_in_raw(self):
        assert svc._coerce_artifact("[1, 2]") == {"raw": [1, 2]}

    def test_invalid_json_wrapped_in_raw(self):
        assert svc._coerce_artifact("not json") == {"raw": "not json"}

    def test_other_type_stringified_in_raw(self):
        assert svc._coerce_artifact(123) == {"raw": "123"}


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_all_derive_from_base(self):
        for exc in (svc.TaskNotFoundError, svc.TaskNotPausedError, svc.GateMismatchError):
            assert issubclass(exc, svc.ApprovalServiceError)

    def test_base_is_exception(self):
        assert issubclass(svc.ApprovalServiceError, Exception)


# ---------------------------------------------------------------------------
# is_gate_enabled
# ---------------------------------------------------------------------------


class TestIsGateEnabled:
    def _sc(self, value):
        sc = MagicMock()
        sc.get.return_value = value
        return sc

    def test_none_site_config_is_false(self):
        assert svc.is_gate_enabled("g", None) is False

    @pytest.mark.parametrize("value", ["on", "true", "1", "yes", "ON", "Yes"])
    def test_truthy_values(self, value):
        assert svc.is_gate_enabled("g", self._sc(value)) is True

    @pytest.mark.parametrize("value", ["off", "false", "0", "", "no", "nonsense"])
    def test_falsy_values(self, value):
        assert svc.is_gate_enabled("g", self._sc(value)) is False


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _silence_audit():
    with patch.object(svc, "audit_log_bg") as m:
        yield m


class TestApprove:
    async def test_task_not_found_raises(self):
        pool = FakePool(FakeConn(fetchrow_result=None))
        with pytest.raises(svc.TaskNotFoundError):
            await svc.approve(task_id="t1", site_config=None, pool=pool)

    async def test_not_paused_raises(self):
        row = {"id": "t1", "status": "in_progress", "awaiting_gate": None}
        pool = FakePool(FakeConn(fetchrow_result=row))
        with pytest.raises(svc.TaskNotPausedError):
            await svc.approve(task_id="t1", site_config=None, pool=pool)

    async def test_gate_mismatch_raises(self):
        row = {"id": "t1", "status": "in_progress", "awaiting_gate": "topic_decision"}
        pool = FakePool(FakeConn(fetchrow_result=row))
        with pytest.raises(svc.GateMismatchError):
            await svc.approve(
                task_id="t1", gate_name="other_gate", site_config=None, pool=pool
            )

    async def test_success_clears_gate_and_writes_history(self):
        row = {"id": "t1", "status": "in_progress", "awaiting_gate": "topic_decision"}
        conn = FakeConn(fetchrow_result=row)
        pool = FakePool(conn)
        out = await svc.approve(
            task_id="t1", feedback="lgtm", site_config=None, pool=pool
        )
        assert out["ok"] is True
        assert out["gate_name"] == "topic_decision"
        assert out["previous_status"] == "in_progress"
        assert out["feedback"] == "lgtm"
        sql = executed_sql(conn)
        assert "UPDATE pipeline_tasks" in sql
        assert "pipeline_gate_history" in sql  # task path records gate history


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


class TestReject:
    async def test_not_found_raises(self):
        pool = FakePool(FakeConn(fetchrow_result=None))
        with pytest.raises(svc.TaskNotFoundError):
            await svc.reject(task_id="t1", site_config=None, pool=pool)

    async def test_default_reject_status(self):
        row = {"id": "t1", "status": "in_progress", "awaiting_gate": "g", "gate_artifact": "{}"}
        conn = FakeConn(fetchrow_result=row)
        pool = FakePool(conn)
        with patch("services.rejection_handlers.dispatch_rejection", new=AsyncMock()):
            out = await svc.reject(task_id="t1", reason="nope", site_config=None, pool=pool)
        assert out["new_status"] == "rejected"
        assert out["reason"] == "nope"

    async def test_per_gate_reject_status_override(self):
        row = {"id": "t1", "status": "in_progress", "awaiting_gate": "g", "gate_artifact": "{}"}
        conn = FakeConn(fetchrow_result=row)
        pool = FakePool(conn)
        sc = MagicMock()
        sc.get.return_value = "dismissed"
        with patch("services.rejection_handlers.dispatch_rejection", new=AsyncMock()):
            out = await svc.reject(task_id="t1", site_config=sc, pool=pool)
        assert out["new_status"] == "dismissed"


# ---------------------------------------------------------------------------
# show_pending / pause_at_gate
# ---------------------------------------------------------------------------


class TestShowAndPause:
    async def test_show_not_found_raises(self):
        pool = FakePool(FakeConn(fetchrow_result=None))
        with pytest.raises(svc.TaskNotFoundError):
            await svc.show_pending(task_id="t1", pool=pool)

    async def test_show_success_shape(self):
        row = {
            "id": "t1", "status": "in_progress", "awaiting_gate": "g",
            "gate_artifact": '{"title": "Hi"}', "gate_paused_at": None,
            "topic": "T", "title": "Hi",
        }
        pool = FakePool(FakeConn(fetchrow_result=row))
        out = await svc.show_pending(task_id="t1", pool=pool)
        assert out["task_id"] == "t1"
        assert out["gate_name"] == "g"
        assert out["artifact"] == {"title": "Hi"}

    async def test_pause_success_writes_update(self):
        conn = FakeConn()
        pool = FakePool(conn)
        out = await svc.pause_at_gate(
            task_id="t1", gate_name="g", artifact={"k": "v"},
            site_config=None, pool=pool, notify=False,
        )
        assert out["ok"] is True
        assert out["gate_name"] == "g"
        assert out["notify"] == {"sent": False, "reason": "skipped"}
        assert "UPDATE pipeline_tasks" in executed_sql(conn)
