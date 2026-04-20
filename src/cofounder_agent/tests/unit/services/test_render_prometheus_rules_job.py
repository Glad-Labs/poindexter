"""Unit tests for ``services.jobs.render_prometheus_rules``.

The Job has three interesting behaviors we exercise here:
1. Write-on-first-run (file doesn't exist yet)
2. No-op when content hasn't changed (byte-identical)
3. Reload POST behavior (skipped, success, failure)

``build_current`` is monkey-patched to return a scripted YAML string
so tests don't depend on the full rule-builder pipeline.
"""

from __future__ import annotations

import pytest

from plugins.job import Job, JobResult
from services.jobs.render_prometheus_rules import RenderPrometheusRulesJob

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
