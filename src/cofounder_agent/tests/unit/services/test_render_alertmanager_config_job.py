"""Unit tests for ``services.jobs.render_alertmanager_config`` (#524).

The dead-man's-switch render job:
1. Reads alertmanager.yml.tmpl
2. Substitutes ${ALERTMANAGER_TELEGRAM_CHAT_ID} with app_settings.telegram_chat_id
3. Writes the rendered config on change + reloads Alertmanager

Most important property under test: the rendered output contains the real
chat_id and NO trace of the placeholder (the public-mirror leak guard
depends on the placeholder only ever living in the template).
"""

from __future__ import annotations

import httpx
import pytest

from plugins.job import Job, JobResult
from services.jobs import render_alertmanager_config as job_module
from services.jobs.render_alertmanager_config import (
    CHAT_ID_PLACEHOLDER,
    RenderAlertmanagerConfigJob,
    _reload_alertmanager,
    render_template,
)

_TEMPLATE = (
    "receivers:\n"
    "  - name: dead-mans-switch-telegram\n"
    "    telegram_configs:\n"
    "      - bot_token_file: /etc/alertmanager/secrets/alertmanager-telegram-token\n"
    "        chat_id: ${ALERTMANAGER_TELEGRAM_CHAT_ID}\n"
)


class _StubSiteConfig:
    """Minimal SiteConfig stand-in — only .get() is exercised here."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


# ---------------------------------------------------------------------------
# Protocol / metadata
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms_to_job_protocol(self):
        assert isinstance(RenderAlertmanagerConfigJob(), Job)

    def test_metadata(self):
        j = RenderAlertmanagerConfigJob()
        assert j.name == "render_alertmanager_config"
        assert j.schedule == "every 5 minutes"
        assert j.idempotent is True


# ---------------------------------------------------------------------------
# render_template (pure)
# ---------------------------------------------------------------------------


class TestRenderTemplate:
    def test_placeholder_substituted_and_gone(self):
        rendered = render_template(_TEMPLATE, "-1001234567890")
        assert "-1001234567890" in rendered
        # The placeholder MUST be fully gone — this is the public-mirror
        # leak guard for the committed template.
        assert CHAT_ID_PLACEHOLDER not in rendered
        assert "${ALERTMANAGER_TELEGRAM_CHAT_ID}" not in rendered

    def test_idempotent_no_placeholder_noop(self):
        # A body with no placeholder is returned unchanged.
        body = "receivers: []\n"
        assert render_template(body, "123") == body


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRun:
    async def test_renders_writes_and_clears_placeholder(
        self, tmp_path, monkeypatch
    ):
        tmpl = tmp_path / "alertmanager.yml.tmpl"
        tmpl.write_text(_TEMPLATE, encoding="utf-8")
        out = tmp_path / "config" / "alertmanager.yml"

        async def fake_reload(_url):
            return True, "alertmanager reloaded"

        monkeypatch.setattr(
            "services.jobs.render_alertmanager_config._reload_alertmanager",
            fake_reload,
        )

        result = await RenderAlertmanagerConfigJob().run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "alertmanager_url": "http://am:9093",
                "reload_on_change": True,
                "_site_config": _StubSiteConfig({"telegram_chat_id": "-100999"}),
            },
        )

        assert isinstance(result, JobResult)
        assert result.ok is True
        assert result.changes_made == 1
        written = out.read_text(encoding="utf-8")
        assert "-100999" in written
        assert CHAT_ID_PLACEHOLDER not in written
        assert "alertmanager reloaded" in result.detail

    async def test_reload_failure_makes_job_not_ok(self, tmp_path, monkeypatch):
        """A written-but-not-reloaded config is a delivery-plane failure:
        the job must report ok=False so the scheduler escalates it (#304)."""
        tmpl = tmp_path / "alertmanager.yml.tmpl"
        tmpl.write_text(_TEMPLATE, encoding="utf-8")
        out = tmp_path / "config" / "alertmanager.yml"

        async def fake_reload(_url):
            return False, "reload returned 400 (config NOT live)"

        monkeypatch.setattr(
            "services.jobs.render_alertmanager_config._reload_alertmanager",
            fake_reload,
        )

        result = await RenderAlertmanagerConfigJob().run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "reload_on_change": True,
                "_site_config": _StubSiteConfig({"telegram_chat_id": "-100999"}),
            },
        )

        assert result.ok is False  # the config is on disk but NOT live
        assert result.changes_made == 1  # write still happened
        assert "NOT live" in result.detail
        assert out.read_text(encoding="utf-8").find("-100999") != -1

    async def test_missing_chat_id_fails_loud(self, tmp_path):
        tmpl = tmp_path / "alertmanager.yml.tmpl"
        tmpl.write_text(_TEMPLATE, encoding="utf-8")
        out = tmp_path / "config" / "alertmanager.yml"

        result = await RenderAlertmanagerConfigJob().run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "_site_config": _StubSiteConfig({"telegram_chat_id": ""}),
            },
        )

        assert result.ok is False
        assert "telegram_chat_id" in result.detail
        # Must NOT write a half-rendered config (placeholder would remain).
        assert not out.exists()

    async def test_no_change_is_noop(self, tmp_path, monkeypatch):
        tmpl = tmp_path / "alertmanager.yml.tmpl"
        tmpl.write_text(_TEMPLATE, encoding="utf-8")
        out = tmp_path / "config" / "alertmanager.yml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_template(_TEMPLATE, "-100777"), encoding="utf-8")

        async def boom_reload(_url):
            raise AssertionError("reload should not run when content unchanged")

        monkeypatch.setattr(
            "services.jobs.render_alertmanager_config._reload_alertmanager",
            boom_reload,
        )

        result = await RenderAlertmanagerConfigJob().run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "reload_on_change": True,
                "_site_config": _StubSiteConfig({"telegram_chat_id": "-100777"}),
            },
        )

        assert result.ok is True
        assert result.changes_made == 0
        assert result.detail == "alertmanager config unchanged"

    async def test_missing_template_returns_not_ok(self, tmp_path):
        out = tmp_path / "config" / "alertmanager.yml"
        result = await RenderAlertmanagerConfigJob().run(
            pool=None,
            config={
                "template_path": str(tmp_path / "does-not-exist.tmpl"),
                "output_path": str(out),
                "_site_config": _StubSiteConfig({"telegram_chat_id": "-1"}),
            },
        )
        assert result.ok is False
        assert "template read failed" in result.detail
        assert not out.exists()

    async def test_reload_skipped_when_disabled(self, tmp_path, monkeypatch):
        tmpl = tmp_path / "alertmanager.yml.tmpl"
        tmpl.write_text(_TEMPLATE, encoding="utf-8")
        out = tmp_path / "config" / "alertmanager.yml"

        async def boom_reload(_url):
            raise AssertionError("reload should be skipped")

        monkeypatch.setattr(
            "services.jobs.render_alertmanager_config._reload_alertmanager",
            boom_reload,
        )

        result = await RenderAlertmanagerConfigJob().run(
            pool=None,
            config={
                "template_path": str(tmpl),
                "output_path": str(out),
                "reload_on_change": False,
                "_site_config": _StubSiteConfig({"telegram_chat_id": "-1"}),
            },
        )
        assert result.ok is True
        assert result.changes_made == 1
        assert "reload skipped" in result.detail


# ---------------------------------------------------------------------------
# _reload_alertmanager
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    posts: list[str] = []

    def __init__(self, response, **_kwargs):
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
class TestReloadAlertmanager:
    async def test_returns_reloaded_on_200(self, monkeypatch):
        _FakeAsyncClient.posts = []
        monkeypatch.setattr(
            job_module.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeAsyncClient(_FakeResponse(200), **kwargs),
        )
        ok, detail = await _reload_alertmanager("http://am:9093")
        assert ok is True
        assert detail == "alertmanager reloaded"
        assert _FakeAsyncClient.posts == ["http://am:9093/-/reload"]

    async def test_returns_status_code_on_non_200(self, monkeypatch):
        _FakeAsyncClient.posts = []
        monkeypatch.setattr(
            job_module.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeAsyncClient(_FakeResponse(405), **kwargs),
        )
        ok, detail = await _reload_alertmanager("http://am:9093")
        assert ok is False
        assert "reload returned 405" in detail

    async def test_handles_http_error(self, monkeypatch):
        monkeypatch.setattr(
            job_module.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeAsyncClient(
                httpx.ConnectError("connection refused"), **kwargs
            ),
        )
        ok, detail = await _reload_alertmanager("http://am:9093")
        assert ok is False
        assert detail.startswith("reload failed:")
        assert "connection refused" in detail
