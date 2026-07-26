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
# PoindexterBrainDbSizeWarning — the #735 item 2 SQL→Prometheus migration
# ---------------------------------------------------------------------------


class TestBrainDbSizeRule:
    """The brain-db-size warning moved off the Grafana SQL ``pg_database_size()``
    poll onto a native Prometheus rule over ``pg_database_size_bytes``
    (poindexter#735 item 2), mirroring the 2026-06-03 disk-space migration."""

    def test_rule_and_threshold_are_registered(self):
        assert "PoindexterBrainDbSizeWarning" in rb.DEFAULT_RULES
        assert rb.DEFAULT_THRESHOLDS["brain_db_size_warning_gb"] == "5"

    def test_rule_renders_against_the_exporter_metric(self):
        rule = rb.DEFAULT_RULES["PoindexterBrainDbSizeWarning"]
        out = rb.render_yaml(dict(rb.DEFAULT_THRESHOLDS), {"PoindexterBrainDbSizeWarning": rule})
        assert "- alert: PoindexterBrainDbSizeWarning" in out
        # Native exporter metric — NOT the SQL pg_database_size() function.
        assert "pg_database_size_bytes" in out
        assert "pg_database_size(" not in out
        assert r'datname=\"poindexter_brain\"' in out
        # Threshold token substituted to its default; no leftover placeholder.
        assert "{threshold.brain_db_size_warning_gb}" not in out
        assert "> 5" in out
        assert "severity: warning" in out

    def test_absent_metric_does_not_alert(self):
        """#581: db-size is a non-page-worthy capacity warning that must NOT
        fire on no-data. A bare ``metric > N`` expr (no ``absent()`` guard,
        unlike the disk rules) naturally yields no series when the exporter is
        down — the Prometheus-native equivalent of the old noDataState: OK."""
        rule = rb.DEFAULT_RULES["PoindexterBrainDbSizeWarning"]
        assert "absent(" not in rule["expr"]


class TestContainerMemoryRule:
    """Per-container RSS ceiling (2026-07-02 observability review): the
    langfuse-clickhouse 15 GB spikes and the cadvisor VM-OOM cascade
    (glad-labs-stack#2019/#2021) had cAdvisor panels but no alert anywhere."""

    def test_rule_and_threshold_are_registered(self):
        assert "PoindexterContainerMemoryHigh" in rb.DEFAULT_RULES
        assert rb.DEFAULT_THRESHOLDS["container_memory_warning_gb"] == "8"

    def test_rule_renders_with_model_server_exclusion(self):
        rule = rb.DEFAULT_RULES["PoindexterContainerMemoryHigh"]
        out = rb.render_yaml(
            dict(rb.DEFAULT_THRESHOLDS), {"PoindexterContainerMemoryHigh": rule}
        )
        assert "- alert: PoindexterContainerMemoryHigh" in out
        assert "container_memory_usage_bytes" in out
        # Model-serving containers legitimately hold weights in RAM — they
        # must be excluded by name, not by raising the shared threshold.
        assert "image-gen-server" in out
        assert "wan-server" in out
        # Threshold token substituted to its default; no leftover placeholder.
        assert "{threshold.container_memory_warning_gb}" not in out
        assert "> 8" in out
        assert "severity: warning" in out
        # Sustained, not spike — model loads / build bursts must not page.
        assert "for: 30m" in out

    def test_absent_metric_does_not_alert(self):
        """#581: capacity warning, not a liveness page — a bare ``> N`` expr
        yields no series when cAdvisor is down (CadvisorDown covers that)."""
        rule = rb.DEFAULT_RULES["PoindexterContainerMemoryHigh"]
        assert "absent(" not in rule["expr"]


class TestHostMemoryPressureRules:
    """Host RAM-pressure alerts (2026-07-10 desktop-freeze investigation).

    The worker host oversubscribes RAM — the container stack, the host-native
    inference fleet, and the operator desktop all compete for the same
    physical RAM, and when available RAM approaches zero the desktop
    compositor stalls (the recurring freeze that escalates to a hard reset).
    There was NO host-memory alert before this — only per-container (cAdvisor)
    and GPU-VRAM rules — so the pressure that actually caused the freezes was
    invisible to Alertmanager.

    Two signals, both from node_exporter (job="node"): available bytes for
    the warning-level headroom floor, and PSI full-stall for the critical
    thrash detector. The thrash rule originally read the swap page-out rate
    (windows_exporter pagefile writes, then node_vmstat_pswpout), but that
    calibration died with the Pop!_OS migration — zram at priority 1000 makes
    page-outs cheap and chronic (~131 false criticals in two weeks), while
    PSI measures the stall time the freeze actually consists of (2026-07-25
    alarm audit).
    """

    def test_rules_and_thresholds_registered(self):
        assert "PoindexterHostMemoryLow" in rb.DEFAULT_RULES
        assert "PoindexterHostMemoryThrashing" in rb.DEFAULT_RULES
        # Calibrated from real telemetry, but every value stays DB-tunable.
        assert rb.DEFAULT_THRESHOLDS["host_memory_available_warning_gb"] == "4"
        assert (
            rb.DEFAULT_THRESHOLDS["host_memory_psi_full_stall_critical_ratio"]
            == "0.25"
        )

    @pytest.mark.asyncio
    async def test_available_low_renders_against_node_exporter(self):
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "alert: PoindexterHostMemoryLow" in out
        section = out.split("alert: PoindexterHostMemoryLow")[1].split("alert:", 1)[0]
        # Sources the available-RAM gauge the freeze investigation measured.
        assert "node_memory_MemAvailable_bytes" in section
        # Threshold substituted to its default; no leftover placeholder.
        assert "{threshold." not in section
        assert "1024*1024*1024) < 4" in section
        # Warning → Discord: a sustained-headroom heads-up, not a phone page.
        assert "severity: warning" in section

    @pytest.mark.asyncio
    async def test_thrashing_is_critical_and_targets_psi_full_stall(self):
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "alert: PoindexterHostMemoryThrashing" in out
        section = out.split("alert: PoindexterHostMemoryThrashing")[1].split(
            "alert:", 1
        )[0]
        # PSI full-stall (all non-idle tasks blocked on memory), NOT the swap
        # page-out rate — zram makes page-outs cheap and chronic on Linux, so
        # pswpout no longer implies thrash (2026-07-25 alarm audit).
        assert "node_pressure_memory_stalled_seconds_total" in section
        assert "node_vmstat_pswpout" not in section
        assert "> 0.25" in section
        # Critical → Telegram: healthy full-stall is <0.01, so it only fires
        # during a real episode.
        assert "severity: critical" in section

    def test_no_absent_guard_so_exporter_death_doesnt_false_fire(self):
        """NodeExporterDown owns exporter death; a bare comparison yields no
        series on no-data, so neither rule carries an absent() guard (#581)."""
        assert "absent(" not in rb.DEFAULT_RULES["PoindexterHostMemoryLow"]["expr"]
        assert (
            "absent(" not in rb.DEFAULT_RULES["PoindexterHostMemoryThrashing"]["expr"]
        )

    def test_descriptions_carry_a_remediation_path(self):
        """Every alert needs an operator-facing next step (feedback_self_heal_
        not_suppress). Both name a concrete lever, not just the symptom."""
        low = rb.DEFAULT_RULES["PoindexterHostMemoryLow"]["description"].lower()
        thrash = rb.DEFAULT_RULES["PoindexterHostMemoryThrashing"][
            "description"
        ].lower()
        assert "wsl" in low or "container" in low
        assert "close" in thrash or "pause" in thrash

    @pytest.mark.asyncio
    async def test_thresholds_operator_tunable(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.host_memory_available_warning_gb",
             "value": "6"},
            {"key": "prometheus.threshold.host_memory_psi_full_stall_critical_ratio",
             "value": "0.6"},
        ])
        out = await rb.build_current(pool)
        assert "1024*1024*1024) < 6" in out
        assert "> 0.6" in out


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
        # Default thresholds substituted. monthly_spend at $65 sits just
        # ABOVE the $60 full-cost ceiling (cost_throttle_monthly_budget_usd)
        # because the gauge is the blended API+electricity axis: it means
        # "the throttle failed to hold the line", not "the budget is in use".
        assert "> 4.0" in out
        assert "> 5.0" in out
        assert "> 65.0" in out
        # EmbeddingsStale uses the seconds threshold as a Prometheus
        # duration literal (``[21600s]``), not a bare comparison.
        assert "[21600s]" in out

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


# ---------------------------------------------------------------------------
# GPU thermal + VRAM alerts (audit C3) — self-supervised via the container
# exporter, NOT the hand-started gpu-scraper.py DB path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGpuRules:
    async def test_gpu_temp_and_vram_rules_present_and_target_live_metrics(self):
        """The GPU thermal + VRAM rules must target the container-scraped
        exporter metrics (verified live: job="nvidia-smi", gpu-exporter:9835),
        so they fire even when the unsupervised gpu-scraper.py host script is
        dead — the inverse of the old noDataState:OK DB-path alert."""
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "alert: GpuTemperatureHigh" in out
        assert "alert: GpuVramHigh" in out
        assert "nvidia_gpu_temperature_celsius" in out
        assert "nvidia_gpu_memory_utilization_percent" in out
        # Defaults substituted: 85°C thermal, 95% VRAM.
        assert "nvidia_gpu_temperature_celsius > 85" in out
        assert "nvidia_gpu_memory_utilization_percent > 95" in out

    async def test_gpu_thresholds_overridable(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.gpu_temperature_celsius", "value": "80"},
            {"key": "prometheus.threshold.gpu_vram_utilization_percent", "value": "90"},
        ])
        out = await rb.build_current(pool)
        assert "nvidia_gpu_temperature_celsius > 80" in out
        assert "nvidia_gpu_memory_utilization_percent > 90" in out


@pytest.mark.unit
class TestQaRailFullySkippedRule:
    """poindexter#553 AC#2 — the QA-rail-skip-rate alert. A rail skipping
    100% of the last N passes (empty research_context, disabled master
    flag, unresolvable judge) must page via the poindexter-content group."""

    def test_default_rule_shape(self):
        rule = rb.DEFAULT_RULES["QaRailFullySkipped"]
        assert rule["group"] == "poindexter-content"
        assert rule["category"] == "content"
        assert rule["severity"] == "warning"  # → Discord, not Telegram spam
        assert "poindexter_qa_rail_skip_ratio" in rule["expr"]
        # Remediation (feedback_alert_auto_triage — every alert needs a path).
        desc = rule["description"].lower()
        assert "research_context" in desc
        assert "master" in desc and "flag" in desc
        assert "judge" in desc
        assert "/d/qa-rails" in rule["description"]

    def test_expr_rides_through_worker_restart_gaps(self):
        """poindexter#839 alert audit: the exporter lives in the worker, and
        every deploy restart blanks the series for a scrape or two. With a
        raw-gauge expr those gaps reset the `for:` clock, so on active days
        (8-45 worker restarts/day observed 2026-06/07) the alert flapped
        fire→resolve→fire, then sat in `pending` forever from 07-05 onward
        and never paged while the ragas rail was 100%-skipped. max_over_time
        holds the last observed ratio across the gap."""
        rule = rb.DEFAULT_RULES["QaRailFullySkipped"]
        assert rule["expr"].startswith("max_over_time(")
        assert "[1h]" in rule["expr"]

    @pytest.mark.asyncio
    async def test_rule_renders_with_threshold_substituted(self):
        """build_current with no DB overrides must render the alert into the
        content group with the 100% (>= 1) trigger — the synthetic
        'alert fires' definition for the metric the exporter sets to 1.0."""
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "alert: QaRailFullySkipped" in out
        # Threshold placeholder substituted to the default "1".
        assert "max_over_time(poindexter_qa_rail_skip_ratio[1h]) >= 1" in out
        # No unsubstituted placeholder leaks into the rendered YAML.
        assert "{threshold." not in out.split("QaRailFullySkipped", 1)[1][:400]

    @pytest.mark.asyncio
    async def test_trigger_ratio_is_operator_tunable(self):
        """An operator can lower the trigger to alert before a rail reaches
        a full 100% skip rate."""
        pool = _FakePool([
            {"key": "prometheus.threshold.qa_rail_skip_ratio", "value": "0.9"},
        ])
        out = await rb.build_current(pool)
        assert "max_over_time(poindexter_qa_rail_skip_ratio[1h]) >= 0.9" in out


# ---------------------------------------------------------------------------
# Worker-restart gap bridging — the #2330 follow-up audit
# ---------------------------------------------------------------------------


class TestRestartGapBridging:
    """Instant-vector rules over worker-exported gauges must bridge deploy-
    restart scrape holes (glad-labs-stack#2330 follow-up audit).

    Mechanism: every worker deploy restart fails 1-3 scrapes (15s interval),
    and a failed scrape writes staleness markers for ALL ``worker:8002``
    series — instant selectors go empty immediately (no 5m grace), resetting
    any in-flight ``for:`` clock. Measured 2026-07-11: 46 restarts and 138
    series holes in one day; ``NoPublishedPostsRecently`` logged 4,002
    pending-minutes across 92 chopped episodes in 14d and never fired once
    (a ``for: 48h`` rule can complete at most ~7 episodes in that span).

    ``last_over_time`` (not ``max_over_time``) is deliberate for all four
    rules here: it returns the newest sample in the window, so it bridges
    *absence* without holding *stale highs* — a genuine recovery (migrations
    applied on boot, month-rollover spend reset, backfilled embeddings, a
    fresh publish) propagates with the first post-recovery sample instead of
    false-firing for up to an hour like a held max would.
    """

    def test_no_published_posts_bridges_both_operands(self):
        rule = rb.DEFAULT_RULES["NoPublishedPostsRecently"]
        expr = rule["expr"]
        # Both sides of the == carry the bridge: the current read AND the
        # offset read (a restart hole 24h ago blanks the offset side today).
        assert expr.count("last_over_time(") == 2
        assert expr.count("[1h]") == 2
        assert "offset 24h" in expr
        # Intent unchanged: still the 24h-growth comparison held for 48h.
        assert rule["for"] == "48h"

    def test_unapplied_migrations_bridges_with_last_not_max(self):
        rule = rb.DEFAULT_RULES["UnappliedMigrationsDrift"]
        assert (
            "last_over_time(poindexter_unapplied_migrations_count[1h])"
            in rule["expr"]
        )
        # A worker restart re-runs migrations on boot, i.e. the restart IS
        # the remediation — a held max would keep the alert firing for up
        # to 1h after the drift was fixed.
        assert "max_over_time" not in rule["expr"]

    def test_monthly_spend_bridges_with_last_not_max(self):
        rule = rb.DEFAULT_RULES["MonthlySpendHigh"]
        assert "last_over_time(poindexter_monthly_spend_usd[1h])" in rule["expr"]
        # Spend gauges legitimately drop to ~0 at month rollover; a held max
        # would false-fire for up to 1h into the new month (for: 10m < 1h).
        assert "max_over_time" not in rule["expr"]

    @pytest.mark.asyncio
    async def test_bridged_rules_render_with_thresholds_substituted(self):
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "last_over_time(poindexter_embeddings_missing_posts[1h]) > 3" in out
        assert "last_over_time(poindexter_monthly_spend_usd[1h]) > 65.0" in out
        assert (
            'last_over_time(poindexter_posts_total{status=\\"published\\"}[1h]'
            " offset 24h)" in out
        )

    def test_ratchet_long_for_rules_bridge_or_run_on_independent_exporters(self):
        """Any rule holding a ``for:`` of 30m+ must either wrap its reads in a
        ``*_over_time`` window (restart-proof) or source an exporter that does
        not restart with worker deploys. A raw instant read cannot survive a
        30m pending window on a box that restarts the worker 8-46x/day."""
        import re

        # cAdvisor / node_exporter / nvidia-smi / postgres_exporter run as
        # independent containers or host services — worker deploys don't
        # blank their series.
        independent = {"PoindexterContainerMemoryHigh"}
        for name, rule in rb.DEFAULT_RULES.items():
            m = re.fullmatch(r"(\d+)([mh])", str(rule["for"]))
            assert m, f"{name}: unparseable for: {rule['for']!r}"
            minutes = int(m.group(1)) * (60 if m.group(2) == "h" else 1)
            if minutes < 30 or name in independent:
                continue
            assert "_over_time(" in rule["expr"], (
                f"{name} holds for: {rule['for']} on a raw instant read — "
                "a worker deploy restart resets its pending clock "
                "(see QaRailFullySkipped / poindexter#839)"
            )


# ---------------------------------------------------------------------------
# WindowsExporterDown — static infrastructure.yml (poindexter#705)
# ---------------------------------------------------------------------------


class TestNodeExporterDownStaticRule:
    """NodeExporterDown must live in the static infrastructure.yml — it is a
    binary up/down rule with no tunable thresholds and must fire even when the
    DB is down (when DB-rendered rules cannot be regenerated).  poindexter#705.
    (WindowsExporterDown pre-migration; the Pop!_OS Task 5.2 swap renamed it.)"""

    @pytest.fixture
    def infra_rules(self) -> list[dict]:
        import pathlib

        import yaml

        alerts_path = (
            pathlib.Path(__file__).parents[5]
            / "infrastructure"
            / "prometheus"
            / "alerts"
            / "infrastructure.yml"
        )
        doc = yaml.safe_load(alerts_path.read_text())
        return [rule for group in doc["groups"] for rule in group.get("rules", [])]

    def test_rule_is_present(self, infra_rules):
        names = [r.get("alert") for r in infra_rules]
        assert "NodeExporterDown" in names

    def test_expr_covers_absent_and_zero(self, infra_rules):
        rule = next(r for r in infra_rules if r.get("alert") == "NodeExporterDown")
        expr = rule["expr"]
        assert 'up{job="node"} == 0' in expr
        assert "absent(up{job=" in expr

    def test_severity_critical(self, infra_rules):
        rule = next(r for r in infra_rules if r.get("alert") == "NodeExporterDown")
        assert rule["labels"]["severity"] == "critical"

    def test_for_at_least_2m(self, infra_rules):
        """Sustained window so a momentary scrape hiccup doesn't page."""
        import re
        rule = next(r for r in infra_rules if r.get("alert") == "NodeExporterDown")
        dur = rule.get("for", "0m")
        m = re.match(r"(\d+)m", dur)
        assert m is not None, f"unexpected for duration: {dur!r}"
        assert int(m.group(1)) >= 2


# ---------------------------------------------------------------------------
# PrometheusConfigReloadFailed — static prometheus-alertmanager.yml
# ---------------------------------------------------------------------------


class TestPrometheusConfigReloadFailedStaticRule:
    """A failing ``/-/reload`` must page. Observed 2026-07-11: the deploy sync
    replaced ``prometheus.yml`` on the host, the container's single-file bind
    mount went stale (ENOENT inside the container), and every reload 500'd for
    ~100 minutes — freshly rendered rule changes sat on disk, unloaded, with
    no alert anywhere. ``prometheus_config_last_reload_successful`` flips to 0
    on the first failed attempt and holds until a reload succeeds, so a static
    rule on it catches every future variant of this failure. Static file (not
    DB-rendered) on purpose: the failure mode is "rendered rule changes don't
    take effect", so the alert covering it must not depend on that pipeline."""

    @pytest.fixture
    def self_monitoring_rules(self) -> list[dict]:
        import pathlib

        import yaml

        alerts_path = (
            pathlib.Path(__file__).parents[5]
            / "infrastructure"
            / "prometheus"
            / "alerts"
            / "prometheus-alertmanager.yml"
        )
        doc = yaml.safe_load(alerts_path.read_text())
        return [rule for group in doc["groups"] for rule in group.get("rules", [])]

    def test_rule_is_present(self, self_monitoring_rules):
        names = [r.get("alert") for r in self_monitoring_rules]
        assert "PrometheusConfigReloadFailed" in names

    def test_expr_targets_the_reload_gauge(self, self_monitoring_rules):
        rule = next(
            r
            for r in self_monitoring_rules
            if r.get("alert") == "PrometheusConfigReloadFailed"
        )
        assert rule["expr"].strip() == "prometheus_config_last_reload_successful == 0"

    def test_warning_severity_with_sustained_for(self, self_monitoring_rules):
        """Warning → Discord (self-healable via container restart, not a 2am
        page), and a sustained ``for:`` so one transient flaky reload attempt
        doesn't fire."""
        import re

        rule = next(
            r
            for r in self_monitoring_rules
            if r.get("alert") == "PrometheusConfigReloadFailed"
        )
        assert rule["labels"]["severity"] == "warning"
        m = re.match(r"(\d+)m", rule.get("for", "0m"))
        assert m is not None and int(m.group(1)) >= 5

    def test_description_names_the_stale_bind_mount_remediation(
        self, self_monitoring_rules
    ):
        """Every alert carries its remediation path: the known cause is a stale
        single-file bind mount after the host file is replaced, and the fix is
        a container restart."""
        rule = next(
            r
            for r in self_monitoring_rules
            if r.get("alert") == "PrometheusConfigReloadFailed"
        )
        desc = rule["annotations"]["description"].lower()
        assert "bind mount" in desc
        assert "docker restart poindexter-prometheus" in desc


# ---------------------------------------------------------------------------
# OllamaNoModelsLoaded — must not false-fire on a transient /api/tags timeout
# ---------------------------------------------------------------------------


class TestOllamaNoModelsLoadedRule:
    """The 'Ollama up but no models' critical must be guarded against
    unreachability.

    ``metrics_exporter`` zeroes BOTH ``poindexter_ollama_reachable`` AND
    ``poindexter_ollama_model_count`` in one ``except`` branch when its 3s
    ``/api/tags`` scrape errors — which happens transiently under heavy GPU
    render load (wan + image-gen saturating the box). A bare ``model_count == 0``
    expr then pages a CRITICAL "up but no models" even though the truth is
    "Ollama didn't answer a health ping in time" — already covered by the
    static ``PoindexterOllamaDown`` (``reachable == 0``) alert. The
    ``unless poindexter_ollama_reachable == 0`` guard routes timeouts to the
    reachability alert and keeps ONLY the genuine up-but-empty case
    (reachable=1, count=0). Observed as a false critical 2026-06-21 18:21
    during a media render.
    """

    def test_expr_present_and_guards_unreachable(self):
        rule = rb.DEFAULT_RULES["OllamaNoModelsLoaded"]
        expr = rule["expr"]
        # Still fires on genuine up-but-empty...
        assert "poindexter_ollama_model_count == 0" in expr
        # ...but NOT when Ollama is unreachable (timeout → reachable==0).
        assert "unless poindexter_ollama_reachable == 0" in expr
        assert rule["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_guard_renders_into_yaml(self):
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "alert: OllamaNoModelsLoaded" in out
        # The reachability guard survives rendering (it's part of the expr).
        assert "unless poindexter_ollama_reachable == 0" in out


# ---------------------------------------------------------------------------
# Disk absent() guards — DB-rendered rules (poindexter#705)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDiskAbsentGuards:
    """Both disk rules must include an absent() guard so exporter death surfaces
    through the disk alert path in addition to NodeExporterDown.  poindexter#705."""

    async def test_disk_low_includes_absent_guard(self):
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "alert: PoindexterDiskSpaceLow" in out
        low_section = out.split("alert: PoindexterDiskSpaceLow")[1].split("alert:", 1)[0]
        assert "absent(node_filesystem_free_bytes)" in low_section

    async def test_disk_critical_includes_absent_guard(self):
        pool = _FakePool([])
        out = await rb.build_current(pool)
        assert "alert: PoindexterDiskSpaceCritical" in out
        crit_section = out.split("alert: PoindexterDiskSpaceCritical")[1].split("alert:", 1)[0]
        assert "absent(node_filesystem_free_bytes)" in crit_section
