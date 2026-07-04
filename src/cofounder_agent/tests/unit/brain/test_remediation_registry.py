import logging

import pytest

from brain.remediation.registry import (
    ACTION_REGISTRY,
    ActionResult,
    RemediationContext,
    execute,
)


def _ctx():
    return RemediationContext(pool=object(), alert={}, logger=logging.getLogger("t"))


@pytest.mark.asyncio
async def test_unknown_action_is_skipped_not_raised():
    result = await execute("no_such_action", {}, _ctx())
    assert result.status == "skipped"
    assert "no_such_action" in result.detail


@pytest.mark.asyncio
async def test_executor_exception_becomes_failed_result():
    async def boom(params, ctx):
        raise RuntimeError("kaboom")

    ACTION_REGISTRY["_test_boom"] = boom
    try:
        result = await execute("_test_boom", {}, _ctx())
    finally:
        ACTION_REGISTRY.pop("_test_boom", None)
    assert result.status == "failed"
    assert "kaboom" in result.detail


@pytest.mark.asyncio
async def test_known_action_dispatches_with_params():
    seen = {}

    async def rec(params, ctx):
        seen.update(params)
        return ActionResult(status="ok", detail="did it")

    ACTION_REGISTRY["_test_rec"] = rec
    try:
        result = await execute("_test_rec", {"k": "v"}, _ctx())
    finally:
        ACTION_REGISTRY.pop("_test_rec", None)
    assert result.status == "ok"
    assert seen == {"k": "v"}
