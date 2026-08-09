"""Unit tests for BackfillVideoShotListsJob (poindexter#1001).

The recovery half of the stranded-piece fix. The invariants that matter are
the ones that keep a *failed* backfill harmless: a piece must never come out
of this job in a worse state than it went in, and a run that regenerates
nothing must not look like progress.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.jobs.backfill_video_shot_lists import (
    BackfillVideoShotListsJob,
)
from services.site_config import SiteConfig

_SHOT_LIST = {"shots": [{"idx": 0}, {"idx": 1}], "total_duration_s": 30.0}


class _FakeConn:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    async def execute(self, sql, *args):  # type: ignore[no-untyped-def]
        self._sink.append((sql, args))

    def transaction(self):  # type: ignore[no-untyped-def]
        class _Tx:
            async def __aenter__(_s):  # noqa: N805
                return None

            async def __aexit__(_s, *_a):  # noqa: N805
                return False

        return _Tx()


class _FakePool:
    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.writes: list = []

    async def fetch(self, _sql, *_a):  # type: ignore[no-untyped-def]
        return self._rows

    def acquire(self):  # type: ignore[no-untyped-def]
        pool = self

        class _Acq:
            async def __aenter__(_s):  # noqa: N805
                return _FakeConn(pool.writes)

            async def __aexit__(_s, *_a):  # noqa: N805
                return False

        return _Acq()


def _row(task="11111111-2222-3333-4444-555555555555"):
    return {
        "task_id": task,
        "version_id": 42,
        "title": "A Post",
        "content": "body text",
        "podcast_script": "narration",
        "video_long_script": "long narration",
        "short_summary_script": "short narration",
    }


def _cfg(**over):
    return {"_site_config": SiteConfig(initial_config=dict(over))}


def _patch_deps(monkeypatch, *, stage_result, platform=object()):
    monkeypatch.setattr(
        "services.di_wiring.build_platform_for_subprocess",
        lambda *a, **k: platform,
    )
    stage = MagicMock()
    stage.execute = AsyncMock(return_value=stage_result)
    monkeypatch.setattr(
        "modules.content.stages.generate_video_shot_list.GenerateVideoShotListStage",
        lambda: stage,
    )
    return stage


@pytest.mark.unit
class TestBackfillVideoShotLists:
    async def test_writes_lists_and_clears_the_marker(self, monkeypatch):
        """Both halves are required: without clearing the marker the piece
        stays retired no matter how good its new shot list is."""
        monkeypatch.setattr(
            "modules.content.jobs.backfill_video_shot_lists.emit_finding", lambda **kw: None,
        )
        _patch_deps(
            monkeypatch,
            stage_result=MagicMock(
                context_updates={"video_shot_list": _SHOT_LIST}, detail="2 shots",
            ),
        )
        pool = _FakePool([_row()])

        result = await BackfillVideoShotListsJob().run(pool, _cfg())

        assert result.ok and result.changes_made == 1
        sqls = " ".join(w[0] for w in pool.writes)
        assert "jsonb_set" in sqls
        assert "media_pipeline_dispatched_at = NULL" in sqls
        written = json.loads(pool.writes[0][1][1])
        assert len(written["shots"]) == 2

    async def test_empty_director_output_changes_nothing(self, monkeypatch):
        """The director skipping again (busy GPU) must be a true no-op — not a
        cleared marker pointing at a piece that still cannot render."""
        monkeypatch.setattr(
            "modules.content.jobs.backfill_video_shot_lists.emit_finding", lambda **kw: None,
        )
        _patch_deps(
            monkeypatch,
            stage_result=MagicMock(context_updates={}, detail="director skipped"),
        )
        pool = _FakePool([_row()])

        result = await BackfillVideoShotListsJob().run(pool, _cfg())

        assert result.changes_made == 0
        assert pool.writes == []  # no write, no un-claim

    async def test_shot_list_without_shots_is_treated_as_empty(self, monkeypatch):
        """`{}` and `{"shots": []}` are exactly the stranded state — writing
        either back would re-strand the piece with a cleared marker."""
        monkeypatch.setattr(
            "modules.content.jobs.backfill_video_shot_lists.emit_finding", lambda **kw: None,
        )
        _patch_deps(
            monkeypatch,
            stage_result=MagicMock(
                context_updates={"video_shot_list": {"shots": []}}, detail="empty",
            ),
        )
        pool = _FakePool([_row()])

        assert (await BackfillVideoShotListsJob().run(pool, _cfg())).changes_made == 0

    async def test_one_bad_piece_does_not_abort_the_batch(self, monkeypatch):
        monkeypatch.setattr(
            "modules.content.jobs.backfill_video_shot_lists.emit_finding", lambda **kw: None,
        )
        monkeypatch.setattr(
            "services.di_wiring.build_platform_for_subprocess", lambda *a, **k: object(),
        )
        stage = MagicMock()
        stage.execute = AsyncMock(side_effect=[
            RuntimeError("model exploded"),
            MagicMock(context_updates={"video_shot_list": _SHOT_LIST}, detail="ok"),
        ])
        monkeypatch.setattr(
            "modules.content.stages.generate_video_shot_list.GenerateVideoShotListStage",
            lambda: stage,
        )
        pool = _FakePool([_row("aaa"), _row("bbb")])

        result = await BackfillVideoShotListsJob().run(pool, _cfg())

        assert result.changes_made == 1
        assert result.metrics["attempted"] == 2

    async def test_missing_platform_handle_aborts_rather_than_no_opping(self, monkeypatch):
        """Without the capability handle the director cannot call an LLM and
        returns a benign no-op — which would clear markers for pieces that
        still have no shot list. Refuse the cycle instead."""
        monkeypatch.setattr(
            "services.di_wiring.build_platform_for_subprocess", lambda *a, **k: None,
        )
        pool = _FakePool([_row()])

        result = await BackfillVideoShotListsJob().run(pool, _cfg())

        assert result.ok is False
        assert pool.writes == []

    async def test_no_stranded_pieces_is_quiet(self, monkeypatch):
        result = await BackfillVideoShotListsJob().run(_FakePool([]), _cfg())
        assert result.ok and result.changes_made == 0
        assert result.metrics["backfilled"] == 0

    async def test_disabled_via_settings(self):
        result = await BackfillVideoShotListsJob().run(
            _FakePool([_row()]), _cfg(backfill_video_shot_lists_enabled="false"),
        )
        assert result.ok and result.changes_made == 0

    async def test_batch_size_is_operator_tunable(self, monkeypatch):
        monkeypatch.setattr(
            "modules.content.jobs.backfill_video_shot_lists.emit_finding", lambda **kw: None,
        )
        _patch_deps(
            monkeypatch,
            stage_result=MagicMock(context_updates={}, detail="skip"),
        )
        seen: list = []

        class _P(_FakePool):
            async def fetch(self, _sql, *a):  # type: ignore[no-untyped-def]
                seen.append(a[0])
                return []

        await BackfillVideoShotListsJob().run(
            _P([]), _cfg(backfill_video_shot_lists_batch="5"),
        )
        assert seen == [5]

    async def test_short_script_is_threaded_so_both_lanes_recover(self, monkeypatch):
        """The 9:16 lane is planned from its own script. Omitting it produced
        long-only recoveries that read as complete: the first live run
        recovered 2 pieces with "11 long shot(s) + 0 short", leaving both
        shorts un-renderable.
        """
        monkeypatch.setattr(
            "modules.content.jobs.backfill_video_shot_lists.emit_finding",
            lambda **kw: None,
        )
        stage = _patch_deps(
            monkeypatch,
            stage_result=MagicMock(
                context_updates={
                    "video_shot_list": _SHOT_LIST,
                    "short_shot_list": _SHOT_LIST,
                },
                detail="both lanes",
            ),
        )
        await BackfillVideoShotListsJob().run(_FakePool([_row()]), _cfg())

        context = stage.execute.await_args[0][0]
        assert context["short_summary_script"] == "short narration"

    async def test_no_pool_fails_loud(self):
        assert (await BackfillVideoShotListsJob().run(None, _cfg())).ok is False

    def test_job_protocol_shape(self):
        job = BackfillVideoShotListsJob()
        assert job.name == "backfill_video_shot_lists"
        # Real GPU work — overlapping instances must not stack.
        assert job.idempotent is False
