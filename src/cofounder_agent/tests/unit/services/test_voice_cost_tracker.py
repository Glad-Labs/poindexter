"""Unit tests for VoiceCostTrackerProcessor in services/voice_agent.py.

The tracker intercepts MetricsFrame (a Pipecat SystemFrame) after each
LLM turn and writes a cost_logs row via asyncpg. These tests verify:

- A MetricsFrame with LLMUsageMetricsData triggers a DB INSERT with the
  correct columns (phase="voice", provider="ollama", cost_usd=0.0, …).
- Non-metrics frames are passed through without touching the DB.
- MetricsFrames containing only TTS / TTFB metrics (no LLM usage) are
  passed through without touching the DB.
- A DB error is caught and logged; the frame still passes through so the
  pipeline is not interrupted.

Pipecat is not a dependency of the main cofounder_agent package (it lives
in the voice-agent container's own venv). The entire module is skipped
when pipecat is not importable via pytest.importorskip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pipecat = pytest.importorskip("pipecat", reason="pipecat not installed")

from pipecat.frames.frames import MetricsFrame, TextFrame  # noqa: E402
from pipecat.metrics.metrics import (  # noqa: E402
    LLMTokenUsage,
    LLMUsageMetricsData,
    TTSUsageMetricsData,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from services.voice_agent import VoiceCostTrackerProcessor  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llm_metrics_frame(prompt: int, completion: int) -> MetricsFrame:
    usage = LLMUsageMetricsData(
        processor="ollama",
        model="gemma:latest",
        value=LLMTokenUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
    )
    return MetricsFrame(data=[usage])


def _make_tracker(pool: Any) -> VoiceCostTrackerProcessor:
    tracker = VoiceCostTrackerProcessor(
        pool, model="gemma:latest", session_id="test-session-uuid"
    )
    tracker.push_frame = AsyncMock()
    return tracker


def _mock_pool() -> tuple[MagicMock, AsyncMock]:
    """Return (pool, conn) mocks wired for async-with pool.acquire()."""
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


from typing import Any


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_records_llm_usage_on_metrics_frame() -> None:
    mock_pool, mock_conn = _mock_pool()
    tracker = _make_tracker(mock_pool)

    frame = _llm_metrics_frame(100, 50)
    await tracker.process_frame(frame, FrameDirection.DOWNSTREAM)

    mock_conn.execute.assert_called_once()
    # Verify positional params to the INSERT
    _sql, *params = mock_conn.execute.call_args[0]
    assert "cost_logs" in _sql
    assert params[0] == "test-session-uuid"  # task_id
    assert params[1] == "voice"              # phase
    assert params[2] == "gemma:latest"       # model
    assert params[3] == "ollama"             # provider
    assert params[4] == 100                  # input_tokens
    assert params[5] == 50                   # output_tokens
    assert params[6] == 150                  # total_tokens
    assert params[7] == 0.0                  # cost_usd
    assert params[8] is True                 # success


@pytest.mark.asyncio
async def test_frame_passes_through_after_record() -> None:
    mock_pool, _ = _mock_pool()
    tracker = _make_tracker(mock_pool)

    frame = _llm_metrics_frame(10, 5)
    await tracker.process_frame(frame, FrameDirection.DOWNSTREAM)

    tracker.push_frame.assert_called_once_with(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_non_metrics_frame_passes_through_without_db() -> None:
    mock_pool = MagicMock()
    tracker = _make_tracker(mock_pool)

    frame = TextFrame(text="hello world")
    await tracker.process_frame(frame, FrameDirection.DOWNSTREAM)

    mock_pool.acquire.assert_not_called()
    tracker.push_frame.assert_called_once_with(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_tts_metrics_frame_skips_db() -> None:
    """A MetricsFrame carrying only TTS usage (no LLM usage) must not write."""
    mock_pool = MagicMock()
    tracker = _make_tracker(mock_pool)

    tts_usage = TTSUsageMetricsData(processor="kokoro", model="kokoro-82m", value=500)
    frame = MetricsFrame(data=[tts_usage])
    await tracker.process_frame(frame, FrameDirection.DOWNSTREAM)

    mock_pool.acquire.assert_not_called()
    tracker.push_frame.assert_called_once_with(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_db_error_is_swallowed_and_frame_still_passes() -> None:
    mock_conn = AsyncMock()
    mock_conn.execute.side_effect = RuntimeError("connection lost")
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    tracker = _make_tracker(mock_pool)
    frame = _llm_metrics_frame(20, 10)

    # Must not raise even though DB fails
    await tracker.process_frame(frame, FrameDirection.DOWNSTREAM)

    # Frame still forwarded downstream despite the error
    tracker.push_frame.assert_called_once_with(frame, FrameDirection.DOWNSTREAM)
