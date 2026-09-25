import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from poindexter.brain.remediation import engine as E
from poindexter.brain.remediation import rules as R
from poindexter.brain.remediation.registry import ActionResult
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
    # Live: these tests are about what an acting long-tail does. The dry run
    # (the shipped default) has its own tests below.
    "llm_dry_run": False,
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


# ---------------------------------------------------------------------------
# The persistence gate, the LLM verify window, and the dry run
# (glad-labs-stack#4022).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("repeat_count,age_minutes,expected", [
    (1, 0.0, False),     # a first sighting
    (2, 0.0, True),      # min_repeats
    (1, 10.0, True),     # min_age_minutes
    (1, 9.9, False),
])
def test_is_persistent_is_either_gate(repeat_count, age_minutes, expected):
    assert E.is_persistent(CFG_LLM, repeat_count=repeat_count, age_minutes=age_minutes) is expected


def test_a_zero_min_age_disables_the_age_gate():
    cfg = {**CFG_LLM, "min_repeats": 5, "min_age_minutes": 0}
    assert E.is_persistent(cfg, repeat_count=4, age_minutes=600.0) is False


_PROBE = {"verify_signal": E.VERIFY_BY_REFIRE}
_NOTIFIER = {"verify_signal": E.VERIFY_BY_RESOLVED_NOTIFICATION}


@pytest.mark.parametrize("target,interval,expected", [
    (_PROBE, None, 120),          # nothing observed: the default grace
    (_PROBE, 30.0, 120),          # a fast producer: the default still wins
    (_PROBE, 2640.0, 5280),       # 44 min apart (the prod median): two of its intervals
    ({}, 2640.0, 5280),           # no producer fingerprint: a re-fire is still the evidence
    (_NOTIFIER, 2640.0, 600),     # a resolved notification proves it whenever it comes
])
def test_an_llm_action_on_a_probe_waits_out_the_alerts_own_cadence(target, interval, expected):
    cfg = {**CFG_LLM, "llm_verify_intervals": 2.0, "alertmanager_verify_after_seconds": 600}
    assert E.llm_verify_after(cfg, target, interval) == expected


def _live_gates(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))


_PICK = {
    "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
    "confidence": 0.8, "reason": "profiler scrape down", "model": "ollama/granite4.2:3b",
}


@pytest.mark.asyncio
async def test_the_dry_run_records_the_pick_and_runs_nothing(monkeypatch):
    """The shipped default: without ``llm_dry_run`` in the config the long-tail
    only observes. The pick is written as a remediation_dry_run row, which the
    breaker, the rate cap and the verify scan never read."""
    _live_gates(monkeypatch)
    ran = []

    async def _exec(*a, **k):
        ran.append(a)
        return ActionResult(status="ok")

    monkeypatch.setattr(E, "execute", _exec)
    pool = FakePool()
    cfg = {k: v for k, v in CFG_LLM.items() if k != "llm_dry_run"}
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=cfg, logger=LOG,
        select_fn=_select_fn(_PICK), repeat_count=2,
    )
    assert ran == []
    assert d.acted is False and d.source == "llm" and d.action_name == "restart_container"
    assert d.reason == 'dry run; would have run it with {"container": "poindexter-pyroscope"}'
    (sql, args), = [e for e in pool.executed if "audit_log" in e[0]]
    assert args[0] == "remediation_dry_run"
    details = json.loads(args[3])
    assert details["params"] == {"container": "poindexter-pyroscope"}
    assert details["confidence"] == 0.8 and details["model"] == "ollama/granite4.2:3b"
    assert details["refused"] is None
    assert "remediation_run_id" not in details  # nothing for a verify to find


@pytest.mark.asyncio
async def test_a_dry_run_pick_the_executor_would_refuse_says_so(monkeypatch):
    """The denylist lives in the executor, which a dry run never calls. Without
    asking it, a pick of the database would read "would have run it" in the
    very review that decides whether the long-tail may act."""
    _live_gates(monkeypatch)
    pool = FakePool()
    pick = {**_PICK, "params": {"container": "poindexter-postgres-local"}}
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config={**CFG_LLM, "llm_dry_run": True},
        logger=LOG, select_fn=_select_fn(pick), repeat_count=2,
    )
    assert d.acted is False
    assert d.reason.startswith("dry run; the executor would refuse it: restart_container: "
                               "poindexter-postgres-local is on the firefighter restart denylist")
    (sql, args), = [e for e in pool.executed if "audit_log" in e[0]]
    assert args[0] == "remediation_dry_run"
    assert "denylist" in json.loads(args[3])["refused"]


@pytest.mark.asyncio
async def test_the_dry_run_still_answers_to_the_gates(monkeypatch):
    """A dry-run row means "would have run it now": a tripped breaker is a
    refusal, the same as when live, and records nothing."""
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(True))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config={**CFG_LLM, "llm_dry_run": True},
        logger=LOG, select_fn=_select_fn(_PICK), repeat_count=2,
    )
    assert d.acted is False and d.reason == "circuit breaker tripped"
    assert pool.executed == []


@pytest.mark.asyncio
async def test_the_persistent_repeat_goes_to_the_llm_with_its_cadence(monkeypatch):
    _live_gates(monkeypatch)
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=5)))
    seen = {}

    async def _select(*, alert, catalog):
        seen["catalog"] = [c["name"] for c in catalog]
        return _PICK

    pool = FakePool()
    d = await E.evaluate_persistent_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_select, repeat_count=2, age_minutes=44.0, refire_interval_seconds=2640.0,
    )
    assert d.acted is True and d.source == "llm"
    assert seen["catalog"] == ["restart_container", "run_auto_remediate"]
    details = json.loads([e for e in pool.executed if "audit_log" in e[0]][0][1][3])
    assert details["verify_after_seconds"] == 5280
    assert (details["repeat_count"], details["age_minutes"]) == (2, 44.0)


@pytest.mark.asyncio
async def test_a_ruled_alert_is_not_offered_to_the_llm_on_its_persistent_repeat(monkeypatch):
    """The rule had the episode's first row. Offering the repeat would either
    run the rule a second time while its verify is pending, or ask the model
    about an alert the operator has already written the answer for."""
    rule = {"id": 1, "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    ran, counter = [], {"n": 0}

    async def _exec(*a, **k):
        ran.append(a)
        return ActionResult(status="ok")

    monkeypatch.setattr(E, "execute", _exec)
    pool = FakePool()
    d = await E.evaluate_persistent_for_dispatch(
        pool, alert=ALERT, fingerprint="fp", config=CFG_LLM, logger=LOG,
        select_fn=_counting_select_fn(counter, _PICK), repeat_count=2, age_minutes=5.0,
    )
    assert d.acted is False and d.reason.startswith("rule-matched")
    assert ran == [] and counter["n"] == 0 and pool.executed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("alert,config,reason", [
    ({**ALERT, "status": "resolved"}, CFG_LLM, "status resolved; nothing to remediate"),
    (ALERT, {**CFG_LLM, "enabled": False}, "disabled"),
    ({"labels": {"alertname": "container_unhealthy", "remediation": "rules_only"}, "annotations": {}},
     CFG_LLM, "no rule; alert allows rule-driven remediation only"),
    ({"labels": {"alertname": "gpu_scheduler:gpu_lock_timeout"}, "annotations": {}},
     CFG_LLM, "no rule; alert excluded from llm path"),
])
async def test_the_persistent_repeat_keeps_every_first_row_refusal(monkeypatch, alert, config, reason):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    counter = {"n": 0}
    d = await E.evaluate_persistent_for_dispatch(
        FakePool(), alert=alert, fingerprint="fp", config=config, logger=LOG,
        select_fn=_counting_select_fn(counter, _PICK), repeat_count=2, age_minutes=5.0,
    )
    assert d.acted is False and d.reason == reason
    assert counter["n"] == 0


# ---------------------------------------------------------------------------
# Status guard + rules-only label (2026-09-24, container health watch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_resolved_alert_never_runs_the_rule_that_matches_its_firing_twin(monkeypatch):
    """A probe's recovery row carries the same alertname/fingerprint prefix as
    the firing one; acting on it would restart something that just recovered."""
    matched = {"n": 0}

    async def _match(*a, **k):
        matched["n"] += 1
        return {"id": 1, "action_name": "restart_container", "params": {"container": "poindexter-speaches"},
                "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}

    monkeypatch.setattr(R, "match_rule", _match)
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=1)))
    pool = FakePool()
    resolved = {**ALERT, "status": "resolved"}
    d = await E.evaluate_for_dispatch(pool, alert=resolved, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is False and "resolved" in d.reason
    assert matched["n"] == 0 and pool.executed == []


@pytest.mark.asyncio
async def test_an_alert_without_a_status_is_treated_as_firing(monkeypatch):
    """The dispatcher defaults a missing status to firing; the engine agrees."""
    rule = {"id": 7, "action_name": "restart_container", "params": {"container": "poindexter-worker"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=1)))
    d = await E.evaluate_for_dispatch(FakePool(), alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is True


@pytest.mark.asyncio
async def test_a_rules_only_alert_never_reaches_the_llm_selector(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    counter = {"n": 0}
    alert = {"labels": {"alertname": "container_unhealthy", "severity": "warning",
                        "container": "poindexter-worker", "remediation": E.RULES_ONLY},
             "annotations": {}}
    d = await E.evaluate_for_dispatch(
        FakePool(), alert=alert, fingerprint="container_health_watch:poindexter-worker|warning",
        config=CFG_LLM, logger=LOG,
        select_fn=_counting_select_fn(counter, {"action_name": "restart_container", "confidence": 0.99}),
        repeat_count=9, age_minutes=60,
    )
    assert d.acted is False and "rule-driven" in d.reason
    assert counter["n"] == 0


@pytest.mark.asyncio
async def test_a_rules_only_alert_still_runs_its_rule(monkeypatch):
    rule = {"id": 3, "action_name": "restart_container", "params": {"container": "poindexter-speaches"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": 900}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=1)))
    alert = {"labels": {"alertname": "container_unhealthy", "severity": "warning",
                        "remediation": E.RULES_ONLY}, "annotations": {}}
    pool = FakePool()
    d = await E.evaluate_for_dispatch(
        pool, alert=alert, fingerprint="container_health_watch:poindexter-speaches|warning",
        config=CFG, logger=LOG,
    )
    assert d.acted is True and d.params == {"container": "poindexter-speaches"}
    details = json.loads([e for e in pool.executed if "audit_log" in e[0]][0][1][3])
    assert details["verify_after_seconds"] == 900


# ---------------------------------------------------------------------------
# latest_attempt — the remediation history the dispatcher reads to find where
# one episode of an alert ends and the next begins.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_attempt_is_none_for_a_fingerprint_never_remediated():
    pool = FakePool()
    seen = {}

    def _row(sql, args):
        seen["sql"], seen["args"] = sql, args
        return None

    pool.set_fetchrow(_row)
    assert await E.latest_attempt(pool, fingerprint="container_health_watch:poindexter-speaches|warning") is None
    assert seen["args"] == ("container_health_watch:poindexter-speaches|warning",)
    assert "remediation_action" in seen["sql"] and "remediation_verify" in seen["sql"]


@pytest.mark.asyncio
async def test_latest_attempt_reports_a_pending_action_as_unverified():
    acted = datetime.now(UTC) - timedelta(minutes=3)
    pool = FakePool()
    pool.set_fetchrow(lambda sql, args: {
        "acted_at": acted, "run_id": "run-1", "action_name": "restart_container",
        "verified_at": None, "verify_result": None,
    })
    attempt = await E.latest_attempt(pool, fingerprint="fp")
    assert attempt is not None
    assert attempt.verify_result is None and attempt.verified_at is None
    assert attempt.acted_at == acted and attempt.action_name == "restart_container"


@pytest.mark.asyncio
async def test_latest_attempt_carries_the_verify_outcome_and_normalises_timestamps():
    pool = FakePool()
    pool.set_fetchrow(lambda sql, args: {
        "acted_at": "2026-09-25T10:00:00Z", "run_id": "run-2", "action_name": "restart_container",
        "verified_at": "2026-09-25T10:15:00+00:00", "verify_result": "resolved",
    })
    attempt = await E.latest_attempt(pool, fingerprint="fp")
    assert attempt.verify_result == "resolved"
    assert attempt.acted_at == datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    assert attempt.verified_at == datetime(2026, 9, 25, 10, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_latest_attempt_lets_a_db_error_reach_the_caller():
    """The dispatcher decides what an unreadable history means (no new
    episode); swallowing it here would read as "never remediated"."""
    pool = FakePool()

    def _boom(sql, args):
        raise RuntimeError("audit_log unavailable")

    pool.set_fetchrow(_boom)
    with pytest.raises(RuntimeError):
        await E.latest_attempt(pool, fingerprint="fp")


# ---------------------------------------------------------------------------
# What an action records for its verify (glad-labs-stack#4023): the row it
# acted on, the producer's fingerprint, the key's severity, and which signal
# proves a fix. The signal also picks the default grace.
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "starts_at,received_at,expected",
    [
        # a probe that leaves starts_at out (container health watch, findings)
        (None, _T0, E.VERIFY_BY_REFIRE),
        # a probe that stamps NOW() in the INSERT: the same transaction
        # timestamp as received_at (docker_port_forward_probe and seven others)
        (_T0, _T0, E.VERIFY_BY_REFIRE),
        # Alertmanager: the episode began before the notification reached us
        (_T0 - timedelta(seconds=37), _T0, E.VERIFY_BY_RESOLVED_NOTIFICATION),
        # Grafana with a skewed clock (Brain Daemon Stale, 2026-07-19): later,
        # but still not the moment the row was written
        (_T0 + timedelta(hours=4), _T0, E.VERIFY_BY_RESOLVED_NOTIFICATION),
        # asyncpg hands datetimes; a replayed JSON row hands ISO strings
        ("2026-09-25T11:59:23+00:00", "2026-09-25T12:00:00+00:00", E.VERIFY_BY_RESOLVED_NOTIFICATION),
        ("2026-09-25T12:00:00+00:00", "2026-09-25T12:00:00Z", E.VERIFY_BY_REFIRE),
    ],
)
def test_the_verify_signal_follows_how_the_producer_reports(starts_at, received_at, expected):
    event = {"id": 1, "fingerprint": "fp", "starts_at": starts_at, "received_at": received_at}
    assert E.verify_signal_for(event) == expected


def test_no_row_means_the_refire_signal():
    assert E.verify_signal_for(None) == E.VERIFY_BY_REFIRE


CFG_AM = {**CFG, "alertmanager_verify_after_seconds": 600}
PYROSCOPE_ALERT = {"labels": {"alertname": "PyroscopeDown", "severity": "warning"}, "annotations": {}}
PYROSCOPE_ROW = {
    "id": 41, "fingerprint": "a9b4c69fd247b1e8", "severity": "warning",
    "starts_at": _T0 - timedelta(seconds=37), "received_at": _T0,
}
PYROSCOPE_RULE = {"id": 1, "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
                  "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}


async def _act(monkeypatch, *, rule, alert, alert_event, config=CFG_AM):
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=1)))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(
        pool, alert=alert, fingerprint=f"{(alert_event or {}).get('fingerprint') or 'fp'}|warning",
        config=config, logger=LOG, alert_event=alert_event,
    )
    assert d.acted is True
    return json.loads([e for e in pool.executed if "audit_log" in e[0]][0][1][3])


@pytest.mark.asyncio
async def test_an_alertmanager_action_records_its_row_and_waits_for_the_resolved_notification(monkeypatch):
    details = await _act(monkeypatch, rule=PYROSCOPE_RULE, alert=PYROSCOPE_ALERT, alert_event=PYROSCOPE_ROW)
    assert details["verify_signal"] == E.VERIFY_BY_RESOLVED_NOTIFICATION
    assert details["alert_event_id"] == 41
    assert details["alert_fingerprint"] == "a9b4c69fd247b1e8"
    assert details["alert_severity"] == "warning"
    # no per-rule grace: the Alertmanager default, not the general 120 s
    assert details["verify_after_seconds"] == 600


@pytest.mark.asyncio
async def test_a_probe_action_keeps_the_general_grace(monkeypatch):
    row = {"id": 7, "fingerprint": "docker-port-forward-restart-skipped-poindexter-postgres-local",
           "severity": "warning", "starts_at": _T0, "received_at": _T0}
    details = await _act(monkeypatch, rule=PYROSCOPE_RULE, alert=PYROSCOPE_ALERT, alert_event=row)
    assert details["verify_signal"] == E.VERIFY_BY_REFIRE
    assert details["verify_after_seconds"] == 120


@pytest.mark.asyncio
async def test_a_rule_grace_wins_over_the_alertmanager_default(monkeypatch):
    rule = {**PYROSCOPE_RULE, "verify_after_seconds": 900}
    details = await _act(monkeypatch, rule=rule, alert=PYROSCOPE_ALERT, alert_event=PYROSCOPE_ROW)
    assert details["verify_after_seconds"] == 900
    assert details["verify_signal"] == E.VERIFY_BY_RESOLVED_NOTIFICATION


@pytest.mark.asyncio
async def test_an_alert_without_a_producer_fingerprint_leaves_the_verify_on_dedup_state(monkeypatch):
    """Nothing to look it up by in alert_events: no target is recorded, so the
    verify uses the legacy oracle, with the general grace. Its page is still
    owed, so the route is recorded all the same."""
    row = {**PYROSCOPE_ROW, "fingerprint": ""}
    details = await _act(monkeypatch, rule=PYROSCOPE_RULE, alert=PYROSCOPE_ALERT, alert_event=row)
    assert not {"verify_signal", "alert_event_id", "alert_fingerprint"} & set(details)
    assert details["verify_after_seconds"] == 120
    assert (details["alertname"], details["alert_severity"]) == ("PyroscopeDown", "warning")


@pytest.mark.parametrize(
    "labels,route",
    [
        # an Alertmanager alert: severity and category, no directive
        ({"alertname": "PromtailDown", "severity": "critical", "category": "infrastructure"},
         ("critical", "infrastructure", "")),
        # a finding whose kind's delivery policy pages a warning on Telegram
        ({"alertname": "deploy_sync_stale", "severity": "warning", "force_channel": "telegram"},
         ("warning", "", "telegram")),
        # no severity label: recorded empty, a route like any other
        ({"alertname": "Mystery"}, ("", "", "")),
    ],
)
@pytest.mark.asyncio
async def test_an_action_records_how_the_alerts_own_page_was_routed(monkeypatch, labels, route):
    """The dispatcher routes a page by these labels. The action keeps them so
    the verify can page a fix that did not hold the same way."""
    details = await _act(monkeypatch, rule=PYROSCOPE_RULE, alert={"labels": labels, "annotations": {}},
                         alert_event=None)
    assert details["alertname"] == labels["alertname"]
    assert (details["alert_severity"], details["alert_category"], details["alert_force_channel"]) == route


@pytest.mark.asyncio
async def test_an_llm_pick_on_an_alertmanager_alert_gets_the_same_verify(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=1)))
    pool = FakePool()
    sel = {"action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
           "confidence": 0.8, "reason": "profiler down", "model": "ollama/granite4.2:3b"}
    d = await E.evaluate_for_dispatch(
        pool, alert=PYROSCOPE_ALERT, fingerprint="a9b4c69fd247b1e8|warning",
        config={**CFG_LLM, **CFG_AM}, logger=LOG, select_fn=_select_fn(sel), repeat_count=5,
        alert_event=PYROSCOPE_ROW,
    )
    assert d.acted is True and d.source == "llm"
    details = json.loads([e for e in pool.executed if "audit_log" in e[0]][0][1][3])
    assert details["verify_signal"] == E.VERIFY_BY_RESOLVED_NOTIFICATION
    assert details["verify_after_seconds"] == 600
    assert details["alert_event_id"] == 41
