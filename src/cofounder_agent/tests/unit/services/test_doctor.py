"""Unit tests for ``services/doctor.py`` — the #527 doctor check-graph.

These tests EXERCISE the reasoning, not a mocked echo of it: a strict fake
``brain_knowledge`` pool feeds real persisted-probe shapes, and we assert the
normalization, root-cause suppression, score math, correlation threshold,
stale-brain meta-check, ``--json`` shape, and CLI exit-code mapping.

Per CONTRIBUTING.md "Testing conventions": fake DB rows are STRICT — their
``__getitem__`` raises ``KeyError`` on a column the row doesn't carry, so a
renamed/dropped column fails the test instead of silently returning None. The
fake pool routes each query to the right canned rows by matching the SQL, so a
query against the wrong table/column would return the wrong (or empty) rows and
break an assertion.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from services import doctor
from services.doctor import (
    CheckResult,
    DEPENDS_ON,
    ROOTS,
    apply_root_cause,
    correlate,
    run_doctor,
    score,
)


# ---------------------------------------------------------------------------
# Strict fake row + pool.
# ---------------------------------------------------------------------------


class _StrictRow:
    """A dict-backed row whose ``__getitem__`` raises on a missing column.

    Mirrors asyncpg.Record's strictness so a dropped/renamed column surfaces
    as a KeyError in the test rather than passing silently.
    """

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]  # KeyError on a column we didn't provide


class _FakePool:
    """Routes ``fetch`` queries to canned rows by matching SQL substrings.

    Only the queries ``services.doctor`` actually issues are handled; any
    other query raises so an unexpected/renamed query is caught.
    """

    def __init__(
        self,
        *,
        probe_rows: list[dict],
        newest_signal: datetime | None,
        settings: dict[str, str] | None = None,
    ):
        self._probe_rows = probe_rows
        self._newest_signal = newest_signal
        self._settings = settings or {}

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())  # collapse whitespace
        if "FROM brain_knowledge" in q and "health_status" in q and "MAX" not in q:
            return [_StrictRow(r) for r in self._probe_rows]
        if "brain.cycle_heartbeat" in q or "AS sources" in q:
            return [_StrictRow({"newest": self._newest_signal})]
        if "FROM app_settings" in q:
            return [
                _StrictRow({"key": k, "value": v}) for k, v in self._settings.items()
            ]
        raise AssertionError(f"unexpected query: {q[:120]}")

    async def close(self):  # pragma: no cover — CLI lifecycle only
        pass


def _probe_row(name: str, result: dict, *, age_seconds: float = 10.0) -> dict:
    """Build a brain_knowledge probe row exactly as the brain persists it."""
    return {
        "entity": f"probe.{name}",
        "value": json.dumps(result),
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    }


def _fresh_signal() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Normalization (severity -> status).
# ---------------------------------------------------------------------------


def test_normalize_ok_to_ok():
    assert doctor._normalize_status({"ok": True, "detail": "fine"}) == "ok"


def test_normalize_warning_to_warn():
    assert (
        doctor._normalize_status({"ok": False, "severity": "warning", "detail": "x"})
        == "warn"
    )


def test_normalize_critical_to_fail():
    assert (
        doctor._normalize_status({"ok": False, "severity": "critical", "detail": "x"})
        == "fail"
    )


def test_normalize_missing_severity_to_fail():
    # Many probes return ok:false with no severity — must be treated as fail.
    assert doctor._normalize_status({"ok": False, "detail": "x"}) == "fail"


@pytest.mark.asyncio
async def test_load_check_results_parses_value_and_age(monkeypatch):
    monkeypatch.setattr(doctor, "_load_remediation_keys", lambda: frozenset())
    pool = _FakePool(
        probe_rows=[
            _probe_row("db_ping", {"ok": True, "detail": "pong", "latency_ms": 3}),
            _probe_row(
                "stuck_tasks",
                {"ok": False, "severity": "warning", "detail": "2 stuck", "count": 2},
            ),
        ],
        newest_signal=_fresh_signal(),
    )
    checks = await doctor.load_check_results(pool)
    by_name = {c.name: c for c in checks}
    assert by_name["db_ping"].status == "ok"
    # extra metric keys land in .metrics, not lost
    assert by_name["db_ping"].metrics == {"latency_ms": 3}
    assert by_name["stuck_tasks"].status == "warn"
    assert by_name["stuck_tasks"].metrics == {"count": 2}
    assert by_name["stuck_tasks"].age_seconds >= 0


# ---------------------------------------------------------------------------
# Root-cause suppression.
# ---------------------------------------------------------------------------


def _c(name: str, status: str) -> CheckResult:
    return CheckResult(name=name, status=status, detail="", age_seconds=1.0)


def test_db_down_suppresses_dependents_under_root():
    # db_ping is a root; its DB-backed dependents should be suppressed, not
    # counted as independent failures.
    checks = [
        _c("db_ping", "fail"),
        _c("stuck_tasks", "fail"),
        _c("approval_queue", "fail"),
        _c("publish_rate", "warn"),
    ]
    apply_root_cause(checks)
    by_name = {c.name: c for c in checks}
    assert by_name["db_ping"].status == "fail"  # root stays a hard failure
    assert by_name["db_ping"].root is None  # roots never get a root
    for dep in ("stuck_tasks", "approval_queue", "publish_rate"):
        assert by_name[dep].status == "suppressed"
        assert by_name[dep].root == "db_ping"


def test_ok_dependent_is_not_suppressed_when_root_down():
    # A genuinely-healthy dependent under a failed root stays ok (don't hide it).
    checks = [_c("db_ping", "fail"), _c("stuck_tasks", "ok")]
    apply_root_cause(checks)
    by_name = {c.name: c for c in checks}
    assert by_name["stuck_tasks"].status == "ok"
    assert by_name["stuck_tasks"].root is None


def test_dependent_failing_with_healthy_root_is_not_suppressed():
    # db_ping ok → a failing dependent is its OWN problem, surfaces directly.
    checks = [_c("db_ping", "ok"), _c("stuck_tasks", "fail")]
    apply_root_cause(checks)
    by_name = {c.name: c for c in checks}
    assert by_name["stuck_tasks"].status == "fail"
    assert by_name["stuck_tasks"].root is None


def test_pipeline_throughput_suppressed_by_worker_root():
    # pipeline_throughput depends on worker_error_rate (and db_ping).
    assert DEPENDS_ON["pipeline_throughput"] == ["worker_error_rate", "db_ping"]
    checks = [_c("worker_error_rate", "fail"), _c("pipeline_throughput", "fail")]
    apply_root_cause(checks)
    by_name = {c.name: c for c in checks}
    assert by_name["pipeline_throughput"].status == "suppressed"
    assert by_name["pipeline_throughput"].root == "worker_error_rate"


# ---------------------------------------------------------------------------
# Score math.
# ---------------------------------------------------------------------------


def test_score_all_ok_is_100():
    assert score([_c("db_ping", "ok"), _c("stuck_tasks", "ok")]) == 100


def test_score_subtracts_warn_penalty():
    # default warn weight is 6 → 100 - 6 = 94
    assert score([_c("stuck_tasks", "warn")]) == 94


def test_score_root_fail_costs_more_than_nonroot_fail():
    # default fail=20, root_multiplier=1.5 → root fail costs 30, nonroot 20.
    root_fail = score([_c("db_ping", "fail")])  # db_ping is a root
    nonroot_fail = score([_c("approval_queue", "fail")])
    assert root_fail < nonroot_fail
    assert nonroot_fail == 80  # 100 - 20
    assert root_fail == 70  # 100 - 30


def test_suppressed_and_stale_do_not_dent_score():
    # A symptom storm under one root must not double-count.
    checks = [
        _c("db_ping", "fail"),  # 100 - 30 (root)
        _c("stuck_tasks", "suppressed"),
        _c("approval_queue", "suppressed"),
        _c("publish_rate", "stale"),
    ]
    assert score(checks) == 70


def test_score_respects_overridden_weights():
    checks = [_c("approval_queue", "fail")]
    assert score(checks, weights={"fail": 50.0}) == 50


def test_score_clamped_to_zero():
    checks = [_c("approval_queue", "fail")] * 10  # 200 penalty
    assert score(checks) == 0


def test_db_ping_is_a_root():
    assert "db_ping" in ROOTS
    assert "ollama_models" in ROOTS
    assert "worker_error_rate" in ROOTS
    assert "stuck_tasks" not in ROOTS


# ---------------------------------------------------------------------------
# Correlation threshold.
# ---------------------------------------------------------------------------


def test_correlate_below_threshold_is_not_systemic():
    checks = [_c("stuck_tasks", "fail"), _c("approval_queue", "warn")]
    assert correlate(checks, threshold=3) is False


def test_correlate_at_threshold_is_systemic():
    checks = [
        _c("stuck_tasks", "fail"),
        _c("approval_queue", "warn"),
        _c("quality_score", "fail"),
    ]
    assert correlate(checks, threshold=3) is True


def test_correlate_ignores_suppressed_symptoms():
    # 1 real root failure + 3 suppressed symptoms is NOT systemic — the
    # symptoms aren't independent failures.
    checks = [
        _c("db_ping", "fail"),
        _c("stuck_tasks", "suppressed"),
        _c("approval_queue", "suppressed"),
        _c("publish_rate", "suppressed"),
    ]
    assert correlate(checks, threshold=3) is False


# ---------------------------------------------------------------------------
# run_doctor end-to-end + stale-brain meta-check.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_doctor_db_down_one_root_failure(monkeypatch):
    monkeypatch.setattr(doctor, "_load_remediation_keys", lambda: frozenset())
    pool = _FakePool(
        probe_rows=[
            _probe_row("db_ping", {"ok": False, "severity": "critical", "detail": "down"}),
            _probe_row("stuck_tasks", {"ok": False, "detail": "cannot query"}),
            _probe_row("approval_queue", {"ok": False, "detail": "cannot query"}),
        ],
        newest_signal=_fresh_signal(),
    )
    report = await run_doctor(pool)
    assert report.brain_stale is False
    by_name = {c.name: c for c in report.checks}
    assert by_name["db_ping"].status == "fail"
    assert by_name["stuck_tasks"].status == "suppressed"
    assert by_name["approval_queue"].status == "suppressed"
    # Only ONE independent failure (the root), not three.
    independent = [c for c in report.checks if c.status in ("fail", "warn")]
    assert len(independent) == 1


@pytest.mark.asyncio
async def test_run_doctor_stale_brain_marks_all_stale(monkeypatch):
    monkeypatch.setattr(doctor, "_load_remediation_keys", lambda: frozenset())
    # Newest signal is 20 minutes old → way past the 600s (2x cycle) window.
    pool = _FakePool(
        probe_rows=[
            _probe_row("db_ping", {"ok": True, "detail": "pong"}),
            _probe_row("stuck_tasks", {"ok": True, "detail": "clear"}),
        ],
        newest_signal=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    report = await run_doctor(pool)
    assert report.brain_stale is True
    # The synthesised meta-check is present and red.
    assert report.checks[0].name == "brain_freshness"
    assert report.checks[0].status == "fail"
    # The real probe results are downgraded to stale (not false-healthy).
    for check in report.checks[1:]:
        assert check.status == "stale"


@pytest.mark.asyncio
async def test_run_doctor_no_signal_is_brain_stale(monkeypatch):
    monkeypatch.setattr(doctor, "_load_remediation_keys", lambda: frozenset())
    pool = _FakePool(probe_rows=[], newest_signal=None)
    report = await run_doctor(pool)
    assert report.brain_stale is True
    assert report.checks[0].name == "brain_freshness"


@pytest.mark.asyncio
async def test_run_doctor_remediation_key_flagged(monkeypatch):
    # worker_error_rate has a REMEDIATIONS entry → its CheckResult should
    # carry a remediation key so --fix can act on it.
    monkeypatch.setattr(
        doctor, "_load_remediation_keys", lambda: frozenset({"worker_error_rate"})
    )
    pool = _FakePool(
        probe_rows=[
            _probe_row(
                "worker_error_rate",
                {"ok": False, "severity": "critical", "detail": "errors spiking"},
            ),
        ],
        newest_signal=_fresh_signal(),
    )
    report = await run_doctor(pool)
    by_name = {c.name: c for c in report.checks}
    assert by_name["worker_error_rate"].remediation == "worker_error_rate"


@pytest.mark.asyncio
async def test_run_doctor_respects_app_settings_threshold(monkeypatch):
    monkeypatch.setattr(doctor, "_load_remediation_keys", lambda: frozenset())
    # Two independent warns + a systemic_threshold of 2 → systemic.
    pool = _FakePool(
        probe_rows=[
            _probe_row("db_ping", {"ok": True, "detail": "pong"}),
            _probe_row("quality_score", {"ok": False, "severity": "warning", "detail": "low"}),
            _probe_row("topic_quality", {"ok": False, "severity": "warning", "detail": "low"}),
        ],
        newest_signal=_fresh_signal(),
        settings={"doctor_systemic_threshold": "2"},
    )
    report = await run_doctor(pool)
    assert report.systemic is True


# ---------------------------------------------------------------------------
# --json shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_to_dict_shape(monkeypatch):
    monkeypatch.setattr(doctor, "_load_remediation_keys", lambda: frozenset())
    pool = _FakePool(
        probe_rows=[_probe_row("db_ping", {"ok": True, "detail": "pong"})],
        newest_signal=_fresh_signal(),
    )
    report = await run_doctor(pool)
    d = report.to_dict()
    assert set(d.keys()) == {"score", "systemic", "brain_stale", "generated_at", "checks"}
    # round-trips through json cleanly (LLM/automation consumable)
    parsed = json.loads(json.dumps(d, default=str))
    assert parsed["score"] == 100
    check = parsed["checks"][0]
    assert set(check.keys()) == {
        "name",
        "status",
        "detail",
        "age_seconds",
        "metrics",
        "remediation",
        "root",
    }


# ---------------------------------------------------------------------------
# CLI exit-code mapping.
# ---------------------------------------------------------------------------


def _report(checks, *, systemic=False, brain_stale=False):
    from services.doctor import DoctorReport

    return DoctorReport(
        score=score(checks),
        systemic=systemic,
        brain_stale=brain_stale,
        checks=checks,
        generated_at="2026-05-30T00:00:00+00:00",
    )


def test_exit_code_healthy_is_zero():
    from poindexter.cli.doctor import _exit_code

    assert _exit_code(_report([_c("db_ping", "ok")])) == 0


def test_exit_code_degraded_is_one():
    from poindexter.cli.doctor import _exit_code

    # a non-root warn, not systemic → degraded
    assert _exit_code(_report([_c("quality_score", "warn")])) == 1


def test_exit_code_root_fail_is_critical():
    from poindexter.cli.doctor import _exit_code

    assert _exit_code(_report([_c("db_ping", "fail")])) == 2


def test_exit_code_systemic_is_critical():
    from poindexter.cli.doctor import _exit_code

    rep = _report([_c("quality_score", "warn")], systemic=True)
    assert _exit_code(rep) == 2


def test_exit_code_brain_stale_is_critical():
    from poindexter.cli.doctor import _exit_code

    rep = _report([_c("db_ping", "stale")], brain_stale=True)
    assert _exit_code(rep) == 2
