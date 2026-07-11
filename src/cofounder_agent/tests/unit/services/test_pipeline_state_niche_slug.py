"""Regression tests — ``niche_slug`` must be a declared ``PipelineState`` channel.

RCA 2026-07-11 (dev_diary auto-publish never fired): ``content_router_service``
has seeded ``niche_slug`` onto the initial pipeline context since 2026-05-28
(the lab-observability seam), but ``PipelineState`` never declared the key.
LangGraph maps ``ainvoke`` input onto DECLARED channels only, so the value was
silently dropped and every stage/atom read ``context.get("niche_slug") is
None``. Downstream casualty: the niche-scoped auto-publish gate (#598)
returned ``disabled: "niche_slug missing"`` on every run from 2026-05-28
through 2026-07-11 — dev_diary posts landed in ``awaiting_approval`` daily
despite the operator opt-in (``dev_diary_auto_publish_threshold=69`` +
``dry_run=false``).

The TypedDict is an ALLOWLIST: any context key a stage or atom needs to read
must be declared on ``PipelineState`` or LangGraph drops it without a sound
(same mechanism as the #674 media-channel incident).
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, StateGraph

from services.template_runner import PipelineState


@pytest.mark.unit
def test_niche_slug_declared_in_pipeline_state() -> None:
    """The channel-allowlist contract: ``niche_slug`` is a declared field."""
    assert "niche_slug" in PipelineState.__annotations__, (
        "PipelineState must declare niche_slug — undeclared keys are dropped "
        "by LangGraph at ainvoke input-mapping, which silently disabled the "
        "niche-scoped auto-publish gate from 2026-05-28 to 2026-07-11."
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_niche_slug_survives_graph_invoke() -> None:
    """Behavioral proof of the fix: a value seeded on the invoke input is
    visible to a node reading the state — i.e. LangGraph registered the
    channel instead of dropping the key."""
    seen: dict[str, Any] = {}

    async def _node(state: PipelineState) -> dict[str, Any]:
        seen["niche_slug"] = state.get("niche_slug")
        return {"content": "ok"}

    g: StateGraph = StateGraph(PipelineState)
    g.add_node("n", _node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    compiled = g.compile()

    await compiled.ainvoke(
        {"task_id": "t1", "topic": "x", "niche_slug": "dev_diary"}
    )

    assert seen["niche_slug"] == "dev_diary", (
        f"node saw niche_slug={seen['niche_slug']!r} — the invoke-input key "
        "was dropped, so stages downstream (finalize_task, "
        "content.evaluate_auto_publish) evaluate the auto-publish gate "
        "with no niche and it returns 'disabled'."
    )
