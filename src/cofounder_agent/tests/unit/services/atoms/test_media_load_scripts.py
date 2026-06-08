"""Unit tests for the media.load_scripts Stage-2 entry atom (poindexter#689).

Pins the contract: given a task_id, load the persisted Stage-1 media artifacts
from pipeline_versions.task_metadata and surface the five declared channels —
robust to asyncpg returning jsonb as str-or-dict, and to tasks with no media.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms.media_load_scripts import run as load_scripts_run


def _pool_returning(row):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


@pytest.mark.asyncio
async def test_media_load_scripts_reads_task_metadata():
    meta = {
        "podcast_script": "POD",
        "video_scenes": ["a", "b"],
        "short_summary_script": "SHORT",
        "video_shot_list": {"version": 1, "shots": [{"idx": 0}]},
        "short_shot_list": {"version": 1, "aspect": "9:16", "shots": [{"idx": 0}]},
        "video_ambient_audio_path": "/tmp/ambient.wav",
    }
    pool, _ = _pool_returning({"task_metadata": meta})
    db = SimpleNamespace(pool=pool)

    result = await load_scripts_run({"task_id": "t-1", "database_service": db})

    assert result["podcast_script"] == "POD"
    assert result["video_scenes"] == ["a", "b"]
    assert result["short_summary_script"] == "SHORT"
    assert result["video_shot_list"] == {"version": 1, "shots": [{"idx": 0}]}
    assert result["short_shot_list"] == {"version": 1, "aspect": "9:16", "shots": [{"idx": 0}]}
    assert result["video_ambient_audio_path"] == "/tmp/ambient.wav"


@pytest.mark.asyncio
async def test_media_load_scripts_parses_json_string_metadata():
    """asyncpg returns jsonb as a str when no codec is registered — must parse."""
    meta = {
        "podcast_script": "POD",
        "video_scenes": [],
        "short_summary_script": "",
        "video_shot_list": None,
        "video_ambient_audio_path": "",
    }
    pool, _ = _pool_returning({"task_metadata": json.dumps(meta)})
    db = SimpleNamespace(pool=pool)

    result = await load_scripts_run({"task_id": "t-2", "database_service": db})

    assert result["podcast_script"] == "POD"


@pytest.mark.asyncio
async def test_media_load_scripts_handles_missing_metadata():
    """No pipeline_versions row → empty defaults, no raise (graceful no-op)."""
    pool, _ = _pool_returning(None)
    db = SimpleNamespace(pool=pool)

    result = await load_scripts_run({"task_id": "t-3", "database_service": db})

    assert result == {
        "podcast_script": "",
        "video_scenes": [],
        "short_summary_script": "",
        "video_shot_list": None,
        "short_shot_list": None,
        "video_ambient_audio_path": "",
    }


@pytest.mark.asyncio
async def test_media_load_scripts_pool_from_state():
    """Pool can come directly from state['pool'] (no database_service)."""
    meta = {"podcast_script": "DIRECT"}
    pool, _ = _pool_returning({"task_metadata": meta})

    result = await load_scripts_run({"task_id": "t-4", "pool": pool})

    assert result["podcast_script"] == "DIRECT"


@pytest.mark.asyncio
async def test_media_load_scripts_requires_task_id():
    pool, _ = _pool_returning(None)
    with pytest.raises(ValueError):
        await load_scripts_run({"database_service": SimpleNamespace(pool=pool)})


@pytest.mark.asyncio
async def test_media_load_scripts_requires_pool():
    with pytest.raises(ValueError):
        await load_scripts_run({"task_id": "t-5"})
