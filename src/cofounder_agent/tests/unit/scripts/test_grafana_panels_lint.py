"""Unit tests for ``scripts/ci/lib_grafana_panels.py``.

Targets the macro-substitution + panel-extraction + error-classification
helpers — the business logic of the Grafana panel lint (Glad-Labs/poindexter
follow-up to PR #308). Live datasource calls are integration territory and
exercised by CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ci is a flat directory (no __init__.py); add it to sys.path so
# we can import lib_grafana_panels directly.
REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPTS_CI = REPO_ROOT / "scripts" / "ci"
sys.path.insert(0, str(SCRIPTS_CI))

from lib_grafana_panels import (  # noqa: E402
    classify,
    classify_pg_error,
    extract_query,
    iter_root_panels,
    substitute,
    walk_panels,
)


# ---------------------------------------------------------------------------
# substitute()
# ---------------------------------------------------------------------------


class TestSubstitute:
    def test_timefilter_callable(self) -> None:
        out = substitute("SELECT 1 FROM t WHERE $__timeFilter(ts)")
        assert "ts BETWEEN NOW() - INTERVAL '1 hour' AND NOW()" in out
        assert "$__" not in out

    def test_timefilter_quoted_column(self) -> None:
        out = substitute('SELECT 1 FROM t WHERE $__timeFilter("timestamp")')
        assert '"timestamp" BETWEEN' in out

    def test_timegroup_emits_date_trunc(self) -> None:
        out = substitute("SELECT $__timeGroup(updated_at, '1d') AS time, COUNT(*) FROM t")
        assert "date_trunc('minute', updated_at)" in out
        assert "$__" not in out

    def test_timegroup_alias_preserves_alias(self) -> None:
        out = substitute("SELECT $__timeGroupAlias(ts, '1m'), COUNT(*) FROM t")
        assert "AS time" in out

    def test_interval_replaced(self) -> None:
        out = substitute("SELECT bucket FROM t WHERE bucket = $__interval")
        assert "$__interval" not in out
        assert "'1 minute'" in out

    def test_timefrom_timeto(self) -> None:
        out = substitute(
            "SELECT * FROM t WHERE ts > $__timeFrom() AND ts < $__timeTo()"
        )
        assert "EXTRACT(EPOCH FROM NOW()" in out
        assert "$__" not in out

    def test_dashboard_variable_replaced(self) -> None:
        # $service / $container etc. resolve to a literal at render time;
        # we substitute a placeholder so EXPLAIN doesn't choke.
        out = substitute('SELECT * FROM t WHERE service = $service')
        assert "$service" not in out
        assert "placeholder" in out

    def test_unknown_macro_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown macro"):
            substitute("SELECT $__doesNotExist FROM t")


# ---------------------------------------------------------------------------
# walk_panels() / iter_root_panels()
# ---------------------------------------------------------------------------


class TestPanelExtraction:
    def test_walks_nested_row_panels(self) -> None:
        # A "row" panel has children under its own ``panels[]`` — they
        # must be yielded, the row itself must NOT be (no targets to lint).
        dashboard = {
            "panels": [
                {
                    "type": "row",
                    "title": "Top",
                    "panels": [
                        {"id": 1, "type": "stat", "title": "child-a"},
                        {"id": 2, "type": "stat", "title": "child-b"},
                    ],
                },
                {"id": 3, "type": "stat", "title": "loose"},
            ]
        }
        ids = []
        for root in iter_root_panels(dashboard):
            for renderable in walk_panels(root):
                ids.append(renderable.get("id"))
        assert ids == [1, 2, 3]

    def test_panel_with_self_panels_yields_self_and_children(self) -> None:
        # Some non-row panels nest visualizations (legacy collapse format).
        # Both the parent and child must be visited.
        dashboard = {
            "panels": [
                {
                    "id": 10,
                    "type": "graph",
                    "panels": [{"id": 11, "type": "stat"}],
                }
            ]
        }
        seen = [
            r.get("id") for root in iter_root_panels(dashboard) for r in walk_panels(root)
        ]
        assert 10 in seen and 11 in seen

    def test_empty_dashboard(self) -> None:
        assert list(iter_root_panels({})) == []
        assert list(iter_root_panels({"panels": []})) == []


# ---------------------------------------------------------------------------
# classify() / extract_query()
# ---------------------------------------------------------------------------


class TestClassify:
    def test_postgres_via_target_datasource(self) -> None:
        target = {"datasource": {"type": "grafana-postgresql-datasource"}, "rawSql": "SELECT 1"}
        assert classify(target, {}) == "postgres"
        assert extract_query(target, "postgres") == "SELECT 1"

    def test_postgres_inherits_from_panel(self) -> None:
        target = {"rawSql": "SELECT 1"}
        panel = {"datasource": {"type": "grafana-postgresql-datasource"}}
        assert classify(target, panel) == "postgres"

    def test_prometheus_uses_expr(self) -> None:
        target = {"datasource": {"type": "prometheus"}, "expr": "up{}"}
        assert classify(target, {}) == "prometheus"
        assert extract_query(target, "prometheus") == "up{}"

    def test_loki_uses_expr_or_query(self) -> None:
        target = {"datasource": {"type": "loki"}, "expr": "{job=\"x\"}"}
        assert classify(target, {}) == "loki"
        assert extract_query(target, "loki") == '{job="x"}'

    def test_unknown_returns_none(self) -> None:
        target = {"datasource": {"type": "tempo"}, "query": "..."}
        assert classify(target, {}) is None

    def test_no_datasource_returns_none(self) -> None:
        assert classify({}, {}) is None


# ---------------------------------------------------------------------------
# classify_pg_error()
# ---------------------------------------------------------------------------


class TestClassifyPgError:
    def test_undefined_column_is_failure(self) -> None:
        assert classify_pg_error("42703") == "FAIL"

    def test_undefined_table_is_failure(self) -> None:
        assert classify_pg_error("42P01") == "FAIL"

    def test_invalid_text_repr_is_warning(self) -> None:
        # macro arg substitution glitches (e.g. $__timeFrom() in a place
        # postgres can't coerce) shouldn't go red.
        assert classify_pg_error("22P02") == "WARN"

    def test_unknown_code_is_warning(self) -> None:
        assert classify_pg_error("99999") == "WARN"

    def test_empty_code_is_warning(self) -> None:
        assert classify_pg_error("") == "WARN"
