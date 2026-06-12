"""Unit tests for ``services.jobs.render_grafana_alerts``.

Mirrors the structure of test_render_prometheus_rules_job.py.
``build_current`` is monkey-patched so tests are DB-free.
"""

from __future__ import annotations

import pytest

from plugins.job import Job
from services.jobs.render_grafana_alerts import RenderGrafanaAlertsJob, _reload_grafana

# ---------------------------------------------------------------------------
# Protocol / metadata
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms_to_job_protocol(self):
        assert isinstance(RenderGrafanaAlertsJob(), Job)

    def test_metadata(self):
        j = RenderGrafanaAlertsJob()
        assert j.name == "render_grafana_alerts"
        assert j.schedule == "every 5 minutes"
        assert j.idempotent is True


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRun:
    async def test_missing_template_returns_error(self, tmp_path, monkeypatch):
        j = RenderGrafanaAlertsJob()
        result = await j.run(
            pool=None,
            config={
                "template_path": str(tmp_path / "nonexistent.tmpl"),
                "output_path": str(tmp_path / "out.yml"),
                "reload_on_change": False,
            },
        )
        assert result.ok is False
        assert "template not found" in result.detail

    async def test_first_run_writes_file(self, tmp_path, monkeypatch):
        tmpl = tmp_path / "alert-rules.yml.tmpl"
        tmpl.write_text("groups: []\n", encoding="utf-8")
        out = tmp_path / "alert-rules.yml"

        async def fake_build(_pool, _path):
            return "groups: []\n"

        monkeypatch.setattr(
            "services.jobs.render_grafana_alerts.build_current", fake_build
        )
        j = RenderGrafanaAlertsJob()
        result = await j.run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "reload_on_change": False,
            },
        )
        assert result.ok is True
        assert result.changes_made == 1
        assert out.read_text(encoding="utf-8") == "groups: []\n"

    async def test_no_op_when_content_unchanged(self, tmp_path, monkeypatch):
        tmpl = tmp_path / "alert-rules.yml.tmpl"
        tmpl.write_text("groups: []\n", encoding="utf-8")
        out = tmp_path / "alert-rules.yml"
        out.write_text("groups: []\n", encoding="utf-8")

        async def fake_build(_pool, _path):
            return "groups: []\n"

        monkeypatch.setattr(
            "services.jobs.render_grafana_alerts.build_current", fake_build
        )
        j = RenderGrafanaAlertsJob()
        result = await j.run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "reload_on_change": False,
            },
        )
        assert result.ok is True
        assert result.changes_made == 0
        assert "unchanged" in result.detail

    async def test_reload_called_on_change(self, tmp_path, monkeypatch):
        tmpl = tmp_path / "t.tmpl"
        tmpl.write_text("v2\n", encoding="utf-8")
        out = tmp_path / "out.yml"
        out.write_text("v1\n", encoding="utf-8")

        async def fake_build(_pool, _path):
            return "v2\n"

        reload_calls = []

        async def fake_token(_pool):
            return "test-token"

        async def fake_reload(_url, _token):
            reload_calls.append((_url, _token))
            return (True, "grafana alerting reloaded")

        monkeypatch.setattr(
            "services.jobs.render_grafana_alerts.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_grafana_alerts._get_grafana_token", fake_token
        )
        monkeypatch.setattr(
            "services.jobs.render_grafana_alerts._reload_grafana", fake_reload
        )
        j = RenderGrafanaAlertsJob()
        result = await j.run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "grafana_url": "http://grafana:3000",
                "reload_on_change": True,
            },
        )
        assert result.ok is True
        assert result.changes_made == 1
        assert reload_calls == [("http://grafana:3000", "test-token")]

    async def test_reload_skipped_without_token(self, tmp_path, monkeypatch):
        tmpl = tmp_path / "t.tmpl"
        tmpl.write_text("new\n", encoding="utf-8")
        out = tmp_path / "out.yml"

        async def fake_build(_pool, _path):
            return "new\n"

        async def fake_token(_pool):
            return ""

        monkeypatch.setattr(
            "services.jobs.render_grafana_alerts.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_grafana_alerts._get_grafana_token", fake_token
        )
        j = RenderGrafanaAlertsJob()
        result = await j.run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "reload_on_change": True,
            },
        )
        assert result.ok is True
        assert result.changes_made == 1
        assert "grafana_api_token not configured" in result.detail


# ---------------------------------------------------------------------------
# _reload_grafana
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient that records POST calls."""

    def __init__(self, status_code: int = 200, **kwargs):
        self._status_code = status_code
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self._status_code)


@pytest.mark.asyncio
class TestReloadGrafana:
    async def test_success(self, monkeypatch):
        import services.jobs.render_grafana_alerts as job_module

        client = _FakeAsyncClient(200)
        monkeypatch.setattr(job_module.httpx, "AsyncClient", lambda **kw: client)
        ok, detail = await _reload_grafana("http://grafana:3000", "mytoken")
        assert ok is True
        assert "reloaded" in detail
        assert client.calls[0]["url"].endswith("/reload")

    async def test_non_200_returns_false(self, monkeypatch):
        import services.jobs.render_grafana_alerts as job_module

        client = _FakeAsyncClient(403)
        monkeypatch.setattr(job_module.httpx, "AsyncClient", lambda **kw: client)
        ok, detail = await _reload_grafana("http://grafana:3000", "token")
        assert ok is False
        assert "403" in detail

    async def test_http_error_returns_false(self, monkeypatch):
        import httpx

        import services.jobs.render_grafana_alerts as job_module

        class _ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(
            job_module.httpx, "AsyncClient", lambda **kw: _ErrorClient()
        )
        ok, detail = await _reload_grafana("http://grafana:3000", "token")
        assert ok is False
        assert "reload failed" in detail
