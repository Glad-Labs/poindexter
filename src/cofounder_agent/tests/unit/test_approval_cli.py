"""Click CLI tests for ``poindexter approve / reject / list-pending /
show-pending / gates`` (#145).

The CLI commands are thin Click wrappers that resolve a DSN, open a
pool, and call into ``services.approval_service``. We patch the pool
and service-module functions so the test suite exercises the Click
glue (option parsing, JSON mode, exit codes) without a live DB.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.approval import (
    approve_command,
    gates_group,
    list_pending_command,
    reject_command,
    show_pending_command,
)
from services.approval_service import (
    GateMismatchError,
    TaskNotFoundError,
    TaskNotPausedError,
)


# ---------------------------------------------------------------------------
# Shared fixture — patches DSN resolution + pool factory + site_config
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch):
    """Patch the CLI's pool factory and site_config bootstrap. Yields a
    dict the test can stuff service-function mocks into."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

    fake_pool = MagicMock()
    fake_pool.close = AsyncMock(return_value=None)
    fake_site_config = MagicMock()

    async def _make_pool():
        return fake_pool

    async def _make_site_config(_pool):
        return fake_site_config

    p1 = patch("poindexter.cli.approval._make_pool", side_effect=_make_pool)
    p2 = patch(
        "poindexter.cli.approval._make_site_config",
        side_effect=_make_site_config,
    )
    p1.start()
    p2.start()
    try:
        yield {"pool": fake_pool, "site_config": fake_site_config}
    finally:
        p1.stop()
        p2.stop()


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


class TestApproveCommand:
    def test_help(self, runner):
        result = runner.invoke(approve_command, ["--help"])
        assert result.exit_code == 0
        assert "approve" in result.output.lower()

    def test_calls_service_with_args(self, runner, cli_env):
        async def _ok(**kwargs):
            return {
                "ok": True,
                "task_id": kwargs["task_id"],
                "gate_name": "topic_decision",
                "previous_status": "in_progress",
                "feedback": kwargs.get("feedback") or "",
            }

        with patch(
            "services.approval_service.approve",
            AsyncMock(side_effect=_ok),
        ) as mock_svc:
            result = runner.invoke(
                approve_command,
                ["t-1", "--gate", "topic_decision", "--feedback", "good"],
            )
        assert result.exit_code == 0
        assert "Approved task t-1" in result.output
        assert "topic_decision" in result.output
        # service called with unwrapped kwargs.
        kwargs = mock_svc.call_args.kwargs
        assert kwargs["task_id"] == "t-1"
        assert kwargs["gate_name"] == "topic_decision"
        assert kwargs["feedback"] == "good"

    def test_json_mode_shape(self, runner, cli_env):
        payload = {
            "ok": True,
            "task_id": "t-1",
            "gate_name": "topic_decision",
            "previous_status": "in_progress",
            "feedback": "",
        }
        with patch(
            "services.approval_service.approve",
            AsyncMock(return_value=payload),
        ):
            result = runner.invoke(approve_command, ["t-1", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed == payload

    def test_task_not_found_exits_nonzero(self, runner, cli_env):
        with patch(
            "services.approval_service.approve",
            AsyncMock(side_effect=TaskNotFoundError("Task t-1 not found")),
        ):
            result = runner.invoke(approve_command, ["t-1"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_gate_mismatch_exits_nonzero(self, runner, cli_env):
        with patch(
            "services.approval_service.approve",
            AsyncMock(side_effect=GateMismatchError("wrong gate")),
        ):
            result = runner.invoke(
                approve_command, ["t-1", "--gate", "wrong"],
            )
        assert result.exit_code != 0
        assert "wrong gate" in result.output.lower() or "wrong" in result.output

    def test_no_gate_passes_none_to_service(self, runner, cli_env):
        async def _ok(**kwargs):
            assert kwargs["gate_name"] is None
            return {"ok": True, "task_id": "t-1", "gate_name": "topic_decision",
                    "previous_status": "in_progress", "feedback": ""}

        with patch(
            "services.approval_service.approve",
            AsyncMock(side_effect=_ok),
        ):
            result = runner.invoke(approve_command, ["t-1"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


class TestRejectCommand:
    def test_help(self, runner):
        result = runner.invoke(reject_command, ["--help"])
        assert result.exit_code == 0
        assert "reject" in result.output.lower()

    def test_default_status(self, runner, cli_env):
        async def _ok(**kwargs):
            return {
                "ok": True,
                "task_id": kwargs["task_id"],
                "gate_name": "topic_decision",
                "new_status": "rejected",
                "reason": kwargs.get("reason") or "",
            }

        with patch(
            "services.approval_service.reject",
            AsyncMock(side_effect=_ok),
        ):
            result = runner.invoke(
                reject_command, ["t-1", "--reason", "off-brand"],
            )
        assert result.exit_code == 0
        assert "Rejected" in result.output
        assert "rejected" in result.output

    def test_json_mode(self, runner, cli_env):
        payload = {
            "ok": True,
            "task_id": "t-1",
            "gate_name": "topic_decision",
            "new_status": "rejected",
            "reason": "x",
        }
        with patch(
            "services.approval_service.reject",
            AsyncMock(return_value=payload),
        ):
            result = runner.invoke(reject_command, ["t-1", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == payload

    def test_task_not_paused_exits_nonzero(self, runner, cli_env):
        with patch(
            "services.approval_service.reject",
            AsyncMock(side_effect=TaskNotPausedError("not paused")),
        ):
            result = runner.invoke(reject_command, ["t-1"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# list-pending
# ---------------------------------------------------------------------------


class TestListPendingCommand:
    def test_help(self, runner):
        result = runner.invoke(list_pending_command, ["--help"])
        assert result.exit_code == 0

    def test_empty_human_output(self, runner, cli_env):
        with patch(
            "services.approval_service.list_pending",
            AsyncMock(return_value=[]),
        ):
            result = runner.invoke(list_pending_command, [])
        assert result.exit_code == 0
        assert "no pending" in result.output.lower()

    def test_json_mode_shape(self, runner, cli_env):
        rows: list[dict[str, Any]] = [
            {
                "task_id": "t-1",
                "gate_name": "topic_decision",
                "artifact": {"topic": "hello"},
                "gate_paused_at": "2026-04-26T12:00:00+00:00",
                "status": "in_progress",
                "topic": "hello",
                "title": "Hi",
            },
        ]
        with patch(
            "services.approval_service.list_pending",
            AsyncMock(return_value=rows),
        ):
            result = runner.invoke(list_pending_command, ["--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed == rows

    def test_filter_by_gate(self, runner, cli_env):
        with patch(
            "services.approval_service.list_pending",
            AsyncMock(return_value=[]),
        ) as mock_svc:
            result = runner.invoke(
                list_pending_command, ["--gate", "preview_approval"],
            )
        assert result.exit_code == 0
        kwargs = mock_svc.call_args.kwargs
        assert kwargs["gate_name"] == "preview_approval"


# ---------------------------------------------------------------------------
# show-pending
# ---------------------------------------------------------------------------


class TestShowPendingCommand:
    def test_help(self, runner):
        result = runner.invoke(show_pending_command, ["--help"])
        assert result.exit_code == 0

    def test_json_mode_shape(self, runner, cli_env):
        payload = {
            "task_id": "t-1",
            "gate_name": "topic_decision",
            "artifact": {"topic": "T"},
            "gate_paused_at": "2026-04-26T12:00:00+00:00",
            "status": "in_progress",
            "topic": "T",
            "title": "Ti",
        }
        with patch(
            "services.approval_service.show_pending",
            AsyncMock(return_value=payload),
        ):
            result = runner.invoke(show_pending_command, ["t-1", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == payload

    def test_human_mode_renders_artifact(self, runner, cli_env):
        payload = {
            "task_id": "t-1",
            "gate_name": "topic_decision",
            "artifact": {"topic": "T", "rationale": "R"},
            "gate_paused_at": "2026-04-26T12:00:00+00:00",
            "status": "in_progress",
            "topic": "T",
            "title": "Ti",
        }
        with patch(
            "services.approval_service.show_pending",
            AsyncMock(return_value=payload),
        ):
            result = runner.invoke(show_pending_command, ["t-1"])
        assert result.exit_code == 0
        assert "topic_decision" in result.output
        assert "topic: T" in result.output
        assert "rationale: R" in result.output

    def test_task_not_found_exits_nonzero(self, runner, cli_env):
        with patch(
            "services.approval_service.show_pending",
            AsyncMock(side_effect=TaskNotFoundError("nope")),
        ):
            result = runner.invoke(show_pending_command, ["t-1"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# gates list / set
# ---------------------------------------------------------------------------


class TestGatesGroup:
    def test_help(self, runner):
        result = runner.invoke(gates_group, ["--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "set" in result.output

    def test_list_json_mode(self, runner, cli_env):
        rows = [
            {
                "gate_name": "topic_decision",
                "enabled": True,
                "setting_key": "pipeline_gate_topic_decision",
                "pending_count": 2,
            }
        ]
        with patch(
            "services.approval_service.list_gates",
            AsyncMock(return_value=rows),
        ):
            result = runner.invoke(gates_group, ["list", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == rows

    def test_list_human_empty(self, runner, cli_env):
        with patch(
            "services.approval_service.list_gates",
            AsyncMock(return_value=[]),
        ):
            result = runner.invoke(gates_group, ["list"])
        assert result.exit_code == 0
        assert "no gates configured" in result.output.lower()

    def test_set_on(self, runner, cli_env):
        async def _ok(**kwargs):
            return {
                "ok": True,
                "gate_name": kwargs["gate_name"],
                "enabled": kwargs["enabled"],
                "key": f"pipeline_gate_{kwargs['gate_name']}",
            }

        with patch(
            "services.approval_service.set_gate_enabled",
            AsyncMock(side_effect=_ok),
        ) as mock_svc:
            result = runner.invoke(
                gates_group, ["set", "topic_decision", "on"],
            )
        assert result.exit_code == 0
        assert "topic_decision" in result.output
        kwargs = mock_svc.call_args.kwargs
        assert kwargs["gate_name"] == "topic_decision"
        assert kwargs["enabled"] is True

    def test_set_off(self, runner, cli_env):
        with patch(
            "services.approval_service.set_gate_enabled",
            AsyncMock(return_value={
                "ok": True, "gate_name": "topic_decision",
                "enabled": False, "key": "pipeline_gate_topic_decision",
            }),
        ) as mock_svc:
            result = runner.invoke(
                gates_group, ["set", "topic_decision", "off"],
            )
        assert result.exit_code == 0
        kwargs = mock_svc.call_args.kwargs
        assert kwargs["enabled"] is False

    def test_set_invalid_state_rejected(self, runner, cli_env):
        # Click's Choice should reject anything that isn't on/off.
        result = runner.invoke(
            gates_group, ["set", "topic_decision", "maybe"],
        )
        assert result.exit_code != 0
