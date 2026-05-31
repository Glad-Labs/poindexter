"""Unit tests for ``brain/findings_dispatcher.py`` (Glad-Labs/poindexter#461).

Pure policy logic (`_resolve_policy` / `_meets_min_severity` /
`_effective_channel`) is tested directly. `poll_and_dispatch` uses a fake
pool that dispatches on SQL content and records every state-mark, so the
tests assert the actual routing contract (channel chosen, cooldown
suppression, log_only marking, never-re-poll) rather than a mock echo.
"""

from __future__ import annotations

import json

import pytest

from brain.findings_dispatcher import (
    _effective_channel,
    _meets_min_severity,
    _resolve_policy,
    poll_and_dispatch,
)


# --------------------------------------------------------------------------
# Pure policy logic
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_resolve_policy_merges_kind_over_default_over_code():
    raw = {
        "findings.default.delivery": "log_only",
        "findings.default.cooldown_minutes": "1440",
        "findings.anomaly.delivery": "telegram",
        "findings.anomaly.min_severity": "critical",
    }
    pol = _resolve_policy("anomaly", raw)
    assert pol["delivery"] == "telegram"          # from kind
    assert pol["min_severity"] == "critical"       # from kind
    assert pol["cooldown_minutes"] == 1440         # from default, int-coerced
    assert pol["fallback"] == "log_only"           # from in-code default


@pytest.mark.unit
def test_resolve_policy_unknown_kind_falls_to_default():
    raw = {"findings.default.delivery": "discord"}
    pol = _resolve_policy("brand_new_kind_never_seen", raw)
    assert pol["delivery"] == "discord"


@pytest.mark.unit
def test_resolve_policy_bad_cooldown_falls_back():
    pol = _resolve_policy("x", {"findings.x.cooldown_minutes": "not-a-number"})
    assert pol["cooldown_minutes"] == 1440


@pytest.mark.unit
@pytest.mark.parametrize(
    "sev,floor,expected",
    [
        ("critical", "critical", True),
        ("warn", "critical", False),
        ("warning", "warn", True),
        ("info", "warn", False),
        ("error", "critical", True),  # error ranks with critical
        ("warn", "info", True),
    ],
)
def test_meets_min_severity(sev, floor, expected):
    assert _meets_min_severity(sev, floor) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "delivery,fallback,expected",
    [
        ("discord", "log_only", "discord"),
        ("telegram", "discord", "telegram"),
        ("auto_fix", "discord", "discord"),       # Phase-2 channel → fallback
        ("github_issue", "log_only", "log_only"),
        ("auto_fix", "github_issue", "log_only"),  # both deferred → log_only
        ("log_only", "log_only", "log_only"),
    ],
)
def test_effective_channel_phase1(delivery, fallback, expected):
    assert _effective_channel({"delivery": delivery, "fallback": fallback}) == expected


# --------------------------------------------------------------------------
# poll_and_dispatch — fake pool
# --------------------------------------------------------------------------

class _FakePool:
    """Dispatches on SQL: the findings poll, the policy load, the cooldown
    lookup, and the state-mark insert. Records marks so tests assert routing."""

    def __init__(self, findings: list[dict], policies: dict[str, str], *, recent_keys=None):
        self._findings = findings
        self._policy_rows = [{"key": k, "value": v} for k, v in policies.items()]
        self._recent = recent_keys or set()  # (kind, dedup_key) already delivered
        self.marks: list[dict] = []

    async def fetch(self, query: str, *args):
        if "FROM audit_log" in query and "event_type = 'finding'" in query:
            return self._findings
        if "app_settings" in query:
            return self._policy_rows
        raise AssertionError(f"unexpected fetch: {query[:60]}")

    async def fetchrow(self, query: str, *args):
        if "findings_dispatch_state" in query and "dispatch_result = 'sent'" in query:
            kind, dedup_key = args[0], args[1]
            return {"x": 1} if (kind, dedup_key) in self._recent else None
        raise AssertionError(f"unexpected fetchrow: {query[:60]}")

    async def execute(self, query: str, *args):
        if query.strip().startswith("INSERT INTO findings_dispatch_state"):
            self.marks.append({
                "finding_id": args[0], "kind": args[1], "dedup_key": args[2],
                "channel": args[3], "result": args[4],
            })
        return "INSERT 0 1"


def _finding(fid, kind, *, severity="warn", dedup_key=None, title="t", body="b"):
    details = {"kind": kind, "title": title, "body": body}
    if dedup_key:
        details["dedup_key"] = dedup_key
    return {"id": fid, "severity": severity, "source": "job", "details": json.dumps(details)}


_POLICIES = {
    "findings.default.delivery": "log_only",
    "findings.default.min_severity": "warn",
    "findings.default.cooldown_minutes": "1440",
    "findings.broken_link.delivery": "discord",
    "findings.broken_link.min_severity": "warn",
    "findings.broken_link.cooldown_minutes": "360",
    "findings.anomaly.delivery": "telegram",
    "findings.anomaly.min_severity": "critical",
    "findings.anomaly.cooldown_minutes": "60",
}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_log_only_finding_is_marked_not_notified():
    calls = []
    async def notify(msg, *, critical=False): calls.append((msg, critical))
    pool = _FakePool([_finding(1, "media_drift")], _POLICIES)
    out = await poll_and_dispatch(pool, notify_fn=notify)
    assert out["log_only"] == 1 and out["sent"] == 0
    assert calls == []  # never notified
    assert pool.marks[0]["result"] == "log_only"  # but marked (won't re-poll)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_discord_finding_notified_noncritical():
    calls = []
    async def notify(msg, *, critical=False): calls.append((msg, critical))
    pool = _FakePool([_finding(2, "broken_link", severity="warn", dedup_key="u1")], _POLICIES)
    out = await poll_and_dispatch(pool, notify_fn=notify)
    assert out["sent"] == 1
    assert len(calls) == 1 and calls[0][1] is False  # discord = not critical
    assert pool.marks[0]["result"] == "sent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_telegram_only_for_critical_severity_gate():
    calls = []
    async def notify(msg, *, critical=False): calls.append((msg, critical))
    # anomaly policy = telegram/min_severity=critical. A warn anomaly must NOT page.
    pool = _FakePool([_finding(3, "anomaly", severity="warn")], _POLICIES)
    out = await poll_and_dispatch(pool, notify_fn=notify)
    assert out["log_only"] == 1 and calls == []  # gated down to log_only
    # A critical anomaly DOES page telegram.
    pool2 = _FakePool([_finding(4, "anomaly", severity="critical")], _POLICIES)
    out2 = await poll_and_dispatch(pool2, notify_fn=notify)
    assert out2["sent"] == 1 and calls[-1][1] is True  # telegram = critical


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_suppresses_repeat_dedup_key():
    calls = []
    async def notify(msg, *, critical=False): calls.append(msg)
    # broken_link with dedup_key u9 already delivered recently → suppress.
    pool = _FakePool(
        [_finding(5, "broken_link", severity="warn", dedup_key="u9")],
        _POLICIES, recent_keys={("broken_link", "u9")},
    )
    out = await poll_and_dispatch(pool, notify_fn=notify)
    assert out["suppressed"] == 1 and calls == []
    assert pool.marks[0]["result"] == "suppressed_cooldown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_notify_channel_marks_error_not_silent_loss(monkeypatch):
    # notify_fn=None means "resolve one"; simulate resolution finding no
    # reachable channel (no worker, no brain.notify) so the error path runs.
    async def _no_channel(pool):
        return None
    monkeypatch.setattr(
        "brain.findings_dispatcher._resolve_notify_fn", _no_channel
    )
    pool = _FakePool([_finding(6, "broken_link", severity="warn")], _POLICIES)
    out = await poll_and_dispatch(pool, notify_fn=None)
    assert out["errors"] == 1
    assert pool.marks[0]["result"].startswith("error:")  # marked, won't re-poll
