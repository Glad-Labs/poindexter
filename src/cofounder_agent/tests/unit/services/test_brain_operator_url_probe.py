"""Unit tests for brain/operator_url_probe.py (GH#214).

Covers dashboard JSON link extraction, app_settings URL collection, tailscale
drift detection, the HTTP probe entry point, and the maybe_run interval gate.
All external I/O (httpx, asyncpg, subprocess) is mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brain import operator_url_probe as oup


def _make_pool(rows=None):
    """Build a mock asyncpg pool whose fetch/fetchrow return canned rows."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture(autouse=True)
def _reset_module_state():
    oup._last_run_ts = 0.0
    yield
    oup._last_run_ts = 0.0


# ---------------------------------------------------------------------------
# Dashboard link extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractDashboardLinks:
    def test_pulls_dashboard_level_links(self, tmp_path: Path):
        dash = {
            "title": "Mission Control",
            "links": [
                {"title": "Prefect UI", "url": "http://100.81.93.12:4200"},
                {"title": "Grafana", "url": "http://localhost:3000"},
            ],
            "panels": [],
        }
        (tmp_path / "mission.json").write_text(json.dumps(dash))
        out = oup.extract_dashboard_links(tmp_path)
        urls = {item["url"] for item in out}
        assert "http://100.81.93.12:4200" in urls
        assert "http://localhost:3000" in urls
        # Surface includes dashboard title + link title.
        assert any("Mission Control :: Prefect UI" == i["surface"] for i in out)

    def test_pulls_panel_level_links(self, tmp_path: Path):
        dash = {
            "title": "D1",
            "panels": [
                {
                    "title": "API Stats",
                    "links": [{"title": "Health", "url": "http://api/health"}],
                }
            ],
        }
        (tmp_path / "d1.json").write_text(json.dumps(dash))
        out = oup.extract_dashboard_links(tmp_path)
        assert any(i["url"] == "http://api/health" for i in out)
        # Surface should reference the panel title.
        assert any("API Stats" in i["surface"] for i in out)

    def test_pulls_data_links(self, tmp_path: Path):
        dash = {
            "title": "D2",
            "panels": [
                {
                    "title": "P",
                    "fieldConfig": {
                        "defaults": {
                            "links": [{"title": "drill", "url": "http://drill.local"}]
                        }
                    },
                }
            ],
        }
        (tmp_path / "d2.json").write_text(json.dumps(dash))
        out = oup.extract_dashboard_links(tmp_path)
        assert any(i["url"] == "http://drill.local" for i in out)

    def test_recurses_into_row_panels(self, tmp_path: Path):
        dash = {
            "title": "D3",
            "panels": [
                {
                    "title": "Row",
                    "panels": [
                        {
                            "title": "Inner",
                            "links": [{"title": "x", "url": "http://inner"}],
                        }
                    ],
                }
            ],
        }
        (tmp_path / "d3.json").write_text(json.dumps(dash))
        out = oup.extract_dashboard_links(tmp_path)
        assert any(i["url"] == "http://inner" for i in out)

    def test_skips_templated_urls(self, tmp_path: Path):
        dash = {
            "title": "D4",
            "links": [
                {"title": "OK", "url": "http://real.local"},
                {"title": "Bad", "url": "http://${ip}:4200"},
                {"title": "Var", "url": "http://x/$__url_time"},
            ],
            "panels": [],
        }
        (tmp_path / "d4.json").write_text(json.dumps(dash))
        out = oup.extract_dashboard_links(tmp_path)
        urls = [i["url"] for i in out]
        assert "http://real.local" in urls
        assert all("${" not in u and "$__" not in u for u in urls)

    def test_dedupes_repeated_url(self, tmp_path: Path):
        dash = {
            "title": "D5",
            "links": [
                {"title": "A", "url": "http://x"},
                {"title": "A", "url": "http://x"},
            ],
            "panels": [],
        }
        (tmp_path / "d5.json").write_text(json.dumps(dash))
        out = oup.extract_dashboard_links(tmp_path)
        assert len(out) == 1

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        out = oup.extract_dashboard_links(tmp_path / "does-not-exist")
        assert out == []

    def test_malformed_json_does_not_raise(self, tmp_path: Path):
        (tmp_path / "broken.json").write_text("{not valid json")
        out = oup.extract_dashboard_links(tmp_path)
        assert out == []  # silently skipped


# ---------------------------------------------------------------------------
# app_settings URL collection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCollectAppSettingUrls:
    @pytest.mark.asyncio
    async def test_picks_up_url_suffix_keys(self):
        pool = _make_pool([
            {"key": "site_url", "value": "https://gladlabs.io"},
            {"key": "storefront_url", "value": "https://gladlabs.ai"},
            {"key": "unrelated_key", "value": "not a url"},
        ])
        out = await oup.collect_app_setting_urls(pool)
        keys = {i["key"] for i in out}
        assert keys == {"site_url", "storefront_url"}

    @pytest.mark.asyncio
    async def test_picks_up_internal_compose_keys(self):
        pool = _make_pool([
            {"key": "prefect_api_url", "value": "http://prefect:4200/api"},
            {"key": "grafana_url", "value": "http://grafana:3000"},
            {"key": "loki_url", "value": "http://loki:3100"},
        ])
        out = await oup.collect_app_setting_urls(pool)
        assert len(out) == 3
        assert all(i["surface"].startswith("app_settings.") for i in out)

    @pytest.mark.asyncio
    async def test_skips_values_without_scheme(self):
        pool = _make_pool([
            {"key": "site_url", "value": "gladlabs.io"},  # no scheme
            {"key": "real_url", "value": "https://x.com"},
        ])
        out = await oup.collect_app_setting_urls(pool)
        urls = {i["url"] for i in out}
        assert urls == {"https://x.com"}

    @pytest.mark.asyncio
    async def test_db_failure_returns_empty(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=Exception("boom"))
        out = await oup.collect_app_setting_urls(pool)
        assert out == []

    @pytest.mark.asyncio
    async def test_skips_non_http_schemes(self):
        # postgresql://, redis://, amqp:// etc are valid URIs but not
        # things HEAD/GET can hit. Probing them returns ConnectError every
        # cycle and clutters the report. They have keys ending in _url
        # (local_database_url) so the existing filter doesn't catch them.
        pool = _make_pool([
            {"key": "site_url", "value": "https://gladlabs.io"},
            {"key": "local_database_url", "value": "postgresql://u:p@db:5432/x"},
            {"key": "redis_url", "value": "redis://cache:6379/0"},
            {"key": "ws_url", "value": "ws://server:8080/socket"},
            {"key": "amqp_url", "value": "amqp://broker:5672"},
        ])
        out = await oup.collect_app_setting_urls(pool)
        urls = {i["url"] for i in out}
        assert urls == {"https://gladlabs.io"}

    @pytest.mark.asyncio
    async def test_skips_keys_in_app_settings_skip_list(self):
        # Operator can mute specific keys via
        # `operator_url_probe_skip_keys` (comma-separated). Useful for
        # social profiles (bot-protected, return 403) and any URL that
        # legitimately shouldn't be HEAD/GET probed.
        async def fetchval_skip_keys(_query, key):
            if key == "operator_url_probe_skip_keys":
                return "social_x_url,social_linkedin_url"
            return None

        pool = _make_pool([
            {"key": "site_url", "value": "https://gladlabs.io"},
            {"key": "social_x_url", "value": "https://x.com/_gladlabs"},
            {"key": "social_linkedin_url", "value": "https://www.linkedin.com/in/m"},
        ])
        pool.fetchval = AsyncMock(side_effect=fetchval_skip_keys)
        out = await oup.collect_app_setting_urls(pool)
        keys = {i["key"] for i in out}
        assert keys == {"site_url"}

    @pytest.mark.asyncio
    async def test_localizes_localhost_when_in_docker(self, monkeypatch):
        # Brain runs in docker, so localhost loops back to the brain
        # container itself — every probe of a host service via
        # http://localhost: will fail. localize_url() rewrites these to
        # host.docker.internal which IS reachable from the brain.
        # Monkey-patch the probe's `_localize` adapter directly rather
        # than chasing IN_DOCKER through whichever import path landed —
        # the probe imports docker_utils at module load time, and the
        # path varies between local pytest (flat `docker_utils` on path)
        # and CI (`brain.docker_utils` only).
        def fake_localize(url: str) -> str:
            return (
                url.replace("://localhost:", "://host.docker.internal:")
                   .replace("://127.0.0.1:", "://host.docker.internal:")
            )
        monkeypatch.setattr(oup, "_localize", fake_localize)

        pool = _make_pool([
            {"key": "grafana_url", "value": "http://localhost:3000"},
            {"key": "ollama_url", "value": "http://127.0.0.1:11434"},
            {"key": "remote_url", "value": "https://gladlabs.io"},
        ])
        out = await oup.collect_app_setting_urls(pool)
        urls = sorted(i["url"] for i in out)
        assert urls == [
            "http://host.docker.internal:11434",
            "http://host.docker.internal:3000",
            "https://gladlabs.io",
        ]


# ---------------------------------------------------------------------------
# Tailscale drift detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTailscaleDrift:
    @pytest.mark.asyncio
    async def test_no_drift_returns_empty(self):
        pool = _make_pool([
            {"name": "workstation", "tailscale_ip": "100.81.93.12"},
        ])
        live = {"workstation": "100.81.93.12"}
        with patch.object(oup, "_run_tailscale_status", return_value=live):
            out = await oup.detect_tailscale_drift(pool)
        assert out == []

    @pytest.mark.asyncio
    async def test_drift_returned_with_fix(self):
        pool = _make_pool([
            {"name": "workstation", "tailscale_ip": "100.64.0.1"},  # stale
            {"name": "pixel-9", "tailscale_ip": "100.42.0.1"},
        ])
        live = {"workstation": "100.81.93.12", "pixel-9": "100.42.0.1"}
        with patch.object(oup, "_run_tailscale_status", return_value=live):
            out = await oup.detect_tailscale_drift(pool)
        assert len(out) == 1
        d = out[0]
        assert d["name"] == "workstation"
        assert d["db_ip"] == "100.64.0.1"
        assert d["live_ip"] == "100.81.93.12"
        assert "UPDATE system_devices" in d["fix"]

    @pytest.mark.asyncio
    async def test_tailscale_unavailable_returns_empty(self):
        pool = _make_pool()
        with patch.object(oup, "_run_tailscale_status", return_value=None):
            out = await oup.detect_tailscale_drift(pool)
        assert out == []

    @pytest.mark.asyncio
    async def test_table_missing_returns_empty(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=Exception("system_devices does not exist"))
        with patch.object(oup, "_run_tailscale_status", return_value={"x": "1"}):
            out = await oup.detect_tailscale_drift(pool)
        assert out == []


# ---------------------------------------------------------------------------
# Top-level run_operator_url_probe
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunOperatorUrlProbe:
    @pytest.mark.asyncio
    async def test_notifies_once_per_failed_surface(self, tmp_path: Path):
        # Two dashboard links, both unreachable. notify_fn should be called
        # twice (once per surface) — the cap is 1 per surface, not 1 total.
        dash = {
            "title": "D",
            "links": [
                {"title": "A", "url": "http://broken-a.local"},
                {"title": "B", "url": "http://broken-b.local"},
            ],
            "panels": [],
        }
        (tmp_path / "d.json").write_text(json.dumps(dash))

        pool = _make_pool([])  # no app_settings rows
        notifies: list[dict] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        async def fake_probe(targets, *, concurrency=10):
            return [
                {"surface": t["surface"], "url": t["url"], "ok": False,
                 "status": 0, "detail": "ConnectError"}
                for t in targets
            ]

        with patch.object(oup, "probe_urls", side_effect=fake_probe), \
             patch.object(oup, "_run_tailscale_status", return_value=None):
            summary = await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path, notify_fn=fake_notify
            )

        assert summary["url_failures"] == 2
        assert summary["notifications_sent"] == 2
        assert len(notifies) == 2
        assert all("Operator surface unreachable" in n["title"] for n in notifies)

    @pytest.mark.asyncio
    async def test_no_notify_when_all_ok(self, tmp_path: Path):
        dash = {"title": "D", "links": [{"title": "A", "url": "http://ok"}], "panels": []}
        (tmp_path / "d.json").write_text(json.dumps(dash))
        pool = _make_pool([])
        notifies = []

        async def fake_probe(targets, *, concurrency=10):
            return [
                {"surface": t["surface"], "url": t["url"], "ok": True,
                 "status": 200, "detail": "HTTP 200"}
                for t in targets
            ]

        with patch.object(oup, "probe_urls", side_effect=fake_probe), \
             patch.object(oup, "_run_tailscale_status", return_value=None):
            summary = await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path,
                notify_fn=lambda **k: notifies.append(k),
            )

        assert summary["url_failures"] == 0
        assert summary["notifications_sent"] == 0

    @pytest.mark.asyncio
    async def test_drift_triggers_notify(self, tmp_path: Path):
        # Pool needs to return [] for the app_settings query and the
        # device row for the system_devices query, so route by SQL keyword.
        pool = MagicMock()

        async def fake_fetch(query, *args, **kwargs):
            if "system_devices" in query:
                return [{"name": "workstation", "tailscale_ip": "100.64.0.1"}]
            return []  # app_settings et al

        pool.fetch = fake_fetch
        pool.execute = AsyncMock()
        notifies = []

        async def fake_probe(targets, *, concurrency=10):
            return []

        with patch.object(oup, "probe_urls", side_effect=fake_probe), \
             patch.object(oup, "_run_tailscale_status",
                          return_value={"workstation": "100.81.93.12"}):
            summary = await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path,
                notify_fn=lambda **k: notifies.append(k),
            )

        assert summary["tailscale_drift_count"] == 1
        assert any("Tailscale IP drift" in n["title"] for n in notifies)


# ---------------------------------------------------------------------------
# Interval gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMaybeRun:
    @pytest.mark.asyncio
    async def test_first_call_runs(self):
        pool = _make_pool()
        with patch.object(oup, "run_operator_url_probe",
                          new=AsyncMock(return_value={"ok": True})) as m:
            result = await oup.maybe_run_operator_url_probe(pool)
        assert result == {"ok": True}
        assert m.await_count == 1

    @pytest.mark.asyncio
    async def test_second_call_within_window_skips(self):
        pool = _make_pool()
        with patch.object(oup, "run_operator_url_probe",
                          new=AsyncMock(return_value={"ok": True})) as m:
            first = await oup.maybe_run_operator_url_probe(pool)
            second = await oup.maybe_run_operator_url_probe(pool)
        assert first == {"ok": True}
        assert second is None  # gated
        assert m.await_count == 1

    @pytest.mark.asyncio
    async def test_crash_returned_as_error_dict(self):
        pool = _make_pool()
        with patch.object(oup, "run_operator_url_probe",
                          new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await oup.maybe_run_operator_url_probe(pool)
        assert result is not None
        assert "error" in result
        assert "RuntimeError" in result["error"]
