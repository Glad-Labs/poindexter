"""Unit tests for ``services.prometheus_rule_builder``.

The renderer has no DB of its own — every test here uses a fake pool
that returns scripted rows, so we can drive edge cases (missing
thresholds, malformed override JSON, disabled rules, unknown alerts)
without spinning up Postgres.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services import prometheus_rule_builder as rb


class _FakePool:
    """Minimal asyncpg-pool stand-in — only fetch() is used by rule_builder."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    async def fetch(self, _query: str, _arg: str) -> list[dict[str, Any]]:
        # Naive LIKE filter: prefix before the trailing `%`.
        prefix = _arg.rstrip("%").replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\")
        return [r for r in self._rows if r["key"].startswith(prefix)]


# ---------------------------------------------------------------------------
# _substitute_thresholds
# ---------------------------------------------------------------------------


class TestSubstituteThresholds:
    def test_substitutes_single_threshold(self):
        out = rb._substitute_thresholds(
            "poindexter_daily_spend_usd > {threshold.daily_spend_warning_usd}",
            {"daily_spend_warning_usd": "4.0"},
        )
        assert out == "poindexter_daily_spend_usd > 4.0"

    def test_leaves_promql_braces_alone(self):
        # PromQL label selectors use {label="value"} — must not be mangled
        expr = 'up{job="poindexter-worker"} == 0'
        assert rb._substitute_thresholds(expr, {"job": "hacked"}) == expr

    def test_missing_threshold_leaves_placeholder(self):
        # Unknown placeholder: left as-is, loud failure at Prometheus reload.
        out = rb._substitute_thresholds("x > {threshold.missing}", {})
        assert out == "x > {threshold.missing}"


# ---------------------------------------------------------------------------
# render_yaml
# ---------------------------------------------------------------------------


class TestRenderYaml:
    def test_renders_groups_and_rules(self):
        rules = {
            "TestAlert": {
                "enabled": True,
                "group": "test-group",
                "interval": "1m",
                "expr": "metric > {threshold.limit}",
                "for": "5m",
                "severity": "warning",
                "category": "content",
                "summary": "metric high",
                "description": "desc",
            }
        }
        out = rb.render_yaml({"limit": "10"}, rules)
        assert "groups:" in out
        assert "- name: test-group" in out
        assert "- alert: TestAlert" in out
        assert 'expr: "metric > 10"' in out  # threshold substituted + quoted
        assert "severity: warning" in out
        assert "category: content" in out

    def test_disabled_rules_omitted(self):
        rules = {
            "Enabled": {"enabled": True, "group": "g", "expr": "a", "for": "1m",
                        "severity": "info", "category": "content",
                        "summary": "", "description": ""},
            "Disabled": {"enabled": False, "group": "g", "expr": "b", "for": "1m",
                         "severity": "info", "category": "content",
                         "summary": "", "description": ""},
        }
        out = rb.render_yaml({}, rules)
        assert "alert: Enabled" in out
        assert "alert: Disabled" not in out

    def test_quotes_embedded_quotes_and_backslashes(self):
        rules = {
            "Quoter": {
                "enabled": True, "group": "g", "interval": "1m",
                "expr": 'up{job="x"} == 0', "for": "1m",
                "severity": "info", "category": "content",
                "summary": 'has "quotes" and \\ backslash',
                "description": "multi\nline",
            }
        }
        out = rb.render_yaml({}, rules)
        # Outer YAML quotes intact, inner chars escaped
        assert r'\"quotes\"' in out
        assert r"\\ backslash" in out
        assert "multi\\nline" in out  # newline escaped, not raw

    def test_groups_by_group_field(self):
        rules = {
            "A": {"enabled": True, "group": "g1", "interval": "30s",
                  "expr": "x", "for": "1m", "severity": "info",
                  "category": "content", "summary": "", "description": ""},
            "B": {"enabled": True, "group": "g2", "interval": "1m",
                  "expr": "y", "for": "1m", "severity": "info",
                  "category": "content", "summary": "", "description": ""},
        }
        out = rb.render_yaml({}, rules)
        # Alphabetical group order
        a_idx = out.index("- name: g1")
        b_idx = out.index("- name: g2")
        assert a_idx < b_idx


# ---------------------------------------------------------------------------
# load_thresholds / load_rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLoading:
    async def test_load_thresholds_returns_defaults_when_empty(self):
        pool = _FakePool([])
        out = await rb.load_thresholds(pool)
        assert out == rb.DEFAULT_THRESHOLDS

    async def test_load_thresholds_overlays_db_values(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.daily_spend_warning_usd", "value": "2.5"},
        ])
        out = await rb.load_thresholds(pool)
        assert out["daily_spend_warning_usd"] == "2.5"
        # Untouched keys keep their defaults
        assert out["monthly_spend_warning_usd"] == rb.DEFAULT_THRESHOLDS["monthly_spend_warning_usd"]

    async def test_load_rules_merges_override(self):
        pool = _FakePool([
            {
                "key": "prometheus.rule.DailySpendApproachingLimit",
                "value": json.dumps({"for": "1m", "severity": "critical"}),
            }
        ])
        out = await rb.load_rules(pool)
        rule = out["DailySpendApproachingLimit"]
        # Override applied
        assert rule["for"] == "1m"
        assert rule["severity"] == "critical"
        # Default fields preserved
        assert rule["group"] == "poindexter-business"
        assert "daily_spend_warning_usd" in rule["expr"]

    async def test_load_rules_can_disable_an_alert(self):
        pool = _FakePool([
            {
                "key": "prometheus.rule.MonthlySpendHigh",
                "value": json.dumps({"enabled": False}),
            }
        ])
        rules = await rb.load_rules(pool)
        assert rules["MonthlySpendHigh"]["enabled"] is False
        yaml_out = rb.render_yaml(rb.DEFAULT_THRESHOLDS, rules)
        assert "MonthlySpendHigh" not in yaml_out

    async def test_load_rules_accepts_new_alert(self):
        pool = _FakePool([
            {
                "key": "prometheus.rule.CustomAlert",
                "value": json.dumps({
                    "group": "custom", "expr": "x > 1", "for": "1m",
                    "severity": "info", "category": "content",
                    "summary": "s", "description": "d",
                }),
            }
        ])
        rules = await rb.load_rules(pool)
        assert "CustomAlert" in rules
        assert rules["CustomAlert"]["expr"] == "x > 1"

    async def test_load_rules_ignores_malformed_json(self):
        pool = _FakePool([
            {"key": "prometheus.rule.DailySpendApproachingLimit", "value": "{not json"},
        ])
        out = await rb.load_rules(pool)
        # Default preserved, malformed override dropped
        assert out["DailySpendApproachingLimit"]["for"] == rb.DEFAULT_RULES["DailySpendApproachingLimit"]["for"]

    async def test_load_rules_ignores_non_object_json(self):
        pool = _FakePool([
            {"key": "prometheus.rule.DailySpendApproachingLimit", "value": "[1,2,3]"},
        ])
        out = await rb.load_rules(pool)
        assert out["DailySpendApproachingLimit"]["for"] == rb.DEFAULT_RULES["DailySpendApproachingLimit"]["for"]


# ---------------------------------------------------------------------------
# build_current (end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBuildCurrent:
    async def test_produces_valid_yaml_with_defaults(self):
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert out.startswith("# Rendered by RenderPrometheusRulesJob")
        # All default content/business alerts present
        for alert in ("EmbeddingsStale", "NoPublishedPostsRecently",
                      "DailySpendApproachingLimit", "DailySpendOverBudget",
                      "MonthlySpendHigh"):
            assert f"alert: {alert}" in out
        # Default thresholds substituted
        assert "> 4.0" in out
        assert "> 5.0" in out
        assert "> 15.0" in out
        assert "> 21600" in out

    async def test_db_overrides_propagate(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.daily_spend_critical_usd", "value": "9.99"},
            {
                "key": "prometheus.rule.MonthlySpendHigh",
                "value": json.dumps({"enabled": False}),
            },
        ])
        out = await rb.build_current(pool)
        assert "> 9.99" in out
        assert "MonthlySpendHigh" not in out
