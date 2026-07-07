"""Tests for services.remediation_rules_service — CRUD over the firefighter
``remediation_rules`` table (the service seam behind ``poindexter firefighter
rule ...``). Rules are operational runtime state, so the service owns add /
list / get / remove / enable-disable; adapters (CLI/API) stay thin.
"""

from __future__ import annotations

import pytest

from services import remediation_rules_service as svc


class _FakeConn:
    def __init__(self, *, fetch=None, fetchrow=None):
        self._fetch = fetch
        self._fetchrow = fetchrow
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetch if self._fetch is not None else []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchrow

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, *, fetch=None, fetchrow=None):
        self.conn = _FakeConn(fetch=fetch, fetchrow=fetchrow)

    def acquire(self):
        return self.conn


def _rule_row(**over):
    row = {
        "id": 1, "alertname": "PyroscopeDown", "match_regex": None,
        "action_name": "restart_container",
        "params": {"container": "poindexter-pyroscope"},
        "enabled": True, "max_attempts_per_window": None, "window_minutes": None,
        "verify_after_seconds": None, "description": "restart the profiler",
    }
    row.update(over)
    return row


# --- add_rule ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_rule_inserts_and_returns_row():
    pool = _FakePool(fetchrow=_rule_row())
    out = await svc.add_rule(
        pool, action_name="restart_container", alertname="PyroscopeDown",
        params={"container": "poindexter-pyroscope"}, description="restart the profiler",
    )
    sql, args = pool.conn.calls[-1]
    assert "INSERT INTO remediation_rules" in sql
    assert "RETURNING" in sql
    assert "PyroscopeDown" in args and "restart_container" in args
    assert out["action_name"] == "restart_container"
    assert out["params"] == {"container": "poindexter-pyroscope"}


@pytest.mark.asyncio
async def test_add_rule_rejects_unknown_action():
    pool = _FakePool()
    with pytest.raises(svc.RemediationRuleError, match="action"):
        await svc.add_rule(
            pool, action_name="rm_minus_rf", alertname="Whatever",
            params={"container": "x"},
        )
    assert pool.conn.calls == []  # never touched the DB


@pytest.mark.asyncio
async def test_add_rule_requires_alertname_or_match_regex():
    pool = _FakePool()
    with pytest.raises(svc.RemediationRuleError, match="alertname|match_regex"):
        await svc.add_rule(pool, action_name="run_auto_remediate")
    assert pool.conn.calls == []


@pytest.mark.asyncio
async def test_add_rule_restart_container_requires_container_param():
    pool = _FakePool()
    with pytest.raises(svc.RemediationRuleError, match="container"):
        await svc.add_rule(
            pool, action_name="restart_container", alertname="X", params={},
        )
    assert pool.conn.calls == []


@pytest.mark.asyncio
async def test_add_rule_accepts_match_regex_only():
    pool = _FakePool(fetchrow=_rule_row(
        id=2, alertname=None, match_regex="topic.batch.stuck",
        action_name="run_auto_remediate", params={},
    ))
    out = await svc.add_rule(
        pool, action_name="run_auto_remediate", match_regex="topic.batch.stuck",
    )
    sql, args = pool.conn.calls[-1]
    assert "INSERT INTO remediation_rules" in sql
    assert "topic.batch.stuck" in args
    assert out["match_regex"] == "topic.batch.stuck"


@pytest.mark.asyncio
async def test_add_rule_parses_jsonb_params_returned_as_text():
    # asyncpg may hand jsonb back as a str — the service must deserialize it.
    pool = _FakePool(fetchrow=_rule_row(params='{"container": "poindexter-promtail"}'))
    out = await svc.add_rule(
        pool, action_name="restart_container", alertname="PromtailDown",
        params={"container": "poindexter-promtail"},
    )
    assert out["params"] == {"container": "poindexter-promtail"}


# --- list_rules -------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rules_returns_all_ordered():
    pool = _FakePool(fetch=[_rule_row(), _rule_row(id=2, alertname="PromtailDown")])
    out = await svc.list_rules(pool)
    sql, args = pool.conn.calls[-1]
    assert "SELECT" in sql and "FROM remediation_rules" in sql
    assert "ORDER BY id" in sql
    assert args == ()  # no filter
    assert len(out) == 2


@pytest.mark.asyncio
async def test_list_rules_filters_enabled():
    pool = _FakePool(fetch=[_rule_row()])
    await svc.list_rules(pool, enabled=True)
    sql, args = pool.conn.calls[-1]
    assert "WHERE enabled" in sql
    assert args == (True,)


@pytest.mark.asyncio
async def test_list_rules_parses_jsonb_text_params():
    pool = _FakePool(fetch=[_rule_row(params='{"container": "c"}')])
    out = await svc.list_rules(pool)
    assert out[0]["params"] == {"container": "c"}


# --- get_rule ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rule_by_id():
    pool = _FakePool(fetchrow=_rule_row())
    out = await svc.get_rule(pool, rule_id=1)
    sql, args = pool.conn.calls[-1]
    assert "WHERE id = $1" in sql and args == (1,)
    assert out["id"] == 1


@pytest.mark.asyncio
async def test_get_rule_by_alertname():
    pool = _FakePool(fetchrow=_rule_row())
    await svc.get_rule(pool, alertname="PyroscopeDown")
    sql, args = pool.conn.calls[-1]
    assert "WHERE alertname = $1" in sql and args == ("PyroscopeDown",)


@pytest.mark.asyncio
async def test_get_rule_none_when_missing():
    pool = _FakePool(fetchrow=None)
    assert await svc.get_rule(pool, rule_id=99) is None


@pytest.mark.asyncio
async def test_get_rule_requires_exactly_one_selector():
    pool = _FakePool()
    with pytest.raises(svc.RemediationRuleError):
        await svc.get_rule(pool)  # neither
    with pytest.raises(svc.RemediationRuleError):
        await svc.get_rule(pool, rule_id=1, alertname="X")  # both
    assert pool.conn.calls == []


# --- remove_rule ------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_rule_by_alertname_returns_deleted():
    pool = _FakePool(fetchrow=_rule_row())
    out = await svc.remove_rule(pool, alertname="PyroscopeDown")
    sql, args = pool.conn.calls[-1]
    assert "DELETE FROM remediation_rules" in sql and "RETURNING" in sql
    assert "WHERE alertname = $1" in sql and args == ("PyroscopeDown",)
    assert out["alertname"] == "PyroscopeDown"


@pytest.mark.asyncio
async def test_remove_rule_by_id_none_when_missing():
    pool = _FakePool(fetchrow=None)
    out = await svc.remove_rule(pool, rule_id=99)
    sql, args = pool.conn.calls[-1]
    assert "WHERE id = $1" in sql and args == (99,)
    assert out is None


@pytest.mark.asyncio
async def test_remove_rule_requires_exactly_one_selector():
    pool = _FakePool()
    with pytest.raises(svc.RemediationRuleError):
        await svc.remove_rule(pool)
    assert pool.conn.calls == []


# --- set_rule_enabled -------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rule_enabled_by_id():
    pool = _FakePool(fetchrow=_rule_row(enabled=False))
    out = await svc.set_rule_enabled(pool, enabled=False, rule_id=1)
    sql, args = pool.conn.calls[-1]
    assert "UPDATE remediation_rules" in sql and "enabled" in sql and "RETURNING" in sql
    assert False in args and 1 in args
    assert out["enabled"] is False


@pytest.mark.asyncio
async def test_set_rule_enabled_none_when_missing():
    pool = _FakePool(fetchrow=None)
    assert await svc.set_rule_enabled(pool, enabled=True, alertname="Nope") is None


@pytest.mark.asyncio
async def test_set_rule_enabled_requires_exactly_one_selector():
    pool = _FakePool()
    with pytest.raises(svc.RemediationRuleError):
        await svc.set_rule_enabled(pool, enabled=True)
    assert pool.conn.calls == []


# --- drift guard ------------------------------------------------------------


def test_known_actions_in_sync_with_brain_registry():
    """The service hard-validates action_name against a local _KNOWN_ACTIONS
    constant (prod code must not import the brain package — only tests may). This
    guard keeps that copy honest: it must equal the brain's real action registry,
    so a new executor can't silently become un-addable via the CLI."""
    from brain.remediation.registry import ACTION_REGISTRY

    assert set(svc._KNOWN_ACTIONS) == set(ACTION_REGISTRY), (
        "services.remediation_rules_service._KNOWN_ACTIONS drifted from "
        "brain/remediation/registry.py ACTION_REGISTRY — sync them (a new brain "
        "action must be added to _KNOWN_ACTIONS or the CLI rejects valid rules)."
    )


def test_known_actions_is_nonempty_tuple_of_str():
    assert isinstance(svc._KNOWN_ACTIONS, tuple) and svc._KNOWN_ACTIONS
    assert all(isinstance(a, str) for a in svc._KNOWN_ACTIONS)
