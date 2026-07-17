"""_make_stage_runner must lift the stage's StageResult.metrics onto the
reserved ``_atom_metrics`` key so _wrap_atom can record them.

Glad-Labs/poindexter#873: the shim passed ``record_sink=None`` to
make_stage_node, whose metrics-carrying record is guarded by
``if record_sink is not None`` — so every stage.* node's metrics were computed
and then thrown away.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.stage import StageResult
from services.atom_registry import _make_stage_runner

pytestmark = pytest.mark.unit


class _Stage:
    name = "demo_stage"
    timeout_seconds = 5
    halts_on_failure = True

    def __init__(self, metrics: dict[str, Any]) -> None:
        self._metrics = metrics

    async def execute(self, context: Any, config: Any) -> StageResult:
        return StageResult(
            ok=True,
            detail="done",
            context_updates={"content": "body"},
            metrics=self._metrics,
        )


def _stage_node_patches() -> tuple:
    """PluginConfig.load runs before execute(); the DB-touching helpers must be
    stubbed so the node runs with pool=None."""
    enabled_cfg = SimpleNamespace(enabled=True, config={}, get=lambda k, d=None: d)
    return (
        patch("plugins.config.PluginConfig.load", AsyncMock(return_value=enabled_cfg)),
        patch("services.template_runner._mark_stage_column", AsyncMock()),
        patch("services.template_runner._emit_progress", AsyncMock()),
    )


async def test_stage_metrics_lifted_onto_atom_metrics() -> None:
    runner = _make_stage_runner(
        _Stage({"content_length": 7, "model_used": "m:1b"}), fallback_pool=None,
    )
    p1, p2, p3 = _stage_node_patches()
    with p1, p2, p3:
        out = await runner({"task_id": "t"})

    assert out["_atom_metrics"] == {"content_length": 7, "model_used": "m:1b"}
    assert out["content"] == "body"


async def test_stage_without_metrics_attaches_no_key() -> None:
    """An empty metrics dict must not add the key — keeps non-emitting stages
    byte-identical to pre-#873."""
    runner = _make_stage_runner(_Stage({}), fallback_pool=None)
    p1, p2, p3 = _stage_node_patches()
    with p1, p2, p3:
        out = await runner({"task_id": "t"})

    assert "_atom_metrics" not in out
