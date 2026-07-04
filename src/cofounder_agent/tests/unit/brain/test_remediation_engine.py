import json
import logging

import pytest

from brain.remediation import engine as E
from brain.remediation import rules as R
from brain.remediation.registry import ActionResult
from tests.unit.brain._remediation_fakes import FakePool

LOG = logging.getLogger("t")
CFG = {
    "enabled": True, "max_attempts_per_window": 3, "window_minutes": 60,
    "verify_after_seconds": 120, "max_actions_per_hour": 10, "action_allowlist": [],
}
ALERT = {"labels": {"alertname": "WorkerDown", "severity": "critical"}, "annotations": {}}


def _acoro(value):
    async def _f(*a, **k):
        return value
    return _f


@pytest.mark.asyncio
async def test_no_rule_means_not_acted(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is False
    assert d.reason == "no rule"


@pytest.mark.asyncio
async def test_disabled_short_circuits():
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config={**CFG, "enabled": False}, logger=LOG)
    assert d.acted is False
    assert d.reason == "disabled"


@pytest.mark.asyncio
async def test_rule_ok_action_holds_page_and_writes_pending_audit(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {"container": "poindexter-worker"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=42)))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is True
    assert d.action_name == "restart_container"
    assert d.run_id
    inserts = [e for e in pool.executed if "audit_log" in e[0]]
    assert len(inserts) == 1  # one pending remediation_action row, no verify yet
    details = json.loads(inserts[0][1][3])
    assert details["fingerprint"] == "fp"
    assert details["action_name"] == "restart_container"
    assert details["execution"]["status"] == "ok"
    assert details["verify_after_seconds"] == 120


@pytest.mark.asyncio
async def test_failed_action_pages_now_and_records_terminal_verify(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {"container": "ghost"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="failed", detail="not found")))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is False  # page now
    events = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    assert len(events) == 2  # an action row AND a terminal verify row
    assert any(ev.get("result") == "action_failed" for ev in events)


@pytest.mark.asyncio
async def test_breaker_tripped_pages_no_execute(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(True))
    called = {"n": 0}

    async def _exec(*a, **k):
        called["n"] += 1
        return ActionResult(status="ok")

    monkeypatch.setattr(E, "execute", _exec)
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is False
    assert "breaker" in d.reason
    assert called["n"] == 0  # never executed


@pytest.mark.asyncio
async def test_action_not_in_allowlist_pages(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    pool = FakePool()
    cfg = {**CFG, "action_allowlist": ["run_auto_remediate"]}
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=cfg, logger=LOG)
    assert d.acted is False
    assert "allowlist" in d.reason
