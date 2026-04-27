"""Unit tests for ``services.jobs.render_prometheus_rules``.

The Job has three interesting behaviors we exercise here:
1. Write-on-first-run (file doesn't exist yet)
2. No-op when content hasn't changed (byte-identical)
3. Reload POST behavior (skipped, success, failure)

``build_current`` is monkey-patched to return a scripted YAML string
so tests don't depend on the full rule-builder pipeline.
"""

from __future__ import annotations

import httpx
import pytest

from plugins.job import Job, JobResult
from services.jobs import render_prometheus_rules as job_module
from services.jobs.render_prometheus_rules import (
    RenderPrometheusRulesJob,
    _reload_prometheus,
)

# ---------------------------------------------------------------------------
# Protocol / metadata
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms_to_job_protocol(self):
        assert isinstance(RenderPrometheusRulesJob(), Job)

    def test_metadata(self):
        j = RenderPrometheusRulesJob()
        assert j.name == "render_prometheus_rules"
        assert j.schedule == "every 5 minutes"
        assert j.idempotent is True


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRun:
    async def test_first_run_writes_file(self, tmp_path, monkeypatch):
        out = tmp_path / "dynamic.yml"

        async def fake_build(_pool):
            return "groups: []\n"

        async def fake_reload(_url):
            return "prometheus reloaded"

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus", fake_reload
        )

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={
                "output_path": str(out),
                "prometheus_url": "http://prom:9090",
                "reload_on_change": True,
            },
        )

        assert isinstance(result, JobResult)
        assert result.ok is True
        assert result.changes_made == 1
        assert out.read_text() == "groups: []\n"
        assert "prometheus reloaded" in result.detail

    async def test_no_change_is_noop(self, tmp_path, monkeypatch):
        out = tmp_path / "dynamic.yml"
        out.write_text("groups: []\n")

        async def fake_build(_pool):
            return "groups: []\n"

        # Should never be called
        async def boom_reload(_url):
            raise AssertionError("reload should not run when content unchanged")

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus", boom_reload
        )

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out), "reload_on_change": True},
        )

        assert result.ok is True
        assert result.changes_made == 0
        assert result.detail == "rules unchanged"

    async def test_reload_skipped_when_disabled(self, tmp_path, monkeypatch):
        out = tmp_path / "dynamic.yml"

        async def fake_build(_pool):
            return "groups: [x]\n"

        async def boom_reload(_url):
            raise AssertionError("reload should be skipped")

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus", boom_reload
        )

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out), "reload_on_change": False},
        )
        assert result.ok is True
        assert result.changes_made == 1
        assert "reload skipped" in result.detail

    async def test_build_failure_returns_not_ok(self, tmp_path, monkeypatch):
        out = tmp_path / "dynamic.yml"

        async def broken_build(_pool):
            raise RuntimeError("db gone")

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", broken_build
        )

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out)},
        )
        assert result.ok is False
        assert "db gone" in result.detail
        # File should not have been written when build failed
        assert not out.exists()

    async def test_creates_parent_directory(self, tmp_path, monkeypatch):
        out = tmp_path / "nested" / "subdir" / "dynamic.yml"

        async def fake_build(_pool):
            return "groups: []\n"

        async def fake_reload(_url):
            return "ok"

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus", fake_reload
        )

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out), "reload_on_change": True},
        )
        assert result.ok is True
        assert out.is_file()

    async def test_strips_trailing_slash_from_prometheus_url(
        self, tmp_path, monkeypatch
    ):
        """Trailing slashes must be stripped before _reload_prometheus is
        called — otherwise the helper would build ``http://prom:9090//-/reload``
        which Prometheus rejects."""
        out = tmp_path / "dynamic.yml"
        captured: list[str] = []

        async def fake_build(_pool):
            return "groups: []\n"

        async def capture_reload(url):
            captured.append(url)
            return "prometheus reloaded"

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus",
            capture_reload,
        )

        await RenderPrometheusRulesJob().run(
            pool=None,
            config={
                "output_path": str(out),
                "prometheus_url": "http://prom:9090///",
                "reload_on_change": True,
            },
        )

        assert captured == ["http://prom:9090"]

    async def test_write_failure_returns_not_ok(self, tmp_path, monkeypatch):
        """OSError on write must surface as ok=False with the error in detail —
        no silent failure, no partial state."""
        out = tmp_path / "dynamic.yml"

        async def fake_build(_pool):
            return "groups: [x]\n"

        async def boom_reload(_url):
            raise AssertionError("reload should not run when write failed")

        original_write_text = type(out).write_text

        def explode_write_text(self, *args, **kwargs):
            if self == out:
                raise OSError("disk full")
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus", boom_reload
        )
        monkeypatch.setattr(type(out), "write_text", explode_write_text)

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out), "reload_on_change": True},
        )

        assert result.ok is False
        assert "write failed" in result.detail
        assert "disk full" in result.detail
        assert result.changes_made == 0

    async def test_proceeds_when_existing_file_unreadable(
        self, tmp_path, monkeypatch
    ):
        """If reading the existing file raises OSError (e.g. permissions), the
        job logs a warning and proceeds to write — never returning a stale
        ``rules unchanged``."""
        out = tmp_path / "dynamic.yml"
        out.write_text("groups: [old]\n")

        async def fake_build(_pool):
            return "groups: [new]\n"

        async def fake_reload(_url):
            return "prometheus reloaded"

        original_read_text = type(out).read_text

        def explode_read_text(self, *args, **kwargs):
            if self == out:
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus", fake_reload
        )
        monkeypatch.setattr(type(out), "read_text", explode_read_text)

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out), "reload_on_change": True},
        )

        assert result.ok is True
        assert result.changes_made == 1
        # The new content was written despite the read failure
        monkeypatch.setattr(type(out), "read_text", original_read_text)
        assert out.read_text() == "groups: [new]\n"

    async def test_metrics_includes_byte_count_on_write(
        self, tmp_path, monkeypatch
    ):
        """Metrics must expose byte length so the Prometheus gauge can track
        the size of the rules file over time."""
        out = tmp_path / "dynamic.yml"
        rendered = "groups:\n- name: x\n  rules: []\n"

        async def fake_build(_pool):
            return rendered

        async def fake_reload(_url):
            return "prometheus reloaded"

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )
        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules._reload_prometheus", fake_reload
        )

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out), "reload_on_change": True},
        )

        assert result.metrics == {"bytes": len(rendered)}

    async def test_metrics_includes_byte_count_on_noop(
        self, tmp_path, monkeypatch
    ):
        """Even when nothing changed, metrics should still report the rendered
        size — operators rely on the gauge being continuously populated."""
        out = tmp_path / "dynamic.yml"
        rendered = "groups: []\n"
        out.write_text(rendered)

        async def fake_build(_pool):
            return rendered

        monkeypatch.setattr(
            "services.jobs.render_prometheus_rules.build_current", fake_build
        )

        result = await RenderPrometheusRulesJob().run(
            pool=None,
            config={"output_path": str(out), "reload_on_change": True},
        )

        assert result.changes_made == 0
        assert result.metrics == {"bytes": len(rendered)}


# ---------------------------------------------------------------------------
# _reload_prometheus
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records the URL of POST calls."""

    posts: list[str] = []

    def __init__(self, response: _FakeResponse | Exception, **_kwargs):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str):
        type(self).posts.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.mark.asyncio
class TestReloadPrometheus:
    async def test_returns_reloaded_on_200(self, monkeypatch):
        _FakeAsyncClient.posts = []
        monkeypatch.setattr(
            job_module.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeAsyncClient(_FakeResponse(200), **kwargs),
        )

        result = await _reload_prometheus("http://prom:9090")

        assert result == "prometheus reloaded"
        assert _FakeAsyncClient.posts == ["http://prom:9090/-/reload"]

    async def test_returns_status_code_on_403(self, monkeypatch):
        """403 means --web.enable-lifecycle isn't set on Prometheus — surface
        the code in the detail string so the operator knows what to fix."""
        _FakeAsyncClient.posts = []
        monkeypatch.setattr(
            job_module.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeAsyncClient(_FakeResponse(403), **kwargs),
        )

        result = await _reload_prometheus("http://prom:9090")

        assert result == "reload returned 403"

    async def test_handles_http_error(self, monkeypatch):
        """A connection error on reload must NOT raise — return a string
        suitable for JobResult.detail so the scheduler doesn't back off."""
        monkeypatch.setattr(
            job_module.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeAsyncClient(
                httpx.ConnectError("connection refused"), **kwargs
            ),
        )

        result = await _reload_prometheus("http://prom:9090")

        assert result.startswith("reload failed:")
        assert "connection refused" in result
