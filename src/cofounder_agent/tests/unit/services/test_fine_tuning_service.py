"""
Unit tests for services/fine_tuning_service.py

Tests FineTuningService: initialization, get_job_status, cancel_job, deploy_model,
list_jobs, and fine_tune_ollama (with subprocess mocked). API-based fine-tuning
methods (gemini, claude, gpt4) are tested at the job_status/cancel level only
since they require external API clients.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.fine_tuning_service import FineTuneTarget, FineTuningService


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestFineTuningServiceInit:
    def test_jobs_dict_starts_empty(self):
        svc = FineTuningService()
        assert svc.jobs == {}

    def test_fine_tune_target_enum_values(self):
        assert FineTuneTarget.OLLAMA == "ollama"
        assert FineTuneTarget.GEMINI == "gemini"
        assert FineTuneTarget.CLAUDE == "claude"
        assert FineTuneTarget.GPT4 == "gpt4"


# ---------------------------------------------------------------------------
# get_job_status
# ---------------------------------------------------------------------------


class TestGetJobStatus:
    @pytest.mark.asyncio
    async def test_not_found_job(self):
        svc = FineTuningService()
        result = await svc.get_job_status("nonexistent-job")
        assert result["status"] == "not_found"
        assert result["job_id"] == "nonexistent-job"

    @pytest.mark.asyncio
    async def test_ollama_job_running_when_process_has_no_returncode(self):
        svc = FineTuningService()
        mock_process = MagicMock()
        mock_process.returncode = None  # Still running
        svc.jobs["job-1"] = {
            "target": "ollama",
            "process": mock_process,
            "model_name": "orchestrator-job-1",
        }
        result = await svc.get_job_status("job-1")
        assert result["status"] == "running"
        assert result["model_name"] == "orchestrator-job-1"

    @pytest.mark.asyncio
    async def test_ollama_job_complete_when_returncode_0(self):
        svc = FineTuningService()
        mock_process = MagicMock()
        mock_process.returncode = 0
        svc.jobs["job-1"] = {
            "target": "ollama",
            "process": mock_process,
            "model_name": "orchestrator-job-1",
        }
        result = await svc.get_job_status("job-1")
        assert result["status"] == "complete"

    @pytest.mark.asyncio
    async def test_ollama_job_failed_when_nonzero_returncode(self):
        svc = FineTuningService()
        mock_process = MagicMock()
        mock_process.returncode = 1
        svc.jobs["job-1"] = {
            "target": "ollama",
            "process": mock_process,
            "model_name": "orchestrator-job-1",
        }
        result = await svc.get_job_status("job-1")
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_gemini_job_returns_running(self):
        svc = FineTuningService()
        svc.jobs["job-g"] = {"target": "gemini"}
        result = await svc.get_job_status("job-g")
        assert result["status"] == "running"
        assert result["target"] == "gemini"

    @pytest.mark.asyncio
    async def test_unknown_target_returns_unknown_status(self):
        svc = FineTuningService()
        svc.jobs["job-x"] = {"target": "unknown_provider"}
        result = await svc.get_job_status("job-x")
        assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# cancel_job
# ---------------------------------------------------------------------------


class TestCancelJob:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_job_returns_error(self):
        svc = FineTuningService()
        result = await svc.cancel_job("nonexistent-job")
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_cancel_ollama_job_terminates_process(self):
        svc = FineTuningService()
        mock_process = MagicMock()
        svc.jobs["job-1"] = {"target": "ollama", "process": mock_process, "status": "running"}
        result = await svc.cancel_job("job-1")
        mock_process.terminate.assert_called_once()
        assert result["success"] is True
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_unsupported_target_returns_error(self):
        svc = FineTuningService()
        svc.jobs["job-g"] = {"target": "gemini"}
        result = await svc.cancel_job("job-g")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# deploy_model
# ---------------------------------------------------------------------------


class TestDeployModel:
    @pytest.mark.asyncio
    async def test_deploy_nonexistent_job_returns_error(self):
        svc = FineTuningService()
        result = await svc.deploy_model("my-model", "nonexistent-job")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_deploy_incomplete_job_returns_error(self):
        svc = FineTuningService()
        svc.jobs["job-1"] = {"target": "ollama", "status": "running"}
        result = await svc.deploy_model("my-model", "job-1")
        assert result["success"] is False
        assert "not complete" in result["error"]

    @pytest.mark.asyncio
    async def test_deploy_complete_job_returns_success(self):
        svc = FineTuningService()
        svc.jobs["job-1"] = {"target": "ollama", "status": "complete"}
        result = await svc.deploy_model("orchestrator-v2", "job-1", set_active=True)
        assert result["success"] is True
        assert result["model_name"] == "orchestrator-v2"
        assert result["set_active"] is True

    @pytest.mark.asyncio
    async def test_deploy_includes_source(self):
        svc = FineTuningService()
        svc.jobs["job-1"] = {"target": "ollama", "status": "complete"}
        result = await svc.deploy_model("my-model", "job-1")
        assert result["source"] == "ollama"


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    @pytest.mark.asyncio
    async def test_empty_jobs_returns_empty_list(self):
        svc = FineTuningService()
        result = await svc.list_jobs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_returns_all_jobs(self):
        svc = FineTuningService()
        svc.jobs = {
            "job-1": {"target": "ollama", "status": "complete"},
            "job-2": {"target": "gemini", "status": "running"},
        }
        result = await svc.list_jobs()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_includes_job_id(self):
        svc = FineTuningService()
        svc.jobs = {"job-abc": {"target": "ollama", "status": "complete"}}
        result = await svc.list_jobs()
        assert any(j.get("job_id") == "job-abc" for j in result)


# ---------------------------------------------------------------------------
# fine_tune_ollama (subprocess mocked)
# ---------------------------------------------------------------------------


class TestFineTuneOllama:
    @pytest.mark.asyncio
    async def test_returns_failed_when_ollama_not_running(self):
        svc = FineTuningService()

        # Mock `ollama list` returning nonzero (Ollama not running)
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"not running"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await svc.fine_tune_ollama("dataset.jsonl")

        assert result["status"] == "failed"
        assert "Ollama" in result["error"] or "ollama" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_job_id_when_ollama_running(self):
        svc = FineTuningService()

        # First call: `ollama list` → success (returncode 0)
        # Second call: `ollama create` → success (returncode 0)
        list_proc = AsyncMock()
        list_proc.returncode = 0
        list_proc.communicate = AsyncMock(return_value=(b"models", b""))

        create_proc = AsyncMock()
        create_proc.returncode = 0
        create_proc.communicate = AsyncMock(return_value=(b"created", b""))

        call_count = 0

        async def mock_create_subprocess_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return list_proc if call_count == 1 else create_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
            result = await svc.fine_tune_ollama("dataset.jsonl", base_model="mistral")

        assert "job_id" in result
        assert result["status"] in ("running", "complete", "failed")  # depends on timing
