import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import poindexter.brain.alert_dispatcher as ad
from poindexter.brain.remediation import engine as E
from poindexter.brain.remediation.engine import RemediationAttempt, RemediationDecision
from poindexter.brain.remediation.registry import ActionResult
from tests.unit.brain._remediation_fakes import FirefighterWorld

_CATALOG = [{"name": "restart_container", "description": "restart it", "params_schema": {"container": "str"}}]


def _make_row(row_id=1, alertname="WorkerDown", severity="critical"):
    return {
        "id": row_id, "alertname": alertname, "status": "firing",
        "severity": severity, "category": "infrastructure",
        "labels": {"alertname": alertname, "severity": severity},
        "annotations": {}, "fingerprint": "fp-worker",
    }


class _Pool:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    async def fetch(self, sql, *a):
        if "alert_events" in sql and "dispatched_at IS NULL" in sql:
            return self._rows
        return []

    async def execute(self, sql, *a):
        self.executed.append((sql, a))

    async def fetchval(self, sql, *a):
        return None

    async def fetchrow(self, sql, *a):
        return None


def _acoro(value):
    async def _f(*a, **k):
        return value
    return _f


@pytest.mark.asyncio
async def test_firefighter_acts_holds_the_page(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(ad, "run_verify_scan_hook",
                        _acoro({"verified": 0, "resolved": 0, "still_firing": 0}), raising=False)
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=True, action_name="restart_container",
                                   run_id="abcd1234ef", result=ActionResult(status="ok"))),
        raising=False,
    )
    summary = await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert summary.get("remediated") == 1
    assert notify.await_count == 0  # page HELD
    assert any("remediating:" in str(a[1]) for a in pool.executed)


@pytest.mark.asyncio
async def test_no_rule_pages_as_usual(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=False, reason="no rule")), raising=False,
    )
    summary = await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert summary["sent"] == 1
    assert notify.await_count == 1  # paged


# ---------------------------------------------------------------------------
# Plan B — the brain-side select_fn transport (POST /api/remediation/select).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remediation_select_returns_parsed_selection(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))
    body = json.dumps({
        "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
        "confidence": 0.82, "reason": "profiler down", "model": "ollama/llama3.2:3b",
    }).encode("utf-8")
    monkeypatch.setattr(ad, "_post_select_sync", lambda url, payload, token, timeout: (200, body))
    out = await ad._remediation_select(_Pool([]), alert={"labels": {}}, catalog=_CATALOG, timeout=5.0)
    assert out is not None
    assert out["action_name"] == "restart_container"
    assert out["params"] == {"container": "poindexter-pyroscope"}
    assert out["confidence"] == 0.82
    assert out["model"] == "ollama/llama3.2:3b"


@pytest.mark.asyncio
async def test_remediation_select_abstain_returns_none(monkeypatch):
    """action_name='' (the worker's abstain) -> None, so the engine pages."""
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))
    body = json.dumps({"action_name": "", "confidence": 0.0}).encode("utf-8")
    monkeypatch.setattr(ad, "_post_select_sync", lambda *a: (200, body))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_no_base_url_returns_none(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro(""))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_no_token_returns_none(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro(None))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_503_returns_none(monkeypatch):
    """Worker 503 (firefighter disabled / no provider) -> None (Ollama-down
    degradation: page as usual)."""
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))
    monkeypatch.setattr(ad, "_post_select_sync", lambda *a: (503, b'{"detail":{"code":"no_provider"}}'))
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_remediation_select_post_raises_returns_none(monkeypatch):
    monkeypatch.setattr(ad, "_read_api_base_url", _acoro("http://worker:8002"))
    monkeypatch.setattr(ad, "_mint_oauth_token", _acoro("tok"))

    def _boom(*a):
        raise OSError("connection refused")

    monkeypatch.setattr(ad, "_post_select_sync", _boom)
    out = await ad._remediation_select(_Pool([]), alert={}, catalog=_CATALOG, timeout=5.0)
    assert out is None


@pytest.mark.asyncio
async def test_poll_wires_select_fn_and_repeat_count(monkeypatch):
    """poll_and_dispatch hands the engine a callable select_fn + an int
    repeat_count so the LLM long-tail path can engage on persistent alerts."""
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True, "llm_longtail_enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    captured = {}

    async def _hook(pool, *, alert, fingerprint, config, logger,
                    select_fn=None, repeat_count=0, age_minutes=0, alert_event=None):
        captured["select_fn"] = select_fn
        captured["repeat_count"] = repeat_count
        captured["alert_event"] = alert_event
        return RemediationDecision(acted=False, reason="no rule")

    monkeypatch.setattr(ad, "evaluate_for_dispatch_hook", _hook, raising=False)
    await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert callable(captured["select_fn"])
    assert isinstance(captured["repeat_count"], int)
    # the row itself, as the verify reads it back (glad-labs-stack#4023)
    assert captured["alert_event"] == {
        "id": 1, "fingerprint": "fp-worker", "severity": "critical",
        "starts_at": None, "received_at": None,
    }


# ---------------------------------------------------------------------------
# Held pages are not triaged, and a page the firefighter let through says why.
# ---------------------------------------------------------------------------


def _no_dedup_config():
    return {
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True},
    }


@pytest.mark.asyncio
async def test_a_held_page_is_not_triaged(monkeypatch):
    """Triage threads its diagnosis under the page; a held row has no page,
    so the diagnosis would go out standalone for an alert being fixed."""
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    triaged = []

    async def _triage(pool, row, notify_result, **k):
        triaged.append(row["id"])

    monkeypatch.setattr(ad, "_read_dedup_config", _acoro(_no_dedup_config()))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(True))
    monkeypatch.setattr(ad, "_triage_one_guarded", _triage)
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=True, action_name="restart_container",
                                   run_id="abcd1234ef", result=ActionResult(status="ok"))),
    )
    await ad.poll_and_dispatch(pool, notify_fn=notify)
    await asyncio.sleep(0)
    assert triaged == []
    assert notify.await_count == 0


@pytest.mark.asyncio
async def test_a_paged_row_is_still_triaged(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    triaged = []

    async def _triage(pool, row, notify_result, **k):
        triaged.append(row["id"])

    monkeypatch.setattr(ad, "_read_dedup_config", _acoro(_no_dedup_config()))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(True))
    monkeypatch.setattr(ad, "_triage_one_guarded", _triage)
    monkeypatch.setattr(ad, "evaluate_for_dispatch_hook",
                        _acoro(RemediationDecision(acted=False, reason="no rule")))
    await ad.poll_and_dispatch(pool, notify_fn=notify)
    await asyncio.sleep(0)
    assert triaged == [1]


@pytest.mark.asyncio
async def test_a_page_the_firefighter_had_an_action_for_says_why_it_was_not_held(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro(_no_dedup_config()))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=False, action_name="restart_container",
                                   source="rule", reason="circuit breaker tripped")),
    )
    await ad.poll_and_dispatch(pool, notify_fn=notify)
    message = notify.await_args.args[0]
    assert message.endswith("Not auto-remediated (restart_container: circuit breaker tripped).")


@pytest.mark.asyncio
async def test_a_page_with_no_rule_carries_no_firefighter_note(monkeypatch):
    """Most alerts have no rule; a note on every page would be noise."""
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro(_no_dedup_config()))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(ad, "evaluate_for_dispatch_hook",
                        _acoro(RemediationDecision(acted=False, reason="no rule")))
    await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert "auto-remediat" not in notify.await_args.args[0]


# ---------------------------------------------------------------------------
# _detect_new_episode — the two episode boundaries, in isolation.
# ---------------------------------------------------------------------------


def _attempt(*, acted_min_ago, verify=None, verified_min_ago=None):
    now = datetime.now(UTC)
    return RemediationAttempt(
        run_id="r", action_name="restart_container",
        acted_at=now - timedelta(minutes=acted_min_ago),
        verify_result=verify,
        verified_at=(now - timedelta(minutes=verified_min_ago)) if verified_min_ago is not None else None,
    )


class _ExistsPool:
    """Answers only the resolved-row EXISTS probe, recording its arguments."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(args)
        return self.answer


async def _detect(monkeypatch, attempt, *, run_started_min_ago=60, exists=False, stored_fp="fp-store"):
    monkeypatch.setattr(ad, "latest_remediation_attempt_hook", _acoro(attempt))
    pool = _ExistsPool(exists)
    episode = await ad._detect_new_episode(
        pool, fingerprint="fp-store|warning",
        run_started_at=datetime.now(UTC) - timedelta(minutes=run_started_min_ago),
        stored_fingerprint=stored_fp, severity="warning", alertname="container_unhealthy",
        row_id=42,
    )
    return episode, pool


@pytest.mark.asyncio
async def test_a_fix_verified_during_this_run_opens_a_new_episode(monkeypatch):
    episode, pool = await _detect(
        monkeypatch, _attempt(acted_min_ago=50, verify="resolved", verified_min_ago=35))
    assert episode == ad.EPISODE_VERIFIED_FIX
    assert pool.calls == []  # the verified fix decides it; no resolved-row lookup


@pytest.mark.asyncio
async def test_a_pending_attempt_is_never_a_boundary(monkeypatch):
    """The verify scan owns an unjudged attempt; a re-fire now means the fix
    did not hold, and that has to page through the verify, not restart again."""
    episode, pool = await _detect(monkeypatch, _attempt(acted_min_ago=5), exists=True)
    assert episode is None
    assert pool.calls == []


@pytest.mark.asyncio
async def test_a_fix_verified_before_this_run_began_is_not_a_boundary(monkeypatch):
    """After a restarted run declined (circuit breaker), its repeats must not
    re-open the episode off the old verify: that would page on every repeat."""
    episode, _ = await _detect(
        monkeypatch, _attempt(acted_min_ago=40, verify="resolved", verified_min_ago=25),
        run_started_min_ago=10, exists=False)
    assert episode is None


@pytest.mark.asyncio
async def test_after_a_failed_fix_a_source_resolved_row_opens_a_new_episode(monkeypatch):
    episode, pool = await _detect(
        monkeypatch, _attempt(acted_min_ago=50, verify="still_firing", verified_min_ago=35),
        run_started_min_ago=55, exists=True)
    assert episode == ad.EPISODE_SOURCE_RESOLVED
    stored_fp, alertname, since, row_id, severity = pool.calls[0]
    assert (stored_fp, alertname, row_id, severity) == ("fp-store", "container_unhealthy", 42, "warning")
    # resolved rows only count after the failed attempt, not after the run start
    assert abs((datetime.now(UTC) - since) - timedelta(minutes=50)) < timedelta(seconds=5)


@pytest.mark.asyncio
async def test_a_run_nothing_remediated_can_still_open_an_episode_on_a_resolved_row(monkeypatch):
    episode, pool = await _detect(monkeypatch, None, run_started_min_ago=30, exists=True)
    assert episode == ad.EPISODE_SOURCE_RESOLVED
    since = pool.calls[0][2]
    assert abs((datetime.now(UTC) - since) - timedelta(minutes=30)) < timedelta(seconds=5)


@pytest.mark.asyncio
async def test_without_a_stored_fingerprint_there_is_no_source_resolved_boundary(monkeypatch):
    episode, pool = await _detect(monkeypatch, None, exists=True, stored_fp="")
    assert episode is None
    assert pool.calls == []


@pytest.mark.asyncio
async def test_an_unreadable_history_means_no_new_episode(monkeypatch, caplog):
    """Fail toward plain dedup — the behaviour before episodes existed."""
    async def _boom(*a, **k):
        raise RuntimeError("audit_log unavailable")

    monkeypatch.setattr(ad, "latest_remediation_attempt_hook", _boom)
    with caplog.at_level(logging.WARNING, logger="brain.alert_dispatcher"):
        episode = await ad._detect_new_episode(
            _ExistsPool(True), fingerprint="fp|warning", run_started_at=datetime.now(UTC),
            stored_fingerprint="fp", severity="warning", alertname="x", row_id=1,
        )
    assert episode is None
    assert any("remediation history read failed" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# End to end across simulated time: the real dispatcher, dedup, engine,
# circuit breaker and verify scan over an in-memory FirefighterWorld, with
# prod's settings (120-min dedup window, 30-min summary, breaker 3 per 60 min)
# and the prod speaches rule (verify after 900 s).
# ---------------------------------------------------------------------------

SPEACHES_FP = "container_health_watch:poindexter-speaches"
SPEACHES_RULE = {
    "id": 35, "alertname": None, "match_regex": r"^container_health_watch:poindexter-speaches\|",
    "action_name": "restart_container", "params": {"container": "poindexter-speaches"},
    "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": 900,
    "enabled": True,
}
PROD_SETTINGS = {
    "alert_repeat_suppress_window_minutes": "120",
    "alert_repeat_summarize_threshold_minutes": "30",
    "ops_triage_enabled": "false",
}
_HEALTH_LABELS = {"probe": "container_health_watch", "container": "poindexter-speaches",
                  "remediation": "rules_only"}


class _Sim:
    def __init__(self, monkeypatch, *, rules=(SPEACHES_RULE,), settings=None):
        self.world = FirefighterWorld(app_settings={**PROD_SETTINGS, **(settings or {})},
                                      rules=list(rules))
        self.notify = AsyncMock(return_value={"ok": True})
        self.restarts = []

        async def _execute(action_name, params, ctx):
            self.restarts.append(params.get("container"))
            return ActionResult(status="ok", detail="restarted", latency_ms=1)

        monkeypatch.setattr(E, "execute", _execute)

    def wedged(self):
        return self.world.fire(alertname="container_unhealthy", fingerprint=SPEACHES_FP,
                               severity="warning", labels=_HEALTH_LABELS,
                               summary="poindexter-speaches has failed its healthcheck")

    def healthy_again(self):
        return self.world.fire(alertname="container_unhealthy", fingerprint=SPEACHES_FP,
                               severity="info", status="resolved", labels=_HEALTH_LABELS,
                               summary="poindexter-speaches is healthy again")

    async def cycle(self, *, after_minutes=0.0):
        self.world.advance(minutes=after_minutes)
        return await ad.poll_and_dispatch(self.world, notify_fn=self.notify)

    def pages(self, needle):
        return [c.args[0] for c in self.notify.await_args_list if needle in c.args[0]]


@pytest.mark.asyncio
async def test_a_flapping_container_is_restarted_each_episode_until_the_breaker_pages(monkeypatch):
    """speaches wedges every ~18 min. Each episode after a verified restart is
    restarted again, silently; the 4th inside an hour trips the breaker, which
    pages ONCE, and the still-wedged repeats after it stay suppressed."""
    sim = _Sim(monkeypatch)
    for _ in range(3):
        sim.wedged()
        await sim.cycle()                       # t+0: dispatch or new episode
        sim.healthy_again()
        await sim.cycle(after_minutes=5)        # t+5: probe reports recovery
        await sim.cycle(after_minutes=11)       # t+16: verify due (900 s)
        sim.world.advance(minutes=2)            # next wedge at t+18
    assert sim.restarts == ["poindexter-speaches"] * 3
    assert sim.notify.await_count == 1          # only the probe's first "healthy again"

    sim.wedged()                                # t=54: 3 restarts inside the hour
    await sim.cycle()
    breaker_pages = sim.pages("[FIRING · warning] container_unhealthy")
    assert len(breaker_pages) == 1
    assert "came back after an auto-remediation that was verified" in breaker_pages[0]
    assert "Not auto-remediated (restart_container: circuit breaker tripped)." in breaker_pages[0]
    assert sim.pages("[FIREFIGHTER]") == []     # every restart verified silent

    # Still wedged, not restarted: the repeats belong to the paged episode.
    for _ in range(4):
        sim.wedged()
        await sim.cycle(after_minutes=5)
    assert sim.restarts == ["poindexter-speaches"] * 3
    assert len(sim.pages("[FIRING · warning] container_unhealthy")) == 1
    verified = [(v["details"]["result"], v["details"]["evidence"])
                for v in sim.world.audit_rows("remediation_verify")]
    assert verified == [("resolved", "no_refire")] * 3


@pytest.mark.asyncio
async def test_after_the_breaker_pages_a_self_recovery_reopens_remediation_once_it_cools(monkeypatch):
    """The breaker is a rate, not a latch. speaches recovers on its own after
    the breaker page; its next wedge, once the oldest restart has aged out of
    the window, is restarted again (and pages again if it keeps flapping)."""
    sim = _Sim(monkeypatch)
    for _ in range(3):
        sim.wedged()
        await sim.cycle()
        sim.healthy_again()
        await sim.cycle(after_minutes=5)
        await sim.cycle(after_minutes=11)
        sim.world.advance(minutes=2)
    sim.wedged()
    await sim.cycle()                           # t=54: breaker page
    assert sim.restarts == ["poindexter-speaches"] * 3
    sim.healthy_again()
    await sim.cycle(after_minutes=10)           # t=64: recovered without a restart
    sim.wedged()
    await sim.cycle(after_minutes=10)           # t=74: restarts at 0 and 18 aged out... 18 is in
    assert sim.restarts == ["poindexter-speaches"] * 4
    assert len(sim.pages("[FIRING · warning] container_unhealthy")) == 1


@pytest.mark.asyncio
async def test_the_second_episode_after_a_verified_restart_is_held_and_marked(monkeypatch):
    sim = _Sim(monkeypatch)
    sim.wedged()
    await sim.cycle()
    await sim.cycle(after_minutes=16)            # verify: resolved
    second = sim.wedged()
    await sim.cycle(after_minutes=29)            # t+45: inside the 120-min window
    assert sim.restarts == ["poindexter-speaches"] * 2
    assert second["dispatch_result"].startswith("remediating: restart_container (run ")
    assert "new episode after a verified fix" in second["dispatch_result"]
    assert sim.notify.await_count == 0           # both episodes healed silently
    state = sim.world.dedup_state[SPEACHES_FP + "|warning"]
    assert state["repeat_count"] == 1            # the run restarted at the new episode


@pytest.mark.asyncio
async def test_a_restart_that_does_not_hold_pages_through_the_verify_and_is_not_repeated(monkeypatch):
    sim = _Sim(monkeypatch)
    sim.wedged()
    await sim.cycle()
    for _ in range(3):                           # still unhealthy after the restart
        sim.wedged()
        await sim.cycle(after_minutes=5)
    await sim.cycle(after_minutes=1)             # t+16: verify
    for _ in range(2):
        sim.wedged()
        await sim.cycle(after_minutes=5)
    assert sim.restarts == ["poindexter-speaches"]
    assert len(sim.pages("[FIREFIGHTER] auto-remediation did not resolve container_unhealthy")) == 1
    assert [v["details"]["result"] for v in sim.world.audit_rows("remediation_verify")] == ["still_firing"]
    # the health watch reports a level: its re-fires are the evidence
    assert [v["details"]["evidence"] for v in sim.world.audit_rows("remediation_verify")] == ["refired"]
    action = sim.world.audit_rows("remediation_action")[0]["details"]
    assert (action["verify_signal"], action["alert_fingerprint"]) == (E.VERIFY_BY_REFIRE, SPEACHES_FP)


@pytest.mark.asyncio
async def test_the_verify_sees_a_refire_dispatched_in_its_own_cycle(monkeypatch):
    """The verify runs after the cycle's rows are dispatched. Run first, it read
    the restart as resolved while the re-fire proving otherwise sat in the
    batch, and the re-fire then looked like a new episode and got restarted."""
    sim = _Sim(monkeypatch)
    sim.wedged()
    await sim.cycle()
    sim.world.advance(minutes=15.5)              # verify now due...
    sim.wedged()                                 # ...and a re-fire lands first
    await sim.cycle()
    assert sim.restarts == ["poindexter-speaches"]
    assert [v["details"]["result"] for v in sim.world.audit_rows("remediation_verify")] == ["still_firing"]
    assert len(sim.pages("[FIREFIGHTER]")) == 1


@pytest.mark.asyncio
async def test_after_a_failed_restart_and_a_manual_fix_the_next_wedge_is_restarted(monkeypatch):
    """The verify paged, someone fixed speaches, the probe said so. The next
    wedge inside the same dedup run is a new episode: before this it was a
    suppressed repeat, and with the run's summary already sent, a silent one."""
    sim = _Sim(monkeypatch)
    sim.wedged()
    await sim.cycle()
    for _ in range(7):                           # restart didn't hold; 35 min wedged
        sim.wedged()
        await sim.cycle(after_minutes=5)
    assert len(sim.pages("[FIREFIGHTER]")) == 1
    assert len(sim.pages("[SUMMARY")) == 1       # the run's one summary is spent
    sim.healthy_again()                          # operator restarted it by hand
    await sim.cycle(after_minutes=5)
    third = sim.wedged()
    await sim.cycle(after_minutes=25)
    assert sim.restarts == ["poindexter-speaches"] * 2
    assert "new episode after the source resolved" in third["dispatch_result"]
    assert third["dispatch_result"].startswith("remediating: ")
    # paging stays with dedup: the run was not restarted, only offered
    state = sim.world.dedup_state[SPEACHES_FP + "|warning"]
    assert state["repeat_count"] > 1
    repeat = sim.wedged()
    await sim.cycle(after_minutes=5)             # the new attempt is pending
    assert repeat["dispatch_result"].startswith("suppressed:")
    assert sim.restarts == ["poindexter-speaches"] * 2


@pytest.mark.asyncio
async def test_a_source_resolved_episode_the_breaker_refuses_stays_suppressed(monkeypatch):
    """The operator was already paged in this run (the failed verify), so a
    refused new episode does not page again; the row records why."""
    rule = {**SPEACHES_RULE, "max_attempts_per_window": 1}
    sim = _Sim(monkeypatch, rules=[rule])
    sim.wedged()
    await sim.cycle()
    for _ in range(7):                           # restart didn't hold; 35 min wedged
        sim.wedged()
        await sim.cycle(after_minutes=5)
    assert len(sim.pages("[FIREFIGHTER]")) == 1  # the failed verify paged
    sim.healthy_again()
    await sim.cycle(after_minutes=5)
    pages_before = sim.notify.await_count
    rewedge = sim.wedged()
    await sim.cycle(after_minutes=10)            # t=50: 1 attempt in the hour, cap 1
    assert sim.restarts == ["poindexter-speaches"]
    assert rewedge["dispatch_result"].startswith("suppressed: ")
    assert ("new episode after the source resolved, not auto-remediated "
            "(restart_container: circuit breaker tripped)") in rewedge["dispatch_result"]
    assert sim.notify.await_count == pages_before


@pytest.mark.asyncio
async def test_a_refused_source_resolved_episode_that_lands_on_the_summary_says_why(monkeypatch):
    """Dedup still decides the page. When this row is the run's summary, the
    summary carries the reason the firefighter stepped aside."""
    rule = {**SPEACHES_RULE, "max_attempts_per_window": 1}
    sim = _Sim(monkeypatch, rules=[rule])
    sim.wedged()
    await sim.cycle()
    for _ in range(3):
        sim.wedged()
        await sim.cycle(after_minutes=5)
    await sim.cycle(after_minutes=1)             # t=16 verify: still firing
    sim.healthy_again()
    await sim.cycle(after_minutes=5)
    rewedge = sim.wedged()
    await sim.cycle(after_minutes=10)            # t=31: past the 30-min summary threshold
    assert sim.restarts == ["poindexter-speaches"]
    assert rewedge["dispatch_result"].startswith("sent: summary")
    summaries = sim.pages("[SUMMARY")
    assert len(summaries) == 1
    assert summaries[0].endswith("Not auto-remediated (restart_container: circuit breaker tripped).")


@pytest.mark.asyncio
async def test_a_recovery_after_a_refire_does_not_undo_the_refire(monkeypatch):
    """A probe that reports a level: speaches came back unhealthy after the
    restart, then recovered on its own. The restart did not fix it, and the
    verify says so; the probe's recovery row pages on its own."""
    sim = _Sim(monkeypatch)
    sim.wedged()
    await sim.cycle()
    sim.world.advance(minutes=11)
    sim.wedged()                                 # unhealthy again after the restart
    await sim.cycle()
    sim.world.advance(minutes=2)
    sim.healthy_again()
    await sim.cycle()
    await sim.cycle(after_minutes=3)             # t+16: verify
    verifies = sim.world.audit_rows("remediation_verify")
    assert [(v["details"]["result"], v["details"]["evidence"]) for v in verifies] == [
        ("still_firing", "refired")]
    assert sim.pages("[FIREFIGHTER]")[0].endswith("(it fired again after the action)")


# ---------------------------------------------------------------------------
# Alertmanager-sourced rules (glad-labs-stack#4023). Alertmanager reports
# changes: one firing notification per episode, a resolved one at the next
# group_interval tick after it clears, a repeat every 4 h while it stays
# firing. Silence after a restart proves nothing; the resolved notification
# does. The live rules on prod: PyroscopeDown and PromtailDown, no per-rule
# grace (so the 600 s Alertmanager default).
# ---------------------------------------------------------------------------

PYROSCOPE_FP = "a9b4c69fd247b1e8"
PYROSCOPE_RULE = {
    "id": 1, "alertname": "PyroscopeDown", "match_regex": None,
    "action_name": "restart_container", "params": {"container": "poindexter-pyroscope"},
    "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None,
    "enabled": True,
}
_PYROSCOPE_LABELS = {"job": "pyroscope", "alertname": "PyroscopeDown",
                     "severity": "warning", "category": "infrastructure"}


class _AlertmanagerSim(_Sim):
    def __init__(self, monkeypatch, *, rule=PYROSCOPE_RULE, settings=None):
        super().__init__(monkeypatch, rules=(rule,), settings=settings)
        self.last = None

    def firing(self, *, new_episode=True):
        """A firing notification. A new episode began group_wait (30 s) before
        Alertmanager sent it; a repeat carries its episode's starts_at."""
        if new_episode or self.last is None:
            starts_at = self.world.now() - timedelta(seconds=37)
        else:
            starts_at = self.last["starts_at"]
        return self._notify("firing", starts_at)

    def resolved(self):
        return self._notify("resolved", self.last["starts_at"])

    def _notify(self, status, starts_at):
        self.last = self.world.fire(
            alertname="PyroscopeDown", fingerprint=PYROSCOPE_FP, severity="warning",
            status=status, labels=_PYROSCOPE_LABELS, summary="Pyroscope is down",
            starts_at=starts_at,
        )
        return self.last

    def verifies(self):
        return [(v["details"]["result"], v["details"]["evidence"])
                for v in self.world.audit_rows("remediation_verify")]


@pytest.mark.asyncio
async def test_an_alertmanager_restart_that_works_is_verified_by_its_resolved_notification(monkeypatch):
    sim = _AlertmanagerSim(monkeypatch)
    sim.firing()
    await sim.cycle()                            # restarted, page held
    action = sim.world.audit_rows("remediation_action")[0]["details"]
    assert action["verify_signal"] == E.VERIFY_BY_RESOLVED_NOTIFICATION
    assert action["verify_after_seconds"] == 600
    sim.world.advance(minutes=5)
    resolved = sim.resolved()                    # the next group_interval tick
    await sim.cycle()
    assert resolved["dispatch_result"].startswith("suppressed:")
    assert sim.verifies() == []                  # not due until 10 min
    await sim.cycle(after_minutes=5.5)
    assert sim.verifies() == [("resolved", "resolved_notification")]
    assert sim.notify.await_count == 0
    assert sim.restarts == ["poindexter-pyroscope"]


@pytest.mark.asyncio
async def test_an_alertmanager_restart_that_does_not_work_pages_when_the_verify_is_due(monkeypatch):
    """The bug in #4023: at 120 s, with Alertmanager silent until its 4-h
    repeat, the verify read a failed restart as resolved. The repeat landed
    outside the dedup window, was restarted again, "resolved" again, and the
    operator never heard of it."""
    sim = _AlertmanagerSim(monkeypatch)
    sim.firing()
    await sim.cycle()
    for _ in range(3):
        await sim.cycle(after_minutes=3)         # t+9: Alertmanager says nothing
    assert sim.verifies() == [] and sim.notify.await_count == 0
    await sim.cycle(after_minutes=1.5)           # t+10.5: the verify is due
    assert sim.verifies() == [("still_firing", "no_resolved_notification")]
    assert sim.pages("[FIREFIGHTER]") == [
        "[FIREFIGHTER] auto-remediation did not resolve PyroscopeDown: attempted "
        "restart_container, still firing after 600s (no resolved notification since the action)"
    ]
    # Alertmanager's 4-h repeat: past the dedup window, a new run. It is
    # restarted again, and when that fails too, it pages again.
    sim.world.advance(minutes=230)
    repeat = sim.firing(new_episode=False)
    await sim.cycle()
    assert repeat["dispatch_result"].startswith("remediating: restart_container")
    await sim.cycle(after_minutes=10.5)
    assert sim.restarts == ["poindexter-pyroscope"] * 2
    assert len(sim.pages("[FIREFIGHTER]")) == 2


@pytest.mark.asyncio
async def test_an_alertmanager_resolved_notification_is_not_read_as_a_refire(monkeypatch):
    """The latent half of #4023: the resolved notification shares the firing
    row's dedup key, so it moves alert_dedup_state.last_seen_at past the
    action. The old oracle read that as a re-fire and paged a fix that
    worked. A grace past the group_interval tick is where it bit."""
    sim = _AlertmanagerSim(monkeypatch, rule={**PYROSCOPE_RULE, "verify_after_seconds": 900})
    sim.firing()
    await sim.cycle()
    sim.world.advance(minutes=5)
    sim.resolved()
    await sim.cycle()
    acted_at = sim.world.audit_rows("remediation_action")[0]["timestamp"]
    assert sim.world.dedup_state[f"{PYROSCOPE_FP}|warning"]["last_seen_at"] > acted_at
    await sim.cycle(after_minutes=10.5)          # t+15.5: the rule's 900 s
    assert sim.verifies() == [("resolved", "resolved_notification")]
    assert sim.notify.await_count == 0


@pytest.mark.asyncio
async def test_an_alertmanager_alert_that_comes_back_inside_the_verify_pages(monkeypatch):
    """The restart looked like it worked (resolved at the first tick), then
    pyroscope died again. The verify reads the latest notification, not the
    first, and the re-fire is not restarted while the attempt is pending."""
    sim = _AlertmanagerSim(monkeypatch)
    sim.firing()
    await sim.cycle()
    sim.world.advance(minutes=5)
    sim.resolved()
    await sim.cycle()
    sim.world.advance(minutes=4.5)
    again = sim.firing()                         # a new episode
    await sim.cycle()
    assert again["dispatch_result"].startswith("suppressed:")
    await sim.cycle(after_minutes=1)             # t+10.5: the verify
    assert sim.verifies() == [("still_firing", "refired")]
    assert sim.pages("[FIREFIGHTER]")[0].endswith("(it fired again after the action)")
    assert sim.restarts == ["poindexter-pyroscope"]


@pytest.mark.asyncio
async def test_a_resolved_notification_from_a_backlog_still_counts(monkeypatch):
    """The brain was down while pyroscope fired and cleared: both
    notifications arrive in one batch, and the firing one is still acted on.
    The resolved notification is the latest state, even though it was written
    before the restart; the legacy oracle saw its dispatch as a re-fire."""
    sim = _AlertmanagerSim(monkeypatch)
    sim.firing()
    sim.world.advance(minutes=5)
    sim.resolved()
    await sim.cycle()
    assert sim.restarts == ["poindexter-pyroscope"]
    await sim.cycle(after_minutes=10.5)
    assert sim.verifies() == [("resolved", "resolved_notification")]
    assert sim.notify.await_count == 0


@pytest.mark.asyncio
async def test_an_alertmanager_resolved_notification_never_opens_an_episode(monkeypatch):
    """Alertmanager's resolved row shares the firing row's dedup key (same
    fingerprint, same severity). It is not a recurrence and restarts nothing;
    the next real firing is the new episode."""
    sim = _AlertmanagerSim(monkeypatch)
    sim.firing()
    await sim.cycle()
    sim.world.advance(minutes=5)
    resolved = sim.resolved()
    await sim.cycle()
    assert resolved["dispatch_result"].startswith("suppressed:")
    await sim.cycle(after_minutes=5.5)           # verify: resolved
    assert sim.restarts == ["poindexter-pyroscope"]
    sim.world.advance(minutes=30)
    again = sim.firing()
    await sim.cycle()
    assert sim.restarts == ["poindexter-pyroscope"] * 2
    assert "new episode after a verified fix" in again["dispatch_result"]
    assert sim.notify.await_count == 0


@pytest.mark.asyncio
async def test_a_probe_that_stamps_starts_at_with_now_is_verified_by_refire(monkeypatch):
    """Eight brain probes write starts_at = NOW(), equal to received_at. They
    report a level, so silence after the action is the fix, at the general
    grace."""
    fingerprint = "docker-port-forward-restart-skipped-poindexter-grafana"
    rule = {"id": 50, "alertname": None, "match_regex": f"^{fingerprint}\\|",
            "action_name": "restart_container", "params": {"container": "poindexter-grafana"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None,
            "enabled": True}
    sim = _Sim(monkeypatch, rules=[rule])
    sim.world.fire(alertname="docker_port_forward_restart_skipped", fingerprint=fingerprint,
                   severity="warning", starts_at="now", labels={"container": "poindexter-grafana"})
    await sim.cycle()
    action = sim.world.audit_rows("remediation_action")[0]["details"]
    assert action["verify_signal"] == E.VERIFY_BY_REFIRE
    assert action["verify_after_seconds"] == 120
    await sim.cycle(after_minutes=2.5)
    assert [(v["details"]["result"], v["details"]["evidence"])
            for v in sim.world.audit_rows("remediation_verify")] == [("resolved", "no_refire")]
    assert sim.notify.await_count == 0


@pytest.mark.asyncio
async def test_with_the_firefighter_off_a_recurrence_is_a_plain_repeat(monkeypatch):
    sim = _Sim(monkeypatch, settings={"ops_firefighter_enabled": "false"})
    queries = []
    real_fetchrow, real_fetchval = sim.world.fetchrow, sim.world.fetchval

    async def fetchrow(sql, *a):
        queries.append(sql)
        return await real_fetchrow(sql, *a)

    async def fetchval(sql, *a):
        queries.append(sql)
        return await real_fetchval(sql, *a)

    sim.world.fetchrow, sim.world.fetchval = fetchrow, fetchval
    sim.wedged()
    await sim.cycle()
    second = sim.wedged()
    await sim.cycle(after_minutes=20)
    assert sim.restarts == []
    assert second["dispatch_result"].startswith("suppressed:")
    assert not any("LEFT JOIN LATERAL" in q or "SELECT EXISTS" in q for q in queries)
