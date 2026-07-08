"""Tests for the per-shot vision-QA frame scorer (video-quality Piece 2, §3.2).

The scorer routes through the LiteLLM dispatcher (``dispatch_complete``) so the
vision call lands in cost_logs + Langfuse and picks up the GPU-pinned api_base
override for free — tests mock ``dispatch_complete`` (not raw httpx).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from schemas.video_shot_list import Shot
from services.site_config import SiteConfig
from services.video_renderers.shot_vision_qa import ShotQAResult, score_shot_frame


def _shot(source="image_gen", prompt="a cyan circuit board, dark navy backdrop"):
    return Shot(
        idx=0,
        duration_s=4.0,
        intent="opening payoff",
        source=source,
        prompt=prompt,
        narration_offset_s=0.0,
    )


def _completion(text: str):
    """A Completion-shaped stub (dispatch_complete returns Completion)."""
    from plugins.llm_provider import Completion

    return Completion(text=text, model="qwen3-vl:30b")


def _sc(**over):
    cfg = {"qa_vision_model": "ollama/qwen3-vl:30b", "ollama_base_url": "http://ollama:11434"}
    cfg.update(over)
    return SiteConfig(initial_config=cfg)


_DISPATCH = "services.llm_providers.dispatcher.dispatch_complete"


@pytest.mark.asyncio
async def test_scores_a_still_frame(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    dispatch = AsyncMock(return_value=_completion('{"score": 82, "reason": "on-brand, sharp"}'))
    with patch(_DISPATCH, dispatch):
        res = await score_shot_frame(
            frame_path=str(frame), shot=_shot(), site_config=_sc(), pool=object(),
        )
    assert isinstance(res, ShotQAResult)
    assert res.score == 82.0
    # Routed through the dispatcher with the multimodal image payload.
    args, kwargs = dispatch.call_args
    messages = args[1]
    model = args[2]
    assert model == "ollama/qwen3-vl:30b"  # full model id (dispatcher resolves prefix)
    content = messages[0]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    assert kwargs["tier"] == "standard"
    assert kwargs["phase"] == "qa_shot_vision"


@pytest.mark.asyncio
async def test_no_pool_returns_none_score(tmp_path):
    """Without a pool the scorer can't dispatch — fail-soft to no-score."""
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    res = await score_shot_frame(
        frame_path=str(frame), shot=_shot(), site_config=_sc(), pool=None,
    )
    assert res.score is None


@pytest.mark.asyncio
async def test_no_model_returns_none_score(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    res = await score_shot_frame(
        frame_path=str(frame), shot=_shot(), site_config=_sc(qa_vision_model=""),
        pool=object(),
    )
    assert res.score is None


@pytest.mark.asyncio
async def test_unparseable_response_returns_none_score(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    dispatch = AsyncMock(return_value=_completion("the image looks fine to me"))
    with patch(_DISPATCH, dispatch):
        res = await score_shot_frame(
            frame_path=str(frame), shot=_shot(), site_config=_sc(), pool=object(),
        )
    assert res.score is None


@pytest.mark.asyncio
async def test_fenced_json_is_parsed(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    dispatch = AsyncMock(return_value=_completion('```json\n{"score": 71, "reason": "ok"}\n```'))
    with patch(_DISPATCH, dispatch):
        res = await score_shot_frame(
            frame_path=str(frame), shot=_shot(), site_config=_sc(), pool=object(),
        )
    assert res.score == 71.0


@pytest.mark.asyncio
async def test_dispatch_failure_returns_none_score(tmp_path):
    """A dispatcher exception is fail-soft — no score, shot still ships."""
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    dispatch = AsyncMock(side_effect=RuntimeError("ollama unreachable"))
    with patch(_DISPATCH, dispatch):
        res = await score_shot_frame(
            frame_path=str(frame), shot=_shot(), site_config=_sc(), pool=object(),
        )
    assert res.score is None


@pytest.mark.asyncio
async def test_dispatch_uses_a_thinking_safe_token_budget(tmp_path):
    """qwen3-vl emits a long <think> trace that shares the token budget with
    the JSON answer — the same bug ``multi_model_qa._check_image_relevance``
    already hit and fixed by raising num_predict 400->1024 (poindexter#563,
    see the comment there). 300 is even smaller than the blog side's
    already-proven-insufficient original value, so this scorer needs the
    same generous budget."""
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    dispatch = AsyncMock(return_value=_completion('{"score": 82, "reason": "ok"}'))
    with patch(_DISPATCH, dispatch):
        await score_shot_frame(
            frame_path=str(frame), shot=_shot(), site_config=_sc(), pool=object(),
        )
    _, kwargs = dispatch.call_args
    assert kwargs["max_tokens"] >= 1024


@pytest.mark.asyncio
async def test_video_frame_is_extracted_before_scoring(tmp_path):
    clip = tmp_path / "shot_00.mp4"
    clip.write_bytes(b"fake-mp4")
    extracted = tmp_path / "frame.png"
    extracted.write_bytes(b"extracted-png")
    dispatch = AsyncMock(return_value=_completion('{"score": 50, "reason": "ok"}'))
    with patch(
        "services.video_renderers.shot_vision_qa._extract_video_frame",
        AsyncMock(return_value=str(extracted)),
    ) as ex, patch(_DISPATCH, dispatch):
        res = await score_shot_frame(
            frame_path=str(clip), shot=_shot(source="wan21"), site_config=_sc(),
            pool=object(),
        )
    ex.assert_awaited_once()
    assert res.score == 50.0
