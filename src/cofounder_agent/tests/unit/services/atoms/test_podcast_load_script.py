"""Unit tests for the ``podcast.load_script`` Stage-3 entry atom (#689)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from modules.content.atoms import podcast_load_script


class _FakeConn:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def fetchrow(self, *a: Any, **k: Any) -> Any:
        return self._row


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *a: Any) -> bool:
        return False


class _FakePool:
    def __init__(self, row: Any) -> None:
        self._row = row

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(_FakeConn(self._row))


@pytest.mark.asyncio
async def test_loads_podcast_script_and_intro() -> None:
    row = {
        "task_metadata": {
            "podcast_script": "Hello listeners, welcome back.",
            "podcast_intro_audio_path": "/tmp/intro.wav",
        }
    }
    result = await podcast_load_script.run({"task_id": "t1", "pool": _FakePool(row)})
    assert result["podcast_script"] == "Hello listeners, welcome back."
    assert result["podcast_intro_audio_path"] == "/tmp/intro.wav"


@pytest.mark.asyncio
async def test_handles_jsonb_returned_as_string() -> None:
    row = {"task_metadata": json.dumps({"podcast_script": "Hi"})}
    result = await podcast_load_script.run({"task_id": "t1", "pool": _FakePool(row)})
    assert result["podcast_script"] == "Hi"


@pytest.mark.asyncio
async def test_no_persisted_script_fails_loud() -> None:
    """No pipeline_versions row → raise with the compose-repair hint.

    Was a silent empty-defaults pass-through: the first live
    architect-composed podcast plan wired this loader against a task with
    no scripts and the whole media chain "succeeded" in 0ms producing
    empty artifacts (feedback_no_silent_defaults)."""
    with pytest.raises(ValueError, match="generate_media_scripts"):
        await podcast_load_script.run({"task_id": "t1", "pool": _FakePool(None)})


@pytest.mark.asyncio
async def test_requires_task_id_and_pool() -> None:
    with pytest.raises(ValueError):
        await podcast_load_script.run({"task_id": "t1"})  # no pool
    with pytest.raises(ValueError):
        await podcast_load_script.run({"pool": _FakePool(None)})  # no task_id
