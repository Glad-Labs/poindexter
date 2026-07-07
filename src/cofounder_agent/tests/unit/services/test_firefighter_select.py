"""Tests for ``firefighter_service.select_remediation_action`` (Plan B core).

The selector asks a local LLM to pick ONE action from a fixed catalog for an
un-ruled alert. The safety-critical contract: the returned ``action_name`` is
ALWAYS in the passed catalog (or ``""`` to abstain). An off-list, malformed, or
failed selection degrades to **abstain** — never to an unvalidated action — and
the function NEVER raises (the brain treats abstain as "page as usual"). The
confidence *threshold* is applied by the engine, not here; this layer only
validates + surfaces the score.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from services.firefighter_service import select_remediation_action
from services.site_config import SiteConfig

_CATALOG = [
    {
        "name": "restart_container",
        "description": "Restart a docker container by name.",
        "params_schema": {"container": "str"},
    },
    {
        "name": "run_auto_remediate",
        "description": "Sweep stuck pipeline tasks.",
        "params_schema": {},
    },
]


def _sc() -> SiteConfig:
    return SiteConfig(initial_config={"ops_firefighter_model": "ollama/llama3.2:3b"})


def _router(text: str) -> AsyncMock:
    r = AsyncMock()
    r.invoke = AsyncMock(
        return_value={"text": text, "model": "ollama/llama3.2:3b", "tokens": 12}
    )
    return r


async def _select(router, *, alertname="PyroscopeDown"):
    return await select_remediation_action(
        alert={"labels": {"alertname": alertname}},
        action_catalog=_CATALOG,
        site_config=_sc(),
        model_router=router,
    )


@pytest.mark.asyncio
async def test_valid_selection_returns_validated_action():
    router = _router(
        json.dumps(
            {
                "action_name": "restart_container",
                "params": {"container": "poindexter-pyroscope"},
                "confidence": 0.82,
                "reason": "profiler scrape down; restart is safe",
            }
        )
    )
    out = await _select(router)
    assert out["action_name"] == "restart_container"
    assert out["params"] == {"container": "poindexter-pyroscope"}
    assert out["confidence"] == pytest.approx(0.82)
    assert out["reason"]
    assert out["model"] == "ollama/llama3.2:3b"


@pytest.mark.asyncio
async def test_off_list_action_degrades_to_abstain():
    """The model naming an action outside the catalog is the untrusted-output
    case — it MUST NOT pass through; the selector abstains."""
    router = _router(
        json.dumps(
            {"action_name": "rm_minus_rf", "params": {}, "confidence": 0.99, "reason": "trust me"}
        )
    )
    out = await _select(router)
    assert out["action_name"] == ""


@pytest.mark.asyncio
async def test_malformed_json_degrades_to_abstain():
    out = await _select(_router("not json at all, just prose"))
    assert out["action_name"] == ""


@pytest.mark.asyncio
async def test_router_exception_degrades_to_abstain():
    r = AsyncMock()
    r.invoke = AsyncMock(side_effect=RuntimeError("ollama down"))
    out = await _select(r)
    assert out["action_name"] == ""


@pytest.mark.asyncio
async def test_explicit_empty_action_is_abstain():
    router = _router(
        json.dumps({"action_name": "", "params": {}, "confidence": 0.0, "reason": "no safe action"})
    )
    out = await _select(router)
    assert out["action_name"] == ""


@pytest.mark.asyncio
async def test_json_fenced_output_is_parsed():
    """Small models often wrap JSON in ```json fences; the parser tolerates it."""
    router = _router(
        "```json\n"
        + json.dumps(
            {"action_name": "run_auto_remediate", "params": {}, "confidence": 0.7, "reason": "stuck"}
        )
        + "\n```"
    )
    out = await _select(router, alertname="StuckTasks")
    assert out["action_name"] == "run_auto_remediate"
    assert out["confidence"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_missing_confidence_defaults_to_zero():
    """A missing/garbage confidence becomes 0.0 so the engine's gate pages."""
    router = _router(
        json.dumps({"action_name": "run_auto_remediate", "params": {}, "reason": "x"})
    )
    out = await _select(router, alertname="Y")
    assert out["action_name"] == "run_auto_remediate"
    assert out["confidence"] == 0.0


@pytest.mark.asyncio
async def test_confidence_clamped_to_unit_interval():
    router = _router(
        json.dumps(
            {"action_name": "restart_container", "params": {"container": "x"}, "confidence": 5, "reason": "r"}
        )
    )
    out = await _select(router)
    assert out["confidence"] == 1.0


@pytest.mark.asyncio
async def test_empty_catalog_abstains_without_calling_model():
    r = AsyncMock()
    r.invoke = AsyncMock()
    out = await select_remediation_action(
        alert={"labels": {"alertname": "X"}},
        action_catalog=[],
        site_config=_sc(),
        model_router=r,
    )
    assert out["action_name"] == ""
    r.invoke.assert_not_awaited()
