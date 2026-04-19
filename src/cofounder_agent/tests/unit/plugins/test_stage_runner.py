"""Unit tests for ``plugins/stage_runner.py`` + ``services/stages/verify_task.py``.

Runner semantics under test:
- Registered stages execute in the configured order
- Disabled-in-app_settings stages are skipped + logged (not run)
- Missing-from-registry stages are skipped + logged (no crash)
- halts_on_failure=True + ok=False halts the run
- halts_on_failure=False + ok=False records failure, keeps going
- continue_workflow=False always halts, even if halts_on_failure=False
- timeout kills the stage + records it as failed
- context_updates merge onto the shared context
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from plugins.stage import StageResult
from plugins.stage_runner import (
    DEFAULT_STAGE_ORDER,
    StageRunner,
    load_stage_order,
)


# ---------------------------------------------------------------------------
# Fake pool — app_settings lookups only.
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, settings: dict[str, str]):
        self._settings = settings

    async def fetchval(self, _sql: str, *args: Any) -> Any:
        key = args[0] if args else None
        return self._settings.get(key)

    async def execute(self, *_args: Any) -> str:
        return "OK"


class _FakePoolCtx:
    def __init__(self, settings: dict[str, str]):
        self._settings = settings

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._settings)

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, settings: dict[str, str] | None = None):
        self.settings: dict[str, str] = settings or {}

    def acquire(self) -> _FakePoolCtx:
        return _FakePoolCtx(self.settings)

    # PluginConfig.load calls pool.fetchval directly (no acquire)
    async def fetchval(self, _sql: str, *args: Any) -> Any:
        key = args[0] if args else None
        return self.settings.get(key)


# ---------------------------------------------------------------------------
# Test stages — tiny, deterministic; avoid depending on real Stage plugins.
# ---------------------------------------------------------------------------


class _OkStage:
    name = "ok_stage"
    description = ""
    timeout_seconds = 30
    halts_on_failure = True

    def __init__(self):
        self.calls = 0

    async def execute(self, ctx, cfg) -> StageResult:
        self.calls += 1
        ctx["ok_ran"] = True
        return StageResult(ok=True, detail="ok")


class _FailStage:
    name = "fail_stage"
    description = ""
    timeout_seconds = 30
    halts_on_failure = True

    async def execute(self, ctx, cfg) -> StageResult:
        return StageResult(ok=False, detail="boom")


class _SoftFailStage:
    name = "soft_fail"
    description = ""
    timeout_seconds = 30
    halts_on_failure = False

    async def execute(self, ctx, cfg) -> StageResult:
        return StageResult(ok=False, detail="soft fail")


class _ExplicitHaltStage:
    name = "explicit_halt"
    description = ""
    timeout_seconds = 30
    halts_on_failure = False

    async def execute(self, ctx, cfg) -> StageResult:
        # ok=True but asks runner to stop (like a conditional early-exit)
        return StageResult(ok=True, detail="asked to halt", continue_workflow=False)


class _UpdatesStage:
    name = "updates"
    description = ""
    timeout_seconds = 30
    halts_on_failure = True

    async def execute(self, ctx, cfg) -> StageResult:
        return StageResult(
            ok=True,
            detail="set keys",
            context_updates={"new_key": "new_value", "ctr": ctx.get("ctr", 0) + 1},
        )


class _SlowStage:
    name = "slow"
    description = ""
    timeout_seconds = 30
    halts_on_failure = True

    async def execute(self, ctx, cfg) -> StageResult:
        await asyncio.sleep(5)  # intentionally longer than our test timeout override
        return StageResult(ok=True, detail="should not reach")


class _RaisingStage:
    name = "raising"
    description = ""
    timeout_seconds = 30
    halts_on_failure = False

    async def execute(self, ctx, cfg) -> StageResult:
        raise RuntimeError("kaboom")


# ---------------------------------------------------------------------------
# load_stage_order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLoadStageOrder:
    async def test_returns_default_when_missing(self):
        pool = _FakePool()
        out = await load_stage_order(pool)
        assert out == DEFAULT_STAGE_ORDER

    async def test_returns_db_value(self):
        pool = _FakePool({"pipeline.stages.order": json.dumps(["a", "b"])})
        out = await load_stage_order(pool)
        assert out == ["a", "b"]

    async def test_malformed_json_falls_back(self):
        pool = _FakePool({"pipeline.stages.order": "{not json"})
        out = await load_stage_order(pool)
        assert out == DEFAULT_STAGE_ORDER

    async def test_non_list_falls_back(self):
        pool = _FakePool({"pipeline.stages.order": '{"not":"list"}'})
        out = await load_stage_order(pool)
        assert out == DEFAULT_STAGE_ORDER

    async def test_non_string_elements_fall_back(self):
        pool = _FakePool({"pipeline.stages.order": "[1, 2, 3]"})
        out = await load_stage_order(pool)
        assert out == DEFAULT_STAGE_ORDER


# ---------------------------------------------------------------------------
# StageRunner.run_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunAll:
    async def test_runs_stages_in_order(self):
        pool = _FakePool()
        ok = _OkStage()
        updates = _UpdatesStage()
        runner = StageRunner(pool, [ok, updates])
        ctx: dict[str, Any] = {}
        summary = await runner.run_all(ctx, order=["ok_stage", "updates"])

        assert summary.ok is True
        assert summary.halted_at is None
        assert [r.name for r in summary.records] == ["ok_stage", "updates"]
        assert ctx["ok_ran"] is True
        assert ctx["new_key"] == "new_value"
        assert ok.calls == 1

    async def test_unregistered_stage_in_order_is_skipped_not_fatal(self):
        pool = _FakePool()
        runner = StageRunner(pool, [_OkStage()])
        summary = await runner.run_all({}, order=["ghost", "ok_stage"])
        assert summary.ok is True
        assert summary.records[0].skipped is True
        assert summary.records[0].detail == "not registered"
        assert summary.records[1].ok is True

    async def test_disabled_in_app_settings_is_skipped(self):
        pool = _FakePool({
            "plugin.stage.ok_stage": json.dumps({"enabled": False}),
        })
        ok = _OkStage()
        runner = StageRunner(pool, [ok])
        summary = await runner.run_all({}, order=["ok_stage"])
        assert summary.records[0].skipped is True
        assert ok.calls == 0  # Disabled — execute() must not be called.

    async def test_halts_on_failure_true_halts_the_run(self):
        pool = _FakePool()
        runner = StageRunner(pool, [_FailStage(), _OkStage()])
        summary = await runner.run_all({}, order=["fail_stage", "ok_stage"])
        assert summary.ok is False
        assert summary.halted_at == "fail_stage"
        # The stage after the halted one should NOT have been recorded
        assert len(summary.records) == 1

    async def test_halts_on_failure_false_continues(self):
        pool = _FakePool()
        runner = StageRunner(pool, [_SoftFailStage(), _OkStage()])
        summary = await runner.run_all({}, order=["soft_fail", "ok_stage"])
        # Overall summary is True because the failing stage was soft,
        # and the run didn't halt.
        assert summary.halted_at is None
        assert [r.ok for r in summary.records] == [False, True]

    async def test_explicit_continue_workflow_false_halts(self):
        pool = _FakePool()
        runner = StageRunner(pool, [_ExplicitHaltStage(), _OkStage()])
        summary = await runner.run_all({}, order=["explicit_halt", "ok_stage"])
        assert summary.halted_at == "explicit_halt"
        assert len(summary.records) == 1

    async def test_timeout_kills_stage(self):
        pool = _FakePool({
            # Override the stage's 30s default to 0.1s via config
            "plugin.stage.slow": json.dumps({
                "enabled": True,
                "config": {"timeout_seconds": 0},  # int() of 0 is still 0
            }),
        })
        # 0s would make asyncio.wait_for raise immediately; use 1s for determinism.
        pool.settings["plugin.stage.slow"] = json.dumps({
            "enabled": True,
            "config": {"timeout_seconds": 1, "halts_on_failure": False},
        })
        runner = StageRunner(pool, [_SlowStage()])
        summary = await runner.run_all({}, order=["slow"])
        assert summary.records[0].ok is False
        assert "timed out" in summary.records[0].detail

    async def test_raising_stage_does_not_crash_runner(self):
        pool = _FakePool()
        runner = StageRunner(pool, [_RaisingStage(), _OkStage()])
        summary = await runner.run_all({}, order=["raising", "ok_stage"])
        assert summary.records[0].ok is False
        assert "RuntimeError" in summary.records[0].detail
        # halts_on_failure=False on the raising stage → runner continues
        assert summary.records[1].ok is True


# ---------------------------------------------------------------------------
# verify_task stage
# ---------------------------------------------------------------------------


class _FakeDbService:
    def __init__(self, existing_task_ids: set[str]):
        self._existing = existing_task_ids

    async def get_task(self, task_id: str):
        if task_id in self._existing:
            return {"id": task_id}
        return None


@pytest.mark.asyncio
class TestVerifyTaskStage:
    async def test_conforms_to_stage_protocol(self):
        from plugins.stage import Stage
        from services.stages.verify_task import VerifyTaskStage
        assert isinstance(VerifyTaskStage(), Stage)

    async def test_task_exists_writes_stages_key(self):
        from services.stages.verify_task import VerifyTaskStage

        ctx = {"task_id": "abc", "database_service": _FakeDbService({"abc"})}
        result = await VerifyTaskStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["content_task_id"] == "abc"
        assert result.context_updates["stages"]["1_content_task_created"] is True

    async def test_task_missing_marks_false_but_does_not_halt(self):
        from services.stages.verify_task import VerifyTaskStage

        ctx = {"task_id": "ghost", "database_service": _FakeDbService(set())}
        result = await VerifyTaskStage().execute(ctx, {})
        assert result.ok is False
        assert result.continue_workflow is True  # Legacy behavior: log + continue.
        assert result.context_updates["stages"]["1_content_task_created"] is False

    async def test_missing_database_service_is_handled(self):
        from services.stages.verify_task import VerifyTaskStage

        ctx = {"task_id": "abc"}
        result = await VerifyTaskStage().execute(ctx, {})
        assert result.ok is False
        assert "database_service" in result.detail

    async def test_missing_task_id_is_handled(self):
        from services.stages.verify_task import VerifyTaskStage

        ctx = {"database_service": _FakeDbService(set())}
        result = await VerifyTaskStage().execute(ctx, {})
        assert result.ok is False
        assert "task_id" in result.detail
