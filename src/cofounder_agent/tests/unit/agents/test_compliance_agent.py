"""
Unit tests for agents/compliance_agent/agent.py

Tests for ComplianceAgent class.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.compliance_agent.agent import ComplianceAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(workspace_root="/tmp/workspace") -> ComplianceAgent:
    with patch("agents.compliance_agent.agent.CrewAIToolsFactory") as mock_factory:
        mock_factory.get_document_tool.return_value = MagicMock()
        mock_factory.get_web_search_tool.return_value = MagicMock()
        agent = ComplianceAgent(workspace_root=workspace_root)
    return agent


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestComplianceAgentInit:
    def test_stores_workspace_root(self):
        agent = _make_agent("/my/workspace")
        assert agent.workspace_root == "/my/workspace"

    def test_tools_list_has_two_tools(self):
        agent = _make_agent()
        assert len(agent.tools) == 2

    def test_logs_info_on_init(self):
        with (
            patch("agents.compliance_agent.agent.logging") as mock_logging,
            patch("agents.compliance_agent.agent.CrewAIToolsFactory"),
        ):
            ComplianceAgent(workspace_root="/tmp")
            mock_logging.info.assert_called()


# ---------------------------------------------------------------------------
# _run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_returns_stdout_on_success(self):
        agent = _make_agent()
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"output text", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await agent._run_command(["echo", "output text"])

        assert result == "output text"

    @pytest.mark.asyncio
    async def test_returns_stderr_on_nonzero_exit(self):
        agent = _make_agent()
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error message"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await agent._run_command(["failing-cmd"])

        assert result == "error message"

    @pytest.mark.asyncio
    async def test_returns_error_string_on_exception(self):
        agent = _make_agent()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            result = await agent._run_command(["nonexistent-binary"])

        assert "Error running" in result
        assert "nonexistent-binary" in result

    @pytest.mark.asyncio
    async def test_logs_error_on_exception(self):
        agent = _make_agent()

        with (
            patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("boom")),
            patch("agents.compliance_agent.agent.logging") as mock_logging,
        ):
            await agent._run_command(["cmd"])
            mock_logging.error.assert_called()


# ---------------------------------------------------------------------------
# run_security_audit
# ---------------------------------------------------------------------------


class TestRunSecurityAudit:
    @pytest.mark.asyncio
    async def test_returns_string_result(self):
        agent = _make_agent()

        async def fake_run_command(args):
            return f"OK: {args[0]}"

        agent._run_command = fake_run_command
        result = await agent.run_security_audit()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_result_contains_audit_sections(self):
        agent = _make_agent()

        async def fake_run_command(args):
            return "audit result"

        agent._run_command = fake_run_command
        result = await agent.run_security_audit()
        # Should include content from multiple audit sections
        assert "NPM" in result or "audit" in result.lower() or "Compliance" in result

    @pytest.mark.asyncio
    async def test_returns_error_message_on_exception(self):
        agent = _make_agent()

        async def exploding_run_command(args):
            raise RuntimeError("subprocess crashed")

        agent._run_command = exploding_run_command
        result = await agent.run_security_audit()
        # Should catch exception and return a friendly message
        assert isinstance(result, str)
        assert "error" in result.lower() or "sorry" in result.lower()

    @pytest.mark.asyncio
    async def test_logs_info_at_start(self):
        agent = _make_agent()

        async def fake_run_command(args):
            return "ok"

        agent._run_command = fake_run_command
        with patch("agents.compliance_agent.agent.logging") as mock_logging:
            await agent.run_security_audit()
            mock_logging.info.assert_called()
