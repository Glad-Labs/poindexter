"""Unit tests for ``services.grafana_alert_builder``.

No DB required — every test uses a fake pool that returns scripted rows.
"""

from __future__ import annotations

import textwrap
from typing import Any

import pytest

from services import grafana_alert_builder as ab


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    async def fetch(self, _query: str, _arg: str) -> list[dict[str, Any]]:
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
            "INTERVAL '{threshold.stale_task_hours} hours' AND val > {threshold.db_size_warning_gb}",
            {"stale_task_hours": "3", "db_size_warning_gb": "10"},
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
        assert thresholds["db_size_warning_gb"] == "5"
        assert thresholds["gpu_temperature_celsius"] == "85"

    @pytest.mark.asyncio
    async def test_db_row_overrides_default(self):
        pool = _FakePool([
            {"key": "grafana.threshold.db_size_warning_gb", "value": "20"},
        ])
        thresholds = await ab.load_thresholds(pool)
        assert thresholds["db_size_warning_gb"] == "20"
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
        tmpl.write_text("threshold: {threshold.db_size_warning_gb}", encoding="utf-8")
        pool = _FakePool([
            {"key": "grafana.threshold.db_size_warning_gb", "value": "50"},
        ])
        rendered = await ab.build_current(pool, tmpl)
        assert "threshold: 50" in rendered
