"""Guard the ``noDataState`` posture of the file-provisioned Grafana alert rules.

Provisioning file:
``infrastructure/grafana/provisioning/alerting/alert-rules.yml``

WHY THIS TEST EXISTS (Glad-Labs/poindexter#581)

A Grafana alert rule has two independent failure-mode knobs:

* ``noDataState`` — the state the rule resolves to when its query *succeeds
  but returns no rows / NULL* (an emptied table, a view repointed by a
  migration, a ``LIMIT 1`` over an empty table, a ``dropNN`` reducer that
  drops a NULL). With ``noDataState: OK`` the rule silently resolves green.
* ``execErrState`` — the state when the query *errors* (renamed column
  ``42703`` / dropped table ``42P01`` / datasource down).

The 2026-06-02 production audit found 9 page-worthy rules on
``noDataState: OK``: when their query went blind (broken/empty), the rule
resolved OK and never paged — "alerts stayed green while subsystems were
dark". That directly contradicts the "fail loud + notify, no silent
fallbacks" principle.

This test pins the fix so it cannot silently regress:

  1. Every page-worthy rule uses ``noDataState: Alerting`` — a blind query
     surfaces instead of passing.
  2. No rule uses ``execErrState: OK`` — a query that errors always
     surfaces (this is the "deliberately-broken rawSql produces an alert"
     acceptance criterion, modelled at config level).
  3. The single intentional exception (DB Size Warning — a non-page-worthy
     capacity warning whose only no-data condition is a datasource outage
     already surfaced by the critical rules) stays documented and on OK.

A live-Grafana firing test is integration territory (booting a Grafana
stack to assert a single alert state isn't worth it in unit CI); the
contract that actually regresses is the per-rule state config, which is
what this test locks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
ALERT_RULES_YML = (
    REPO_ROOT
    / "infrastructure"
    / "grafana"
    / "provisioning"
    / "alerting"
    / "alert-rules.yml"
)

# Rules whose query reports a dark/broken subsystem when it returns no
# data (every one of these returns a row in the healthy state, so no-data
# can only mean a renamed/dropped table-or-view or a downed datasource —
# i.e. the rule went blind). These MUST surface, never resolve OK.
#
# Keyed by uid so a rule rename doesn't silently drop it from the guard.
PAGE_WORTHY_UIDS = {
    "brain-high-error-rate",  # count(*) over audit_log
    "brain-stale-tasks",  # count(*) over pipeline_tasks_view (a VIEW)
    "brain-daily-cost-spike",  # SUM over cost_logs
    "brain-content-quality-drop",  # AVG over pipeline_tasks_view (a VIEW)
    "brain-traffic-anomaly",  # CTE over page_views
    "brain-ollama-down",  # CASE over pipeline_tasks_view + cost_logs
    # brain-gpu-temp-high and brain-gpu-metrics-stale were REMOVED 2026-06-18
    # (poindexter#653). GPU temp alerting moved to Prometheus GpuTemperatureHigh;
    # exporter death is caught by the static WindowsExporterDown rule.
}

# No remaining rule is intentionally on noDataState: OK. The one prior
# exception (#581) — brain-db-size-warning, a non-page-worthy capacity
# warning — was migrated to the native Prometheus rule
# PoindexterBrainDbSizeWarning (poindexter#735 item 2). A Prometheus
# `metric > N` expr (no absent() guard) simply yields no series when the
# exporter is down, so it never fires on no-data — the same intent without
# a Grafana SQL poll.
INTENTIONAL_OK_UIDS: set[str] = set()


def _load_rules() -> list[dict]:
    assert ALERT_RULES_YML.is_file(), f"missing provisioning file: {ALERT_RULES_YML}"
    data = yaml.safe_load(ALERT_RULES_YML.read_text(encoding="utf-8"))
    groups = data["groups"]
    rules: list[dict] = []
    for group in groups:
        rules.extend(group.get("rules", []))
    assert rules, "no alert rules parsed from provisioning file"
    return rules


def _rules_by_uid() -> dict[str, dict]:
    return {r["uid"]: r for r in _load_rules()}


def test_every_page_worthy_uid_is_present() -> None:
    """The guarded uids must still exist (catches a rename/removal)."""
    by_uid = _rules_by_uid()
    missing = PAGE_WORTHY_UIDS - set(by_uid)
    assert not missing, (
        f"page-worthy alert rules vanished from the provisioning file: {sorted(missing)}. "
        "If a rule was intentionally removed, update PAGE_WORTHY_UIDS and document it."
    )


@pytest.mark.parametrize("uid", sorted(PAGE_WORTHY_UIDS))
def test_page_worthy_rules_alert_on_no_data(uid: str) -> None:
    """A blind (no-data) query on a page-worthy rule must surface, not pass."""
    rule = _rules_by_uid()[uid]
    assert rule["noDataState"] == "Alerting", (
        f"{uid!r} ({rule.get('title')!r}) has noDataState={rule['noDataState']!r}; "
        "a broken/empty query would silently resolve OK and never page (#581). "
        "Page-worthy rules must use noDataState: Alerting."
    )


def test_no_rule_silently_passes_on_query_error() -> None:
    """No rule may swallow a query *error* (renamed column / dropped table).

    This is the config-level model of the #581 acceptance criterion: a
    deliberately-broken rawSql must produce an alert, never an OK resolve.
    Grafana surfaces a query error via ``execErrState`` — so it must never
    be ``OK`` for any rule.
    """
    offenders = [
        f"{r['uid']} ({r.get('title')!r})"
        for r in _load_rules()
        if r.get("execErrState") == "OK"
    ]
    assert not offenders, (
        "rules whose execErrState is OK swallow a broken-query error instead "
        f"of surfacing it: {offenders}"
    )


def test_intentional_ok_exception_stays_documented() -> None:
    """The one rule kept on noDataState: OK must be the documented exception."""
    by_uid = _rules_by_uid()
    ok_rules = {
        uid for uid, r in by_uid.items() if r.get("noDataState") == "OK"
    }
    assert ok_rules == INTENTIONAL_OK_UIDS, (
        "the set of rules on noDataState: OK drifted from the documented "
        f"exception. expected {sorted(INTENTIONAL_OK_UIDS)}, found {sorted(ok_rules)}. "
        "Adding a rule to OK is a silent-failure risk — flip it to Alerting "
        "or document the exception here and in the YAML (#581)."
    )


def test_no_page_worthy_uid_left_on_ok() -> None:
    """Belt-and-suspenders: the OK set must not intersect the page-worthy set."""
    by_uid = _rules_by_uid()
    leaked = {
        uid
        for uid in PAGE_WORTHY_UIDS
        if by_uid.get(uid, {}).get("noDataState") == "OK"
    }
    assert not leaked, f"page-worthy rules left on noDataState: OK: {sorted(leaked)} (#581)"
