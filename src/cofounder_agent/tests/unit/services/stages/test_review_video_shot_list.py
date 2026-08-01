"""ReviewVideoShotListStage — director self-critique pass (Piece 1, spec §3.1).

Mirrors the dispatch-path test harness from test_generate_video_shot_list.py:
both services.gpu_scheduler.gpu and services.prompt_manager.get_prompt_manager
are patched, because the stage acquires the GPU lock and renders a skill prompt.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _valid_list(*, source1: str = "image_kenburns") -> dict:
    return {
        "version": 1,
        "aspect": "16:9",
        "total_duration_s": 10.0,
        "shots": [
            {"idx": 0, "duration_s": 5.0, "intent": "open", "source": "pexels",
             "query": "server room", "narration_offset_s": 0.0},
            {"idx": 1, "duration_s": 5.0, "intent": "close", "source": source1,
             "prompt": "flat vector circuit, deep navy and cyan, faceless",
             "narration_offset_s": 5.0},
        ],
        "director_model": "draft-model",
        "director_prompt_version": "v1.2",
        "director_decided_at": "2026-06-19T00:00:00+00:00",
    }


def _make_db() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    db = MagicMock()
    db.pool = pool
    return db


def _platform(
    *, dispatch_text: str | None = None, model: str = "reviewer", timeout: int = 300,
) -> MagicMock:
    p = MagicMock()
    p.config.get = MagicMock(return_value=model)
    # get_int: the configured timeout for the timeout knob, 0 retries so these
    # legacy fixtures stay single-dispatch (retry has its own tests below), and
    # the caller default (e.g. max_tokens 8192) for everything else.
    def _get_int(key: str, default: int = 0) -> int:
        if key == "video_director_timeout_seconds":
            return timeout
        if key == "video_director_max_retries":
            return 0
        return default
    p.config.get_int = MagicMock(side_effect=_get_int)
    p.dispatch.complete = AsyncMock(return_value=MagicMock(text=dispatch_text))
    return p


@pytest.mark.asyncio
async def test_revised_list_replaces_original() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")  # reviewer promoted a hero shot
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": _platform(dispatch_text=json.dumps(revised)),
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "wan21"


@pytest.mark.asyncio
async def test_long_review_prefers_video_long_script() -> None:
    """Silent-tail fix: the long review's reference script is the one the
    narration will voice (video_long_script, fallback podcast_script) —
    matching the director and media.render_narration."""
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    ctx = {
        "title": "T", "content": "C body " * 20,
        "podcast_script": "podcast " * 20,
        "video_long_script": "narration words " * 20,
        "video_shot_list": _valid_list(),
        "platform": _platform(dispatch_text=json.dumps(_valid_list())),
        "database_service": _make_db(),
        "task_id": "t-src",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    # First get_prompt call is the LONG review; its {podcast_script} template
    # var (legacy name) must carry the narration script.
    kw = mock_pm.return_value.get_prompt.call_args_list[0].kwargs
    assert kw["podcast_script"] == ctx["video_long_script"].strip()


@pytest.mark.asyncio
async def test_failure_keeps_original_non_halting() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    original = _valid_list()  # shot[1].source == "image_kenburns"
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": original,
        "platform": _platform(dispatch_text="I refuse to output JSON."),
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok  # non-halting
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "image_kenburns"


@pytest.mark.asyncio
async def test_skips_when_no_shot_list() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage
    result = await ReviewVideoShotListStage().execute({"task_id": "t"}, {})
    assert result.ok
    assert result.metrics.get("skipped") is True


@pytest.mark.asyncio
async def test_short_list_also_reviewed() -> None:
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    long_revised = _valid_list(source1="wan21")
    short_revised = _valid_list(source1="wan21")
    short_revised["aspect"] = "9:16"
    platform = _platform()
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text=json.dumps(long_revised)),
        MagicMock(text=json.dumps(short_revised)),
    ])
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "short_summary_script": "short " * 10,
        "video_shot_list": _valid_list(),
        "short_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert platform.dispatch.complete.call_count == 2
    assert result.context_updates["short_shot_list"]["aspect"] == "9:16"


@pytest.mark.asyncio
async def test_review_timeout_read_from_db_setting() -> None:
    """The per-call LLM timeout is read from ``video_director_timeout_seconds``
    and threaded into the dispatch — not hardcoded. Finding: the old hardcoded
    120s timed out the writer-grade reviewer mid shot-list (same bug the director
    had, #1750), leaving the draft list unreviewed on every run."""
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")
    platform = _platform(dispatch_text=json.dumps(revised), timeout=555)
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        await ReviewVideoShotListStage().execute(ctx, {})

    # timeout sourced from the DB setting (not the old hardcoded 120). get_int
    # is now called for several knobs (timeout / max_tokens / max_retries), so
    # scan the call list rather than asserting on the last call.
    assert any(
        c.args[0] == "video_director_timeout_seconds"
        for c in platform.config.get_int.call_args_list
    )
    _, kwargs = platform.dispatch.complete.call_args
    assert kwargs["timeout_s"] == 555


@pytest.mark.asyncio
async def test_review_recovers_unquoted_key_dialect() -> None:
    """The reviewer emits the same near-JSON dialect the director does
    (unquoted keys) — the shared ``_tolerant_json_loads`` repairs it
    deterministically instead of discarding the revision."""
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    dialect_revision = (
        '{ version: 1, aspect: "16:9", total_duration_s: 10.0, shots: ['
        '{ idx: 0, duration_s: 5.0, intent: "open", source: "pexels", '
        'query: "server room", narration_offset_s: 0.0 },'
        '{ idx: 1, duration_s: 5.0, intent: "close", source: "wan21", '
        'prompt: "flat vector circuit, faceless", narration_offset_s: 5.0 }'
        '], director_model: "reviewer", director_prompt_version: "review_v1", '
        'director_decided_at: "2026-07-03T00:00:00+00:00" }'
    )
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": _platform(dispatch_text=dialect_revision),
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert result.metrics["reviewed"] is True
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "wan21"


@pytest.mark.asyncio
async def test_review_dispatch_disables_thinking_by_default() -> None:
    """think=False reaches the reviewer dispatch (default-on
    video_director_disable_thinking). The reviewer is the same thinking-capable
    director model; leaving the reasoning channel on starves its revised JSON
    the same way it empties the director's, so the self-critique fell back to
    the draft on every run (2026-07-07 fix — mirrors the writer path, #2163)."""
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")
    # Dict-backed config so the disable-thinking key resolves to its default
    # ("true"), unlike _platform() which returns the model for every key.
    platform = MagicMock()
    _cfg = {"video_director_model": "ollama/gemma-4-31B-it-qat:latest"}
    platform.config.get = MagicMock(side_effect=lambda k, d=None: _cfg.get(k, d))
    platform.config.get_int = MagicMock(side_effect=lambda k, d=0: d)
    platform.dispatch.complete = AsyncMock(return_value=MagicMock(text=json.dumps(revised)))
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert platform.dispatch.complete.call_args.kwargs["think"] is False


@pytest.mark.asyncio
async def test_review_dispatch_uses_configured_max_tokens() -> None:
    """max_tokens (default 8192) reaches the reviewer dispatch — same headroom
    the director got, since the reviewer serializes the same JSON shape."""
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")
    platform = MagicMock()
    _cfg = {"video_director_model": "ollama/gemma-4-31B-it-qat:latest"}
    platform.config.get = MagicMock(side_effect=lambda k, d=None: _cfg.get(k, d))
    platform.config.get_int = MagicMock(side_effect=lambda k, d=0: d)
    platform.dispatch.complete = AsyncMock(return_value=MagicMock(text=json.dumps(revised)))
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        await ReviewVideoShotListStage().execute(ctx, {})

    assert platform.dispatch.complete.call_args.kwargs["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_review_retries_on_empty_extract_and_recovers() -> None:
    """The reviewer retries a no-JSON (truncated) revision — same guard as the
    director — so a verbose revision that overflows the budget isn't silently
    discarded (which would ship the unreviewed draft)."""
    from modules.content.stages.review_video_shot_list import ReviewVideoShotListStage

    revised = _valid_list(source1="wan21")
    platform = MagicMock()
    _cfg = {"video_director_model": "ollama/gemma-4-31B-it-qat:latest"}
    platform.config.get = MagicMock(side_effect=lambda k, d=None: _cfg.get(k, d))
    platform.config.get_int = MagicMock(side_effect=lambda k, d=0: d)  # max_retries=1
    platform.dispatch.complete = AsyncMock(side_effect=[
        MagicMock(text='{ "version": 1, "shots": [ {"idx": 0'),  # truncated → no object
        MagicMock(text=json.dumps(revised)),                       # retry → valid
    ])
    ctx = {
        "title": "T", "content": "C body " * 20, "podcast_script": "script " * 20,
        "video_shot_list": _valid_list(),
        "platform": platform,
        "database_service": _make_db(),
        "task_id": "t1",
    }

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm, \
         patch("services.gpu_scheduler.gpu", SimpleNamespace(lock=lambda *a, **k: _FakeLock())):
        mock_pm.return_value.get_prompt = MagicMock(return_value="review prompt")
        result = await ReviewVideoShotListStage().execute(ctx, {})

    assert result.ok
    assert platform.dispatch.complete.call_count == 2  # retried once
    assert result.metrics["reviewed"] is True
    assert result.context_updates["video_shot_list"]["shots"][1]["source"] == "wan21"
