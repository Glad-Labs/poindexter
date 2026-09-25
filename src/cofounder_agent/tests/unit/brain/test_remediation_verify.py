import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from poindexter.brain.remediation import engine as E
from tests.unit.brain._remediation_fakes import FakePool

LOG = logging.getLogger("t")
CFG = {"verify_after_seconds": 120}


def _pending_row(run_id, fingerprint, acted_at, verify_after=120):
    return {
        "id": 1, "timestamp": acted_at,
        "details": json.dumps({
            "remediation_run_id": run_id, "fingerprint": fingerprint,
            "alertname": "WorkerDown", "action_name": "restart_container",
            "verify_after_seconds": verify_after, "source": "rule",
        }),
    }


def _pending_llm_row(run_id, fingerprint, acted_at, verify_after=120):
    """A pending remediation_action written by the LLM long-tail path."""
    return {
        "id": 1, "timestamp": acted_at,
        "details": json.dumps({
            "remediation_run_id": run_id, "fingerprint": fingerprint,
            "alertname": "NovelUnruledAlert", "action_name": "restart_container",
            "verify_after_seconds": verify_after, "source": "llm",
            "confidence": 0.81, "model": "ollama/llama3.2:3b",
            "params": {"container": "poindexter-pyroscope"},
        }),
    }


def _targeted_row(run_id, acted_at, *, signal, verify_after=600):
    """A pending remediation_action that recorded what the verify reads: the
    PyroscopeDown row it acted on (id 41), by the producer's fingerprint."""
    return {
        "id": 1, "timestamp": acted_at,
        "details": json.dumps({
            "remediation_run_id": run_id, "fingerprint": "a9b4c69fd247b1e8|warning",
            "alertname": "PyroscopeDown", "action_name": "restart_container",
            "verify_after_seconds": verify_after, "source": "rule",
            "verify_signal": signal, "alert_event_id": 41,
            "alert_fingerprint": "a9b4c69fd247b1e8", "alert_severity": "warning",
        }),
    }


def _findings(pool):
    """audit_log rows written with event_type='finding' (args[0] of _write_audit)."""
    return [
        json.loads(e[1][3]) for e in pool.executed
        if "audit_log" in e[0] and e[1][0] == "finding"
    ]


def _verify_rows(pool):
    return [
        json.loads(e[1][3]) for e in pool.executed
        if "audit_log" in e[0] and e[1][0] == "remediation_verify"
    ]


@pytest.mark.asyncio
async def test_resolved_writes_verify_and_does_not_page():
    acted = datetime.now(UTC) - timedelta(seconds=200)  # past grace
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r1", "fp1", acted)])
    # dedup_state.last_seen_at BEFORE we acted -> not re-fired -> resolved
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted - timedelta(seconds=5)})
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    assert out["resolved"] == 1 and out["still_firing"] == 0
    assert paged == []
    verify_rows = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    assert any(v.get("result") == "resolved" for v in verify_rows)
    # recorded before the verify read alert_events: the legacy oracle judged it
    assert [v["evidence"] for v in _verify_rows(pool)] == ["dedup_state"]


@pytest.mark.asyncio
async def test_still_firing_pages_and_writes_verify():
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r2", "fp2", acted)])
    # last_seen_at AFTER we acted -> re-fired -> still firing
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted + timedelta(seconds=30)})
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    assert out["still_firing"] == 1 and out["resolved"] == 0
    assert len(paged) == 1 and "still firing" in paged[0]
    verify_rows = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    assert any(v.get("result") == "still_firing" for v in verify_rows)


@pytest.mark.asyncio
async def test_not_yet_due_is_skipped():
    acted = datetime.now(UTC) - timedelta(seconds=10)  # inside grace
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r3", "fp3", acted)])
    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=None)
    assert out == {"verified": 0, "resolved": 0, "still_firing": 0}


@pytest.mark.asyncio
async def test_still_firing_check_db_error_logs_warning_and_pages(caplog):
    # A DB read failure during the still-firing check must be VISIBLE (logged)
    # and fail toward paging — never a silent swallow.
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r4", "fp4", acted)])

    def _boom(sql, args):
        raise RuntimeError("dedup_state read failed")

    pool.set_fetchrow(_boom)
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    with caplog.at_level(logging.WARNING):
        out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)

    assert out["still_firing"] == 1 and out["resolved"] == 0
    assert len(paged) == 1
    assert paged[0].endswith("(the verify could not read the alert's state)")
    assert [v["evidence"] for v in _verify_rows(pool)] == ["check_failed"]
    assert any("still-firing check failed" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Plan B — candidate-rule learning loop: a RESOLVED llm-source self-heal emits
# a remediation_candidate_rule finding for the operator to promote.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_llm_source_emits_candidate_rule_finding():
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_llm_row("r5", "fp5", acted)])
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted - timedelta(seconds=5)})  # resolved
    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=None)
    assert out["resolved"] == 1
    findings = _findings(pool)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "remediation_candidate_rule"
    assert "dedup_key" in f  # stable so repeats don't re-page
    assert f["extra"]["action_name"] == "restart_container"
    assert f["extra"]["alertname"] == "NovelUnruledAlert"
    assert f["extra"]["confidence"] == 0.81


@pytest.mark.asyncio
async def test_resolved_rule_source_emits_no_finding():
    """A deterministic rule self-heal is expected behaviour, not a candidate —
    no learning-loop finding."""
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r6", "fp6", acted)])  # source="rule"
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted - timedelta(seconds=5)})
    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=None)
    assert out["resolved"] == 1
    assert _findings(pool) == []


@pytest.mark.asyncio
async def test_still_firing_llm_source_emits_no_finding():
    """Only a RESOLVED llm action is worth codifying — a still-firing one is a
    failed attempt, not a candidate rule."""
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_llm_row("r7", "fp7", acted)])
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted + timedelta(seconds=30)})  # still firing
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    assert out["still_firing"] == 1
    assert _findings(pool) == []


# ---------------------------------------------------------------------------
# The evidence the verify reads from alert_events (glad-labs-stack#4023). An
# action records the row it acted on; the verify asks alert_events about that
# alert by the producer's fingerprint, and what counts depends on the producer.
# ---------------------------------------------------------------------------


def _evidence_pool(acted, *, signal, answer):
    """A pool whose pending action targets row 41, answering the evidence query
    with ``answer`` and recording what it was asked. ``alert_dedup_state`` is
    never consulted for a targeted action, so reading it fails the test."""
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_targeted_row("r9", acted, signal=signal)])
    asked = []

    def _fetchval(sql, args):
        asked.append((sql, args))
        return answer

    def _no_dedup_state(sql, args):
        raise AssertionError("the verify read alert_dedup_state for a targeted action")

    pool.set_fetchval(_fetchval)
    pool.set_fetchrow(_no_dedup_state)
    return pool, asked


async def _scan(pool):
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    return out, paged


@pytest.mark.asyncio
async def test_a_probe_that_fired_again_after_the_action_is_still_firing():
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool, asked = _evidence_pool(acted, signal=E.VERIFY_BY_REFIRE, answer=True)
    out, paged = await _scan(pool)
    assert out["still_firing"] == 1
    assert paged == [
        "[FIREFIGHTER] auto-remediation did not resolve PyroscopeDown: attempted "
        "restart_container, still firing after 600s (it fired again after the action)"
    ]
    assert [v["evidence"] for v in _verify_rows(pool)] == ["refired"]
    sql, args = asked[0]
    assert "lower(status) = 'firing'" in sql and "received_at > $4" in sql
    # after the row acted on, by the producer's fingerprint + the key's severity,
    # received after the action
    assert args == (41, "a9b4c69fd247b1e8", "warning", acted)


@pytest.mark.asyncio
async def test_a_probe_that_went_quiet_after_the_action_is_resolved():
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool, _ = _evidence_pool(acted, signal=E.VERIFY_BY_REFIRE, answer=False)
    out, paged = await _scan(pool)
    assert out["resolved"] == 1 and paged == []
    assert [v["evidence"] for v in _verify_rows(pool)] == ["no_refire"]


@pytest.mark.asyncio
async def test_a_resolved_notification_is_the_evidence_of_a_fix():
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool, asked = _evidence_pool(acted, signal=E.VERIFY_BY_RESOLVED_NOTIFICATION, answer="resolved")
    out, paged = await _scan(pool)
    assert out["resolved"] == 1 and paged == []
    assert [v["evidence"] for v in _verify_rows(pool)] == ["resolved_notification"]
    sql, args = asked[0]
    assert "ORDER BY id DESC" in sql
    # the notifier's latest word since the row acted on; no time bound
    assert args == (41, "a9b4c69fd247b1e8", "warning")


@pytest.mark.asyncio
async def test_no_resolved_notification_by_the_verify_means_still_firing():
    """glad-labs-stack#4023. Alertmanager re-sends a live alert only every
    repeat_interval (4 h), so silence after a restart proves nothing. The old
    oracle read that silence as resolved and a failed restart never paged."""
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool, _ = _evidence_pool(acted, signal=E.VERIFY_BY_RESOLVED_NOTIFICATION, answer=None)
    out, paged = await _scan(pool)
    assert out["still_firing"] == 1
    assert paged == [
        "[FIREFIGHTER] auto-remediation did not resolve PyroscopeDown: attempted "
        "restart_container, still firing after 600s (no resolved notification since the action)"
    ]
    assert [v["evidence"] for v in _verify_rows(pool)] == ["no_resolved_notification"]


@pytest.mark.asyncio
async def test_a_notifier_whose_latest_word_is_firing_is_still_firing():
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool, _ = _evidence_pool(acted, signal=E.VERIFY_BY_RESOLVED_NOTIFICATION, answer="firing")
    out, paged = await _scan(pool)
    assert out["still_firing"] == 1 and len(paged) == 1
    assert [v["evidence"] for v in _verify_rows(pool)] == ["refired"]


@pytest.mark.asyncio
async def test_an_evidence_read_failure_pages():
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool, _ = _evidence_pool(acted, signal=E.VERIFY_BY_RESOLVED_NOTIFICATION, answer=None)

    def _boom(sql, args):
        raise RuntimeError("alert_events unavailable")

    pool.set_fetchval(_boom)
    out, paged = await _scan(pool)
    assert out["still_firing"] == 1 and len(paged) == 1
    assert [v["evidence"] for v in _verify_rows(pool)] == ["check_failed"]


@pytest.mark.asyncio
async def test_an_alertmanager_action_is_not_verified_before_its_grace():
    """600 s, not the general 120: a resolved notification only arrives at the
    notifier's next group_interval tick."""
    acted = datetime.now(UTC) - timedelta(seconds=300)
    pool, asked = _evidence_pool(acted, signal=E.VERIFY_BY_RESOLVED_NOTIFICATION, answer=None)
    out, paged = await _scan(pool)
    assert out == {"verified": 0, "resolved": 0, "still_firing": 0}
    assert asked == [] and paged == []


# ---------------------------------------------------------------------------
# Where the page for a failed fix goes. The firefighter held the alert's own
# page, so this one must reach the same channels: through the dispatcher's
# severity router (route_fn), with the route the action recorded. Before
# 2026-09-25 it went to the plain notifier, which in the brain reaches
# Telegram whatever the severity.
# ---------------------------------------------------------------------------

_FAILED_FIX_PAGE = (
    "[FIREFIGHTER] auto-remediation did not resolve PyroscopeDown: attempted "
    "restart_container, still firing after 600s (it fired again after the action)"
)


def _routed_row(acted_at, **route):
    """A pending action on PyroscopeDown that recorded its page route."""
    row = _targeted_row("r10", acted_at, signal=E.VERIFY_BY_REFIRE)
    row["details"] = json.dumps({**json.loads(row["details"]), **route})
    return row


def _still_firing_pool(row):
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [row])
    pool.set_fetchval(lambda sql, args: True)                  # it fired again
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": datetime.now(UTC)})  # the legacy oracle agrees
    return pool


async def _scan_routed(pool, *, with_router=True):
    routed, notified = [], []

    async def route(message, *, severity, alertname, category, force_channel):
        routed.append((message, {"severity": severity, "alertname": alertname,
                                 "category": category, "force_channel": force_channel}))

    async def notify(message, *, critical):
        notified.append((message, critical))

    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify,
                                  route_fn=route if with_router else None)
    return out, routed, notified


@pytest.mark.asyncio
async def test_a_failed_fix_is_paged_through_the_router_with_the_route_the_action_recorded():
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool = _still_firing_pool(_routed_row(acted, alert_category="infrastructure", alert_force_channel=""))
    out, routed, notified = await _scan_routed(pool)
    assert out["still_firing"] == 1
    assert routed == [(_FAILED_FIX_PAGE, {"severity": "warning", "alertname": "PyroscopeDown",
                                          "category": "infrastructure", "force_channel": ""})]
    assert notified == []


@pytest.mark.asyncio
async def test_a_findings_delivery_policy_travels_with_the_route():
    """A finding whose kind is delivered on Telegram paged a warning there; its
    failed fix goes there too, not to Discord on the severity alone."""
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool = _still_firing_pool(_routed_row(acted, alert_category="", alert_force_channel="telegram"))
    _, routed, _ = await _scan_routed(pool)
    assert routed[0][1]["force_channel"] == "telegram"


@pytest.mark.asyncio
async def test_an_action_from_before_routes_were_recorded_routes_on_its_severity():
    """Recorded between #4030 and this change: severity and alertname, no
    category or directive. It routes on what it has."""
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool = _still_firing_pool(_targeted_row("r11", acted, signal=E.VERIFY_BY_REFIRE))
    _, routed, notified = await _scan_routed(pool)
    assert [r[1] for r in routed] == [{"severity": "warning", "alertname": "PyroscopeDown",
                                       "category": "", "force_channel": ""}]
    assert notified == []


@pytest.mark.asyncio
async def test_an_action_with_no_recorded_severity_pages_loud():
    """Nothing says where the page belongs, and it is the operator's only word
    of an alert whose page was held. critical=True: both channels, as every
    verify page went before routing, rather than a guess that could keep a
    critical alert off Telegram."""
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = _still_firing_pool(_pending_row("r12", "fp12", acted))
    out, routed, notified = await _scan_routed(pool)
    assert out["still_firing"] == 1
    assert routed == []
    assert [critical for _, critical in notified] == [True]
    assert "did not resolve WorkerDown" in notified[0][0]


@pytest.mark.asyncio
async def test_an_alert_that_had_no_severity_is_routed_like_its_own_page():
    """An empty severity was recorded, so it is a route: the dispatcher sent
    the alert's page to Discord alone, and the failed fix follows it."""
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool = _still_firing_pool(_routed_row(acted, alert_severity="", alert_category="", alert_force_channel=""))
    _, routed, notified = await _scan_routed(pool)
    assert [r[1]["severity"] for r in routed] == [""]
    assert notified == []


@pytest.mark.asyncio
async def test_without_a_router_a_failed_fix_pages_loud():
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool = _still_firing_pool(_routed_row(acted, alert_category="", alert_force_channel=""))
    _, routed, notified = await _scan_routed(pool, with_router=False)
    assert routed == []
    assert notified == [(_FAILED_FIX_PAGE, True)]


@pytest.mark.asyncio
async def test_a_page_the_router_cannot_deliver_is_logged_and_the_verdict_stands(caplog):
    """Discord refused a warning's page, and the router raised (the dispatcher's
    NotifyFailed is a RuntimeError). The verify still records still_firing, so
    the breaker counts the attempt."""
    acted = datetime.now(UTC) - timedelta(seconds=700)
    pool = _still_firing_pool(_routed_row(acted, alert_category="", alert_force_channel=""))

    async def route(message, **route):
        raise RuntimeError("discord-only routing failed")

    with caplog.at_level(logging.WARNING):
        out = await E.run_verify_scan(pool, config=CFG, logger=LOG, route_fn=route)
    assert out["still_firing"] == 1
    assert [v["result"] for v in _verify_rows(pool)] == ["still_firing"]
    assert any("verify page failed" in r.getMessage() for r in caplog.records)
