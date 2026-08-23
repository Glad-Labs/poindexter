"""image_decision_agent.plan_images — failure surfacing + reasoning off.

2026-08-23: every canonical_blog post for a week shipped with zero inline
images because the planner (a 31B thinking model, reloaded right before the
call under VRAM contention) timed out at 300 s, and the timeout was swallowed
into an empty plan indistinguishable from "this article needs no images".
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig


def _sc():
    sc = SiteConfig(initial_config={
        "model_role_image_decision": "ollama/gemma-4-31B-it-qat:latest",
        "image_decision_section_body_chars": "500",
    })
    sc._pool = MagicMock()  # "production has a pool" — the dispatch path
    return sc


CONTENT = "## Alpha\n\nSome body text here.\n\n## Beta\n\nMore body text here."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_is_surfaced_on_the_result():
    from services import image_decision_agent as ida

    with patch.object(ida, "dispatch_complete", AsyncMock(side_effect=TimeoutError("300 s"))), \
         patch.object(ida, "get_prompt_manager") as gpm:
        gpm.return_value.get_prompt.return_value = "plan please"
        res = await ida.plan_images(CONTENT, "Topic", "technology", site_config=_sc())

    assert res.images == []
    assert res.error.startswith("TimeoutError")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unparseable_plan_is_surfaced_on_the_result():
    from services import image_decision_agent as ida

    completion = MagicMock(text="not json at all")
    with patch.object(ida, "dispatch_complete", AsyncMock(return_value=completion)), \
         patch.object(ida, "get_prompt_manager") as gpm:
        gpm.return_value.get_prompt.return_value = "plan please"
        res = await ida.plan_images(CONTENT, "Topic", "technology", site_config=_sc())

    assert res.images == []
    assert "unparseable" in res.error


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_call_disables_reasoning():
    """JSON-shaped task: think=False, like the social poster. A thinking
    model otherwise burns the 800-token budget deliberating."""
    import asyncpg

    from services import image_decision_agent as ida

    completion = MagicMock(text='{"featured": {}, "inline": []}')
    dispatch = AsyncMock(return_value=completion)
    # The success path logs the decision via a fresh asyncpg connection —
    # stub it so the unit test never reaches the real Postgres (#1011).
    with patch.object(ida, "dispatch_complete", dispatch), \
         patch.object(asyncpg, "connect", AsyncMock(side_effect=RuntimeError("no db in tests"))), \
         patch.object(ida, "get_prompt_manager") as gpm:
        gpm.return_value.get_prompt.return_value = "plan please"
        res = await ida.plan_images(CONTENT, "Topic", "technology", site_config=_sc())

    assert dispatch.await_args.kwargs["think"] is False
    assert res.error == ""  # a genuine empty plan carries no error
