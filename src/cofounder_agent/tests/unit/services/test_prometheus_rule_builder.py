"""Unit tests for ``services.prometheus_rule_builder``.

The renderer has no DB of its own — every test here uses a fake pool
that returns scripted rows, so we can drive edge cases (missing
thresholds, malformed override JSON, disabled rules, unknown alerts)
without spinning up Postgres.
"""

from __future__ import annotations

import json
import re
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
    """Per-container memory ceiling (2026-07-02 observability review): the
    langfuse-clickhouse 15 GB spikes and the cadvisor VM-OOM cascade
    (glad-labs-stack#2019/#2021) had cAdvisor panels but no alert anywhere."""

    def test_rule_and_threshold_are_registered(self):
        assert "PoindexterContainerMemoryHigh" in rb.DEFAULT_RULES
        assert rb.DEFAULT_THRESHOLDS["container_memory_warning_gb"] == "8"

    def test_measures_working_set_not_usage(self):
        """The threshold is a memory-PRESSURE ceiling, so it must read the
        non-reclaimable quantity.

        `container_memory_usage_bytes` includes page cache, which is
        reclaimable and not pressure. Measured 2026-07-26: image-gen-server
        read 10.72 GB usage vs 3.63 GB working set — a 3x overstatement caused
        purely by having streamed model weights off disk into page cache.
        `working_set_bytes` (usage minus inactive file cache) is what the
        kernel OOM killer acts on.
        """
        rule = rb.DEFAULT_RULES["PoindexterContainerMemoryHigh"]
        assert "container_memory_working_set_bytes" in rule["expr"]
        assert "container_memory_usage_bytes" not in rule["expr"]

    def test_model_servers_are_not_excluded(self):
        """image-gen and wan must be COVERED, not excluded.

        They were excluded because `usage_bytes` put image-gen-server
        permanently above the 8 GB threshold (10.72 GB of mostly page cache).
        The exclusion silenced a false page at the cost of real coverage on
        the two containers most likely to genuinely OOM — they're the model
        loaders (cf. poindexter#907 on wan-server's OOM behaviour). On
        working_set they measure 3.63 GB and 0.02 GB, so the exclusion is
        both unnecessary and harmful.
        """
        expr = rb.DEFAULT_RULES["PoindexterContainerMemoryHigh"]["expr"]
        assert "image-gen-server" not in expr
        assert "wan-server" not in expr
        assert 'name=~"poindexter-.*"' in expr

    def test_rule_renders(self):
        rule = rb.DEFAULT_RULES["PoindexterContainerMemoryHigh"]
        out = rb.render_yaml(
            dict(rb.DEFAULT_THRESHOLDS), {"PoindexterContainerMemoryHigh": rule}
        )
        assert "- alert: PoindexterContainerMemoryHigh" in out
        assert "container_memory_working_set_bytes" in out
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
# Mains undervoltage (Glad-Labs/poindexter#924)
# ---------------------------------------------------------------------------


def _band_bounds(section: str) -> tuple[float | None, float | None]:
    """Pull the numeric (lower, upper) bounds out of a rendered rule section.

    Reads the bounds back off the *rendered PromQL* rather than recomputing
    them from the thresholds, so a drift between the expr and the intended
    band fails the sweep below instead of passing on parallel arithmetic.
    """
    import re

    def _val(pattern: str) -> float | None:
        m = re.search(pattern, section)
        if not m:
            return None
        return float(m.group(1)) * float(m.group(2)) / 100

    lower = _val(r">= \((\d+(?:\.\d+)?) \* (\d+(?:\.\d+)?) / 100\)")
    upper = _val(r"< \((\d+(?:\.\d+)?) \* (\d+(?:\.\d+)?) / 100\)")
    return lower, upper


class TestMainsUndervoltage:
    """Undervoltage alerting on ``psu_line_voltage_volts`` (the Shelly
    wall-plug meter — true outlet voltage, not a PSU-internal estimate).

    A brownout kills the host mid-instruction: no journal entry, no MCE, no
    thermal trace. Two such crashes hit the operator rig in five days
    (2026-07-23 16:24 at 93.5V under load; 2026-07-27 17:52 idle), which is
    what these rules exist to pre-empt.
    """

    def test_rules_and_thresholds_registered(self):
        assert "MainsVoltageLow" in rb.DEFAULT_RULES
        assert "MainsVoltageCritical" in rb.DEFAULT_RULES
        # 92% = ANSI C84.1 Range B lower bound; 87% approaches ATX dropout.
        assert rb.DEFAULT_THRESHOLDS["psu_line_voltage_warning_percent"] == "92"
        assert rb.DEFAULT_THRESHOLDS["psu_line_voltage_critical_percent"] == "87"

    def test_ships_inert_because_nominal_voltage_is_regional(self):
        """Mains nominal is 120V or 230V depending on country, so neither rule
        may carry a shipped default — a 230V operator must not inherit a 120V
        operator's threshold. Same precedent as ``expected_gpu_count``."""
        assert rb.DEFAULT_THRESHOLDS["psu_nominal_line_voltage_volts"] == "0"

    @pytest.mark.asyncio
    async def test_default_render_can_never_match(self):
        """With nominal "0" both exprs reduce to ``< 0``, which no sample
        matches. Verified end-to-end rather than by reading the default."""
        out = await rb.build_current(_FakePool([]))
        for name in ("MainsVoltageLow", "MainsVoltageCritical"):
            section = out.split(f"alert: {name}")[1].split("alert:", 1)[0]
            assert "{threshold." not in section
            _lower, upper = _band_bounds(section)
            # Upper bound of 0 => the band is empty for every real reading.
            assert upper == 0.0, f"{name} is not inert by default"

    @pytest.mark.asyncio
    async def test_bands_are_disjoint_so_one_brownout_pages_once(self):
        """Alertmanager's inhibit rule keys on ``equal: [alertname, instance]``,
        so a nested warning/critical pair with different alertnames would page
        twice for a single brownout. The warning band is floored at the
        critical threshold to keep them mutually exclusive."""
        pool = _FakePool([
            {"key": "prometheus.threshold.psu_nominal_line_voltage_volts",
             "value": "120"},
        ])
        out = await rb.build_current(pool)
        warn = out.split("alert: MainsVoltageLow")[1].split("alert:", 1)[0]
        crit = out.split("alert: MainsVoltageCritical")[1].split("alert:", 1)[0]

        w_lo, w_hi = _band_bounds(warn)
        _c_lo, c_hi = _band_bounds(crit)
        assert (w_lo, w_hi) == (104.4, 110.4)
        assert c_hi == 104.4
        # The warning floor IS the critical ceiling — no overlap, no gap.
        assert w_lo == c_hi

        def fires(volts: float) -> set[str]:
            hit = set()
            if w_lo <= volts < w_hi:
                hit.add("warning")
            if 0 < volts < c_hi:
                hit.add("critical")
            return hit

        # Healthy supply: silent. 108.2V was the operator rig's real reading
        # hours after the 2026-07-27 crash — below ANSI spec, so it warns.
        assert fires(120.0) == set()
        assert fires(114.0) == set()
        assert fires(108.2) == {"warning"}
        assert fires(104.4) == {"warning"}
        # 93.5V was measured at the 2026-07-23 hard power-off.
        assert fires(93.5) == {"critical"}
        # Never both, at any voltage.
        for tenth in range(0, 1400):
            assert len(fires(tenth / 10)) <= 1

    @pytest.mark.asyncio
    async def test_zero_reading_does_not_page_critical(self):
        """A meter that reports a literal 0.0 on a failed read must not look
        like the deepest possible brownout. (A genuinely absent Shelly omits
        the series, which yields no samples and is already safe.)"""
        pool = _FakePool([
            {"key": "prometheus.threshold.psu_nominal_line_voltage_volts",
             "value": "120"},
        ])
        out = await rb.build_current(pool)
        crit = out.split("alert: MainsVoltageCritical")[1].split("alert:", 1)[0]
        assert "psu_line_voltage_volts > 0" in crit

    @pytest.mark.asyncio
    async def test_thresholds_port_to_a_230v_region(self):
        """Percent-of-nominal, so an EU operator sets one key and gets
        correct absolute bounds."""
        pool = _FakePool([
            {"key": "prometheus.threshold.psu_nominal_line_voltage_volts",
             "value": "230"},
        ])
        out = await rb.build_current(pool)
        warn = out.split("alert: MainsVoltageLow")[1].split("alert:", 1)[0]
        w_lo, w_hi = _band_bounds(warn)
        assert (round(w_lo, 1), round(w_hi, 1)) == (200.1, 211.6)

    def test_severities_route_correctly(self):
        # Warning → Discord (routine); critical → Telegram (imminent hard cut).
        assert rb.DEFAULT_RULES["MainsVoltageLow"]["severity"] == "warning"
        assert rb.DEFAULT_RULES["MainsVoltageCritical"]["severity"] == "critical"

    def test_pending_windows_are_asymmetric_by_design(self):
        """The warning holds 30m so this host's own GPU jobs — which sag the
        line into the warning band for 5-10min at a time — don't page as if
        they were a supply fault. At the original 5m the band covered 18.6% of
        a 7-day window (~31h/week), which is mute-the-alert territory.

        The critical must NOT inherit that patience: below 87% of nominal a
        hard cut is imminent (the rig lost power at 93.5V on 2026-07-23), so it
        stays at 1m. Widening it would trade the one alert that predicts a
        crash for a quieter board.
        """
        assert rb.DEFAULT_RULES["MainsVoltageLow"]["for"] == "30m"
        assert rb.DEFAULT_RULES["MainsVoltageCritical"]["for"] == "1m"

    def test_no_absent_guard_so_meter_death_doesnt_false_fire(self):
        """A missing Shelly is the wall-power watchdog's job (brain/psu_power
        .py), not this rule's — a bare comparison yields no series on no-data
        (#581)."""
        for name in ("MainsVoltageLow", "MainsVoltageCritical"):
            assert "absent(" not in rb.DEFAULT_RULES[name]["expr"]

    def test_descriptions_separate_the_two_root_causes(self):
        """Sag that tracks our own draw is upstream circuit impedance (an
        electrician); sag that doesn't is utility-side (a UPS with AVR). The
        operator needs to be pointed at the right one."""
        low = rb.DEFAULT_RULES["MainsVoltageLow"]["description"].lower()
        assert "psu_total_power_watts" in low
        assert "ups" in low and "avr" in low
        assert "circuit" in low or "receptacle" in low
        crit = rb.DEFAULT_RULES["MainsVoltageCritical"]["description"].lower()
        assert "shed load" in crit or "pause" in crit


# ---------------------------------------------------------------------------
# UPS via NUT — Glad-Labs/poindexter#958
# ---------------------------------------------------------------------------


_UPS_RULES = (
    "UpsOnBattery",
    "UpsLowBattery",
    "UpsCommsLost",
    "UpsInputVoltageLow",
    "UpsLoadHigh",
    "UpsLoadCritical",
)


class TestPsuInternalsRules:
    """HX1500i internals via the corsair-psu kernel hwmon driver →
    node_exporter (``node_hwmon_*``, job="node").

    Discovered 2026-08-07: the driver had been auto-binding and exporting
    since at least Jul 23 with nothing alerting on it. These rules watch the
    two failure modes the sensors actually predict — a cooking VRM and DC
    rails drifting out of ATX tolerance.
    """

    def test_rules_and_thresholds_registered(self):
        assert "PsuVrmTempHigh" in rb.DEFAULT_RULES
        assert "PsuRailVoltageOutOfBand" in rb.DEFAULT_RULES
        assert rb.DEFAULT_THRESHOLDS["psu_vrm_temp_warning_celsius"] == "85"
        assert rb.DEFAULT_THRESHOLDS["psu_rail_voltage_tolerance_percent"] == "5"

    def test_ships_armed_because_atx_rails_are_not_regional(self):
        """Contrast with the mains pair above: AC nominal is regional (120V vs
        230V) so those ship inert, but DC rails are ATX physical constants
        (12/5/3.3V) and VRM temperature is absolute — shipping these disarmed
        would just be shipping them broken."""
        assert float(rb.DEFAULT_THRESHOLDS["psu_vrm_temp_warning_celsius"]) > 0
        assert float(rb.DEFAULT_THRESHOLDS["psu_rail_voltage_tolerance_percent"]) > 0

    def test_chip_label_is_regex_never_pinned(self):
        """The hwmon chip label embeds the USB path + HID instance and rotates
        every boot (``…_0005`` → ``_0006`` → ``_000c`` observed on the operator
        rig). A pinned label goes permanently silent at the next reboot."""
        for name in ("PsuVrmTempHigh", "PsuRailVoltageOutOfBand"):
            expr = rb.DEFAULT_RULES[name]["expr"]
            assert 'chip=~".*1b1c:1c1f.*"' in expr
            # No literal HID-instance suffix anywhere in the matcher.
            assert not re.search(r'chip="[^"]*_[0-9a-f]{4}"', expr), name

    def test_severities_route_to_discord_not_telegram(self):
        # Both are act-this-week signals, not act-this-minute — routine tier.
        assert rb.DEFAULT_RULES["PsuVrmTempHigh"]["severity"] == "warning"
        assert rb.DEFAULT_RULES["PsuRailVoltageOutOfBand"]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_default_render_is_armed_and_fully_substituted(self):
        out = await rb.build_current(_FakePool([]))
        vrm = out.split("alert: PsuVrmTempHigh")[1].split("alert:", 1)[0]
        rail = out.split("alert: PsuRailVoltageOutOfBand")[1].split("alert:", 1)[0]
        assert "{threshold." not in vrm
        assert "{threshold." not in rail
        assert "> 85" in vrm
        # Each ATX rail is present with the tolerance substituted in. The
        # renderer backslash-escapes embedded quotes in the YAML, so match
        # the escaped form.
        for sensor, nominal in (("in1", "12"), ("in2", "5"), ("in3", "3.3")):
            assert f'sensor=\\"{sensor}\\"' in rail
            assert f"({nominal} * 5 / 100)" in rail

    @pytest.mark.asyncio
    async def test_thresholds_operator_tunable(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.psu_vrm_temp_warning_celsius",
             "value": "70"},
            {"key": "prometheus.threshold.psu_rail_voltage_tolerance_percent",
             "value": "3"},
        ])
        out = await rb.build_current(pool)
        vrm = out.split("alert: PsuVrmTempHigh")[1].split("alert:", 1)[0]
        rail = out.split("alert: PsuRailVoltageOutOfBand")[1].split("alert:", 1)[0]
        assert "> 70" in vrm
        assert "(12 * 3 / 100)" in rail


class TestGpuPowerPinRules:
    """Per-pin 12V-2x6 current alerting on the ASUS Astral IT8915FN data
    (``gpu_12vhpwr_pin_*`` from the gpu-exporter).

    The connector-melt failure mode is current concentrating on a few pins
    while total board power looks fine — so the rules watch per-pin absolutes
    (disjoint warning/critical bands) plus the pin-to-pin spread precursor.
    """

    def test_rules_and_thresholds_registered(self):
        for name in (
            "GpuPowerPinCurrentHigh",
            "GpuPowerPinCurrentCritical",
            "GpuPowerPinImbalance",
        ):
            assert name in rb.DEFAULT_RULES
        assert rb.DEFAULT_THRESHOLDS["gpu_pin_current_warning_amps"] == "8.0"
        # 9.2 = the community vhpwr-guard shutdown line, just under the
        # Micro-Fit+ 9.5A pin rating.
        assert rb.DEFAULT_THRESHOLDS["gpu_pin_current_critical_amps"] == "9.2"
        assert rb.DEFAULT_THRESHOLDS["gpu_pin_imbalance_spread_amps"] == "3"
        assert rb.DEFAULT_THRESHOLDS["gpu_pin_imbalance_min_load_amps"] == "2"

    def test_bands_are_disjoint_so_one_hot_pin_pages_once(self):
        """Same rationale as the MainsVoltage pair: nested bands with
        different alertnames would page twice for one event."""
        warn = rb.DEFAULT_RULES["GpuPowerPinCurrentHigh"]["expr"]
        crit = rb.DEFAULT_RULES["GpuPowerPinCurrentCritical"]["expr"]
        assert "< {threshold.gpu_pin_current_critical_amps}" in warn
        assert ">= {threshold.gpu_pin_current_critical_amps}" in crit

    def test_severities_escalate_discord_to_telegram(self):
        assert rb.DEFAULT_RULES["GpuPowerPinCurrentHigh"]["severity"] == "warning"
        assert (
            rb.DEFAULT_RULES["GpuPowerPinCurrentCritical"]["severity"] == "critical"
        )
        assert rb.DEFAULT_RULES["GpuPowerPinImbalance"]["severity"] == "warning"

    def test_imbalance_is_load_gated(self):
        """At idle the pins carry a few hundred mA and spread math would be
        noise — the rule must only judge balance under real load."""
        expr = rb.DEFAULT_RULES["GpuPowerPinImbalance"]["expr"]
        assert "avg(gpu_12vhpwr_pin_current_amps)" in expr
        assert "{threshold.gpu_pin_imbalance_min_load_amps}" in expr

    @pytest.mark.asyncio
    async def test_default_render_is_armed_and_fully_substituted(self):
        out = await rb.build_current(_FakePool([]))
        for name in (
            "GpuPowerPinCurrentHigh",
            "GpuPowerPinCurrentCritical",
            "GpuPowerPinImbalance",
        ):
            section = out.split(f"alert: {name}")[1].split("alert:", 1)[0]
            assert "{threshold." not in section
        warn = out.split("alert: GpuPowerPinCurrentHigh")[1].split("alert:", 1)[0]
        crit = out.split("alert: GpuPowerPinCurrentCritical")[1].split("alert:", 1)[0]
        imb = out.split("alert: GpuPowerPinImbalance")[1].split("alert:", 1)[0]
        assert ">= 8.0" in warn and "< 9.2" in warn
        assert ">= 9.2" in crit
        assert "> 3" in imb and "> 2" in imb

    @pytest.mark.asyncio
    async def test_thresholds_operator_tunable(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.gpu_pin_current_critical_amps",
             "value": "8.8"},
        ])
        out = await rb.build_current(pool)
        crit = out.split("alert: GpuPowerPinCurrentCritical")[1].split("alert:", 1)[0]
        assert ">= 8.8" in crit


class TestUpsRules:
    """UPS alerting on the ``network_ups_tools_*`` series (NUT nut-exporter,
    job="nut").

    Anchored on the 2026-07-31 operator incident: a 1000W-rated line-
    interactive UPS transferred to battery during a sag while its single
    load — the PC — was bursting past 90% of the rating (840→910W sustained,
    ~1008W burst peak), and the inverter overload-tripped its output — the
    fourth utility power cut in five days on a house line that never
    exceeded 114.6V against a 120V nominal.
    """

    def test_rules_and_thresholds_registered(self):
        for name in _UPS_RULES:
            assert name in rb.DEFAULT_RULES, name
        assert rb.DEFAULT_THRESHOLDS["ups_load_warning_percent"] == "85"
        assert rb.DEFAULT_THRESHOLDS["ups_load_critical_percent"] == "95"
        assert rb.DEFAULT_THRESHOLDS["ups_battery_charge_critical_percent"] == "50"

    def test_hardware_specific_keys_ship_inert(self):
        """A host with no UPS must never page: expected-count ships "0" (the
        ``expected_gpu_count`` precedent) and the undervoltage line ships "0"
        because mains nominal is regional (the ``psu_nominal_line_voltage_
        volts`` precedent). The load/battery rules need no such guard — they
        read series that simply don't exist without a UPS."""
        assert rb.DEFAULT_THRESHOLDS["expected_ups_count"] == "0"
        assert rb.DEFAULT_THRESHOLDS["ups_input_voltage_low_volts"] == "0"

    @pytest.mark.asyncio
    async def test_default_render_is_inert_for_ups_less_hosts(self):
        """End-to-end: with shipped defaults, UpsCommsLost renders ``< 0``
        (count() can never be negative) and UpsInputVoltageLow renders
        ``< 0`` (no voltage sample matches)."""
        out = await rb.build_current(_FakePool([]))
        comms = out.split("alert: UpsCommsLost")[1].split("alert:", 1)[0]
        assert "{threshold." not in comms
        assert "< 0" in comms
        volt = out.split("alert: UpsInputVoltageLow")[1].split("alert:", 1)[0]
        assert "{threshold." not in volt
        assert "< 0" in volt

    @pytest.mark.asyncio
    async def test_operator_render_arms_the_gated_rules(self):
        """One key each arms them: expected_ups_count=1, input floor=114 (the
        ANSI C84.1 Range A lower bound on a 120V service, and the ceiling the
        operator house never exceeded across 9 metered days)."""
        pool = _FakePool([
            {"key": "prometheus.threshold.expected_ups_count", "value": "1"},
            {"key": "prometheus.threshold.ups_input_voltage_low_volts",
             "value": "114"},
        ])
        out = await rb.build_current(pool)
        comms = out.split("alert: UpsCommsLost")[1].split("alert:", 1)[0]
        assert "or vector(0)) < 1" in comms
        volt = out.split("alert: UpsInputVoltageLow")[1].split("alert:", 1)[0]
        assert "< 114" in volt

    def test_comms_lost_counts_against_absence(self):
        """count() yields NO sample when the series family vanishes — the
        ``or vector(0)`` is what turns total absence into a firing zero
        instead of a silent empty result."""
        expr = rb.DEFAULT_RULES["UpsCommsLost"]["expr"]
        assert "count(" in expr
        assert "or vector(0)" in expr
        # Absence IS the signal: bridging would delay it. for: 10m rides out
        # exporter recreates instead (the GpuCountBelowExpected rationale).
        assert "_over_time" not in expr
        assert rb.DEFAULT_RULES["UpsCommsLost"]["for"] == "10m"

    def test_low_battery_only_fires_while_discharging(self):
        """A post-outage recharge sits below any sane floor for a while ON
        LINE — the OB gate is what keeps that from paging. ``on(ups)`` because
        only the status series carries the ``flag`` label."""
        expr = rb.DEFAULT_RULES["UpsLowBattery"]["expr"]
        assert 'flag="OB"' in expr
        assert "and on(ups)" in expr

    def test_undervoltage_advisory_ignores_outages(self):
        """On battery the UPS reports input.voltage 0 — that is UpsOnBattery's
        story, and without the ``> 0`` guard every outage would drag the
        advisory in with it."""
        expr = rb.DEFAULT_RULES["UpsInputVoltageLow"]["expr"]
        assert "> 0" in expr

    def test_load_bands_are_disjoint_so_one_overload_pages_once(self):
        """Same inhibit-rule reasoning as MainsVoltage*: warning covers
        [85, 95), critical [95, ∞) — escalation, never a double page."""
        warn = rb.DEFAULT_RULES["UpsLoadHigh"]["expr"]
        assert ">= {threshold.ups_load_warning_percent}" in warn
        assert "< {threshold.ups_load_critical_percent}" in warn
        crit = rb.DEFAULT_RULES["UpsLoadCritical"]["expr"]
        assert ">= {threshold.ups_load_critical_percent}" in crit
        assert "ups_load_warning_percent" not in crit

    def test_severities_route_correctly(self):
        """Critical → Telegram (act now), warning → Discord (advisory) per
        the brain dispatcher's severity matrix. On-battery, low-battery,
        comms-lost and load-critical are all page-worthy; chronic
        undervoltage and the load warning band are watch-items."""
        expected = {
            "UpsOnBattery": "critical",
            "UpsLowBattery": "critical",
            "UpsCommsLost": "critical",
            "UpsInputVoltageLow": "warning",
            "UpsLoadHigh": "warning",
            "UpsLoadCritical": "critical",
        }
        for name, severity in expected.items():
            assert rb.DEFAULT_RULES[name]["severity"] == severity, name

    def test_incident_rules_bridge_scrape_holes_with_last_not_max(self):
        """Unlike the raw-read Shelly rules (independent exporter, long
        ``for:``), the short-``for:`` UPS rules bridge with last_over_time:
        their firing window IS the outage, exactly when containers restart,
        and a one-scrape hole on a raw read would send a false "resolved"
        mid-incident. ``last`` so recovery propagates with the first fresh
        sample; ``max`` would hold OB=1 for the whole lookback after power
        returned."""
        for name in ("UpsOnBattery", "UpsLowBattery", "UpsInputVoltageLow",
                     "UpsLoadHigh", "UpsLoadCritical"):
            expr = rb.DEFAULT_RULES[name]["expr"]
            assert "last_over_time(" in expr, name
            assert "max_over_time" not in expr, name

    def test_pending_windows_are_asymmetric_by_design(self):
        """1m to page on a real transfer (sub-minute sag transfers are the
        UPS doing its job); 5m before the load warning (GPU bursts visit the
        80s legitimately); 1m at ≥95% where the next burst trips the
        inverter; 30m for the chronic-undervoltage advisory (the condition
        worth flagging runs for hours)."""
        assert rb.DEFAULT_RULES["UpsOnBattery"]["for"] == "1m"
        assert rb.DEFAULT_RULES["UpsLowBattery"]["for"] == "1m"
        assert rb.DEFAULT_RULES["UpsLoadHigh"]["for"] == "5m"
        assert rb.DEFAULT_RULES["UpsLoadCritical"]["for"] == "1m"
        assert rb.DEFAULT_RULES["UpsInputVoltageLow"]["for"] == "30m"


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
        # VRAM is a CAPACITY question, so it reads used/total — not
        # nvidia_gpu_memory_utilization_percent, which is bandwidth
        # (poindexter#920). See TestThresholdAxisCoherence.
        assert "nvidia_gpu_memory_used_mib" in out
        assert "nvidia_gpu_memory_total_mib" in out
        # Defaults substituted: 85°C thermal, 95% VRAM capacity.
        assert "nvidia_gpu_temperature_celsius > 85" in out
        assert "nvidia_gpu_memory_total_mib > 95" in out

    async def test_gpu_thresholds_overridable(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.gpu_temperature_celsius", "value": "80"},
            {"key": "prometheus.threshold.gpu_vram_utilization_percent", "value": "90"},
        ])
        out = await rb.build_current(pool)
        assert "nvidia_gpu_temperature_celsius > 80" in out
        assert "nvidia_gpu_memory_total_mib > 90" in out


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
        independent = {
            "PoindexterContainerMemoryHigh",
            # psu_line_voltage_volts is scraped from gpu-exporter (job
            # "nvidia-smi") — named in the comment above as one of the
            # exporters that survives a worker deploy.
            "MainsVoltageLow",
            # node_systemd_timer_last_trigger_seconds comes from node_exporter
            # on the HOST (job="node", host.docker.internal:9100) — the third
            # exporter named above. It has no relationship to the worker
            # container, so a deploy restart cannot blank the series and the
            # 30m pending clock is safe. Bridging with last_over_time here
            # would be cargo-cult: it would imply a restart hazard that does
            # not exist for host-side series.
            "PoindexterSystemdTimerStale",
            # node_memory_* is the same host-side node_exporter as the timer
            # rule above — a worker deploy cannot blank it, so its 2h pending
            # clock is safe on a raw read (poindexter#1021 swap alert).
            "PoindexterHostSwapExhausted",
        }
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


class TestThresholdAxisCoherence:
    """Every threshold comparison must measure the quantity its threshold names.

    The 2026-07-26 sweep found four instances of one bug class — *a quantity
    compared against the wrong thing's limit* — across cost and infra alerting:

    - `cost_guard._emit_soft_alert` measured the TOTAL axis against the
      API-only `daily_spend_limit_usd` (poindexter#912).
    - `MonthlySpendHigh`'s threshold was set on the wrong scale.
    - `GpuVramHigh` compared a *bandwidth* metric to a *capacity* threshold
      (poindexter#920).
    - `PoindexterContainerMemoryHigh` compared page-cache-inclusive usage to a
      threshold documented as RSS.

    These are cheap to reintroduce and expensive to notice, because the alert
    keeps evaluating and just answers a different question than its name. These
    tests pin the specific pairings that were wrong.
    """

    def test_vram_rule_measures_capacity_not_bandwidth(self):
        """`nvidia_gpu_memory_utilization_percent` is nvidia-smi's
        `utilization.memory` — percent of TIME memory was read/written, i.e.
        bandwidth. A `> 95` capacity threshold on it can never fire: measured
        2026-07-26, a 3090 at 83.3% FULL reported 0.
        """
        expr = rb.DEFAULT_RULES["GpuVramHigh"]["expr"]
        assert "nvidia_gpu_memory_used_mib" in expr
        assert "nvidia_gpu_memory_total_mib" in expr
        assert "nvidia_gpu_memory_utilization_percent" not in expr

    def test_vram_rule_yields_a_percentage(self):
        """The threshold is a percent (default 95), so the expr must scale the
        used/total ratio by 100 — otherwise it compares a 0..1 ratio to 95 and
        silently never fires, which is the same defect in a new costume."""
        expr = rb.DEFAULT_RULES["GpuVramHigh"]["expr"]
        assert expr.startswith("100 *")

    def test_gb_thresholds_are_compared_against_gb(self):
        """Byte-valued metrics carry a `*_gb` threshold, so each such expr must
        divide by 1024^3 exactly once. A missing divisor compares bytes to a
        single-digit GB number and fires permanently."""
        for name in (
            "PoindexterContainerMemoryHigh",
            "PoindexterHostMemoryLow",
            "PoindexterBrainDbSizeWarning",
        ):
            expr = rb.DEFAULT_RULES[name]["expr"]
            assert "_bytes" in expr, name
            assert expr.count("(1024*1024*1024)") == 1, name

    def test_spend_rules_name_the_axis_they_measure(self):
        """`poindexter_daily_spend_usd` / `poindexter_monthly_spend_usd` are the
        TOTAL axis (paid API + measured electricity). Their descriptions must
        say so, and must NOT send the operator to `daily_spend_limit_usd` — the
        API-only hard cap cost_guard enforces, which is not what fired. That
        misdirection is what produced the July mixed-axis spend scare.
        """
        for name in (
            "DailySpendApproachingLimit",
            "DailySpendOverBudget",
        ):
            rule = rb.DEFAULT_RULES[name]
            text = f"{rule['summary']} {rule['description']}"
            assert "TOTAL" in text, name
            assert "electricity" in text.lower(), name

    def test_daily_spend_critical_does_not_claim_cost_guard_failed(self):
        """It used to read 'Pipeline should have stopped itself but didn't.'
        cost_guard gates the API axis against $2 and never gates this total-axis
        quantity, so that sentence reported an enforcement bug that cannot
        exist — and pointed triage at the wrong setting."""
        desc = rb.DEFAULT_RULES["DailySpendOverBudget"]["description"]
        assert "should have stopped itself" not in desc
        assert "cost_throttle_daily_budget_usd" in desc


# ---------------------------------------------------------------------------
# Host systemd units — the probe_scheduled_tasks replacement
# ---------------------------------------------------------------------------


_SYSTEMD_RULES = ("PoindexterSystemdUnitFailed", "PoindexterSystemdTimerStale")


class TestSystemdUnitRules:
    """Alerting on node_exporter's systemd collector (``job="node"``).

    These replace the retired ``probe_scheduled_tasks``, which asked the host
    Recovery Agent to enumerate WINDOWS Task Scheduler entries — a surface that
    stopped existing at the Pop!_OS migration. The replacement is deliberately
    NOT another probe: node_exporter's systemd collector was already enabled and
    scraping, so the old design's premise (the brain must ask something else to
    see the host scheduler) was already obsolete.
    """

    def test_rules_and_threshold_registered(self):
        for name in _SYSTEMD_RULES:
            assert name in rb.DEFAULT_RULES, name
        # 10 days — must clear the SLOWEST declared cadence (triage-sweep is
        # weekly, Mon 07:00) or the weekly timers page every single week.
        assert rb.DEFAULT_THRESHOLDS["systemd_timer_stale_seconds"] == "864000"
        assert int(rb.DEFAULT_THRESHOLDS["systemd_timer_stale_seconds"]) > 7 * 86400

    def test_rules_scope_to_poindexter_units(self):
        """Both must match `poindexter.*` and nothing wider. An unscoped match
        would page on every failed unit on the host — a user's own systemd
        services are not this system's business, and the noise would bury the
        signal these exist for."""
        for name in _SYSTEMD_RULES:
            expr = rb.DEFAULT_RULES[name]["expr"]
            assert 'name=~"poindexter.*"' in expr, name

    def test_no_expected_count_threshold_needed(self):
        """A host without these units has no matching series, so both rules are
        naturally inert — unlike the count-vs-expected rules (GPU, UPS) which
        need a "0" default to stay quiet. Encoded as: neither expr uses count().
        """
        for name in _SYSTEMD_RULES:
            assert "count(" not in rb.DEFAULT_RULES[name]["expr"], name

    def test_failed_rule_reads_state_exactly(self):
        """node_exporter emits one series per (unit, state) with 1 on the state
        the unit is currently in, so this is an exact read, not a threshold."""
        expr = rb.DEFAULT_RULES["PoindexterSystemdUnitFailed"]["expr"]
        assert 'state="failed"' in expr
        assert "== 1" in expr
        assert "{threshold." not in expr

    def test_timer_rule_guards_against_never_fired(self):
        """node_exporter reports 0 for a timer that has not fired since boot.
        Without the `> 0` guard, `time() - 0` is a ~57-year age that fires
        instantly on every reboot — a self-inflicted page storm."""
        expr = rb.DEFAULT_RULES["PoindexterSystemdTimerStale"]["expr"]
        assert "> 0" in expr
        assert "time() -" in expr

    def test_severity_is_warning_not_critical(self):
        """No user-facing surface is down when a host unit fails — the content
        pipeline runs in Docker, independent of these. Paging critical for a
        failed weekly lint session is how an alert channel gets muted."""
        for name in _SYSTEMD_RULES:
            assert rb.DEFAULT_RULES[name]["severity"] == "warning", name

    def test_stale_rule_admits_it_cannot_do_per_cadence(self):
        """The single threshold cannot express per-cadence freshness — the
        timers span every-10-minutes to weekly and node_exporter exposes no
        next-elapse series. The description must SAY a daily timer going quiet
        is not caught, so nobody reads this as coverage it does not provide."""
        desc = rb.DEFAULT_RULES["PoindexterSystemdTimerStale"]["description"]
        assert "DAILY" in desc or "daily" in desc
        assert "next-elapse" in desc

    def test_descriptions_route_triage_to_the_host(self):
        """These units live on the host, not in a container — triage commands
        must be host-side or the operator starts in the wrong place."""
        failed = rb.DEFAULT_RULES["PoindexterSystemdUnitFailed"]["description"]
        assert "systemctl status" in failed
        assert "journalctl" in failed
        stale = rb.DEFAULT_RULES["PoindexterSystemdTimerStale"]["description"]
        assert "systemctl list-timers" in stale

    @pytest.mark.asyncio
    async def test_default_render_substitutes_the_threshold(self):
        out = await rb.build_current(_FakePool([]))
        block = out.split("alert: PoindexterSystemdTimerStale")[1].split("alert:", 1)[0]
        assert "{threshold." not in block
        assert "864000" in block

    @pytest.mark.asyncio
    async def test_operator_can_tighten_the_stale_window(self):
        pool = _FakePool([
            {"key": "prometheus.threshold.systemd_timer_stale_seconds", "value": "172800"},
        ])
        out = await rb.build_current(pool)
        block = out.split("alert: PoindexterSystemdTimerStale")[1].split("alert:", 1)[0]
        assert "> 172800" in block


class TestCpuTemperatureRules:
    """CPU package thermal alerting (node_exporter hwmon).

    Until these landed, GPU temperature paged but CPU temperature had no
    alert path on EITHER source — a dead pump or a dust-blocked tower was a
    silent failure. The pair is deliberately split by what each can detect:
    ``CpuTemperatureHigh`` is acute (cooling stopped working *now*),
    ``CpuTemperatureBaselineDrift`` is chronic (cooling is degrading).
    """

    def test_rules_and_thresholds_registered(self):
        for name in ("CpuTemperatureHigh", "CpuTemperatureBaselineDrift"):
            assert name in rb.DEFAULT_RULES
        assert rb.DEFAULT_THRESHOLDS["cpu_temperature_celsius"] == "88"
        assert rb.DEFAULT_THRESHOLDS["cpu_temperature_baseline_celsius"] == "72"

    def test_baseline_threshold_sits_below_the_acute_one(self):
        """The drift rule is an EARLY signal — a baseline at or above the
        acute line would mean the chronic alert never fires first, defeating
        the point of having two."""
        acute = float(rb.DEFAULT_THRESHOLDS["cpu_temperature_celsius"])
        baseline = float(rb.DEFAULT_THRESHOLDS["cpu_temperature_baseline_celsius"])
        assert baseline < acute

    @pytest.mark.parametrize(
        "name", ["CpuTemperatureHigh", "CpuTemperatureBaselineDrift"]
    )
    def test_sensor_selected_by_label_never_by_chip(self, name):
        """THE load-bearing invariant. node_exporter's ``chip`` label is an
        enumeration path that can rotate across boots (the corsair-psu
        precedent), so pinning a literal chip is a reboot away from silently
        matching nothing — a monitoring rule that cannot fire. Selection must
        go through node_hwmon_sensor_label and filter on the LABEL.
        """
        expr = rb.DEFAULT_RULES[name]["expr"]
        assert "node_hwmon_sensor_label" in expr
        assert "on(chip,sensor) group_left(label)" in expr
        assert 'label=~"Tctl|Package id 0"' in expr
        # No hardcoded chip path (the enumerated form always contains "0000:").
        assert 'chip="' not in expr

    @pytest.mark.parametrize(
        "name", ["CpuTemperatureHigh", "CpuTemperatureBaselineDrift"]
    )
    def test_covers_amd_and_intel_package_sensors(self, name):
        """Tctl is k10temp (AMD), "Package id 0" is coretemp (Intel). One rule
        covers either host; a host with neither yields no series and the rule
        is naturally inert rather than firing on no-data."""
        expr = rb.DEFAULT_RULES[name]["expr"]
        assert "Tctl" in expr
        assert "Package id 0" in expr

    def test_acute_rule_rides_out_boost_spikes(self):
        """Brief excursions to Tjmax are how modern boost works — measured at
        0.19% of the time (~2.7 min/day) on the operator rig. A short `for:`
        would page on every CI burst, so the long window IS the signal."""
        rule = rb.DEFAULT_RULES["CpuTemperatureHigh"]
        assert rule["for"] == "10m"
        assert rule["severity"] == "critical"

    def test_drift_rule_watches_the_median_not_the_peak(self):
        """Peaks are clamped by the CPU itself and carry almost no information
        about cooler health; the FLOOR is what rises as cooling degrades. Using
        max_over_time/avg_over_time here would silently measure the wrong thing.
        """
        expr = rb.DEFAULT_RULES["CpuTemperatureBaselineDrift"]["expr"]
        assert "quantile_over_time(0.5," in expr
        assert "[24h:5m]" in expr
        assert "max_over_time" not in expr
        assert "avg_over_time" not in expr

    def test_severities_route_acute_to_telegram_chronic_to_discord(self):
        assert rb.DEFAULT_RULES["CpuTemperatureHigh"]["severity"] == "critical"
        assert (
            rb.DEFAULT_RULES["CpuTemperatureBaselineDrift"]["severity"] == "warning"
        )

    def test_drift_rule_is_evaluated_slowly(self):
        """A 24h-windowed statistic cannot move meaningfully in 30s; evaluating
        the subquery that often is pure waste."""
        assert rb.DEFAULT_RULES["CpuTemperatureBaselineDrift"]["interval"] == "5m"

    @pytest.mark.asyncio
    async def test_default_render_is_armed_and_fully_substituted(self):
        out = await rb.build_current(_FakePool([]))
        for name, expected in (
            ("CpuTemperatureHigh", "> 88"),
            ("CpuTemperatureBaselineDrift", "> 72"),
        ):
            section = out.split(f"alert: {name}")[1].split("alert:", 1)[0]
            assert "{threshold." not in section
            assert expected in section

    @pytest.mark.asyncio
    async def test_intel_operator_can_raise_the_acute_line(self):
        """Intel desktop parts run to a ~100C Tjmax and can legitimately sit
        near 90 under sustained all-core load, so 88 must be tunable rather
        than baked in."""
        pool = _FakePool([
            {"key": "prometheus.threshold.cpu_temperature_celsius", "value": "95"},
        ])
        out = await rb.build_current(pool)
        block = out.split("alert: CpuTemperatureHigh")[1].split("alert:", 1)[0]
        assert "> 95" in block


# ---------------------------------------------------------------------------
# PoindexterHostSwapExhausted (poindexter#1021 — the 2026-08-24 OOM/thrash ran
# 24h+ with swap at zero free while MemAvailable looked fine, so neither
# existing host-memory rule fired early)
# ---------------------------------------------------------------------------

class TestHostSwapExhaustedRule:
    """The chronic third leg of the host-memory family: MemoryLow watches
    RAM headroom, Thrashing watches acute PSI stall, this one watches the
    swap file quietly filling with dormant model servers days ahead."""

    def test_is_a_sustained_warning_not_a_page(self):
        rule = rb.DEFAULT_RULES["PoindexterHostSwapExhausted"]
        assert rule["severity"] == "warning"
        assert (
            "{threshold.host_memory_swap_free_warning_percent}" in rule["expr"]
        )

    def test_averages_the_window_so_blips_cannot_reset_the_clock(self):
        """2026-08-27 regression: the rule read the RAW gauge with `for: 2h`.

        A thrashing host reclaims in bursts, so swap-free rattles above the
        threshold every few minutes and each blip restarts the pending
        clock. Measured on that incident's own series, the longest unbroken
        sub-5% run was 71 min — the rule sat `pending` for 8h15m and never
        fired while the box froze hard. Averaging the window first is what
        makes the condition reachable, so it is the assertion, not the
        `for:` duration.
        """
        rule = rb.DEFAULT_RULES["PoindexterHostSwapExhausted"]
        assert "avg_over_time(node_memory_SwapFree_bytes[2h])" in rule["expr"]
        # A bare SwapFree read outside the average would reintroduce the bug.
        expr_without_avg = rule["expr"].replace(
            "avg_over_time(node_memory_SwapFree_bytes[2h])", ""
        )
        assert "node_memory_SwapFree_bytes" not in expr_without_avg

    def test_pending_clock_stays_shorter_than_the_average_window(self):
        """Detection latency is window + `for:`, not `for:` alone.

        Swap hitting 0% takes ~1.9h to drag a 2h mean under threshold, so a
        second 2h here would push detection past 4h — slower than the rule
        it replaced, which is how this regresses quietly.
        """
        rule = rb.DEFAULT_RULES["PoindexterHostSwapExhausted"]
        assert rule["for"] == "30m"

    def test_no_swap_host_never_fires(self):
        """SwapTotal=0 → NaN division (dropped by the comparison); the
        explicit conjunct documents that a swapless OSS install stays
        silent by design."""
        rule = rb.DEFAULT_RULES["PoindexterHostSwapExhausted"]
        assert "node_memory_SwapTotal_bytes > 0" in rule["expr"]

    def test_threshold_is_seeded_and_tunable(self):
        assert (
            rb.DEFAULT_THRESHOLDS["host_memory_swap_free_warning_percent"]
            == "5"
        )

    @pytest.mark.asyncio
    async def test_renders_with_defaults_substituted(self):
        out = await rb.build_current(_FakePool([]))
        section = out.split("alert: PoindexterHostSwapExhausted")[1].split(
            "alert:", 1
        )[0]
        assert "{threshold." not in section
        assert "< 5" in section
