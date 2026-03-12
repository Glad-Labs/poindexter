"""
Unit tests for agent_schemas.py

Tests field validation, defaults, and model behaviour without any DB or LLM calls.
"""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from schemas.agent_schemas import (
    AgentStatusEnum,
    SystemHealthEnum,
    AgentCommandEnum,
    AgentStatus,
    AllAgentsStatus,
    AgentCommand,
    AgentCommandResult,
    AgentLog,
    AgentLogs,
    MemoryStats,
    AgentHealth,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentStatusEnum:
    def test_all_values(self):
        assert AgentStatusEnum.IDLE == "idle"
        assert AgentStatusEnum.BUSY == "busy"
        assert AgentStatusEnum.ERROR == "error"
        assert AgentStatusEnum.OFFLINE == "offline"

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            AgentStatus(
                name="agent",
                type="content",
                status="unknown",  # type: ignore[arg-type]
            )


@pytest.mark.unit
class TestSystemHealthEnum:
    def test_all_values(self):
        assert SystemHealthEnum.HEALTHY == "healthy"
        assert SystemHealthEnum.DEGRADED == "degraded"
        assert SystemHealthEnum.ERROR == "error"


@pytest.mark.unit
class TestAgentCommandEnum:
    def test_all_values(self):
        assert AgentCommandEnum.EXECUTE == "execute"
        assert AgentCommandEnum.STOP == "stop"
        assert AgentCommandEnum.RESTART == "restart"
        assert AgentCommandEnum.RESET == "reset"
        assert AgentCommandEnum.STATUS == "status"


# ---------------------------------------------------------------------------
# AgentStatus
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentStatus:
    def _valid(self, **kwargs):
        defaults = {"name": "content_agent", "type": "content", "status": "idle"}
        defaults.update(kwargs)
        return AgentStatus(**defaults)  # type: ignore[arg-type]

    def test_minimal_valid(self):
        agent = self._valid()
        assert agent.name == "content_agent"
        assert agent.tasks_completed == 0
        assert agent.tasks_failed == 0
        assert agent.execution_time_avg == 0.0
        assert agent.uptime_seconds == 0
        assert agent.last_activity is None
        assert agent.error_message is None

    def test_name_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(name="")

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(name="a" * 101)

    def test_type_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(type="")

    def test_negative_tasks_completed_raises(self):
        with pytest.raises(ValidationError):
            self._valid(tasks_completed=-1)

    def test_negative_tasks_failed_raises(self):
        with pytest.raises(ValidationError):
            self._valid(tasks_failed=-1)

    def test_negative_execution_time_raises(self):
        with pytest.raises(ValidationError):
            self._valid(execution_time_avg=-0.1)

    def test_negative_uptime_raises(self):
        with pytest.raises(ValidationError):
            self._valid(uptime_seconds=-1)

    def test_with_datetime_last_activity(self):
        now = datetime.now(timezone.utc)
        agent = self._valid(last_activity=now)
        assert agent.last_activity == now

    def test_with_error_message(self):
        agent = self._valid(status="error", error_message="Connection refused")
        assert agent.error_message == "Connection refused"

    def test_error_message_too_long_raises(self):
        with pytest.raises(ValidationError):
            self._valid(error_message="x" * 1001)

    def test_all_status_values(self):
        for status in ["idle", "busy", "error", "offline"]:
            agent = self._valid(status=status)
            assert agent.status == status


# ---------------------------------------------------------------------------
# AllAgentsStatus
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAllAgentsStatus:
    def test_valid(self):
        agent = AgentStatus(name="agent1", type="content", status="idle")  # type: ignore[call-arg]
        all_status = AllAgentsStatus(
            status="healthy",  # type: ignore[arg-type]
            timestamp=datetime.now(timezone.utc),
            agents={"agent1": agent},
            system_health={"cpu": 30, "memory": 60},
        )
        assert all_status.status == SystemHealthEnum.HEALTHY
        assert "agent1" in all_status.agents

    def test_invalid_system_health_status(self):
        with pytest.raises(ValidationError):
            AllAgentsStatus(
                status="unknown",  # type: ignore[arg-type]
                timestamp=datetime.now(timezone.utc),
                agents={},
                system_health={},
            )


# ---------------------------------------------------------------------------
# AgentCommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentCommand:
    def test_valid_command(self):
        cmd = AgentCommand(command="execute")  # type: ignore[call-arg]
        assert cmd.command == AgentCommandEnum.EXECUTE
        assert cmd.parameters is None

    def test_with_parameters(self):
        cmd = AgentCommand(command="execute", parameters={"task_id": "abc123"})  # type: ignore[arg-type]
        assert cmd.parameters == {"task_id": "abc123"}

    def test_all_commands_valid(self):
        for cmd_val in ["execute", "stop", "restart", "reset", "status"]:
            cmd = AgentCommand(command=cmd_val)  # type: ignore[arg-type]
            assert cmd.command == cmd_val

    def test_invalid_command_raises(self):
        with pytest.raises(ValidationError):
            AgentCommand(command="fly")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AgentCommandResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentCommandResult:
    def test_valid(self):
        result = AgentCommandResult(
            status="success",
            message="Command executed",
            timestamp=datetime.now(timezone.utc),
        )
        assert result.status == "success"
        assert result.result is None

    def test_with_result(self):
        result = AgentCommandResult(
            status="success",
            message="Done",
            result={"output": "data"},
            timestamp=datetime.now(timezone.utc),
        )
        assert result.result == {"output": "data"}


# ---------------------------------------------------------------------------
# AgentLog
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentLog:
    def test_valid(self):
        log = AgentLog(
            timestamp=datetime.now(timezone.utc),
            level="INFO",
            agent="content_agent",
            message="Task started",
        )
        assert log.level == "INFO"
        assert log.context is None

    def test_with_context(self):
        log = AgentLog(
            timestamp=datetime.now(timezone.utc),
            level="ERROR",
            agent="content_agent",
            message="Task failed",
            context={"task_id": "abc"},
        )
        assert log.context == {"task_id": "abc"}


# ---------------------------------------------------------------------------
# AgentLogs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentLogs:
    def test_valid(self):
        log = AgentLog(
            timestamp=datetime.now(timezone.utc),
            level="INFO",
            agent="agent1",
            message="msg",
        )
        logs = AgentLogs(logs=[log], total=1, filtered_by={"level": "INFO"})
        assert logs.total == 1
        assert len(logs.logs) == 1

    def test_empty_logs(self):
        logs = AgentLogs(logs=[], total=0, filtered_by={})
        assert logs.total == 0


# ---------------------------------------------------------------------------
# MemoryStats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMemoryStats:
    def test_valid(self):
        stats = MemoryStats(
            total_memories=100,
            short_term_count=20,
            long_term_count=80,
            memory_usage_bytes=1024000,
            memory_usage_mb=1.024,
            by_agent={"agent1": {"count": 50}},
        )
        assert stats.total_memories == 100
        assert stats.last_cleanup is None

    def test_with_last_cleanup(self):
        now = datetime.now(timezone.utc)
        stats = MemoryStats(
            total_memories=50,
            short_term_count=10,
            long_term_count=40,
            memory_usage_bytes=512000,
            memory_usage_mb=0.512,
            by_agent={},
            last_cleanup=now,
        )
        assert stats.last_cleanup == now


# ---------------------------------------------------------------------------
# AgentHealth
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentHealth:
    def test_valid(self):
        health = AgentHealth(
            status="healthy",
            timestamp=datetime.now(timezone.utc),
            all_agents_running=True,
            error_count=0,
            warning_count=0,
            uptime_seconds=3600,
            details={"agent1": "ok"},
        )
        assert health.status == "healthy"
        assert health.all_agents_running is True

    def test_degraded_status(self):
        health = AgentHealth(
            status="degraded",
            timestamp=datetime.now(timezone.utc),
            all_agents_running=False,
            error_count=2,
            warning_count=5,
            uptime_seconds=7200,
            details={},
        )
        assert health.all_agents_running is False
        assert health.error_count == 2
