import logging

import pytest

from poindexter.brain import docker_utils as du
from poindexter.brain.remediation.registry import (
    ACTION_REGISTRY,
    ActionResult,
    RemediationContext,
    execute,
)
from tests.unit.brain._restart_fakes import outcome


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


def _selectable():
    """Every registered action except the rules-only ones."""
    from poindexter.brain.remediation.registry import is_rules_only

    return {name for name in ACTION_REGISTRY if not is_rules_only(name)}


def test_describe_catalog_covers_every_registered_action():
    from poindexter.brain.remediation.registry import describe_catalog

    catalog = describe_catalog()
    names = {a["name"] for a in catalog}
    # Every executable action the model may pick is described — no silent gaps
    # it can't see. A rules-only action is left out on purpose (see
    # test_remediation_host_service.py), and there is only the one.
    assert names == _selectable()
    assert set(ACTION_REGISTRY) - names == {"restart_host_service"}
    for entry in catalog:
        assert set(entry) >= {"name", "description", "params_schema"}
        assert isinstance(entry["description"], str) and entry["description"]
        assert isinstance(entry["params_schema"], dict)


def test_every_registered_action_is_described():
    """Rules-only actions are not offered, but they are described: the
    metadata is the one account of each action, for people as well as the
    model."""
    from poindexter.brain.remediation.registry import _ACTION_META

    assert set(_ACTION_META) == set(ACTION_REGISTRY)
    for name, meta in _ACTION_META.items():
        assert isinstance(meta.get("description"), str) and meta["description"], name
        assert isinstance(meta.get("params_schema"), dict), name


def test_describe_catalog_filters_to_allowlist():
    from poindexter.brain.remediation.registry import describe_catalog

    catalog = describe_catalog(allowlist=["restart_container"])
    assert {a["name"] for a in catalog} == {"restart_container"}


def test_describe_catalog_empty_allowlist_means_all():
    """Mirrors config semantics: an empty allowlist = no restriction = every
    action the model may pick."""
    from poindexter.brain.remediation.registry import describe_catalog

    assert {a["name"] for a in describe_catalog(allowlist=[])} == _selectable()


def test_describe_catalog_ignores_unknown_allowlist_entries():
    """An allowlist naming a non-registered action never fabricates a catalog
    entry — you can only offer actions that actually execute."""
    from poindexter.brain.remediation.registry import describe_catalog

    catalog = describe_catalog(allowlist=["restart_container", "no_such_action"])
    assert {a["name"] for a in catalog} == {"restart_container"}


# ---------------------------------------------------------------------------
# restart_container blast-radius denylist (poindexter#1026)
# ---------------------------------------------------------------------------
#
# The firefighter executes `restart_container` with whatever container name the
# picker supplied, and `brain_daemon.docker_restart_container` restarts
# whatever it is handed. Two names must never reach it: restarting either
# destroys the record of the action in flight (the brain RUNS the executor;
# postgres HOLDS audit_log), and for the database also risks data loss.
#
# This is not hypothetical. Replaying the real
# `docker_port_forward_restart_skipped` alert — whose annotation says the
# restart was skipped BECAUSE the target is a database container — the model
# picked restart_container on poindexter-postgres-local at confidence ABOVE
# ops_firefighter_min_confidence, so the confidence gate would not have stopped
# it. A better model lowers that rate; only this guard makes it zero.


class _SpyDaemon:
    """Stand-in for brain_daemon that records whether a restart was attempted.

    Answers like the real ``docker_restart_container``: the shared helper's
    ``ContainerRestart`` outcome, of whatever ``status`` the test asks for.
    """

    def __init__(self, status: str = du.RESTART_OK) -> None:
        self.calls: list[str] = []
        self.forced: list[bool] = []
        self._status = status

    async def docker_restart_container(self, container, *, pool=None, force=False):
        self.calls.append(container)
        self.forced.append(force)
        return outcome(container, self._status)


def _pool_with_setting(value):
    """FakePool whose app_settings read returns ``value`` (None = unseeded)."""
    from tests.unit.brain._remediation_fakes import FakePool

    pool = FakePool()
    pool.set_fetchval(lambda sql, args: value)
    return pool


def _ctx_with(pool):
    return RemediationContext(pool=pool, alert={}, logger=logging.getLogger("t"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "container", ["poindexter-postgres-local", "poindexter-brain-daemon"]
)
async def test_hardcoded_denylist_blocks_restart(monkeypatch, container):
    """The two invariants are refused, and docker is never reached."""
    import poindexter.brain.remediation.registry as reg

    spy = _SpyDaemon()
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)

    result = await reg._restart_container(
        {"container": container}, _ctx_with(_pool_with_setting(None))
    )

    assert result.status == "skipped"
    assert container in result.detail
    assert spy.calls == [], "denied container still reached docker_restart_container"


@pytest.mark.asyncio
async def test_denied_status_is_not_ok_so_the_engine_pages(monkeypatch):
    """`engine` holds the page only on status == "ok"; anything else pages now.

    That is the whole safety property: refusing the action must ALSO surface the
    alert to a human, not silently swallow it.
    """
    import poindexter.brain.remediation.registry as reg

    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: _SpyDaemon())
    result = await reg._restart_container(
        {"container": "poindexter-postgres-local"},
        _ctx_with(_pool_with_setting(None)),
    )
    assert result.status != "ok"


@pytest.mark.asyncio
async def test_allowed_container_still_restarts(monkeypatch):
    """No regression: an ordinary container is untouched by the guard."""
    import poindexter.brain.remediation.registry as reg

    spy = _SpyDaemon()
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)

    result = await reg._restart_container(
        {"container": "poindexter-pyroscope"}, _ctx_with(_pool_with_setting(None))
    )

    assert result.status == "ok"
    assert spy.calls == ["poindexter-pyroscope"]
    assert result.detail == "restarted poindexter-pyroscope"


# ---------------------------------------------------------------------------
# What each restart outcome means to the firefighter
# ---------------------------------------------------------------------------
#
# docker_utils refuses a container that started moments ago (a second restart
# lands mid-startup). The firefighter is automated, so it meets that guard, and
# a refusal is not an attempt that went wrong: the audit row must say
# `skipped`, not `failed`, the way the denylist's refusals do.


@pytest.mark.asyncio
async def test_a_restart_the_guard_declines_is_skipped_not_failed(monkeypatch):
    import poindexter.brain.remediation.registry as reg

    spy = _SpyDaemon(du.RESTART_RECENTLY_STARTED)
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)

    result = await reg._restart_container(
        {"container": "poindexter-pyroscope"}, _ctx_with(_pool_with_setting(None)),
    )

    assert result.status == "skipped"
    assert result.status != "ok", "the engine holds a page only on ok; a refusal pages now"
    assert result.detail == outcome("poindexter-pyroscope", du.RESTART_RECENTLY_STARTED).detail
    assert "poindexter-pyroscope started" in result.detail
    assert spy.calls == ["poindexter-pyroscope"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (du.RESTART_OK, "ok"),
        (du.RESTART_RECENTLY_STARTED, "skipped"),
        (du.RESTART_MISSING, "failed"),
        (du.RESTART_FAILED, "failed"),
        (du.RESTART_TIMED_OUT, "failed"),
        (du.RESTART_NO_DOCKER_CLI, "failed"),
        (du.RESTART_ERROR, "failed"),
    ],
)
async def test_each_restart_outcome_maps_to_one_action_status(monkeypatch, status, expected):
    import poindexter.brain.remediation.registry as reg

    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: _SpyDaemon(status))

    result = await reg._restart_container(
        {"container": "poindexter-pyroscope"}, _ctx_with(_pool_with_setting(None)),
    )

    assert result.status == expected
    assert result.detail == outcome("poindexter-pyroscope", status).detail


@pytest.mark.asyncio
async def test_the_firefighter_never_forces_past_the_guard(monkeypatch):
    """Only an operator's explicit restart is forced, and that goes through the
    console queue. A rule or the LLM picking a restart is automated."""
    import poindexter.brain.remediation.registry as reg

    spy = _SpyDaemon()
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)

    await reg._restart_container(
        {"container": "poindexter-pyroscope"}, _ctx_with(_pool_with_setting(None)),
    )

    assert spy.forced == [False]


@pytest.mark.asyncio
async def test_operator_additions_are_honoured(monkeypatch):
    """`ops_firefighter_restart_denylist` ADDS install-specific names."""
    import poindexter.brain.remediation.registry as reg

    spy = _SpyDaemon()
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)

    result = await reg._restart_container(
        {"container": "poindexter-wan-server"},
        _ctx_with(_pool_with_setting("poindexter-wan-server, poindexter-speaches")),
    )

    assert result.status == "skipped"
    assert spy.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setting_value",
    [None, "", "   ", ",,,", "poindexter-other"],
    ids=["unseeded", "empty", "whitespace", "commas-only", "unrelated-name"],
)
async def test_setting_can_never_reopen_the_floor(monkeypatch, setting_value):
    """The setting is a UNION, not a replacement.

    Emptying or garbling `ops_firefighter_restart_denylist` must not re-open the
    two invariants — otherwise the guard has an off switch, which is exactly the
    foot-gun it exists to remove.
    """
    import poindexter.brain.remediation.registry as reg

    spy = _SpyDaemon()
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)

    result = await reg._restart_container(
        {"container": "poindexter-postgres-local"},
        _ctx_with(_pool_with_setting(setting_value)),
    )

    assert result.status == "skipped"
    assert spy.calls == []


@pytest.mark.asyncio
async def test_guard_fails_closed_when_the_settings_read_raises(monkeypatch):
    """An unreachable DB must deny, not allow.

    A guard that fails open is worse than none: it would be silently absent in
    exactly the degraded conditions where the firefighter is most active.
    """
    import poindexter.brain.remediation.registry as reg

    class _ExplodingPool:
        async def fetchval(self, *a, **k):
            raise RuntimeError("db down")

    spy = _SpyDaemon()
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)

    result = await reg._restart_container(
        {"container": "poindexter-postgres-local"}, _ctx_with(_ExplodingPool())
    )

    assert result.status == "skipped"
    assert spy.calls == []


@pytest.mark.asyncio
async def test_the_refusal_check_says_what_the_executor_would_do(monkeypatch):
    """A dry run never calls the executor, so it asks ``refusal`` instead. The
    answer must be the executor's own: the same function decides both."""
    import poindexter.brain.remediation.registry as reg

    spy = _SpyDaemon()
    monkeypatch.setattr(reg, "_resolve_brain_daemon", lambda: spy)
    ctx = _ctx_with(_pool_with_setting(None))
    denied = {"container": "poindexter-postgres-local"}
    refused = await reg.refusal("restart_container", denied, ctx)
    executed = await reg.execute("restart_container", denied, ctx)
    assert refused and refused == executed.detail
    assert await reg.refusal("restart_container", {"container": "poindexter-pyroscope"}, ctx) is None
    assert await reg.refusal("restart_container", {}, ctx) == "restart_container: no 'container' param"
    assert await reg.refusal("run_auto_remediate", {}, ctx) is None
    assert await reg.refusal("rm_minus_rf", {}, ctx) == "unknown action: rm_minus_rf"
    assert spy.calls == []  # asking never restarts anything


def test_catalog_warns_the_model_off_the_denied_containers():
    """Layer one: the catalog description is the ONLY thing the selector sees,
    so naming the forbidden containers there suppresses the pick at the source.
    The executor guard is the backstop for when it picks them anyway."""
    from poindexter.brain.remediation.registry import describe_catalog

    desc = next(
        a["description"] for a in describe_catalog() if a["name"] == "restart_container"
    )
    assert "poindexter-postgres-local" in desc
    assert "poindexter-brain-daemon" in desc
