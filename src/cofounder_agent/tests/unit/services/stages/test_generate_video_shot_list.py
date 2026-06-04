"""Tests for ``modules.content.stages.generate_video_shot_list``.

The LLM call + DB pool are mocked. Focus: contract behavior at the
stage boundary (input context → output context + audit_log + skip
conditions). The schema-level validation is covered separately in
``tests/unit/schemas/test_video_shot_list.py``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.stages.generate_video_shot_list import (
    GenerateVideoShotListStage,
    _estimate_target_duration,
    _extract_json_object,
)


# ---------------------------------------------------------------------------
# Pure helpers — _estimate_target_duration + _extract_json_object
# ---------------------------------------------------------------------------


def test_estimate_target_duration_empty_script_returns_default() -> None:
    assert _estimate_target_duration("") == 60.0


def test_estimate_target_duration_from_word_count() -> None:
    """~150 words → ~60 seconds at 2.5 wps."""
    script = " ".join(["word"] * 150)
    assert 55.0 <= _estimate_target_duration(script) <= 65.0


def test_estimate_target_duration_clamps_below_20() -> None:
    """A 10-word script doesn't get a 4-second video — clamp up to 20."""
    assert _estimate_target_duration("ten words " * 5) == 20.0


def test_estimate_target_duration_clamps_above_300() -> None:
    """Long scripts get clamped to 5 minutes — renderer practical limit."""
    long_script = " ".join(["word"] * 5000)
    assert _estimate_target_duration(long_script) == 300.0


def test_extract_json_object_strips_code_fence() -> None:
    text = '```json\n{"shots": []}\n```'
    assert _extract_json_object(text) == '{"shots": []}'


def test_extract_json_object_handles_prose_prefix() -> None:
    text = 'Here is the shot list:\n{"version": 1}'
    assert _extract_json_object(text) == '{"version": 1}'


def test_extract_json_object_balances_brackets() -> None:
    """The whole object including nested braces gets returned."""
    text = '{"a": {"b": 1}, "c": 2}'
    assert _extract_json_object(text) == '{"a": {"b": 1}, "c": 2}'


def test_extract_json_object_returns_none_when_no_object() -> None:
    assert _extract_json_object("no json here") is None
    assert _extract_json_object("") is None


# ---------------------------------------------------------------------------
# Stage skip conditions — non-critical, halts_on_failure=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_no_title() -> None:
    stage = GenerateVideoShotListStage()
    result = await stage.execute(
        context={"title": "", "content": "body", "podcast_script": "script"},
        config={},
    )
    assert result.ok
    assert result.metrics["skipped"] is True


@pytest.mark.asyncio
async def test_skips_when_no_podcast_script() -> None:
    """Without the narration script, shot durations can't be aligned."""
    stage = GenerateVideoShotListStage()
    result = await stage.execute(
        context={"title": "t", "content": "c", "podcast_script": ""},
        config={},
    )
    assert result.ok
    assert result.metrics["skipped"] is True


@pytest.mark.asyncio
async def test_skips_when_no_db_pool() -> None:
    """Tests / bootstrap path — no DB → no LLM call. Non-critical."""
    stage = GenerateVideoShotListStage()
    result = await stage.execute(
        context={
            "title": "t", "content": "c", "podcast_script": "script",
            "database_service": None,
        },
        config={},
    )
    assert result.ok


# ---------------------------------------------------------------------------
# Stage happy path + failure modes — LLM stubbed
# ---------------------------------------------------------------------------


def _make_valid_director_output() -> str:
    """JSON output that satisfies the schema validator."""
    return json.dumps({
        "version": 1,
        "total_duration_s": 15.0,
        "shots": [
            {
                "idx": 0, "duration_s": 5.0, "intent": "establish",
                "source": "pexels", "query": "data center",
                "narration_offset_s": 0.0,
            },
            {
                "idx": 1, "duration_s": 5.0, "intent": "abstract",
                "source": "sdxl_kenburns",
                "prompt": "a glass door with data flowing through",
                "narration_offset_s": 5.0,
            },
            {
                "idx": 2, "duration_s": 5.0, "intent": "closer",
                "source": "pexels", "query": "sunset over server farm",
                "narration_offset_s": 10.0,
            },
        ],
        "director_model": "test-model",
        "director_prompt_version": "v1",
        "director_decided_at": "2026-05-28T00:00:00+00:00",
    })


def _make_db_service() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    db = MagicMock()
    db.pool = pool
    return db


@pytest.mark.asyncio
async def test_happy_path_persists_shot_list_to_context() -> None:
    db_service = _make_db_service()
    context = {
        "title": "Test Post",
        "content": "Some content " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1",
        "database_service": db_service,
        "site_config": MagicMock(get=MagicMock(return_value="test-model")),
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.llm_providers.dispatcher.dispatch_complete") as mock_dispatch, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="rendered prompt")
        mock_dispatch.return_value = MagicMock(text=_make_valid_director_output())
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert "video_shot_list" in context
    assert len(context["video_shot_list"]["shots"]) == 3
    # Audit log got the success event.
    audit_call = db_service.pool.execute.call_args
    assert "video_director.shot_list_produced" in audit_call.args[1]


@pytest.mark.asyncio
async def test_llm_failure_logs_audit_does_not_raise() -> None:
    """LLM dispatch raising must be swallowed — director is non-critical."""
    db_service = _make_db_service()
    context = {
        "title": "t", "content": "c body " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1", "database_service": db_service,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.llm_providers.dispatcher.dispatch_complete") as mock_dispatch, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="prompt")
        mock_dispatch.side_effect = RuntimeError("model unavailable")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok  # halts_on_failure=False
    assert result.metrics.get("failed") is True
    assert "video_shot_list" not in context
    # Audit log got the failure event.
    failure_calls = [
        c for c in db_service.pool.execute.call_args_list
        if "video_director.shot_list_failed" in c.args[1]
    ]
    assert failure_calls


@pytest.mark.asyncio
async def test_invalid_json_output_records_failure() -> None:
    """Director returning prose-only / malformed → failure metric."""
    db_service = _make_db_service()
    context = {
        "title": "t", "content": "c body " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1", "database_service": db_service,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.llm_providers.dispatcher.dispatch_complete") as mock_dispatch, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="prompt")
        mock_dispatch.return_value = MagicMock(text="I refuse to output JSON.")
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert result.metrics.get("failed") is True
    assert "video_shot_list" not in context


@pytest.mark.asyncio
async def test_invalid_schema_output_records_failure() -> None:
    """Director returning JSON that fails schema validation → failure."""
    bad_output = json.dumps({
        "version": 1,
        "total_duration_s": 100.0,  # Won't match shot durations
        "shots": [
            {
                "idx": 0, "duration_s": 5.0, "intent": "x",
                "source": "pexels", "query": "test",
                "narration_offset_s": 0.0,
            },
        ],
        "director_model": "test",
        "director_prompt_version": "v1",
        "director_decided_at": "2026-05-28T00:00:00+00:00",
    })
    db_service = _make_db_service()
    context = {
        "title": "t", "content": "c body " * 50,
        "podcast_script": "script " * 40,
        "task_id": "task-1", "database_service": db_service,
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.llm_providers.dispatcher.dispatch_complete") as mock_dispatch, \
         patch("services.gpu_scheduler.gpu") as mock_gpu:
        mock_pm.return_value.get_prompt = MagicMock(return_value="prompt")
        mock_dispatch.return_value = MagicMock(text=bad_output)
        mock_gpu.lock = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        ))

        stage = GenerateVideoShotListStage()
        result = await stage.execute(context, {})

    assert result.ok
    assert result.metrics.get("failed") is True
