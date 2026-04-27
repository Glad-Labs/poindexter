"""Click CLI tests for ``poindexter topics`` (#146).

The CLI commands are thin Click wrappers — DSN resolution + pool
factory + service-module call. We patch the pool factory and the
service-module functions so the test suite exercises the Click glue
(option parsing, JSON mode, --source filter, exit codes) without a
live DB.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.topics import topics_group


# ---------------------------------------------------------------------------
# Shared fixture — patches DSN resolution + pool factory + site_config
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

    fake_pool = MagicMock()
    fake_pool.close = AsyncMock(return_value=None)
    fake_site_config = MagicMock()

    async def _make_pool():
        return fake_pool

    async def _make_site_config(_pool):
        return fake_site_config

    p1 = patch("poindexter.cli.topics._make_pool", side_effect=_make_pool)
    p2 = patch(
        "poindexter.cli.topics._make_site_config",
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
# topics list
# ---------------------------------------------------------------------------


class TestTopicsList:
    def test_help(self, runner):
        result = runner.invoke(topics_group, ["list", "--help"])
        assert result.exit_code == 0
        assert "topic_decision" in result.output.lower() or \
               "topic-decision" in result.output.lower() or \
               "topic decision" in result.output.lower() or \
               "queue" in result.output.lower()

    def test_empty_queue_exits_zero(self, runner, cli_env):
        with patch(
            "services.approval_service.list_pending",
            AsyncMock(return_value=[]),
        ):
            result = runner.invoke(topics_group, ["list"])
        assert result.exit_code == 0
        assert "no topics" in result.output.lower()

    def test_renders_table_when_present(self, runner, cli_env):
        rows = [
            {
                "task_id": "abcd1234efgh5678",
                "gate_name": "topic_decision",
                "gate_paused_at": "2026-04-26T12:00:00+00:00",
                "status": "in_progress",
                "topic": "Custom water cooling 2026",
                "title": None,
                "artifact": {
                    "topic": "Custom water cooling 2026",
                    "source": "manual",
                    "tags": ["pc-hardware"],
                },
            },
        ]
        with patch(
            "services.approval_service.list_pending",
            AsyncMock(return_value=rows),
        ):
            result = runner.invoke(topics_group, ["list"])
        assert result.exit_code == 0
        assert "abcd1234" in result.output
        assert "manual" in result.output
        assert "Custom water cooling 2026" in result.output

    def test_json_output_shape(self, runner, cli_env):
        rows = [
            {
                "task_id": "t-1",
                "gate_name": "topic_decision",
                "gate_paused_at": "2026-04-26T12:00:00+00:00",
                "artifact": {"topic": "Hello", "source": "manual"},
            },
        ]
        with patch(
            "services.approval_service.list_pending",
            AsyncMock(return_value=rows),
        ):
            result = runner.invoke(topics_group, ["list", "--json"])
        assert result.exit_code == 0
        # Should be valid JSON list.
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["task_id"] == "t-1"

    def test_source_filter(self, runner, cli_env):
        rows = [
            {
                "task_id": "t-manual",
                "gate_name": "topic_decision",
                "gate_paused_at": None,
                "artifact": {"topic": "Manual idea", "source": "manual"},
            },
            {
                "task_id": "t-anticipation",
                "gate_name": "topic_decision",
                "gate_paused_at": None,
                "artifact": {"topic": "Auto idea", "source": "anticipation_engine"},
            },
        ]
        with patch(
            "services.approval_service.list_pending",
            AsyncMock(return_value=rows),
        ):
            result = runner.invoke(
                topics_group, ["list", "--source", "manual", "--json"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert len(parsed) == 1
        assert parsed[0]["task_id"] == "t-manual"


# ---------------------------------------------------------------------------
# topics show
# ---------------------------------------------------------------------------


class TestTopicsShow:
    def test_renders_artifact(self, runner, cli_env):
        artifact = {
            "topic": "AI inference on local hardware",
            "primary_keyword": "ai inference",
            "tags": ["ai", "hardware"],
            "source": "manual",
            "category_suggestion": "ai-ml",
            "research_summary": "Local inference is competitive on RTX 5090.",
            "score_signals": {"novelty": 0.7, "internal_link_potential": None,
                              "category_balance": "good"},
        }
        with patch(
            "services.approval_service.show_pending",
            AsyncMock(return_value={
                "task_id": "t-1",
                "gate_name": "topic_decision",
                "gate_paused_at": None,
                "status": "in_progress",
                "artifact": artifact,
            }),
        ):
            result = runner.invoke(topics_group, ["show", "t-1"])
        assert result.exit_code == 0
        assert "AI inference on local hardware" in result.output
        assert "ai inference" in result.output
        assert "Local inference is competitive" in result.output

    def test_rejects_wrong_gate(self, runner, cli_env):
        with patch(
            "services.approval_service.show_pending",
            AsyncMock(return_value={
                "task_id": "t-1",
                "gate_name": "preview_approval",
                "artifact": {},
            }),
        ):
            result = runner.invoke(topics_group, ["show", "t-1"])
        assert result.exit_code != 0
        assert "preview_approval" in result.output


# ---------------------------------------------------------------------------
# topics approve / reject
# ---------------------------------------------------------------------------


class TestTopicsApprove:
    def test_calls_service_with_topic_decision(self, runner, cli_env):
        async def _ok(**kwargs):
            return {
                "ok": True,
                "task_id": kwargs["task_id"],
                "gate_name": kwargs["gate_name"],
                "previous_status": "in_progress",
                "feedback": kwargs.get("feedback") or "",
            }

        with patch(
            "services.approval_service.approve",
            AsyncMock(side_effect=_ok),
        ) as mock_svc:
            result = runner.invoke(
                topics_group, ["approve", "t-1", "--feedback", "yes"],
            )
        assert result.exit_code == 0
        # Verify the service was called with gate_name="topic_decision".
        assert mock_svc.await_count == 1
        kwargs = mock_svc.await_args.kwargs
        assert kwargs["gate_name"] == "topic_decision"
        assert kwargs["feedback"] == "yes"

    def test_json_output(self, runner, cli_env):
        async def _ok(**kwargs):
            return {"ok": True, "task_id": "t-1", "gate_name": "topic_decision"}

        with patch(
            "services.approval_service.approve",
            AsyncMock(side_effect=_ok),
        ):
            result = runner.invoke(
                topics_group, ["approve", "t-1", "--json"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True


class TestTopicsReject:
    def test_calls_service_with_topic_decision(self, runner, cli_env):
        async def _ok(**kwargs):
            return {
                "ok": True,
                "task_id": kwargs["task_id"],
                "gate_name": kwargs["gate_name"],
                "new_status": "dismissed",
                "reason": kwargs.get("reason") or "",
            }

        with patch(
            "services.approval_service.reject",
            AsyncMock(side_effect=_ok),
        ) as mock_svc:
            result = runner.invoke(
                topics_group, ["reject", "t-1", "--reason", "off-brand"],
            )
        assert result.exit_code == 0
        kwargs = mock_svc.await_args.kwargs
        assert kwargs["gate_name"] == "topic_decision"
        assert kwargs["reason"] == "off-brand"
        assert "dismissed" in result.output


# ---------------------------------------------------------------------------
# topics propose
# ---------------------------------------------------------------------------


class TestTopicsPropose:
    def test_help(self, runner):
        result = runner.invoke(topics_group, ["propose", "--help"])
        assert result.exit_code == 0
        assert "--topic" in result.output

    def test_missing_topic_fails(self, runner, cli_env):
        # No --topic flag → Click's required-option error.
        result = runner.invoke(topics_group, ["propose"])
        assert result.exit_code != 0

    def test_empty_topic_fails(self, runner, cli_env):
        result = runner.invoke(topics_group, ["propose", "--topic", "   "])
        assert result.exit_code != 0

    def test_calls_service(self, runner, cli_env):
        async def _ok(**kwargs):
            return {
                "ok": True,
                "task_id": "t-new",
                "topic": kwargs["topic"],
                "awaiting_gate": "topic_decision",
                "status": "pending",
                "gate_enabled": True,
                "queue_full": False,
            }

        with patch(
            "services.topic_proposal_service.propose_topic",
            AsyncMock(side_effect=_ok),
        ) as mock_svc:
            result = runner.invoke(
                topics_group,
                [
                    "propose",
                    "--topic", "AI inference on RTX 5090",
                    "--keyword", "rtx 5090 inference",
                    "--tags", "ai,hardware",
                    "--category", "hardware",
                    "--source", "manual",
                ],
            )
        assert result.exit_code == 0
        assert "t-new" in result.output
        kwargs = mock_svc.await_args.kwargs
        assert kwargs["topic"] == "AI inference on RTX 5090"
        assert kwargs["primary_keyword"] == "rtx 5090 inference"
        assert kwargs["tags"] == ["ai", "hardware"]
        assert kwargs["category"] == "hardware"
        assert kwargs["source"] == "manual"

    def test_json_output(self, runner, cli_env):
        async def _ok(**kwargs):
            return {
                "ok": True,
                "task_id": "t-new",
                "topic": kwargs["topic"],
                "awaiting_gate": "topic_decision",
                "status": "pending",
                "gate_enabled": True,
                "queue_full": False,
            }

        with patch(
            "services.topic_proposal_service.propose_topic",
            AsyncMock(side_effect=_ok),
        ):
            result = runner.invoke(
                topics_group,
                ["propose", "--topic", "Hello", "--json"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["task_id"] == "t-new"

    def test_queue_full_exits_nonzero(self, runner, cli_env):
        async def _full(**kwargs):
            return {
                "ok": False,
                "task_id": None,
                "topic": kwargs["topic"],
                "awaiting_gate": None,
                "gate_enabled": True,
                "queue_full": True,
                "detail": "Topic queue is full (cap=50). Drain pending topics first.",
            }

        with patch(
            "services.topic_proposal_service.propose_topic",
            AsyncMock(side_effect=_full),
        ):
            result = runner.invoke(
                topics_group, ["propose", "--topic", "Sample"],
            )
        assert result.exit_code != 0
        assert "queue is full" in result.output.lower() or "cap" in result.output.lower()
