"""Tests for the per-shot vision-QA frame scorer (video-quality Piece 2, §3.2)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


def _ollama_client(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"message": {"content": json_body}})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_scores_a_still_frame(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    sc = SiteConfig(initial_config={
        "qa_vision_model": "ollama/qwen3-vl:30b",
        "ollama_base_url": "http://ollama:11434",
    })
    client = _ollama_client('{"score": 82, "reason": "on-brand, sharp"}')
    res = await score_shot_frame(
        frame_path=str(frame), shot=_shot(), site_config=sc,
        http_client_factory=lambda *a, **k: client,
    )
    assert isinstance(res, ShotQAResult)
    assert res.score == 82.0
    # The vision call POSTs the frame as a base64 image in the Ollama chat shape.
    body = client.post.call_args.kwargs["json"]
    assert body["model"] == "qwen3-vl:30b"  # ollama/ prefix stripped
    assert body["messages"][0]["images"]  # non-empty images array
    # qwen3-vl is a thinking model — thinking MUST be off or `content` comes
    # back empty and the score never parses (silent no-op). #video-vision-qa.
    assert body["think"] is False


@pytest.mark.asyncio
async def test_no_model_returns_none_score(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    sc = SiteConfig(initial_config={"qa_vision_model": ""})
    res = await score_shot_frame(
        frame_path=str(frame), shot=_shot(), site_config=sc,
        http_client_factory=lambda *a, **k: None,
    )
    assert res.score is None


@pytest.mark.asyncio
async def test_unparseable_response_returns_none_score(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    sc = SiteConfig(initial_config={"qa_vision_model": "qwen3-vl:30b"})
    client = _ollama_client("the image looks fine to me")
    res = await score_shot_frame(
        frame_path=str(frame), shot=_shot(), site_config=sc,
        http_client_factory=lambda *a, **k: client,
    )
    assert res.score is None


@pytest.mark.asyncio
async def test_fenced_json_is_parsed(tmp_path):
    frame = tmp_path / "shot_00.png"
    frame.write_bytes(b"fake-png")
    sc = SiteConfig(initial_config={"qa_vision_model": "qwen3-vl:30b"})
    client = _ollama_client('```json\n{"score": 71, "reason": "ok"}\n```')
    res = await score_shot_frame(
        frame_path=str(frame), shot=_shot(), site_config=sc,
        http_client_factory=lambda *a, **k: client,
    )
    assert res.score == 71.0


@pytest.mark.asyncio
async def test_video_frame_is_extracted_before_scoring(tmp_path):
    clip = tmp_path / "shot_00.mp4"
    clip.write_bytes(b"fake-mp4")
    sc = SiteConfig(initial_config={"qa_vision_model": "qwen3-vl:30b"})
    client = _ollama_client('{"score": 50, "reason": "ok"}')
    extracted = tmp_path / "frame.png"
    extracted.write_bytes(b"extracted-png")
    with patch(
        "services.video_renderers.shot_vision_qa._extract_video_frame",
        AsyncMock(return_value=str(extracted)),
    ) as ex:
        res = await score_shot_frame(
            frame_path=str(clip), shot=_shot(source="wan21"), site_config=sc,
            http_client_factory=lambda *a, **k: client,
        )
    ex.assert_awaited_once()
    assert res.score == 50.0
