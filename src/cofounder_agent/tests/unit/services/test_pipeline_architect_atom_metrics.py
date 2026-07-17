"""_wrap_atom must merge an atom's reserved ``_atom_metrics`` into its
TemplateRunRecord — the seam carrying StageResult.metrics to atom_runs.metrics.

Glad-Labs/poindexter#873: _wrap_atom hardcoded its metrics dict, so 6,243
atom_runs rows carried only IO digests and every StageResult.metrics field
(content_length / model_used / prompt_template_key / variant_id, plus the
poindexter#868 writer_prompt_* fields) was silently discarded.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.pipeline_architect import _wrap_atom

pytestmark = pytest.mark.unit


async def test_atom_metrics_merged_into_record() -> None:
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "body",
            "_atom_metrics": {"content_length": 42, "model_used": "m:1b"},
        }

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert len(sink) == 1
    assert sink[0].metrics["content_length"] == 42
    assert sink[0].metrics["model_used"] == "m:1b"


async def test_atom_metrics_stripped_from_returned_state() -> None:
    """_atom_metrics must never reach LangGraph. A declared channel would be
    checkpointer-durable and smear one node's metrics onto its successors —
    the phantom-2093 failure from poindexter#868 Task 3."""
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {"content": "body", "_atom_metrics": {"content_length": 42}}

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    out = await node({"task_id": "t"}, None)

    assert "_atom_metrics" not in out
    assert out["content"] == "body"


async def test_output_keys_exclude_atom_metrics() -> None:
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {"content": "body", "_atom_metrics": {"x": 1}}

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert sink[0].metrics["output_keys"] == ["content"]


async def test_record_unchanged_when_no_atom_metrics() -> None:
    """Atoms that don't opt in must produce byte-identical rows to pre-#873."""
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {"content": "body"}

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert set(sink[0].metrics) == {
        "input_keys", "output_keys", "input_digest", "output_digest",
    }


async def test_structural_keys_win_over_atom_supplied() -> None:
    """An atom must not be able to corrupt input_keys/digests by returning
    its own values under those names."""
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "body",
            "_atom_metrics": {"input_keys": ["BOGUS"], "input_digest": "bogus"},
        }

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert sink[0].metrics["input_keys"] == ["task_id"]
    assert sink[0].metrics["input_digest"] != "bogus"
