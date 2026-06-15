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
    """Build a mock asyncpg pool whose fetch/fetchrow return canned rows.

    fetchval defaults to None so the operator_url_probe's
    target-overrides loader resolves cleanly without firing its
    JSON-parse warning. Tests that need specific fetchval results
    override after construction.
    """
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    pool.fetchrow = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
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
                {"title": "Prefect UI", "url": "http://100.64.0.42:4200"},
                {"title": "Grafana", "url": "http://localhost:3000"},
            ],
            "panels": [],
        }
        (tmp_path / "mission.json").write_text(json.dumps(dash))
        out = oup.extract_dashboard_links(tmp_path)
        urls = {item["url"] for item in out}
        assert "http://100.64.0.42:4200" in urls
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
            {"name": "workstation", "tailscale_ip": "100.64.0.42"},
        ])
        live = {"workstation": "100.64.0.42"}
        with patch.object(oup, "_run_tailscale_status", return_value=live):
            out = await oup.detect_tailscale_drift(pool)
        assert out == []

    @pytest.mark.asyncio
    async def test_drift_returned_with_fix(self):
        pool = _make_pool([
            {"name": "workstation", "tailscale_ip": "100.64.0.1"},  # stale
            {"name": "pixel-9", "tailscale_ip": "100.42.0.1"},
        ])
        live = {"workstation": "100.64.0.42", "pixel-9": "100.42.0.1"}
        with patch.object(oup, "_run_tailscale_status", return_value=live):
            out = await oup.detect_tailscale_drift(pool)
        assert len(out) == 1
        d = out[0]
        assert d["name"] == "workstation"
        assert d["db_ip"] == "100.64.0.1"
        assert d["live_ip"] == "100.64.0.42"
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

        async def fake_probe(targets, *, concurrency=10, overrides=None):
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

        async def fake_probe(targets, *, concurrency=10, overrides=None):
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

        async def fake_probe(targets, *, concurrency=10, overrides=None):
            return []

        with patch.object(oup, "probe_urls", side_effect=fake_probe), \
             patch.object(oup, "_run_tailscale_status",
                          return_value={"workstation": "100.64.0.42"}):
            summary = await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path,
                notify_fn=lambda **k: notifies.append(k),
            )

        assert summary["tailscale_drift_count"] == 1
        assert any("Tailscale IP drift" in n["title"] for n in notifies)


@pytest.mark.unit
class TestProbeCompletedAuditLog:
    """Closes cycle-4 audit #245: a healthy probe must leave an
    ``audit_log`` row so operators can confirm it ran. Without this
    the success path is silent and looks identical to a dead probe.
    """

    @pytest.mark.asyncio
    async def test_writes_probe_completed_row_on_success(self, tmp_path: Path):
        pool = _make_pool([])

        async def fake_probe(targets, *, concurrency=10, overrides=None):
            return []  # zero targets → zero failures → success path

        with patch.object(oup, "probe_urls", side_effect=fake_probe), \
             patch.object(oup, "_run_tailscale_status", return_value=None):
            await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path,
                notify_fn=lambda **k: None,
            )

        # The mock pool's execute() was called at least once for the audit_log
        # INSERT. Find that call and assert its shape.
        audit_calls = [
            call for call in pool.execute.await_args_list
            if call.args and "audit_log" in call.args[0]
        ]
        assert audit_calls, (
            "expected an audit_log INSERT for probe_completed — got: "
            f"{pool.execute.await_args_list}"
        )
        # Args: (sql, event_type, source, details_json, severity)
        call_args = audit_calls[0].args
        assert call_args[1] == "probe_completed"
        assert call_args[2] == "brain.operator_url_probe"
        details = json.loads(call_args[3])
        assert "total_urls_probed" in details
        assert "url_failures" in details
        assert "tailscale_drift_count" in details
        assert "notifications_sent" in details
        assert call_args[4] == "info"

    @pytest.mark.asyncio
    async def test_writes_row_even_when_failures_are_present(self, tmp_path: Path):
        """A probe with failures STILL writes the success row — the row
        records 'I ran a cycle', not 'all targets passed'. Failures
        get their own notify_operator calls already covered above."""
        dash = {
            "title": "D",
            "links": [{"title": "broken", "url": "http://broken.local"}],
            "panels": [],
        }
        (tmp_path / "d.json").write_text(json.dumps(dash))
        pool = _make_pool([])

        async def fake_probe(targets, *, concurrency=10, overrides=None):
            return [
                {"surface": t["surface"], "url": t["url"], "ok": False,
                 "status": 0, "detail": "broken"}
                for t in targets
            ]

        with patch.object(oup, "probe_urls", side_effect=fake_probe), \
             patch.object(oup, "_run_tailscale_status", return_value=None):
            summary = await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path,
                notify_fn=lambda **k: None,
            )

        assert summary["url_failures"] == 1
        audit_calls = [
            c for c in pool.execute.await_args_list
            if c.args and "audit_log" in c.args[0]
        ]
        assert len(audit_calls) == 1
        details = json.loads(audit_calls[0].args[3])
        assert details["url_failures"] == 1

    @pytest.mark.asyncio
    async def test_audit_write_failure_is_non_fatal(self, tmp_path: Path):
        """If audit_log INSERT raises, the probe still returns its
        summary — observability writes must never fail the cycle."""
        pool = _make_pool([])
        # First execute call succeeds (any internal DB writes), but the
        # audit_log INSERT raises. The probe should swallow it and
        # return normally. We use a side_effect that raises on the
        # specific audit_log query.
        original_execute = pool.execute

        async def execute_with_audit_failure(*args, **kwargs):
            if args and "audit_log" in args[0]:
                raise RuntimeError("simulated DB outage")
            return await original_execute(*args, **kwargs)

        pool.execute = AsyncMock(side_effect=execute_with_audit_failure)

        async def fake_probe(targets, *, concurrency=10, overrides=None):
            return []

        with patch.object(oup, "probe_urls", side_effect=fake_probe), \
             patch.object(oup, "_run_tailscale_status", return_value=None):
            # Must NOT raise.
            summary = await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path,
                notify_fn=lambda **k: None,
            )
        assert summary["url_failures"] == 0  # probe still produced a summary


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


# ---------------------------------------------------------------------------
# Per-URL probe overrides (#347 alternative-to-skip-list)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseAliveCodes:
    """``_parse_alive_codes`` accepts the operator-facing string DSL
    that the override JSON specifies."""

    def test_simple_range(self):
        out = oup._parse_alive_codes("200-399")
        assert any(200 in r for r in out)
        assert any(399 in r for r in out)
        assert not any(400 in r for r in out)

    def test_extended_range_includes_4xx(self):
        out = oup._parse_alive_codes("200-499")
        assert any(404 in r for r in out)
        assert any(405 in r for r in out)
        assert not any(500 in r for r in out)

    def test_singletons_in_csv(self):
        out = oup._parse_alive_codes("200,201,418")
        assert any(200 in r for r in out)
        assert any(418 in r for r in out)
        assert not any(202 in r for r in out)

    def test_mixed_singleton_and_range(self):
        out = oup._parse_alive_codes("200-299,418")
        assert any(250 in r for r in out)
        assert any(418 in r for r in out)
        assert not any(300 in r for r in out)

    def test_malformed_falls_back_to_default(self):
        """Garbage in the override doesn't make every URL look alive —
        falls back to the strict 200–399 range."""
        out = oup._parse_alive_codes("not-a-range,abc,999-foo")
        assert any(200 in r for r in out)
        assert any(399 in r for r in out)
        assert not any(404 in r for r in out)


@pytest.mark.unit
class TestIsAlivePerOverride:
    """``_is_alive_per_override`` decides per-URL whether a status
    code counts as alive based on the override config."""

    def test_no_override_uses_default(self):
        assert oup._is_alive_per_override(200, None) is True
        assert oup._is_alive_per_override(404, None) is False
        assert oup._is_alive_per_override(500, None) is False

    def test_extended_alive_codes_accepts_4xx(self):
        override = {"alive_codes": "200-499"}
        assert oup._is_alive_per_override(404, override) is True
        assert oup._is_alive_per_override(405, override) is True
        assert oup._is_alive_per_override(500, override) is False
        assert oup._is_alive_per_override(0, override) is False  # network error

    def test_specific_codes_only(self):
        override = {"alive_codes": "200,418"}
        assert oup._is_alive_per_override(200, override) is True
        assert oup._is_alive_per_override(418, override) is True
        assert oup._is_alive_per_override(201, override) is False


@pytest.mark.unit
class TestLoadTargetOverrides:
    """``_load_target_overrides`` reads the JSON config from
    app_settings; degrades safely on errors."""

    @pytest.mark.asyncio
    async def test_missing_setting_returns_empty(self):
        pool = _make_pool()
        # fetchval already returns None by default
        out = await oup._load_target_overrides(pool)
        assert out == {}

    @pytest.mark.asyncio
    async def test_valid_json_parsed(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=json.dumps({
            "google_sitemap_ping_url": {"alive_codes": "200-499"},
        }))
        out = await oup._load_target_overrides(pool)
        assert "google_sitemap_ping_url" in out
        assert out["google_sitemap_ping_url"]["alive_codes"] == "200-499"

    @pytest.mark.asyncio
    async def test_malformed_json_warns_returns_empty(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="{not-json")
        out = await oup._load_target_overrides(pool)
        assert out == {}  # safe degradation

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
        out = await oup._load_target_overrides(pool)
        assert out == {}


@pytest.mark.unit
class TestProbeUrlsHonorsOverrides:
    """End-to-end: ``probe_urls`` plumbs overrides through to the
    per-target probe so 4xx is rendered as 'ok' for outbound APIs."""

    @pytest.mark.asyncio
    async def test_outbound_api_4xx_marked_alive_with_override(self):
        # Patch _probe_one_url to inspect what the caller passes for
        # override + return a fake 405 response so we can confirm the
        # override is what makes it alive (not the default).
        captured = []

        async def fake_probe_one(client, sem, surface, url, override=None):
            captured.append((url, override))
            # 400 is dead-by-default but alive under the 200-499 override,
            # so it cleanly shows the override (not the gated-by-default
            # set) is what flips the keyed target alive. 405 would now be
            # alive by default and wouldn't isolate the override.
            return {
                "surface": surface,
                "url": url,
                "ok": oup._is_alive_per_override(400, override),
                "status": 400,
                "detail": "HTTP 400",
                "override_applied": bool(override),
                "override_reason": (override or {}).get("reason", ""),
            }

        targets = [
            {"surface": "app_settings.google_sitemap_ping_url",
             "key": "google_sitemap_ping_url",
             "url": "https://www.google.com/ping"},
            {"surface": "app_settings.dashboards.grafana",
             "key": "",  # dashboard targets have no key
             "url": "https://grafana.example/health"},
        ]
        overrides = {
            "google_sitemap_ping_url": {"alive_codes": "200-499"},
        }

        with patch.object(oup, "_probe_one_url", new=fake_probe_one):
            results = await oup.probe_urls(
                targets, concurrency=2, overrides=overrides,
            )

        # Override applied to the keyed target, not the dashboard one.
        keyed = next(r for r in results if "google" in r["url"])
        dash = next(r for r in results if "grafana" in r["url"])
        assert keyed["ok"] is True       # 405 alive under 200-499
        assert dash["ok"] is False       # 405 NOT alive under default

    @pytest.mark.asyncio
    async def test_no_overrides_uses_strict_default(self):
        async def fake_probe_one(client, sem, surface, url, override=None):
            return {
                "surface": surface,
                "url": url,
                "ok": oup._is_alive_per_override(404, override),
                "status": 404,
                "detail": "HTTP 404",
                "override_applied": bool(override),
                "override_reason": "",
            }

        with patch.object(oup, "_probe_one_url", new=fake_probe_one):
            results = await oup.probe_urls(
                [{"surface": "x", "key": "anything", "url": "https://x"}],
                concurrency=1,
            )
        assert results[0]["ok"] is False  # 404 fails strict check


# ---------------------------------------------------------------------------
# probe_url redirect — health-endpoint remapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeUrlRedirect:
    """``probe_url`` in the override dict lets operators redirect health
    checks to a dedicated endpoint (e.g. /health) when the setting value
    itself is a POST-only path or a bare base URL.

    The original setting URL must remain in the result for alert messages
    so operators know which setting is broken, not just which health
    endpoint was checked.
    """

    @pytest.mark.asyncio
    async def test_probe_url_used_for_request_setting_url_in_result(self):
        """When override has probe_url, the HTTP call goes to probe_url
        but result.url stays as the original setting value."""
        probed_urls: list[str] = []

        async def fake_probe_one(client, sem, surface, url, override=None):
            probe_url = (override or {}).get("probe_url") or url
            probed_urls.append(probe_url)
            return {
                "surface": surface,
                "url": url,  # original setting URL
                "ok": True,
                "status": 200,
                "detail": "HTTP 200",
                "override_applied": bool(override),
                "override_reason": (override or {}).get("reason", ""),
            }

        targets = [
            {
                "surface": "app_settings.voice_agent_stt_base_url",
                "key": "voice_agent_stt_base_url",
                "url": "http://speaches:8000/v1",
            }
        ]
        overrides = {
            "voice_agent_stt_base_url": {
                "probe_url": "http://speaches:8000/health",
                "reason": "speaches /v1 is the OpenAI-compat base path; /health is the liveness endpoint",
            }
        }

        with patch.object(oup, "_probe_one_url", new=fake_probe_one):
            results = await oup.probe_urls(targets, concurrency=1, overrides=overrides)

        assert results[0]["ok"] is True
        assert results[0]["url"] == "http://speaches:8000/v1"  # original preserved
        assert probed_urls == ["http://speaches:8000/health"]  # health endpoint used

    @pytest.mark.asyncio
    async def test_empty_probe_url_falls_back_to_setting_url(self):
        """Empty string probe_url must behave as if probe_url were absent."""
        probed_urls: list[str] = []

        async def fake_probe_one(client, sem, surface, url, override=None):
            probe_url = (override or {}).get("probe_url") or url
            probed_urls.append(probe_url)
            return {"surface": surface, "url": url, "ok": True, "status": 200,
                    "detail": "HTTP 200", "override_applied": bool(override),
                    "override_reason": ""}

        targets = [{"surface": "s", "key": "k", "url": "http://real.local/v1"}]
        overrides = {"k": {"probe_url": ""}}  # empty → fall back

        with patch.object(oup, "_probe_one_url", new=fake_probe_one):
            await oup.probe_urls(targets, concurrency=1, overrides=overrides)

        assert probed_urls == ["http://real.local/v1"]

    @pytest.mark.asyncio
    async def test_probe_url_integration_hits_correct_url(self):
        """End-to-end: _probe_one_url itself uses probe_url for the HTTP
        call when the override supplies one, keeping url in the result."""
        import asyncio

        import httpx

        response_map: dict[str, int] = {
            "http://speaches:8000/health": 200,
            "http://speaches:8000/v1": 404,
        }
        transport = httpx.MockTransport(
            handler=lambda req: httpx.Response(
                response_map.get(str(req.url).split("?")[0], 500)
            )
        )

        async with httpx.AsyncClient(transport=transport) as client:
            sem = asyncio.Semaphore(1)
            result = await oup._probe_one_url(
                client, sem,
                surface="app_settings.voice_agent_stt_base_url",
                url="http://speaches:8000/v1",
                override={
                    "probe_url": "http://speaches:8000/health",
                    "method": "GET",
                },
            )

        assert result["ok"] is True
        assert result["status"] == 200
        assert result["url"] == "http://speaches:8000/v1"  # original preserved

    @pytest.mark.asyncio
    async def test_probe_url_is_localized(self, monkeypatch):
        """Regression (2026-06-08): an override probe_url pointing at
        localhost must be run through _localize() before the HTTP call,
        exactly like the collected setting url is. Before the fix the
        override probe_url bypassed localization, so a localhost health
        endpoint was unreachable from inside the brain container — which
        is how data_fabric_loki_url / data_fabric_tempo_url (root path
        404s, /ready is the health endpoint) paged 50x/day.
        """
        import asyncio

        import httpx

        # Simulate running inside the container: localhost -> host.docker.internal.
        def fake_localize(url: str) -> str:
            return url.replace("localhost", "host.docker.internal")

        monkeypatch.setattr(oup, "_localize", fake_localize)

        requested: list[str] = []

        def handler(req):
            requested.append(str(req.url))
            # Only the localized health URL answers 200; the raw localhost
            # form (or the setting's root path) would 404/500.
            if str(req.url) == "http://host.docker.internal:3100/ready":
                return httpx.Response(200)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler=handler)
        async with httpx.AsyncClient(transport=transport) as client:
            sem = asyncio.Semaphore(1)
            result = await oup._probe_one_url(
                client, sem,
                surface="app_settings.data_fabric_loki_url",
                url="http://host.docker.internal:3100",  # already-localized setting value
                override={
                    "probe_url": "http://localhost:3100/ready",  # NOT yet localized
                    "method": "GET",
                },
            )

        assert result["ok"] is True
        assert result["status"] == 200
        # The HTTP call must have hit the LOCALIZED health URL.
        assert "http://host.docker.internal:3100/ready" in requested
        assert "http://localhost:3100/ready" not in requested
        # Result still reports the original setting url for the operator.
        assert result["url"] == "http://host.docker.internal:3100"


# ---------------------------------------------------------------------------
# Default "alive but gated" status handling (#1594 follow-up)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultAliveButGated:
    """Without any override, a status that proves the host answered but
    refused THIS request shape — 401 (auth), 403 (forbidden), 405 (method
    not allowed), 501 (not implemented) — counts as ALIVE. This is the
    recurring false-positive class: POST-only beacons return 405 to
    HEAD/GET, and auth-gated surfaces return 401/403. 404/410/5xx and
    connection errors stay DOWN — those are the renamed/missing/broken
    signals the probe exists to catch (GH#214).
    """

    def test_method_and_auth_gated_codes_alive_by_default(self):
        for code in (401, 403, 405, 501):
            assert oup._is_alive_per_override(code, None) is True, code

    def test_missing_and_server_error_codes_dead_by_default(self):
        for code in (404, 410, 500, 502, 503, 0):
            assert oup._is_alive_per_override(code, None) is False, code

    def test_success_and_redirect_still_alive_by_default(self):
        for code in (200, 204, 301, 302, 399):
            assert oup._is_alive_per_override(code, None) is True, code

    def test_explicit_override_still_wins_over_gated_default(self):
        # An operator who narrows alive_codes to exclude 405 gets exactly
        # that — the explicit config overrides the gated-by-default set.
        override = {"alive_codes": "200-204"}
        assert oup._is_alive_per_override(405, override) is False
        assert oup._is_alive_per_override(200, override) is True


@pytest.mark.unit
class TestMergeTargets:
    """``_merge_targets`` dedups dashboard + app_settings targets by URL,
    preserving the dashboard surface anchor but propagating the
    app_settings ``key`` so a shadowed URL's per-URL override still
    applies.
    """

    def test_appsetting_key_propagates_to_shadowing_dashboard_target(self):
        dash = [{"dashboard": "MC", "surface": "MC :: Beacon", "url": "https://b"}]
        appset = [{"surface": "app_settings.cloudflare_beacon_url",
                   "key": "cloudflare_beacon_url", "url": "https://b"}]
        merged = oup._merge_targets(dash, appset)
        assert len(merged) == 1
        # Dashboard surface stays as the operator anchor...
        assert merged[0]["surface"] == "MC :: Beacon"
        # ...but the key rides along so override routing works.
        assert merged[0]["key"] == "cloudflare_beacon_url"

    def test_distinct_urls_all_kept(self):
        dash = [{"dashboard": "D", "surface": "D :: a", "url": "https://a"}]
        appset = [{"surface": "app_settings.x_url", "key": "x_url", "url": "https://x"}]
        merged = oup._merge_targets(dash, appset)
        assert {t["url"] for t in merged} == {"https://a", "https://x"}

    def test_appsetting_only_url_keeps_key(self):
        merged = oup._merge_targets(
            [], [{"surface": "app_settings.x_url", "key": "x_url", "url": "https://x"}]
        )
        assert merged[0]["key"] == "x_url"

    def test_dashboard_only_url_has_no_key(self):
        merged = oup._merge_targets(
            [{"dashboard": "D", "surface": "D :: a", "url": "https://a"}], []
        )
        assert not merged[0].get("key")

    def test_does_not_mutate_source_lists(self):
        dash = [{"dashboard": "MC", "surface": "MC :: Beacon", "url": "https://b"}]
        appset = [{"surface": "app_settings.beacon_url", "key": "beacon_url",
                   "url": "https://b"}]
        oup._merge_targets(dash, appset)
        assert "key" not in dash[0]  # original dashboard target untouched


@pytest.mark.unit
class TestShadowedOverrideRegression:
    """Regression for the 2026-06-15 beacon false-positive root cause: a
    URL that is BOTH a dashboard link (no key) and an app_settings surface
    with a per-URL override must still honor that override, instead of the
    keyless dashboard copy winning the dedup and bypassing it.
    """

    @pytest.mark.asyncio
    async def test_dashboard_shadowed_url_still_honors_appsetting_override(
        self, tmp_path: Path
    ):
        url = "https://page-views-beacon.example.workers.dev"
        # Same URL appears as a dashboard link...
        dash = {"title": "MC", "links": [{"title": "Beacon", "url": url}], "panels": []}
        (tmp_path / "mc.json").write_text(json.dumps(dash))
        # ...and as an app_settings key carrying a widen-to-4xx override.
        pool = _make_pool([{"key": "cloudflare_beacon_url", "value": url}])

        async def fetchval(_query, key):
            if key == "operator_url_probe_target_overrides":
                return json.dumps(
                    {"cloudflare_beacon_url": {"alive_codes": "200-499",
                                               "method": "HEAD"}}
                )
            return None

        pool.fetchval = AsyncMock(side_effect=fetchval)

        # Status 400 is dead-by-default but alive under the 200-499
        # override — so this isolates the key-propagation fix, independent
        # of the gated-by-default change (405 would pass either way).
        async def fake_probe_one(client, sem, surface, url_, override=None):
            return {
                "surface": surface, "url": url_,
                "ok": oup._is_alive_per_override(400, override),
                "status": 400, "detail": "HTTP 400",
                "override_applied": bool(override),
                "override_reason": (override or {}).get("reason", ""),
            }

        notifies: list[dict] = []
        with patch.object(oup, "_probe_one_url", new=fake_probe_one), \
             patch.object(oup, "_run_tailscale_status", return_value=None):
            summary = await oup.run_operator_url_probe(
                pool, dashboards_dir=tmp_path,
                notify_fn=lambda **k: notifies.append(k),
            )

        assert summary["url_failures"] == 0
        assert notifies == []


@pytest.mark.unit
class TestDefaultHeadProbeBehavior:
    """``_probe_one_url`` with no override: a bare HEAD that returns 405
    (POST-only surface) is ALIVE without needing a per-URL override.
    """

    @pytest.mark.asyncio
    async def test_head_405_is_alive_by_default(self):
        import asyncio

        import httpx

        methods: list[str] = []

        def handler(req):
            methods.append(req.method)
            return httpx.Response(405)

        transport = httpx.MockTransport(handler=handler)
        async with httpx.AsyncClient(transport=transport) as client:
            sem = asyncio.Semaphore(1)
            result = await oup._probe_one_url(
                client, sem,
                surface="app_settings.cloudflare_beacon_url",
                url="https://beacon.example/",
            )

        assert result["ok"] is True
        assert result["status"] == 405
        assert "HEAD" in methods
