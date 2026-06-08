"""Unit test: content.persist_task writes the media artifacts (incl. the
ambient audio path) into task_metadata."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms.content_persist_task import run as persist_run


@pytest.mark.asyncio
async def test_persist_includes_media_artifacts():
    captured: dict = {}

    async def _update_task(*, task_id, updates):
        captured.update(updates)

    db = SimpleNamespace(
        pool=MagicMock(),
        update_task=_update_task,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )

    state = {
        "task_id": "t-1",
        "content": "body",
        "title": "Title",
        "database_service": db,
        "podcast_script": "POD",
        "video_scenes": ["a"],
        "short_summary_script": "SHORT",
        "video_shot_list": {"version": 1, "shots": [{"idx": 0}]},
        "short_shot_list": {"version": 1, "aspect": "9:16", "shots": [{"idx": 0}]},
        "video_ambient_audio_path": "/tmp/ambient.wav",
        "podcast_audio_path": "/tmp/podcast_tts.wav",
        "podcast_intro_audio_path": "/tmp/intro.wav",
    }

    # pipeline_versions + log_revision are best-effort; let them no-op.
    import services.pipeline_db as _pdb
    _pdb.PipelineDB = lambda *_a, **_k: SimpleNamespace(upsert_version=AsyncMock())

    result = await persist_run(state)

    meta = captured["task_metadata"]
    assert meta["podcast_script"] == "POD"
    assert meta["video_shot_list"] == {"version": 1, "shots": [{"idx": 0}]}
    assert meta["short_shot_list"] == {"version": 1, "aspect": "9:16", "shots": [{"idx": 0}]}
    assert meta["video_ambient_audio_path"] == "/tmp/ambient.wav"
    assert meta["podcast_audio_path"] == "/tmp/podcast_tts.wav"
    assert meta["podcast_intro_audio_path"] == "/tmp/intro.wav"
    assert result["status"] == "awaiting_approval"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_db(guarded_return: str | None = "ok", guarded_raise: Exception | None = None):
    captured: dict = {}

    async def _update(*, task_id, updates):
        captured.update(updates)

    async def _guarded(**kw):
        if guarded_raise is not None:
            raise guarded_raise
        return guarded_return

    return SimpleNamespace(
        pool=MagicMock(),
        update_task=_update,
        update_task_status_guarded=_guarded,
        _captured=captured,
    )


def _base_state(**overrides):
    state: dict = {
        "task_id": "t-99",
        "content": "body text",
        "title": "My Title",
        "database_service": _make_db(),
    }
    state.update(overrides)
    return state


def _patch_pdb():
    import services.pipeline_db as _pdb
    _pdb.PipelineDB = lambda *_a, **_k: SimpleNamespace(upsert_version=AsyncMock())


# ── error-path tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_task_id_raises():
    """No task_id in state → ValueError before any DB call."""
    state = {"content": "body", "database_service": _make_db()}
    with pytest.raises(ValueError, match="task_id"):
        await persist_run(state)


@pytest.mark.asyncio
async def test_missing_database_service_raises():
    """No database_service in state → ValueError before any DB call."""
    state = {"task_id": "t-1", "content": "body"}
    with pytest.raises(ValueError, match="database_service"):
        await persist_run(state)


@pytest.mark.asyncio
async def test_guard_returns_none_aborts():
    """Guard returning None means the task was stolen; must raise RuntimeError (GH-90)."""
    _patch_pdb()
    db = _make_db(guarded_return=None)
    with pytest.raises(RuntimeError, match="aborted"):
        await persist_run(_base_state(database_service=db))


@pytest.mark.asyncio
async def test_guard_exception_falls_back_and_completes():
    """Guard raising an exception → fallback, run still sets awaiting_approval."""
    _patch_pdb()
    db = _make_db(guarded_raise=Exception("transient db error"))
    result = await persist_run(_base_state(database_service=db))
    assert result["status"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_no_guard_method_falls_back():
    """DB without update_task_status_guarded → fallback path, completes normally."""
    _patch_pdb()
    db = SimpleNamespace(pool=MagicMock(), update_task=AsyncMock())
    result = await persist_run(_base_state(database_service=db))
    assert result["status"] == "awaiting_approval"
    assert result["post_id"] is None


# ── field-mapping tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seo_keywords_list_joined():
    """seo_keywords as list → comma-joined string stored in the DB update."""
    _patch_pdb()
    captured: dict = {}

    async def _update(*, task_id, updates):
        captured.update(updates)

    db = SimpleNamespace(
        pool=MagicMock(),
        update_task=_update,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )
    await persist_run(_base_state(database_service=db, seo_keywords=["gpu", "ai", "rtx"]))
    assert captured["seo_keywords"] == "gpu, ai, rtx"


@pytest.mark.asyncio
async def test_seo_keywords_string_passed_through():
    """seo_keywords as a plain string → stored as-is in the DB update."""
    _patch_pdb()
    captured: dict = {}

    async def _update(*, task_id, updates):
        captured.update(updates)

    db = SimpleNamespace(
        pool=MagicMock(),
        update_task=_update,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )
    await persist_run(_base_state(database_service=db, seo_keywords="gaming, rtx, dlss"))
    assert captured["seo_keywords"] == "gaming, rtx, dlss"


@pytest.mark.asyncio
async def test_explicit_quality_score_used():
    """Explicit quality_score in state takes precedence over quality_result."""
    _patch_pdb()
    captured: dict = {}

    async def _update(*, task_id, updates):
        captured.update(updates)

    db = SimpleNamespace(
        pool=MagicMock(),
        update_task=_update,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )
    await persist_run(_base_state(database_service=db, quality_score=77))
    assert captured["quality_score"] == 77


@pytest.mark.asyncio
async def test_existing_stages_preserved():
    """Pre-existing stages dict entries are kept; 5_post_created added as False."""
    _patch_pdb()
    result = await persist_run(_base_state(stages={"1_verify": True}))
    assert result["stages"]["1_verify"] is True
    assert result["stages"]["5_post_created"] is False


@pytest.mark.asyncio
async def test_title_falls_back_to_topic():
    """No title or seo_title → topic is used as the canonical title."""
    _patch_pdb()
    captured: dict = {}

    async def _update(*, task_id, updates):
        captured.update(updates)

    db = SimpleNamespace(
        pool=MagicMock(),
        update_task=_update,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )
    await persist_run(_base_state(
        database_service=db,
        title=None,
        seo_title=None,
        topic="AI Gaming Hardware",
    ))
    assert "AI Gaming Hardware" in captured["title"]
