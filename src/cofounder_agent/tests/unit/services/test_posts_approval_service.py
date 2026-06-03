"""Characterization tests for ``services.posts_approval_service`` (#622).

Pins the current behavior of the final-publish HITL gate service (operates on
the ``posts`` table) BEFORE the shared gate-machinery extraction. No tests
existed here previously — this also closes a coverage gap on the publish gate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.posts_approval_service as svc
from tests.unit.services._gate_fakes import FakeConn, FakePool, executed_sql

pytestmark = pytest.mark.unit


class TestCoerceArtifact:
    def test_none_returns_empty_dict(self):
        assert svc._coerce_artifact(None) == {}

    def test_dict_passthrough(self):
        d = {"slug": "x"}
        assert svc._coerce_artifact(d) is d

    def test_json_string_parsed(self):
        assert svc._coerce_artifact('{"a": 1}') == {"a": 1}

    def test_non_dict_json_wrapped(self):
        assert svc._coerce_artifact("[1]") == {"raw": [1]}

    def test_invalid_json_wrapped(self):
        assert svc._coerce_artifact("nope") == {"raw": "nope"}


class TestExceptionHierarchy:
    def test_all_derive_from_base(self):
        for exc in (svc.PostNotFoundError, svc.PostNotPausedError, svc.PostGateMismatchError):
            assert issubclass(exc, svc.PostsApprovalServiceError)


@pytest.fixture(autouse=True)
def _silence_audit():
    with patch.object(svc, "audit_log_bg") as m:
        yield m


class TestApprovePublish:
    async def test_post_not_found_raises(self):
        pool = FakePool(FakeConn(fetchrow_result=None))
        with pytest.raises(svc.PostNotFoundError):
            await svc.approve_publish(post_id="p1", site_config=None, pool=pool)

    async def test_not_paused_raises(self):
        row = {"id": "p1", "status": "scheduled", "awaiting_gate": None}
        pool = FakePool(FakeConn(fetchrow_result=row))
        with pytest.raises(svc.PostNotPausedError):
            await svc.approve_publish(post_id="p1", site_config=None, pool=pool)

    async def test_gate_mismatch_raises(self):
        row = {"id": "p1", "status": "scheduled", "awaiting_gate": "final_publish_approval"}
        pool = FakePool(FakeConn(fetchrow_result=row))
        with pytest.raises(svc.PostGateMismatchError):
            await svc.approve_publish(
                post_id="p1", gate_name="other", site_config=None, pool=pool
            )

    async def test_success_clears_gate(self):
        row = {"id": "p1", "status": "scheduled", "awaiting_gate": "final_publish_approval"}
        conn = FakeConn(fetchrow_result=row)
        pool = FakePool(conn)
        out = await svc.approve_publish(
            post_id="p1", feedback="ship it", site_config=None, pool=pool
        )
        assert out["ok"] is True
        assert out["post_id"] == "p1"
        assert out["gate_name"] == "final_publish_approval"
        assert out["feedback"] == "ship it"
        assert "UPDATE posts" in executed_sql(conn)


class TestRejectPublish:
    async def test_not_found_raises(self):
        pool = FakePool(FakeConn(fetchrow_result=None))
        with pytest.raises(svc.PostNotFoundError):
            await svc.reject_publish(post_id="p1", site_config=None, pool=pool)

    async def test_default_reject_status(self):
        row = {"id": "p1", "status": "scheduled", "awaiting_gate": "g", "gate_artifact": "{}"}
        conn = FakeConn(fetchrow_result=row)
        pool = FakePool(conn)
        with patch("services.rejection_handlers.dispatch_rejection", new=AsyncMock()):
            out = await svc.reject_publish(post_id="p1", reason="no", site_config=None, pool=pool)
        assert out["new_status"] == "rejected"
        assert out["reason"] == "no"
        assert "UPDATE posts" in executed_sql(conn)

    async def test_per_gate_reject_status_override(self):
        row = {"id": "p1", "status": "scheduled", "awaiting_gate": "g", "gate_artifact": "{}"}
        conn = FakeConn(fetchrow_result=row)
        pool = FakePool(conn)
        sc = MagicMock()
        sc.get.return_value = "draft"
        with patch("services.rejection_handlers.dispatch_rejection", new=AsyncMock()):
            out = await svc.reject_publish(post_id="p1", site_config=sc, pool=pool)
        assert out["new_status"] == "draft"


class TestShowAndPause:
    async def test_show_not_found_raises(self):
        pool = FakePool(FakeConn(fetchrow_result=None))
        with pytest.raises(svc.PostNotFoundError):
            await svc.show_pending_publish(post_id="p1", pool=pool)

    async def test_show_success_shape(self):
        row = {
            "id": "p1", "slug": "hello", "title": "Hello", "status": "scheduled",
            "published_at": None, "awaiting_gate": "g",
            "gate_artifact": '{"slug": "hello"}', "gate_paused_at": None,
        }
        pool = FakePool(FakeConn(fetchrow_result=row))
        out = await svc.show_pending_publish(post_id="p1", pool=pool)
        assert out["post_id"] == "p1"
        assert out["slug"] == "hello"
        assert out["gate_name"] == "g"
        assert out["artifact"] == {"slug": "hello"}

    async def test_pause_success_writes_update(self):
        conn = FakeConn()
        pool = FakePool(conn)
        out = await svc.pause_post_at_gate(
            post_id="p1", gate_name="final_publish_approval", artifact={"slug": "x"},
            site_config=None, pool=pool, notify=False,
        )
        assert out["ok"] is True
        assert out["post_id"] == "p1"
        assert out["notify"] == {"sent": False, "reason": "skipped"}
        assert "UPDATE posts" in executed_sql(conn)
