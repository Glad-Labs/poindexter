"""content.generate_draft must hand its StageResult.metrics to _wrap_atom via
the reserved ``_atom_metrics`` key.

Glad-Labs/poindexter#873: the atom read only ``result.context_updates``, so the
writer's metrics — including the poindexter#868 writer_prompt_* prompt-size
fields — never reached atom_runs.metrics, and their five Grafana panels would
have read "No data" forever.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.stage import StageResult

pytestmark = pytest.mark.unit


def _stage_result(metrics: dict[str, Any]) -> StageResult:
    return StageResult(
        ok=True,
        detail="ok",
        context_updates={
            "content": "body",
            "research_context": "",
            "model_used": "m:1b",
            "models_used_by_phase": {},
            "generate_metrics": {},
        },
        metrics=metrics,
    )


async def test_stage_metrics_exposed_as_atom_metrics() -> None:
    from modules.content.atoms import content_generate_draft

    metrics = {
        "content_length": 4,
        "model_used": "m:1b",
        "prompt_template_key": "writer/blog",
        "writer_prompt_draft_chars": 5000,
    }
    with patch(
        "modules.content.writer_core.GenerateContentStage.execute",
        new=AsyncMock(return_value=_stage_result(metrics)),
    ):
        out = await content_generate_draft.run({"task_id": "t"})

    assert out["_atom_metrics"] == metrics
    assert out["content"] == "body"


async def test_no_metrics_attaches_no_key() -> None:
    from modules.content.atoms import content_generate_draft

    with patch(
        "modules.content.writer_core.GenerateContentStage.execute",
        new=AsyncMock(return_value=_stage_result({})),
    ):
        out = await content_generate_draft.run({"task_id": "t"})

    assert "_atom_metrics" not in out
