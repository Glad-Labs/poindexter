import asyncio
import json
import logging
import sys
import types
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


class _BrainSenders:
    """The brain's two senders, as the dispatcher's production path reaches
    them: ``notify`` pages Telegram and Discord, ``send_discord`` Discord alone."""

    def __init__(self):
        self.sent = []

    async def notify(self, message, *, pool=None):
        self.sent.append(("telegram+discord", message))
        return {"ok": True, "telegram_message_id": 1, "discord_message_id": "1"}

    async def send_discord(self, message, webhook_url=None, *, pool=None, message_reference_id=None):
        self.sent.append(("discord", message))
        return "1"

    def channels(self, needle):
        return [channels for channels, message in self.sent if needle in message]


def _install_brain_senders(monkeypatch):
    """Page the way the brain does: the worker's notify_operator does not
    import there, so the dispatcher falls back to brain_daemon's senders."""
    senders = _BrainSenders()
    brain_daemon = types.ModuleType("poindexter.brain.brain_daemon")
    brain_daemon.notify = senders.notify
    brain_daemon.send_discord = senders.send_discord
    monkeypatch.setitem(sys.modules, "poindexter.brain.brain_daemon", brain_daemon)
    monkeypatch.setitem(sys.modules, "poindexter.services.integrations.operator_notify", None)
    return senders


class _Sim:
    def __init__(self, monkeypatch, *, rules=(SPEACHES_RULE,), settings=None, brain_senders=False):
        self.world = FirefighterWorld(app_settings={**PROD_SETTINGS, **(settings or {})},
                                      rules=list(rules))
        self.notify = AsyncMock(return_value={"ok": True})
        # With brain_senders the sim injects no notify_fn: pages take the
        # production path, and self.senders records which channels they reach.
        self.senders = _install_brain_senders(monkeypatch) if brain_senders else None
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
        notify_fn = None if self.senders is not None else self.notify
        return await ad.poll_and_dispatch(self.world, notify_fn=notify_fn)

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
PYROSCOPE = {"alertname": "PyroscopeDown", "fingerprint": PYROSCOPE_FP, "severity": "warning",
             "labels": _PYROSCOPE_LABELS, "summary": "Pyroscope is down"}
# Rule 2 on prod. PromtailDown is critical (observability-sidecars.yml).
PROMTAIL_RULE = {
    "id": 2, "alertname": "PromtailDown", "match_regex": None,
    "action_name": "restart_container", "params": {"container": "poindexter-promtail"},
    "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None,
    "enabled": True,
}
PROMTAIL = {"alertname": "PromtailDown", "fingerprint": "5b1d2e7c90a4f311", "severity": "critical",
            "labels": {"job": "promtail", "alertname": "PromtailDown",
                       "severity": "critical", "category": "infrastructure"},
            "summary": "Promtail is down"}


class _AlertmanagerSim(_Sim):
    def __init__(self, monkeypatch, *, rule=PYROSCOPE_RULE, alert=PYROSCOPE, settings=None,
                 brain_senders=False):
        super().__init__(monkeypatch, rules=(rule,), settings=settings, brain_senders=brain_senders)
        self.alert = alert
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
        self.last = self.world.fire(status=status, starts_at=starts_at, **self.alert)
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
async def test_an_alertmanager_alert_back_inside_a_run_its_resolution_started_is_remediated(monkeypatch):
    """glad-labs-stack#4024. The restart did not hold, and the verify paged.
    Pyroscope stayed down past the 120-min window (Alertmanager repeats a
    firing alert only every 4 h), then recovered, so its resolved notification
    started a new dedup run. It went down again 20 min later. That firing row
    used to be a suppressed repeat of a run that had never fired: no restart,
    no page. Now it is a first fire, so the firefighter acts on it again."""
    sim = _AlertmanagerSim(monkeypatch)
    sim.firing()
    await sim.cycle()                             # restart 1, page held
    await sim.cycle(after_minutes=11)             # no resolved notification in 600 s
    assert [r for r, _ in sim.verifies()] == ["still_firing"]
    sim.world.advance(minutes=140)
    resolved = sim.resolved()                     # 151 min after the last firing row
    await sim.cycle()
    assert resolved["dispatch_result"] == "sent"  # starts a run; the operator hears it
    sim.world.advance(minutes=20)
    back = sim.firing()
    await sim.cycle()
    assert sim.restarts == ["poindexter-pyroscope"] * 2
    assert back["dispatch_result"].startswith("remediating: restart_container (run ")


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
    # No remediation history is read (the episode queries). Dedup's own
    # run-status check (_RUN_HAS_FIRED_SQL) runs either way.
    assert not any("LEFT JOIN LATERAL" in q or "alert_events r" in q for q in queries)
    assert any(q == ad._RUN_HAS_FIRED_SQL for q in queries)


# ---------------------------------------------------------------------------
# The LLM long-tail's look: the repeat where an un-ruled alert first becomes
# persistent (glad-labs-stack#4022). Before this the dispatcher offered only
# first rows (repeat_count 1, no age), which the persistence gate always
# refused: 0 selector calls on prod in 29 days, 643 in the 90-day replay after.
# ---------------------------------------------------------------------------

_GATES = {"enabled": True, "llm_longtail_enabled": True, "min_repeats": 2, "min_age_minutes": 10}


def _state(*, count, first_min_ago, last_min_ago, now):
    return {"repeat_count": count,
            "first_seen_at": now - timedelta(minutes=first_min_ago),
            "last_seen_at": now - timedelta(minutes=last_min_ago)}


def test_the_second_row_is_the_persistent_repeat():
    now = datetime.now(UTC)
    crossing = ad._persistence_crossing(_GATES, state=_state(count=1, first_min_ago=20, last_min_ago=20, now=now), now=now)
    assert crossing == {"repeat_count": 2, "age_minutes": pytest.approx(20.0),
                        "refire_interval_seconds": pytest.approx(1200.0)}


def test_a_repeat_after_the_persistent_one_is_not_offered_again():
    now = datetime.now(UTC)
    assert ad._persistence_crossing(_GATES, state=_state(count=2, first_min_ago=40, last_min_ago=20, now=now), now=now) is None


def test_the_age_gate_opens_on_the_first_repeat_past_it():
    """min_repeats 5: rows at 0, 4, 8, 12, 16 minutes. The age gate (10 min)
    opens on the row at 12 (repeat 4), not on the rows either side of it."""
    gates = {**_GATES, "min_repeats": 5}
    now = datetime.now(UTC)
    at_8 = ad._persistence_crossing(gates, state=_state(count=2, first_min_ago=8, last_min_ago=4, now=now), now=now)
    at_12 = ad._persistence_crossing(gates, state=_state(count=3, first_min_ago=12, last_min_ago=4, now=now), now=now)
    at_16 = ad._persistence_crossing(gates, state=_state(count=4, first_min_ago=16, last_min_ago=4, now=now), now=now)
    assert at_8 is None and at_16 is None
    assert at_12["repeat_count"] == 4 and at_12["refire_interval_seconds"] == pytest.approx(240.0)


def test_with_min_repeats_one_the_first_row_had_the_look():
    """A first row offered with repeat_count 1 already passes min_repeats 1, so
    no later repeat is a crossing."""
    now = datetime.now(UTC)
    gates = {**_GATES, "min_repeats": 1}
    assert ad._persistence_crossing(gates, state=_state(count=1, first_min_ago=5, last_min_ago=5, now=now), now=now) is None


TAPS_FP = "job_failure:scheduler.run_taps"
_TAPS_PICK = {"action_name": "restart_container", "params": {"container": "poindexter-worker"},
              "confidence": 0.8, "reason": "the tap job keeps failing", "model": "ollama/granite4.2:3b"}


class _LongTailSim(_Sim):
    """The replay world with no rules, and a scripted LLM selector."""

    def __init__(self, monkeypatch, *, pick=_TAPS_PICK, settings=None, rules=(), brain_senders=False):
        super().__init__(monkeypatch, rules=rules, settings=settings, brain_senders=brain_senders)
        self.pick = pick
        self.selections = []

        async def _select(*, alert, catalog):
            self.selections.append(alert["labels"]["alertname"])
            return self.pick

        monkeypatch.setattr(ad, "_make_select_fn", lambda pool: _select)

    def taps_failed(self):
        return self.world.fire(alertname="scheduler.run_taps:job_failure", fingerprint=TAPS_FP,
                               severity="warning", summary="run_taps failed 3 times in a row")


@pytest.mark.asyncio
async def test_the_long_tail_sees_an_unruled_alert_once_on_its_persistent_repeat(monkeypatch):
    """Shipped default (dry run): the pick is recorded and noted on the row,
    nothing runs, and paging is exactly what dedup decided."""
    sim = _LongTailSim(monkeypatch)
    sim.taps_failed()
    await sim.cycle()                               # first fire pages; never persistent
    assert sim.selections == [] and sim.notify.await_count == 1
    second = sim.taps_failed()
    await sim.cycle(after_minutes=20)
    assert sim.selections == ["scheduler.run_taps:job_failure"]
    assert second["dispatch_result"].startswith("suppressed: repeat 2")
    assert second["dispatch_result"].endswith(
        'persistent at repeat 2 (20 min), not auto-remediated (restart_container: '
        'dry run; would have run it with {"container": "poindexter-worker"})')
    for _ in range(3):
        sim.taps_failed()
        await sim.cycle(after_minutes=20)
    assert len(sim.selections) == 1                 # one look per run
    assert sim.restarts == []
    dry = sim.world.audit_rows("remediation_dry_run")
    assert [d["details"]["params"] for d in dry] == [{"container": "poindexter-worker"}]
    assert sim.world.audit_rows("remediation_action") == []
    assert sim.notify.await_count == 2              # first fire + the run's 30-min summary


@pytest.mark.asyncio
async def test_an_abstain_on_the_persistent_repeat_is_noted_on_the_row(monkeypatch):
    sim = _LongTailSim(monkeypatch, pick=None)
    sim.taps_failed()
    await sim.cycle()
    second = sim.taps_failed()
    await sim.cycle(after_minutes=20)
    assert second["dispatch_result"].endswith(
        "persistent at repeat 2 (20 min), not auto-remediated (no rule; llm abstained)")
    assert sim.world.audit == []


@pytest.mark.asyncio
async def test_a_live_llm_action_is_held_and_judged_over_the_alerts_own_cadence(monkeypatch):
    """Live: the repeat is held. The alert re-fires every 20 min, so the verify
    waits two of those (40 min), not the flat 120 s, which would have read the
    quiet minutes after the restart as a fix."""
    sim = _LongTailSim(monkeypatch, settings={"ops_firefighter_llm_dry_run": "false"})
    sim.taps_failed()
    await sim.cycle()
    second = sim.taps_failed()
    await sim.cycle(after_minutes=20)
    assert second["dispatch_result"].startswith("remediating: restart_container (run ")
    assert second["dispatch_result"].endswith("; persistent at repeat 2 (20 min))")
    assert sim.restarts == ["poindexter-worker"]
    (action,) = sim.world.audit_rows("remediation_action")
    assert action["details"]["source"] == "llm"
    assert action["details"]["verify_after_seconds"] == pytest.approx(2400, abs=1)  # real-clock ms
    await sim.cycle(after_minutes=5)                # the old verify would have judged here
    assert sim.world.audit_rows("remediation_verify") == []
    sim.taps_failed()
    await sim.cycle(after_minutes=15)               # still failing: a pending attempt, not an episode
    await sim.cycle(after_minutes=21)               # verify due
    assert [v["details"]["result"] for v in sim.world.audit_rows("remediation_verify")] == ["still_firing"]
    assert len(sim.pages("[FIREFIGHTER] auto-remediation did not resolve scheduler.run_taps:job_failure")) == 1
    assert sim.world.audit_rows("finding") == []
    assert sim.restarts == ["poindexter-worker"]


@pytest.mark.asyncio
async def test_a_live_llm_fix_that_holds_resolves_silently_and_proposes_a_rule(monkeypatch):
    sim = _LongTailSim(monkeypatch, settings={"ops_firefighter_llm_dry_run": "false"})
    sim.taps_failed()
    await sim.cycle()
    sim.taps_failed()
    await sim.cycle(after_minutes=20)
    await sim.cycle(after_minutes=41)
    assert [v["details"]["result"] for v in sim.world.audit_rows("remediation_verify")] == ["resolved"]
    (finding,) = sim.world.audit_rows("finding")
    assert finding["details"]["kind"] == "remediation_candidate_rule"
    assert sim.notify.await_count == 1              # only the first fire


@pytest.mark.asyncio
async def test_a_verified_llm_fix_gives_the_next_episode_its_own_look(monkeypatch):
    """The recurrence restarts the run: its first row pages (a first sighting
    is never persistent), and its persistent repeat is offered again."""
    sim = _LongTailSim(monkeypatch, settings={"ops_firefighter_llm_dry_run": "false"})
    sim.taps_failed()
    await sim.cycle()
    sim.taps_failed()
    await sim.cycle(after_minutes=20)
    await sim.cycle(after_minutes=41)               # resolved
    sim.taps_failed()
    await sim.cycle(after_minutes=10)               # came back: a new run
    assert len(sim.pages("came back after an auto-remediation that was verified")) == 1
    sim.taps_failed()
    await sim.cycle(after_minutes=20)
    assert len(sim.selections) == 2
    assert sim.restarts == ["poindexter-worker"] * 2


@pytest.mark.asyncio
async def test_a_ruled_alert_is_not_restarted_again_on_its_persistent_repeat(monkeypatch):
    """The rule acted on the first row and its verify is pending. The second
    row is the persistent repeat, but the long-tail stays out: no second
    restart, no model call, no note."""
    rule = {"id": 2, "alertname": "PromtailDown", "match_regex": None,
            "action_name": "restart_container", "params": {"container": "poindexter-promtail"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None,
            "enabled": True}
    sim = _LongTailSim(monkeypatch, rules=[rule], settings={"ops_firefighter_llm_dry_run": "false"})

    def promtail_down():
        return sim.world.fire(alertname="PromtailDown", fingerprint="5b1d2e7c90a4f311",
                              severity="warning", labels={"alertname": "PromtailDown", "job": "promtail"})

    promtail_down()
    await sim.cycle()
    second = promtail_down()
    await sim.cycle(after_minutes=1)
    assert sim.restarts == ["poindexter-promtail"]
    assert sim.selections == []
    assert second["dispatch_result"].startswith("suppressed: repeat 2")
    assert "persistent" not in second["dispatch_result"]


@pytest.mark.asyncio
async def test_with_the_long_tail_off_there_is_no_persistent_look(monkeypatch):
    sim = _LongTailSim(monkeypatch, settings={"ops_firefighter_llm_longtail_enabled": "false"})
    sim.taps_failed()
    await sim.cycle()
    second = sim.taps_failed()
    await sim.cycle(after_minutes=20)
    assert sim.selections == []
    assert "persistent" not in second["dispatch_result"]


@pytest.mark.asyncio
async def test_a_resolved_row_is_never_offered_to_the_long_tail(monkeypatch):
    """Alertmanager's resolved notification shares the firing row's dedup key,
    so it can land on the persistent repeat. Nothing is asked about it; the
    next firing row is a new episode, a first sighting."""
    sim = _LongTailSim(monkeypatch)

    def ram_thrash(status):
        return sim.world.fire(alertname="PoindexterHostMemoryThrashing", fingerprint="c0ffee42aa17",
                              severity="critical", status=status,
                              labels={"alertname": "PoindexterHostMemoryThrashing", "severity": "critical"})

    ram_thrash("firing")
    await sim.cycle()
    ram_thrash("resolved")
    await sim.cycle(after_minutes=6)
    again = ram_thrash("firing")
    await sim.cycle(after_minutes=6)
    assert sim.selections == []
    assert again["dispatch_result"].startswith("suppressed: repeat 3")


@pytest.mark.asyncio
async def test_a_persistent_repeat_that_is_the_runs_summary_carries_the_look(monkeypatch):
    """The median gap between an alert's first two rows on prod is 44 min, so
    the persistent repeat is often past the 30-min summary threshold. Dedup
    still sends the summary; the page and the row both say what the model
    would have done."""
    sim = _LongTailSim(monkeypatch)
    sim.taps_failed()
    await sim.cycle()
    second = sim.taps_failed()
    await sim.cycle(after_minutes=44)
    assert sim.selections == ["scheduler.run_taps:job_failure"]
    assert second["dispatch_result"].startswith("sent: summary (repeat 2); persistent at repeat 2 (44 min), ")
    (summary_page,) = sim.pages("[SUMMARY")
    assert summary_page.endswith(
        'Not auto-remediated (restart_container: dry run; would have run it with '
        '{"container": "poindexter-worker"}).')


@pytest.mark.asyncio
async def test_a_live_llm_action_on_a_grafana_alert_is_proved_by_its_resolved_notification(monkeypatch):
    """Grafana re-sends hourly, so the persistent repeat comes an hour in. For
    a notifier the evidence is the resolved notification, which arrives when it
    arrives: the grace stays the notifier default, not two re-fire intervals."""
    sim = _LongTailSim(monkeypatch, settings={"ops_firefighter_llm_dry_run": "false"},
                       pick={**_TAPS_PICK, "params": {"container": "poindexter-worker"}})
    episode_start = sim.world.now() - timedelta(minutes=5)

    def anomaly(status="firing"):
        return sim.world.fire(alertname="Traffic Anomaly", fingerprint="7f0e11ab2c3d4e5f",
                              severity="warning", status=status, starts_at=episode_start,
                              labels={"alertname": "Traffic Anomaly", "severity": "warning"})

    anomaly()
    await sim.cycle()
    repeat = anomaly()
    await sim.cycle(after_minutes=60)               # Grafana's repeat_interval
    assert repeat["dispatch_result"].startswith("remediating: restart_container")
    (action,) = sim.world.audit_rows("remediation_action")
    assert action["details"]["verify_signal"] == "resolved_notification"
    assert action["details"]["verify_after_seconds"] == 600
    anomaly("resolved")
    await sim.cycle(after_minutes=5)
    await sim.cycle(after_minutes=6)                # verify due
    (verify,) = sim.world.audit_rows("remediation_verify")
    assert (verify["details"]["result"], verify["details"]["evidence"]) == ("resolved", "resolved_notification")


# ---------------------------------------------------------------------------
# Where a failed fix pages. The firefighter held the alert's own page, so the
# verify's page is the operator's first word of the alert, and it goes where
# that page would have: Telegram and Discord for critical, Discord alone for a
# warning. Before 2026-09-25 it went to the plain notifier, which in the brain
# sends to both, so every failed fix reached Telegram (both PyroscopeDown pages
# in that day's drill did). brain_senders runs the production notify path.
# ---------------------------------------------------------------------------

_FAILED_FIX = "[FIREFIGHTER] auto-remediation did not resolve"


@pytest.mark.parametrize(
    "rule,alert,channels",
    [(PYROSCOPE_RULE, PYROSCOPE, "discord"), (PROMTAIL_RULE, PROMTAIL, "telegram+discord")],
    ids=["PyroscopeDown-warning", "PromtailDown-critical"],
)
@pytest.mark.asyncio
async def test_a_failed_alertmanager_fix_pages_the_channels_of_its_severity(monkeypatch, rule, alert, channels):
    sim = _AlertmanagerSim(monkeypatch, rule=rule, alert=alert, brain_senders=True)
    sim.firing()
    await sim.cycle()                            # restarted, page held
    assert sim.senders.sent == []
    await sim.cycle(after_minutes=10.5)          # no resolved notification by the verify
    assert sim.verifies() == [("still_firing", "no_resolved_notification")]
    assert sim.senders.channels(f"{_FAILED_FIX} {alert['alertname']}:") == [channels]
    assert len(sim.senders.sent) == 1            # the operator's only word of it


@pytest.mark.asyncio
async def test_a_wedged_sidecar_whose_restart_did_not_hold_pages_discord_only(monkeypatch):
    """container_unhealthy is a warning: the health watch leaves severity and
    category out of its labels, and the dispatcher fills them from the row."""
    sim = _Sim(monkeypatch, brain_senders=True)
    sim.wedged()
    await sim.cycle()
    for _ in range(3):                           # still unhealthy after the restart
        sim.wedged()
        await sim.cycle(after_minutes=5)
    await sim.cycle(after_minutes=1)             # t+16: verify
    assert [v["details"]["result"] for v in sim.world.audit_rows("remediation_verify")] == ["still_firing"]
    action = sim.world.audit_rows("remediation_action")[0]["details"]
    assert (action["alert_severity"], action["alert_category"], action["alert_force_channel"]) == (
        "warning", "infrastructure", "")
    assert sim.senders.channels(_FAILED_FIX) == ["discord"]
    assert len(sim.senders.sent) == 1


@pytest.mark.asyncio
async def test_a_failed_fix_of_a_finding_follows_its_kinds_delivery_policy(monkeypatch):
    """findings.<kind>.delivery=telegram pages a warning finding on Telegram
    (deploy_sync_stale, db_clock_skew and wan_ip_changed do on prod). Its
    failed fix goes there too, not to Discord on the severity alone."""
    sim = _LongTailSim(monkeypatch, settings={"ops_firefighter_llm_dry_run": "false"}, brain_senders=True)

    def stale():
        return sim.world.fire(alertname="deploy_sync_probe:deploy_sync_stale", fingerprint="deploy-sync-stale",
                              severity="warning", labels={"force_channel": "telegram"},
                              summary="the deploy clone is 3 commits behind origin/main")

    stale()
    await sim.cycle()                               # first fire pages, on its policy's channels
    stale()
    await sim.cycle(after_minutes=20)               # persistent repeat: the LLM restarts, page held
    stale()
    await sim.cycle(after_minutes=20)               # still failing
    await sim.cycle(after_minutes=21)               # verify due
    assert [v["details"]["result"] for v in sim.world.audit_rows("remediation_verify")] == ["still_firing"]
    assert sim.senders.sent[0][0] == "telegram+discord"
    assert sim.senders.channels(_FAILED_FIX) == ["telegram+discord"]


@pytest.mark.asyncio
async def test_an_action_recorded_without_a_route_still_pages_loud(monkeypatch):
    """The shape written before routes were (the 2026-07-04 drill row): no
    alert_severity, nothing to say where the page belongs. It is the operator's
    only word of an alert whose page was held, so it goes to both channels, as
    every verify page did before. Even for a warning."""
    sim = _Sim(monkeypatch, brain_senders=True)
    sim.wedged()
    await sim.cycle()                            # restarted, page held
    action = sim.world.audit_rows("remediation_action")[0]["details"]
    for key in ("alert_severity", "alert_category", "alert_force_channel",
                "verify_signal", "alert_event_id", "alert_fingerprint"):
        del action[key]
    for _ in range(3):
        sim.wedged()
        await sim.cycle(after_minutes=5)
    await sim.cycle(after_minutes=1)             # t+16: verify, on the legacy oracle
    assert [(v["details"]["result"], v["details"]["evidence"])
            for v in sim.world.audit_rows("remediation_verify")] == [("still_firing", "dedup_state")]
    assert sim.senders.channels(_FAILED_FIX) == ["telegram+discord"]


@pytest.mark.parametrize(
    "rule,alert,critical",
    [(PYROSCOPE_RULE, PYROSCOPE, False), (PROMTAIL_RULE, PROMTAIL, True)],
    ids=["PyroscopeDown-warning", "PromtailDown-critical"],
)
@pytest.mark.asyncio
async def test_an_injected_notifier_hears_a_failed_fix_at_its_alerts_routing(monkeypatch, rule, alert, critical):
    """A test that injects notify_fn reads the route off ``critical``, as the
    dedup tests do: True is Telegram and Discord, False Discord alone."""
    sim = _AlertmanagerSim(monkeypatch, rule=rule, alert=alert)
    sim.firing()
    await sim.cycle()
    await sim.cycle(after_minutes=10.5)
    (page,) = [c for c in sim.notify.await_args_list if c.args[0].startswith(_FAILED_FIX)]
    assert page.kwargs == {"critical": critical}
