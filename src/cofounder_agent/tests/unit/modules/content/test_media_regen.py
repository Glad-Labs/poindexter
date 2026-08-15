"""Unit tests for ``modules/content/media_regen.py`` (2026-08-15).

The shared regen core behind the ``regen_media_scripts`` CLI and the
``backfill_media_scripts`` job. The invariants that matter:

- video-only mode is REQUESTED (the stage must not touch podcast artifacts);
- the writeback carries ONLY video keys;
- every nothing-usable path writes NOTHING (fail-conservative — a caller can
  retry a later cycle with the task no worse off);
- ``apply=False`` never writes;
- ``apply=True`` heals: writeback + asset delete + approvals reset + marker
  clear.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.media_regen import regen_video_scripts

_TASK = "11111111-2222-3333-4444-555555555555"
_SHOT_LIST = {"shots": [{"idx": 0}, {"idx": 1}], "total_duration_s": 30.0}
_SHORT_LIST = {"shots": [{"idx": 0}], "total_duration_s": 12.0}


class _FakePool:
    def __init__(self, *, post_row: Any = "default", version_row: Any = "default"):
        self.post_row = (
            {"post_id": "p1", "title": "T", "content": "body", "seo_title": None}
            if post_row == "default" else post_row
        )
        self.version_row = (
            {"id": 42, "version": 3} if version_row == "default" else version_row
        )
        self.executes: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):  # type: ignore[no-untyped-def]
        if "FROM posts" in sql:
            return self.post_row
        return self.version_row

    async def execute(self, sql, *args):  # type: ignore[no-untyped-def]
        self.executes.append((sql, args))
        return "OK 1"


def _stage(updates: dict | None, detail: str = "") -> MagicMock:
    stage = MagicMock()
    stage.execute = AsyncMock(
        return_value=MagicMock(context_updates=updates or {}, detail=detail),
    )
    return stage


def _patch_stages(
    monkeypatch,
    *,
    scripts_updates: dict | None,
    director_updates: dict | None = None,
    review_updates: dict | None = None,
):
    """Patch the three stage classes at their source modules (the core
    imports them inside the function body). Returns the scripts stage so a
    test can inspect the context it received."""
    scripts_stage = _stage(scripts_updates, "scripts detail")
    monkeypatch.setattr(
        "modules.content.stages.generate_media_scripts.GenerateMediaScriptsStage",
        lambda: scripts_stage,
    )
    monkeypatch.setattr(
        "modules.content.stages.generate_video_shot_list.GenerateVideoShotListStage",
        lambda: _stage(director_updates),
    )
    monkeypatch.setattr(
        "modules.content.stages.review_video_shot_list.ReviewVideoShotListStage",
        lambda: _stage(review_updates),
    )
    return scripts_stage


_GOOD_SCRIPTS = {
    "video_long_script": "A fresh long narration. " * 10,
    "short_summary_script": "A fresh short. " * 5,
    "video_scenes": ["scene one", "scene two"],
}


async def _run(pool, *, apply: bool):
    return await regen_video_scripts(
        _TASK,
        pool=pool,
        site_config=MagicMock(),
        platform=MagicMock(),
        database_service=MagicMock(),
        apply=apply,
    )


@pytest.mark.unit
class TestRegenVideoScripts:
    async def test_requests_video_only_mode(self, monkeypatch):
        """The load-bearing flag: without it the stage regenerates the
        podcast script and synthesizes fresh podcast audio for a published
        episode."""
        scripts_stage = _patch_stages(
            monkeypatch,
            scripts_updates=_GOOD_SCRIPTS,
            director_updates={"video_shot_list": _SHOT_LIST},
        )
        await _run(_FakePool(), apply=False)

        ctx = scripts_stage.execute.call_args[0][0]
        assert ctx["media_scripts_video_only"] is True
        assert ctx["task_id"] == _TASK

    async def test_apply_writes_only_video_keys_and_heals(self, monkeypatch):
        _patch_stages(
            monkeypatch,
            scripts_updates=_GOOD_SCRIPTS,
            director_updates={"video_shot_list": _SHOT_LIST},
            review_updates={"short_shot_list": _SHORT_LIST},
        )
        pool = _FakePool()

        outcome = await _run(pool, apply=True)

        assert outcome.ok
        assert outcome.long_shots == 2 and outcome.short_shots == 1
        sqls = " ".join(sql for sql, _ in pool.executes)
        assert "jsonb_set" in sqls
        assert "DELETE FROM media_assets" in sqls
        assert "SET status = 'pending'" in sqls
        assert "media_pipeline_dispatched_at = NULL" in sqls

        written = json.loads(pool.executes[0][1][1])
        assert set(written) == {
            "video_long_script", "short_summary_script", "video_scenes",
            "video_shot_list", "short_shot_list",
        }  # podcast keys must never ride along
        assert len(written["video_shot_list"]["shots"]) == 2

    async def test_dry_run_writes_nothing_but_returns_scripts(self, monkeypatch):
        _patch_stages(
            monkeypatch,
            scripts_updates=_GOOD_SCRIPTS,
            director_updates={"video_shot_list": _SHOT_LIST},
        )
        pool = _FakePool()

        outcome = await _run(pool, apply=False)

        assert outcome.ok
        assert pool.executes == []
        assert outcome.long_script.startswith("A fresh long narration.")
        assert "dry run" in outcome.detail

    async def test_empty_long_script_writes_nothing(self, monkeypatch):
        """A GPU-busy skip inside the stage yields no long script — the core
        must refuse to write (an empty writeback would be the original bug,
        now self-inflicted)."""
        _patch_stages(monkeypatch, scripts_updates={"video_long_script": ""})
        pool = _FakePool()

        outcome = await _run(pool, apply=True)

        assert outcome.ok is False
        assert pool.executes == []
        assert "no video_long_script" in outcome.detail

    async def test_no_shot_list_writes_nothing(self, monkeypatch):
        """Fresh scripts with a skipped director must not be written either:
        the shot list was planned over the OLD (podcast-fallback) content, so
        a scripts-only writeback would freeze a plan/narration mismatch."""
        _patch_stages(
            monkeypatch, scripts_updates=_GOOD_SCRIPTS, director_updates={},
        )
        pool = _FakePool()

        outcome = await _run(pool, apply=True)

        assert outcome.ok is False
        assert pool.executes == []
        assert "no shot list" in outcome.detail

    async def test_missing_post_row_is_conservative(self, monkeypatch):
        _patch_stages(monkeypatch, scripts_updates=_GOOD_SCRIPTS)
        pool = _FakePool(post_row=None)

        outcome = await _run(pool, apply=True)

        assert outcome.ok is False and pool.executes == []

    async def test_missing_version_row_is_conservative(self, monkeypatch):
        _patch_stages(monkeypatch, scripts_updates=_GOOD_SCRIPTS)
        pool = _FakePool(version_row=None)

        outcome = await _run(pool, apply=True)

        assert outcome.ok is False and pool.executes == []
