"""
Unit tests for routes/command_queue_routes.py.

Tests cover:
- POST /api/commands/               — dispatch_command
- GET  /api/commands/{id}           — get_command
- GET  /api/commands/               — list_commands
- POST /api/commands/{id}/complete  — complete_command
- POST /api/commands/{id}/fail      — fail_command
- POST /api/commands/{id}/cancel    — cancel_command
- GET  /api/commands/stats/queue-stats — get_queue_stats
- POST /api/commands/cleanup/clear-old — clear_old_commands

create_command and get_command_queue are patched to avoid real I/O.
Auth is required on these endpoints — get_current_user overridden with TEST_USER.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from routes.command_queue_routes import router
from tests.unit.routes.conftest import TEST_USER

COMMAND_ID = "cmd-11111111-1111-1111-1111-111111111111"


def _make_cmd_dict(status="pending"):
    return {
        "id": COMMAND_ID,
        "agent_type": "content_agent",
        "action": "generate_blog",
        "status": status,
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
    }


def _make_cmd_obj(status="pending"):
    cmd = MagicMock()
    cmd.to_dict = MagicMock(return_value=_make_cmd_dict(status))
    return cmd


_SENTINEL = object()


def _make_queue(cmd=None, cmd_list=_SENTINEL):
    queue = MagicMock()
    queue.get_command = AsyncMock(return_value=cmd or _make_cmd_obj())
    queue.list_commands = AsyncMock(
        return_value=[_make_cmd_obj()] if cmd_list is _SENTINEL else cmd_list
    )
    queue.complete_command = AsyncMock(return_value=cmd or _make_cmd_obj("completed"))
    queue.fail_command = AsyncMock(return_value=cmd or _make_cmd_obj("failed"))
    queue.cancel_command = AsyncMock(return_value=cmd or _make_cmd_obj("cancelled"))
    queue.clear_old_commands = AsyncMock(return_value=None)
    queue.get_stats = MagicMock(
        return_value={"total": 5, "pending": 2, "completed": 3, "failed": 0}
    )
    return queue


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app


VALID_DISPATCH_PAYLOAD = {
    "agent_type": "content_agent",
    "action": "generate_blog",
    "payload": {"topic": "AI trends"},
}


# ---------------------------------------------------------------------------
# POST /api/commands/
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDispatchCommand:
    def test_dispatch_returns_200(self):
        cmd = _make_cmd_obj()
        with (
            patch(
                "routes.command_queue_routes.create_command",
                new=AsyncMock(return_value=cmd),
            ),
            patch(
                "routes.command_queue_routes.get_command_queue",
                return_value=_make_queue(cmd=cmd),
            ),
        ):
            client = TestClient(_build_app())
            resp = client.post("/api/commands/", json=VALID_DISPATCH_PAYLOAD)
        assert resp.status_code == 200

    def test_dispatch_response_has_command_id(self):
        cmd = _make_cmd_obj()
        with patch(
            "routes.command_queue_routes.create_command",
            new=AsyncMock(return_value=cmd),
        ):
            client = TestClient(_build_app())
            data = client.post("/api/commands/", json=VALID_DISPATCH_PAYLOAD).json()
        assert "id" in data
        assert "status" in data

    def test_missing_agent_type_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/commands/", json={"action": "generate_blog"})
        assert resp.status_code == 422

    def test_service_error_returns_400(self):
        with patch(
            "routes.command_queue_routes.create_command",
            new=AsyncMock(side_effect=RuntimeError("queue full")),
        ):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.post("/api/commands/", json=VALID_DISPATCH_PAYLOAD)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/commands/{command_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCommand:
    def test_returns_200_for_existing_command(self):
        cmd = _make_cmd_obj()
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(cmd=cmd),
        ):
            client = TestClient(_build_app())
            resp = client.get(f"/api/commands/{COMMAND_ID}")
        assert resp.status_code == 200

    def test_returns_404_for_nonexistent_command(self):
        queue = _make_queue()
        queue.get_command = AsyncMock(return_value=None)
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/commands/nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/commands/
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCommands:
    def test_returns_200(self):
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/commands/")
        assert resp.status_code == 200

    def test_response_has_commands_and_total(self):
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/commands/").json()
        assert "commands" in data
        assert "total" in data

    def test_invalid_status_filter_returns_400(self):
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/commands/?status=invalid_status")
        assert resp.status_code == 400

    def test_valid_status_filter_accepted(self):
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/commands/?status=pending")
        assert resp.status_code == 200

    def test_empty_list_returns_200(self):
        queue = _make_queue(cmd_list=[])
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            data = client.get("/api/commands/").json()
        assert data["total"] == 0
        assert data["commands"] == []


# ---------------------------------------------------------------------------
# POST /api/commands/{id}/complete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompleteCommand:
    def test_returns_200(self):
        cmd = _make_cmd_obj("completed")
        queue = _make_queue()
        queue.complete_command = AsyncMock(return_value=cmd)
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            resp = client.post(
                f"/api/commands/{COMMAND_ID}/complete",
                json={"result": {"output": "done"}},
            )
        assert resp.status_code == 200

    def test_not_found_returns_404(self):
        queue = _make_queue()
        queue.complete_command = AsyncMock(return_value=None)
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/commands/nonexistent/complete",
                json={"result": {}},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/commands/{id}/fail
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailCommand:
    def test_returns_200(self):
        cmd = _make_cmd_obj("failed")
        queue = _make_queue()
        queue.fail_command = AsyncMock(return_value=cmd)
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            resp = client.post(
                f"/api/commands/{COMMAND_ID}/fail",
                json={"error": "Processing failed", "retry": True},
            )
        assert resp.status_code == 200

    def test_not_found_returns_404(self):
        queue = _make_queue()
        queue.fail_command = AsyncMock(return_value=None)
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            resp = client.post(
                "/api/commands/nonexistent/fail",
                json={"error": "fail"},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/commands/{id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelCommand:
    def test_returns_200(self):
        cmd = _make_cmd_obj("cancelled")
        queue = _make_queue()
        queue.cancel_command = AsyncMock(return_value=cmd)
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            resp = client.post(f"/api/commands/{COMMAND_ID}/cancel")
        assert resp.status_code == 200

    def test_not_found_returns_404(self):
        queue = _make_queue()
        queue.cancel_command = AsyncMock(return_value=None)
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=queue,
        ):
            client = TestClient(_build_app())
            resp = client.post("/api/commands/nonexistent/cancel")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/commands/stats/queue-stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetQueueStats:
    def test_returns_200(self):
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/commands/stats/queue-stats")
        assert resp.status_code == 200

    def test_response_has_total(self):
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(),
        ):
            client = TestClient(_build_app())
            data = client.get("/api/commands/stats/queue-stats").json()
        assert "total" in data


# ---------------------------------------------------------------------------
# POST /api/commands/cleanup/clear-old
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClearOldCommands:
    def test_returns_200(self):
        with patch(
            "routes.command_queue_routes.get_command_queue",
            return_value=_make_queue(),
        ):
            client = TestClient(_build_app())
            resp = client.post("/api/commands/cleanup/clear-old")
        assert resp.status_code == 200
