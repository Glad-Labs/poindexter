"""Unit tests for FindingsAlertRouterJob — pin the audit_log → alert_events
bridge contract that closes the long-standing silent-route gap.

Captured 2026-05-15: 108 critical findings written to ``audit_log`` in
7 days, zero ever reached the operator. These tests make sure the bridge
forwards severity>=warn findings, uses a stable fingerprint the existing
``alert_dispatcher`` dedup engine can consume, and advances the watermark
only past successfully-routed rows.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import services.jobs.findings_alert_router as router_mod
from plugins.job import JobResult
from services.jobs.findings_alert_router import (
    _AUTOFIX_JOBS,
    _SEV_RANK,
    FindingsAlertRouterJob,
    _build_alertname,
    _build_fingerprint,
    _delivery_for,
    _normalize_severity,
)
from services.jobs.fix_broken_external_links import FixBrokenExternalLinksJob as _FBEL
from services.jobs.fix_broken_internal_links import FixBrokenInternalLinksJob as _FBIL
from services.jobs.fix_uncategorized_posts import FixUncategorizedPostsJob as _FUP

# No module-level ``pytestmark = pytest.mark.asyncio``: the project runs
# ``asyncio_mode = "auto"`` (pyproject.toml), so coroutine tests are
# auto-marked. An explicit mark here wrongly tagged the sync tests in this
# module, emitting a PytestWarning (Glad-Labs/poindexter#997).


class _FakePoolCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


def _pool_with(*, fetchrow=None, fetch=None, execute=None, policy_rows=None):
    """Build a MagicMock asyncpg-style pool. ``fetchrow`` is for watermark
    read; ``fetch`` returns findings for the audit_log select and the
    per-kind policy rows for the app_settings select. ``policy_rows`` are
    {"key": "findings.<kind>.<field>", "value": "..."} dicts."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    findings_rows = fetch or []
    pol_rows = policy_rows or []

    async def _fetch(sql, *args):
        if "app_settings" in sql:  # the _load_policies query
            return pol_rows
        return findings_rows  # the audit_log unrouted-findings select

    conn.fetch = AsyncMock(side_effect=_fetch)
    conn.execute = AsyncMock(return_value=execute)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolCtx(conn))
    return pool, conn


# ---- Pure helpers ----------------------------------------------------------


def test_normalize_severity_maps_warn_to_warning():
    """emit_finding accepts 'warn'; Prometheus / alert_dispatcher expect
    'warning'. The bridge normalizes so the dispatcher's severity matrix
    routes correctly."""
    assert _normalize_severity("warn") == "warning"
    assert _normalize_severity("WARN") == "warning"


def test_normalize_severity_passes_critical_through():
    assert _normalize_severity("critical") == "critical"


def test_normalize_severity_passes_unknown_through():
    """Unknown severities pass through so the dispatcher can log the
    mismatch instead of silently dropping the finding."""
    assert _normalize_severity("urgent") == "urgent"
    assert _normalize_severity("") == ""


def test_build_fingerprint_prefers_dedup_key():
    """Caller-provided ``dedup_key`` is the stable identity; the bridge
    must use it so repeated fires of the same logical alert collapse
    into one dispatcher row."""
    fp = _build_fingerprint("audit_published_quality", {"dedup_key": "post:abc123"})
    assert fp == "finding:audit_published_quality:post:abc123"


def test_build_fingerprint_falls_back_to_source_kind():
    """When no dedup_key, source+kind is the coarsest-but-stable shape."""
    fp = _build_fingerprint("media_reconciliation", {"kind": "media_drift"})
    assert fp == "finding:media_reconciliation:media_drift"


def test_build_alertname_uses_source_and_kind():
    """alertname is operator-facing in Discord/Telegram embeds — keep
    1-to-1 with the audit_log row's source:kind shape."""
    assert _build_alertname("flag_missing_seo", {"kind": "missing_seo"}) == (
        "flag_missing_seo:missing_seo"
    )


# ---- Job behavior ----------------------------------------------------------


async def test_run_returns_ok_with_no_findings_above_watermark():
    """Fresh poll with no new rows — quiet success, watermark unchanged."""
    pool, conn = _pool_with(fetchrow={"value": "100"}, fetch=[])
    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert result.changes_made == 0
    assert "watermark 100" in result.detail
    # Should NOT issue an UPDATE for watermark since nothing to advance.
    # (fetch + fetchrow happened, but no execute calls.)
    assert conn.execute.await_count == 0


async def test_run_forwards_critical_finding_to_alert_events():
    """The bug case — a critical finding in audit_log must result in
    exactly one alert_events insert AND a watermark bump."""
    rows = [{
        "id": 250,
        "source": "media_reconciliation",
        "severity": "critical",
        "details": json.dumps({
            "kind": "media_drift",
            "title": "11 videos missing",
            "body": "details here",
            "dedup_key": "media_drift:videos",
        }),
    }]
    pool, conn = _pool_with(fetchrow={"value": "100"}, fetch=rows)

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert result.changes_made == 1
    # Two execute calls: insert into alert_events, then watermark UPSERT.
    assert conn.execute.await_count == 2

    # First execute is the alert_events INSERT — verify shape.
    insert_call = conn.execute.await_args_list[0]
    insert_sql = insert_call.args[0]
    assert "INSERT INTO alert_events" in insert_sql
    assert insert_call.args[1] == "media_reconciliation:media_drift"  # alertname
    assert insert_call.args[2] == "critical"                          # severity
    # fingerprint must come from dedup_key so dispatcher dedup works
    assert insert_call.args[5] == "finding:media_reconciliation:media_drift:videos"

    # Second execute is the watermark UPSERT.
    upsert_call = conn.execute.await_args_list[1]
    assert "INSERT INTO app_settings" in upsert_call.args[0]
    assert upsert_call.args[1] == "findings_alert_route_watermark"
    assert upsert_call.args[2] == "250"


async def test_run_skips_info_severity():
    """``severity='info'`` findings stay in audit_log only — the bridge's
    SQL filters them out via ``severity = ANY(...)`` so they never
    appear in the fetch result. This test pins that contract from the
    job side: even if the fetch somehow returned an info row, the SQL
    is what blocks it (verified via the literal ``_ROUTABLE_SEVERITIES``
    tuple)."""
    from services.jobs.findings_alert_router import _ROUTABLE_SEVERITIES
    assert "info" not in _ROUTABLE_SEVERITIES
    assert set(_ROUTABLE_SEVERITIES) == {"warn", "warning", "critical"}


async def test_run_advances_watermark_past_successfully_routed_rows():
    """Watermark advances to the MAX id in the batch — guarantees no
    row is processed twice on the next cycle."""
    rows = [
        {"id": 101, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
        {"id": 102, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
        {"id": 103, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
    ]
    pool, conn = _pool_with(fetchrow={"value": "100"}, fetch=rows)

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.changes_made == 3
    # Last execute call is the watermark UPSERT; its 2nd arg is the new value.
    upsert_call = conn.execute.await_args_list[-1]
    assert upsert_call.args[2] == "103"


async def test_run_keeps_watermark_if_all_rows_fail():
    """If every insert errors, watermark must NOT advance — next cycle
    retries the same rows. Anti-foot-gun against losing findings on a
    transient DB hiccup."""
    rows = [{"id": 200, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})}]
    pool, conn = _pool_with(fetchrow={"value": "150"}, fetch=rows)
    # First execute (the alert_events insert) raises; the loop catches it,
    # and there should be NO second execute (the watermark UPSERT) because
    # max_id didn't advance past watermark.
    conn.execute = AsyncMock(side_effect=RuntimeError("DB down"))

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is False
    assert result.changes_made == 0
    # Exactly 1 attempted insert; no watermark update.
    assert conn.execute.await_count == 1


async def test_run_does_not_advance_watermark_past_first_failed_row(monkeypatch):
    """#613 — if a MIDDLE row's delivery fails but later rows succeed, the
    watermark must NOT leap past the failed row (which could be critical).
    It caps at first_failed_id - 1 so the failure is retried next cycle,
    rather than being silently skipped forever."""
    rows = [
        {"id": 201, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
        {"id": 202, "source": "src", "severity": "critical", "details": json.dumps({"kind": "k"})},
        {"id": 203, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
    ]
    pool, conn = _pool_with(fetchrow={"value": "150"}, fetch=rows)

    async def _fake_insert(_pool, r, **_kwargs):
        if r["id"] == 202:
            raise RuntimeError("delivery failed for 202")

    monkeypatch.setattr(
        router_mod, "_insert_alert_event", AsyncMock(side_effect=_fake_insert),
    )

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is False
    # 201 and 203 routed; 202 failed.
    assert result.changes_made == 2
    # The watermark UPSERT must cap at 201 (202 - 1), NOT 203 — otherwise
    # the critical finding 202 is skipped forever.
    upsert_call = conn.execute.await_args_list[-1]
    assert upsert_call.args[1] == "findings_alert_route_watermark"
    assert upsert_call.args[2] == "201"


async def test_run_normalizes_warn_to_warning_in_alert_events():
    """End-to-end check that emit_finding's 'warn' becomes 'warning' in
    the alert_events row, so the dispatcher's severity matrix routes
    correctly."""
    rows = [{
        "id": 50,
        "source": "flag_missing_seo",
        "severity": "warn",
        "details": json.dumps({"kind": "missing_seo", "title": "10 posts"}),
    }]
    pool, conn = _pool_with(fetchrow={"value": "0"}, fetch=rows)

    await FindingsAlertRouterJob().run(pool, {})

    insert_call = conn.execute.await_args_list[0]
    severity_arg = insert_call.args[2]
    assert severity_arg == "warning"  # NOT "warn"


async def test_run_handles_missing_dedup_key_with_source_kind_fallback():
    """When emit_finding callers don't set dedup_key, the bridge must
    still produce a STABLE fingerprint so dispatcher dedup works for
    those alert classes. ``source:kind`` is the agreed fallback."""
    rows = [{
        "id": 99,
        "source": "audit_published_quality",
        "severity": "critical",
        "details": json.dumps({"kind": "quality_regression", "title": "5 issues"}),
        # NO dedup_key field
    }]
    pool, conn = _pool_with(fetchrow={"value": "0"}, fetch=rows)

    await FindingsAlertRouterJob().run(pool, {})

    insert_call = conn.execute.await_args_list[0]
    fingerprint_arg = insert_call.args[5]
    assert fingerprint_arg == "finding:audit_published_quality:quality_regression"


async def test_watermark_missing_resets_to_zero():
    """Fresh install / corrupted row — bridge should replay everything
    from id=0 (which is what we want; alert_dispatcher dedup will collapse
    historical duplicates)."""
    pool, conn = _pool_with(fetchrow=None, fetch=[])
    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert "watermark 0" in result.detail


async def test_watermark_unparseable_resets_to_zero():
    """Defensive: a manual psql write with garbage in the value column
    shouldn't crash the bridge."""
    pool, conn = _pool_with(fetchrow={"value": "not-a-number"}, fetch=[])
    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert "watermark 0" in result.detail


# ---- #300: per-kind log_only suppression -----------------------------------


async def test_run_suppresses_log_only_warn_finding_but_advances_watermark():
    """A warn finding of a log_only kind is recorded (audit_log) but NOT
    bridged to alert_events — and the watermark still advances so it isn't
    re-evaluated every cycle."""
    rows = [{
        "id": 300,
        "source": "media_reconciliation",
        "severity": "warn",
        "details": json.dumps({"kind": "media_drift", "title": "drift"}),
    }]
    pool, conn = _pool_with(
        fetchrow={"value": "100"},
        fetch=rows,
        policy_rows=[{"key": "findings.media_drift.delivery", "value": "log_only"}],
    )

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert result.changes_made == 0           # nothing routed
    assert "suppressed 1" in result.detail
    # Only the watermark UPSERT executed — NO alert_events insert.
    assert conn.execute.await_count == 1
    upsert_call = conn.execute.await_args_list[0]
    assert "INSERT INTO app_settings" in upsert_call.args[0]
    assert upsert_call.args[2] == "300"       # watermark advanced past it


async def test_run_still_routes_log_only_kind_when_critical():
    """Even for a log_only kind, a CRITICAL finding must page (fail loud)."""
    rows = [{
        "id": 301,
        "source": "media_reconciliation",
        "severity": "critical",
        "details": json.dumps({"kind": "media_drift", "title": "drift"}),
    }]
    pool, conn = _pool_with(
        fetchrow={"value": "100"},
        fetch=rows,
        policy_rows=[{"key": "findings.media_drift.delivery", "value": "log_only"}],
    )

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.changes_made == 1           # routed despite log_only
    # alert_events insert + watermark upsert.
    assert conn.execute.await_count == 2


async def test_run_routes_unconfigured_kind_normally():
    """A warn finding whose kind has no log_only policy routes as before —
    protects newly-added critical kinds (job_failure, missing_table) from
    being silently swallowed by findings.default."""
    rows = [{
        "id": 302,
        "source": "scheduler.some_job",
        "severity": "warn",
        "details": json.dumps({"kind": "job_failure", "title": "boom"}),
    }]
    pool, conn = _pool_with(
        fetchrow={"value": "100"},
        fetch=rows,
        # unrelated log_only policy for a different kind
        policy_rows=[{"key": "findings.media_drift.delivery", "value": "log_only"}],
    )

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.changes_made == 1           # routed
    assert conn.execute.await_count == 2


# ---- #461: per-kind policy loader + min_severity gating --------------------

# policies dict shape mirrors what _load_policies returns:
# {kind: {field: value}}. Absence of a kind => route (stay loud).
_POL = {
    "media_drift": {"delivery": "log_only"},
    "anomaly": {"delivery": "telegram", "min_severity": "critical"},
    "broken_external_link": {"delivery": "auto_fix", "fallback": "discord", "min_severity": "warn"},
    "quality_regression": {"delivery": "github_issue", "min_severity": "warn"},
}


def test_unconfigured_kind_routes_even_when_default_is_log_only():
    assert _delivery_for("brand_new_kind", "warning", _POL) == "route"


def test_log_only_kind_is_suppressed():
    assert _delivery_for("media_drift", "warning", _POL) == "log_only"


def test_critical_log_only_is_refused_and_routes():
    assert _delivery_for("media_drift", "critical", _POL) == "route"


def test_min_severity_gates_below_threshold():
    assert _delivery_for("anomaly", "warning", _POL) == "log_only"


def test_min_severity_passes_at_threshold():
    assert _delivery_for("anomaly", "critical", _POL) == "telegram"


def test_auto_fix_delivery_passthrough():
    assert _delivery_for("broken_external_link", "warning", _POL) == "auto_fix"


def test_github_issue_delivery_passthrough():
    assert _delivery_for("quality_regression", "warning", _POL) == "github_issue"


def test_sev_rank_orders_severities():
    assert _SEV_RANK["info"] < _SEV_RANK["warn"] == _SEV_RANK["warning"] < _SEV_RANK["critical"]


def test_min_severity_normalization_applies_to_both_sides():
    # 'warn' (incoming) normalizes to 'warning'; policy min_severity 'warn'
    # also normalizes to 'warning' -> equal ranks -> passes the gate.
    assert _delivery_for("broken_external_link", "warn", _POL) == "auto_fix"


# ---- #461: auto_fix delivery triggers the matching fix job ------------------


def test_autofix_map_covers_seeded_auto_fix_kinds():
    assert _AUTOFIX_JOBS["broken_external_link"] is _FBEL
    assert _AUTOFIX_JOBS["broken_internal_link"] is _FBIL
    assert _AUTOFIX_JOBS["uncategorized_post"] is _FUP


async def test_run_auto_fix_triggers_mapped_job(monkeypatch):
    ran = {"called": False}

    class _FakeFixJob:
        async def run(self, pool, config):
            ran["called"] = True
            return JobResult(ok=True, detail="fixed 2", changes_made=2)

    monkeypatch.setattr(
        "services.jobs.findings_alert_router._AUTOFIX_JOBS",
        {"broken_external_link": _FakeFixJob},
    )
    pool, conn = _pool_with(
        fetchrow={"value": "0"},
        fetch=[{"id": 5, "source": "linkcheck", "severity": "warn",
                "details": json.dumps({"kind": "broken_external_link"})}],
        policy_rows=[
            {"key": "findings.broken_external_link.delivery", "value": "auto_fix"},
            {"key": "findings.broken_external_link.min_severity", "value": "warn"},
        ],
    )
    result = await FindingsAlertRouterJob().run(pool, {"_site_config": object()})
    assert ran["called"] is True
    assert result.ok is True
    assert "auto-fixed 1" in result.detail
    assert result.changes_made == 1


async def test_run_auto_fix_fallback_routes_when_job_returns_not_ok(monkeypatch):
    class _FailFixJob:
        async def run(self, pool, config):
            return JobResult(ok=False, detail="nothing to fix", changes_made=0)

    monkeypatch.setattr(
        "services.jobs.findings_alert_router._AUTOFIX_JOBS",
        {"broken_external_link": _FailFixJob},
    )
    pool, conn = _pool_with(
        fetchrow={"value": "0"},
        fetch=[{"id": 7, "source": "lc", "severity": "warn",
                "details": json.dumps({"kind": "broken_external_link"})}],
        policy_rows=[
            {"key": "findings.broken_external_link.delivery", "value": "auto_fix"},
            {"key": "findings.broken_external_link.fallback", "value": "route"},
        ],
    )
    result = await FindingsAlertRouterJob().run(pool, {})
    # fallback 'route' -> _insert_alert_event + _write_watermark = 2 executes
    assert conn.execute.await_count == 2
    assert "routed 1" in result.detail


async def test_run_auto_fix_log_only_fallback_suppresses_when_job_not_ok(monkeypatch):
    class _FailFixJob:
        async def run(self, pool, config):
            return JobResult(ok=False, detail="nothing", changes_made=0)

    monkeypatch.setattr(
        "services.jobs.findings_alert_router._AUTOFIX_JOBS",
        {"broken_external_link": _FailFixJob},
    )
    pool, conn = _pool_with(
        fetchrow={"value": "0"},
        fetch=[{"id": 8, "source": "lc", "severity": "warn",
                "details": json.dumps({"kind": "broken_external_link"})}],
        policy_rows=[
            {"key": "findings.broken_external_link.delivery", "value": "auto_fix"},
            {"key": "findings.broken_external_link.fallback", "value": "log_only"},
        ],
    )
    result = await FindingsAlertRouterJob().run(pool, {})
    # fallback 'log_only' -> no alert_events insert; only the watermark upsert
    assert conn.execute.await_count == 1
    assert "suppressed 1" in result.detail


# ---- #461: github_issue delivery via gh CLI ---------------------------------


class _FakeProc:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._out = stdout
        self._err = stderr

    async def communicate(self):
        return self._out, self._err

    def kill(self):
        pass


async def test_github_issue_skips_when_duplicate_open_issue_exists(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if "list" in args:  # dedup `gh issue list` -> one match
            return _FakeProc(0, stdout=b'[{"title":"quality regression: foo"}]')
        return _FakeProc(0)

    monkeypatch.setattr(router_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    finding = {"id": 1, "source": "audit_published_quality",
               "details": {"kind": "quality_regression",
                           "title": "quality regression: foo", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(finding, "quality_regression")
    assert ok is True
    assert not any("create" in c for c in calls)  # never created a dup


async def test_github_issue_creates_when_no_duplicate(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if "list" in args:
            return _FakeProc(0, stdout=b"[]")  # no existing match
        return _FakeProc(0)  # create succeeds

    monkeypatch.setattr(router_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    finding = {"id": 2, "source": "audit_published_quality",
               "details": {"kind": "quality_regression",
                           "title": "qr: bar", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(finding, "quality_regression")
    assert ok is True
    assert any("create" in c for c in calls)  # created the issue


async def test_github_issue_returns_false_when_gh_missing(monkeypatch):
    monkeypatch.setattr(router_mod.shutil, "which", lambda _: None)
    finding = {"id": 3, "source": "x",
               "details": {"kind": "quality_regression", "title": "t", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(finding, "quality_regression")
    assert ok is False


async def test_github_issue_returns_false_on_gh_timeout(monkeypatch):
    async def fake_exec(*a, **k):
        return _FakeProc(0)

    async def boom(*a, **k):
        raise asyncio.TimeoutError

    monkeypatch.setattr(router_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(router_mod.asyncio, "wait_for", boom)
    finding = {"id": 4, "source": "x",
               "details": {"kind": "quality_regression", "title": "t", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(finding, "quality_regression")
    assert ok is False


async def test_github_issue_passes_kind_labels_to_create(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if "list" in args:
            return _FakeProc(0, stdout=b"[]")  # no dup -> proceed to create
        return _FakeProc(0)

    monkeypatch.setattr(router_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    finding = {"id": 9, "source": "audit_published_quality",
               "details": {"kind": "quality_regression", "title": "qr: z", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(
        finding, "quality_regression", labels=["finding", "bug", "pipeline"]
    )
    assert ok is True
    create = next(c for c in calls if "create" in c)
    # every label is passed as its own --label arg
    for lbl in ("finding", "bug", "pipeline"):
        assert lbl in create
    assert create.count("--label") == 3


async def test_github_issue_defaults_to_finding_label_only(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if "list" in args:
            return _FakeProc(0, stdout=b"[]")
        return _FakeProc(0)

    monkeypatch.setattr(router_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    finding = {"id": 10, "source": "x",
               "details": {"kind": "topic_gap", "title": "t", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(finding, "topic_gap", labels=None)
    assert ok is True
    create = next(c for c in calls if "create" in c)
    assert "finding" in create and create.count("--label") == 1


async def test_run_github_issue_counts_filed(monkeypatch):
    monkeypatch.setattr(
        "services.jobs.findings_alert_router._dispatch_github_issue",
        AsyncMock(return_value=True),
    )
    pool, conn = _pool_with(
        fetchrow={"value": "0"},
        fetch=[{"id": 9, "source": "audit_published_quality", "severity": "warn",
                "details": json.dumps({"kind": "quality_regression",
                                       "title": "qr", "body": "b"})}],
        policy_rows=[
            {"key": "findings.quality_regression.delivery", "value": "github_issue"},
            {"key": "findings.quality_regression.min_severity", "value": "warn"},
        ],
    )
    result = await FindingsAlertRouterJob().run(pool, {})
    assert "filed 1" in result.detail
    assert result.changes_made == 1


# ---- #461: per-kind delivery=telegram/discord channel forcing ---------------
# The router carries the per-kind delivery channel into alert_events.labels as
# `force_channel`; brain/alert_dispatcher honors it. Without this, a warn-level
# finding with delivery=telegram would only ever reach Discord (severity matrix).


def _labels_of(insert_call) -> dict:
    """Decode the labels JSONB arg ($3 -> args[3]) of an alert_events insert."""
    return json.loads(insert_call.args[3])


async def test_insert_alert_event_sets_force_channel_when_given():
    pool, conn = _pool_with(execute=None)
    finding = {"id": 1, "source": "anomaly_detector", "severity": "warn",
               "details": json.dumps({"kind": "anomaly", "title": "spike"})}
    await router_mod._insert_alert_event(pool, finding, force_channel="telegram")
    labels = _labels_of(conn.execute.await_args_list[0])
    assert labels["force_channel"] == "telegram"


async def test_insert_alert_event_omits_force_channel_by_default():
    pool, conn = _pool_with(execute=None)
    finding = {"id": 1, "source": "s", "severity": "warn",
               "details": json.dumps({"kind": "k", "title": "t"})}
    await router_mod._insert_alert_event(pool, finding)
    labels = _labels_of(conn.execute.await_args_list[0])
    assert "force_channel" not in labels


async def test_run_telegram_delivery_carries_force_channel_label():
    """A warn finding whose policy delivery=telegram routes to alert_events
    with labels.force_channel='telegram' so the dispatcher pages Telegram."""
    rows = [{"id": 400, "source": "anomaly_detector", "severity": "warn",
             "details": json.dumps({"kind": "anomaly", "title": "spike"})}]
    pool, conn = _pool_with(
        fetchrow={"value": "0"},
        fetch=rows,
        policy_rows=[
            {"key": "findings.anomaly.delivery", "value": "telegram"},
            {"key": "findings.anomaly.min_severity", "value": "warn"},
        ],
    )
    result = await FindingsAlertRouterJob().run(pool, {})
    assert result.changes_made == 1
    labels = _labels_of(conn.execute.await_args_list[0])
    assert labels["force_channel"] == "telegram"


async def test_run_discord_delivery_carries_force_channel_label():
    rows = [{"id": 401, "source": "linkcheck", "severity": "warn",
             "details": json.dumps({"kind": "broken_link", "title": "404"})}]
    pool, conn = _pool_with(
        fetchrow={"value": "0"},
        fetch=rows,
        policy_rows=[
            {"key": "findings.broken_link.delivery", "value": "discord"},
            {"key": "findings.broken_link.min_severity", "value": "warn"},
        ],
    )
    await FindingsAlertRouterJob().run(pool, {})
    labels = _labels_of(conn.execute.await_args_list[0])
    assert labels["force_channel"] == "discord"


async def test_run_route_delivery_has_no_force_channel():
    """An unconfigured kind routes plainly — no force_channel, dispatcher
    decides the channel by severity (the unchanged default)."""
    rows = [{"id": 402, "source": "scheduler.job", "severity": "warn",
             "details": json.dumps({"kind": "job_failure", "title": "boom"})}]
    pool, conn = _pool_with(fetchrow={"value": "0"}, fetch=rows, policy_rows=[])
    await FindingsAlertRouterJob().run(pool, {})
    labels = _labels_of(conn.execute.await_args_list[0])
    assert "force_channel" not in labels


async def test_deliver_fallback_telegram_carries_force_channel(monkeypatch):
    """When auto_fix fails and fallback=telegram, the routed alert_events
    row must still force Telegram."""
    class _FailFixJob:
        async def run(self, pool, config):
            return JobResult(ok=False, detail="nope", changes_made=0)

    monkeypatch.setattr(
        "services.jobs.findings_alert_router._AUTOFIX_JOBS",
        {"broken_external_link": _FailFixJob},
    )
    rows = [{"id": 403, "source": "lc", "severity": "warn",
             "details": json.dumps({"kind": "broken_external_link"})}]
    pool, conn = _pool_with(
        fetchrow={"value": "0"},
        fetch=rows,
        policy_rows=[
            {"key": "findings.broken_external_link.delivery", "value": "auto_fix"},
            {"key": "findings.broken_external_link.fallback", "value": "telegram"},
        ],
    )
    await FindingsAlertRouterJob().run(pool, {})
    # First execute is the fallback alert_events insert.
    labels = _labels_of(conn.execute.await_args_list[0])
    assert labels["force_channel"] == "telegram"
