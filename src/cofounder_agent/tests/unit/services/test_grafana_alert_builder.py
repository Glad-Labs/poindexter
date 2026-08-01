"""Unit tests for ``services.grafana_alert_builder``.

No DB required — every test uses a fake pool that returns scripted rows.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from services import grafana_alert_builder as ab


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    async def fetch(self, _query: str, _arg: str) -> list[dict[str, Any]]:  # noqa: ARG002
        prefix = _arg.rstrip("%").replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\")
        return [r for r in self._rows if r["key"].startswith(prefix)]


# ---------------------------------------------------------------------------
# _substitute_thresholds
# ---------------------------------------------------------------------------


class TestSubstituteThresholds:
    def test_substitutes_single_token(self):
        out = ab._substitute_thresholds(
            "count(*) > {threshold.error_rate_hourly_max}",
            {"error_rate_hourly_max": "5"},
        )
        assert out == "count(*) > 5"

    def test_substitutes_multiple_tokens(self):
        out = ab._substitute_thresholds(
            "INTERVAL '{threshold.stale_task_hours} hours' AND val > {threshold.embedding_lag_hours}",
            {"stale_task_hours": "3", "embedding_lag_hours": "10"},
        )
        assert "INTERVAL '3 hours'" in out
        assert "val > 10" in out

    def test_leaves_unknown_placeholder(self):
        out = ab._substitute_thresholds(
            "val > {threshold.unknown_key}",
            {"other_key": "99"},
        )
        assert "{threshold.unknown_key}" in out

    def test_does_not_mangle_grafana_template_vars(self):
        text = "current: {{ $values.B.Value }}"
        assert ab._substitute_thresholds(text, {"values": "hacked"}) == text


# ---------------------------------------------------------------------------
# load_thresholds
# ---------------------------------------------------------------------------


class TestLoadThresholds:
    @pytest.mark.asyncio
    async def test_defaults_when_no_db_rows(self):
        pool = _FakePool([])
        thresholds = await ab.load_thresholds(pool)
        assert thresholds["error_rate_hourly_max"] == "5"
        # gpu_temperature_celsius and gpu_metrics_stale_minutes were removed
        # (poindexter#653) — GPU temp now alerts via Prometheus GpuTemperatureHigh.
        # db_size_warning_gb was removed (poindexter#735 item 2) — DB size now
        # alerts via the native Prometheus rule PoindexterBrainDbSizeWarning.
        assert "gpu_temperature_celsius" not in thresholds
        assert "gpu_metrics_stale_minutes" not in thresholds
        assert "db_size_warning_gb" not in thresholds

    @pytest.mark.asyncio
    async def test_db_row_overrides_default(self):
        pool = _FakePool([
            {"key": "grafana.threshold.stale_task_hours", "value": "20"},
        ])
        thresholds = await ab.load_thresholds(pool)
        assert thresholds["stale_task_hours"] == "20"
        # Other defaults unchanged
        assert thresholds["error_rate_hourly_max"] == "5"

    @pytest.mark.asyncio
    async def test_whitespace_stripped_from_db_value(self):
        pool = _FakePool([
            {"key": "grafana.threshold.stale_task_hours", "value": "  4  "},
        ])
        thresholds = await ab.load_thresholds(pool)
        assert thresholds["stale_task_hours"] == "4"

    @pytest.mark.asyncio
    async def test_none_value_ignored(self):
        pool = _FakePool([
            {"key": "grafana.threshold.embedding_lag_hours", "value": None},
        ])
        thresholds = await ab.load_thresholds(pool)
        assert thresholds["embedding_lag_hours"] == "6"  # default


# ---------------------------------------------------------------------------
# build_current
# ---------------------------------------------------------------------------


class TestBuildCurrent:
    @pytest.mark.asyncio
    async def test_substitutes_into_template(self, tmp_path):
        tmpl = tmp_path / "alert-rules.yml.tmpl"
        tmpl.write_text(
            textwrap.dedent("""\
                rawSql: "count(*) > {threshold.error_rate_hourly_max}"
                interval: "{threshold.error_rate_window_hours} hours"
            """),
            encoding="utf-8",
        )
        pool = _FakePool([])  # use defaults
        rendered = await ab.build_current(pool, tmpl)
        assert "count(*) > 5" in rendered
        assert '"1 hours"' in rendered

    @pytest.mark.asyncio
    async def test_db_override_reflected_in_output(self, tmp_path):
        tmpl = tmp_path / "t.tmpl"
        tmpl.write_text("threshold: {threshold.stale_task_hours}", encoding="utf-8")
        pool = _FakePool([
            {"key": "grafana.threshold.stale_task_hours", "value": "50"},
        ])
        rendered = await ab.build_current(pool, tmpl)
        assert "threshold: 50" in rendered


# ---------------------------------------------------------------------------
# Real-template coverage: every {threshold.X} in the shipped tmpl must have a
# code default. Unknown placeholders pass through literally by design
# (test_leaves_unknown_placeholder), which inside rawSql means silently
# broken SQL — this pins the failure to CI instead.
# ---------------------------------------------------------------------------


def _find_repo_tmpl() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "infrastructure"
            / "grafana"
            / "provisioning"
            / "alerting"
            / "alert-rules.yml.tmpl"
        )
        if cand.exists():
            return cand
    raise RuntimeError("alert-rules.yml.tmpl not found walking up from test")


def test_real_template_placeholders_all_have_defaults():
    import re

    tmpl = _find_repo_tmpl().read_text(encoding="utf-8")
    tokens = set(re.findall(r"\{threshold\.([a-z0-9_]+)\}", tmpl))
    assert tokens, "template has no {threshold.*} tokens — anchor drifted?"
    missing = tokens - set(ab.DEFAULT_GRAFANA_THRESHOLDS)
    assert not missing, (
        f"template placeholders without a DEFAULT_GRAFANA_THRESHOLDS entry: "
        f"{sorted(missing)} — they would render as literal '{{threshold.…}}' "
        f"inside rawSql"
    )


def test_traffic_volume_floor_default_is_numeric():
    """The floor lands in SQL arithmetic — a non-numeric override would break
    the query at eval time, so at least the shipped default must parse."""
    assert float(ab.DEFAULT_GRAFANA_THRESHOLDS["traffic_min_daily_views"]) > 0
