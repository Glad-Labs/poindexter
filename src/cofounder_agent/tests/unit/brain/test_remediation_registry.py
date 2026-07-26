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


# --- describe_catalog: the {name, description, params_schema} payload the brain
#     sends the LLM selector (Plan B). --------------------------------------


def test_describe_catalog_covers_every_registered_action():
    from brain.remediation.registry import describe_catalog

    catalog = describe_catalog()
    names = {a["name"] for a in catalog}
    # Every executable action is described — no silent gaps the model can't see.
    assert names == set(ACTION_REGISTRY.keys())
    for entry in catalog:
        assert set(entry) >= {"name", "description", "params_schema"}
        assert isinstance(entry["description"], str) and entry["description"]
        assert isinstance(entry["params_schema"], dict)


def test_describe_catalog_filters_to_allowlist():
    from brain.remediation.registry import describe_catalog

    catalog = describe_catalog(allowlist=["restart_container"])
    assert {a["name"] for a in catalog} == {"restart_container"}


def test_describe_catalog_empty_allowlist_means_all():
    """Mirrors config semantics: an empty allowlist = no restriction = all."""
    from brain.remediation.registry import describe_catalog

    assert {a["name"] for a in describe_catalog(allowlist=[])} == set(ACTION_REGISTRY.keys())


def test_describe_catalog_ignores_unknown_allowlist_entries():
    """An allowlist naming a non-registered action never fabricates a catalog
    entry — you can only offer actions that actually execute."""
    from brain.remediation.registry import describe_catalog

    catalog = describe_catalog(allowlist=["restart_container", "no_such_action"])
    assert {a["name"] for a in catalog} == {"restart_container"}
