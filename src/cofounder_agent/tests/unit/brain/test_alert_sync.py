"""Unit tests for brain/alert_sync.py — datasource routing and hash stability.

Covers the bugs fixed in poindexter#XXXX:
1. SQL queries must route to ``local-brain-db`` (not ``prometheus``)
2. PromQL queries must route to ``local-prometheus`` (not ``prometheus``)
3. ``_hash_rule`` must include ``datasource_type`` so a routing-logic fix
   invalidates stale hashes and triggers a re-sync without a DB row edit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from brain.alert_sync import (  # noqa: E402
    _hash_rule,
    _is_sql_query,
    _rule_uid,
    rule_to_grafana_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    *,
    name: str = "Test Rule",
    query: str = "up{job='poindexter-worker'}",
    threshold: float = 0.0,
    duration: str = "5m",
    severity: str = "warning",
    labels: dict | None = None,
    annotations: dict | None = None,
) -> dict:
    return {
        "name": name,
        "promql_query": query,
        "threshold": str(threshold),
        "duration": duration,
        "severity": severity,
        "labels_json": labels or {},
        "annotations_json": annotations or {"summary": "test"},
    }


# ---------------------------------------------------------------------------
# _is_sql_query
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("SELECT COUNT(*) FROM foo", True),
    ("select count(*) from foo", True),
    ("  SELECT * FROM bar WHERE x > 1", True),
    ("up{job='worker'}", False),
    ("rate(http_requests_total[5m])", False),
    ("", False),
])
def test_is_sql_query(query: str, expected: bool) -> None:
    assert _is_sql_query(query) is expected


# ---------------------------------------------------------------------------
# rule_to_grafana_payload — SQL path
# ---------------------------------------------------------------------------

def test_sql_rule_routes_to_brain_db() -> None:
    row = _make_row(
        name="Tasks Stuck In Progress",
        query="SELECT COUNT(*) as stuck_count FROM pipeline_tasks_view WHERE status = 'in_progress'",
        threshold=0.0,
    )
    payload = rule_to_grafana_payload(row, folder_uid="test-folder")

    assert payload["condition"] == "C"
    query_stage = payload["data"][0]
    assert query_stage["datasourceUid"] == "local-brain-db"
    assert query_stage["model"]["rawSql"] == row["promql_query"]
    assert query_stage["model"]["format"] == "table"

    reduce_stage = payload["data"][1]
    assert reduce_stage["datasourceUid"] == "__expr__"
    assert reduce_stage["model"]["type"] == "reduce"

    threshold_stage = payload["data"][2]
    assert threshold_stage["datasourceUid"] == "__expr__"
    assert threshold_stage["model"]["type"] == "threshold"
    assert threshold_stage["model"]["expression"] == "B"


def test_sql_rule_never_references_prometheus() -> None:
    row = _make_row(query="SELECT 1 as val FROM cost_logs")
    payload = rule_to_grafana_payload(row, folder_uid="f")
    datasource_uids = {stage["datasourceUid"] for stage in payload["data"]}
    assert "prometheus" not in datasource_uids


# ---------------------------------------------------------------------------
# rule_to_grafana_payload — PromQL path
# ---------------------------------------------------------------------------

def test_promql_rule_routes_to_local_prometheus() -> None:
    row = _make_row(
        name="Poindexter Worker Down",
        query="up{job='poindexter-worker'}",
        threshold=1.0,
    )
    payload = rule_to_grafana_payload(row, folder_uid="test-folder")

    assert payload["condition"] == "B"
    query_stage = payload["data"][0]
    assert query_stage["datasourceUid"] == "local-prometheus"
    assert query_stage["model"]["expr"] == row["promql_query"]
    assert query_stage["model"].get("instant") is True

    threshold_stage = payload["data"][1]
    assert threshold_stage["datasourceUid"] == "__expr__"
    assert threshold_stage["model"]["expression"] == "A"


def test_promql_rule_has_two_stages() -> None:
    row = _make_row(query="rate(errors_total[5m])")
    payload = rule_to_grafana_payload(row, folder_uid="f")
    assert len(payload["data"]) == 2


def test_sql_rule_has_three_stages() -> None:
    row = _make_row(query="SELECT AVG(quality_score) FROM pipeline_tasks_view")
    payload = rule_to_grafana_payload(row, folder_uid="f")
    assert len(payload["data"]) == 3


# ---------------------------------------------------------------------------
# _hash_rule — datasource_type included
# ---------------------------------------------------------------------------

def test_hash_differs_for_sql_vs_promql() -> None:
    """A SQL and PromQL rule with the same name/threshold should hash differently."""
    sql_row = _make_row(name="Metric", query="SELECT COUNT(*) FROM foo")
    promql_row = _make_row(name="Metric", query="up{job='poindexter-worker'}")
    assert _hash_rule(sql_row) != _hash_rule(promql_row)


def test_hash_is_stable_for_same_row() -> None:
    row = _make_row()
    assert _hash_rule(row) == _hash_rule(row)


def test_hash_changes_on_query_edit() -> None:
    row_a = _make_row(query="up{job='worker'}")
    row_b = _make_row(query="up{job='worker-v2'}")
    assert _hash_rule(row_a) != _hash_rule(row_b)


# ---------------------------------------------------------------------------
# _rule_uid
# ---------------------------------------------------------------------------

def test_rule_uid_has_pdx_prefix() -> None:
    assert _rule_uid("Tasks Stuck In Progress").startswith("pdx-")


def test_rule_uid_is_deterministic() -> None:
    assert _rule_uid("My Rule") == _rule_uid("My Rule")


def test_rule_uid_differs_per_name() -> None:
    assert _rule_uid("Rule A") != _rule_uid("Rule B")
