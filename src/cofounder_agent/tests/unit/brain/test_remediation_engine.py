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


# ---------------------------------------------------------------------------
# Plan B — LLM long-tail branch (no rule -> ask the selector, gated).
# ---------------------------------------------------------------------------

CFG_LLM = {
    **CFG,
    "llm_longtail_enabled": True,
    "min_repeats": 2,
    "min_age_minutes": 10,
    "min_confidence": 0.6,
    "llm_exclude_regex": r"(?i)(ollama|gpu|vram|cuda|inference)",
}


def _select_fn(selection):
    """A fake select_fn returning a fixed selection (or None to abstain)."""
    async def _f(*, alert, catalog):
        return selection
    return _f


def _counting_select_fn(counter, selection=None):
    async def _f(*, alert, catalog):
        counter["n"] += 1
        return selection
    return _f


@pytest.mark.asyncio
async def test_no_rule_no_select_fn_still_pages_no_rule(monkeypatch):
    """Back-compat: with no select_fn injected, the no-rule path is unchanged."""
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG)
    assert d.acted is False
    assert d.reason == "no rule"


@pytest.mark.asyncio
async def test_llm_longtail_disabled_skips_selector(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    counter = {"n": 0}
    pool = FakePool()
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp",
        config={**CFG_LLM, "llm_longtail_enabled": False}, logger=LOG,
        select_fn=_counting_select_fn(counter, {"action_name": "restart_container", "confidence": 0.9}),
        repeat_count=5,
    )
    assert d.acted is False
    assert counter["n"] == 0  # selector never consulted when long-tail is off


@pytest.mark.asyncio
async def test_non_persistent_alert_skips_selector(monkeypatch):
    """repeat_count below min_repeats (and no age) -> a first-sighting blip pages
    as usual without burning an inference call."""
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    counter = {"n": 0}
    pool = FakePool()
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_counting_select_fn(counter), repeat_count=1,
    )
    assert d.acted is False
    assert counter["n"] == 0


@pytest.mark.asyncio
async def test_persistent_by_age_invokes_selector(monkeypatch):
    """age_minutes >= min_age_minutes qualifies even when repeat_count is low."""
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    counter = {"n": 0}
    pool = FakePool()
    await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_counting_select_fn(counter, None), repeat_count=0, age_minutes=15,
    )
    assert counter["n"] == 1  # age gate opened the path


@pytest.mark.asyncio
async def test_excluded_alert_skips_selector(monkeypatch):
    """Circular-dependency guard: an Ollama/GPU alert never reaches the LLM path
    (the model must not be asked to fix the substrate it runs on)."""
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    counter = {"n": 0}
    pool = FakePool()
    ollama_alert = {"labels": {"alertname": "OllamaUnresponsive", "severity": "critical"}, "annotations": {}}
    d = await E.evaluate_for_dispatch(
        pool, alert=ollama_alert, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_counting_select_fn(counter), repeat_count=5,
    )
    assert d.acted is False
    assert counter["n"] == 0


@pytest.mark.asyncio
async def test_llm_abstain_pages_without_action(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok")))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_select_fn(None), repeat_count=5,
    )
    assert d.acted is False
    assert not [e for e in pool.executed if "audit_log" in e[0]]  # no action row on abstain


@pytest.mark.asyncio
async def test_llm_low_confidence_pages_no_execute(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    called = {"n": 0}

    async def _exec(*a, **k):
        called["n"] += 1
        return ActionResult(status="ok")

    monkeypatch.setattr(E, "execute", _exec)
    pool = FakePool()
    sel = {"action_name": "restart_container", "params": {}, "confidence": 0.3, "reason": "meh"}
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_select_fn(sel), repeat_count=5,
    )
    assert d.acted is False
    assert called["n"] == 0
    assert "confidence" in d.reason.lower()


@pytest.mark.asyncio
async def test_llm_off_list_action_pages_no_execute(monkeypatch):
    """Defense-in-depth: even if a selection names a non-catalog action, the
    engine re-validates against the registry and refuses to execute it."""
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    called = {"n": 0}

    async def _exec(*a, **k):
        called["n"] += 1
        return ActionResult(status="ok")

    monkeypatch.setattr(E, "execute", _exec)
    pool = FakePool()
    sel = {"action_name": "rm_minus_rf", "params": {}, "confidence": 0.99}
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_select_fn(sel), repeat_count=5,
    )
    assert d.acted is False
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_llm_valid_selection_acts_with_source_llm(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=10)))
    pool = FakePool()
    sel = {
        "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
        "confidence": 0.8, "reason": "profiler scrape down", "model": "ollama/llama3.2:3b",
    }
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_select_fn(sel), repeat_count=5,
    )
    assert d.acted is True
    assert d.source == "llm"
    assert d.action_name == "restart_container"
    assert d.run_id
    inserts = [e for e in pool.executed if "audit_log" in e[0]]
    assert len(inserts) == 1  # one pending remediation_action row
    details = json.loads(inserts[0][1][3])
    assert details["source"] == "llm"
    assert details["action_name"] == "restart_container"
    assert details["confidence"] == 0.8
    assert details["model"] == "ollama/llama3.2:3b"
    assert details["execution"]["status"] == "ok"


@pytest.mark.asyncio
async def test_llm_selection_still_honors_breaker(monkeypatch):
    """The LLM path runs through the SAME breaker as rules — a tripped breaker
    pages without executing, source recorded as llm."""
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(True))
    called = {"n": 0}

    async def _exec(*a, **k):
        called["n"] += 1
        return ActionResult(status="ok")

    monkeypatch.setattr(E, "execute", _exec)
    pool = FakePool()
    sel = {"action_name": "restart_container", "params": {}, "confidence": 0.9}
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_select_fn(sel), repeat_count=5,
    )
    assert d.acted is False
    assert "breaker" in d.reason
    assert called["n"] == 0
