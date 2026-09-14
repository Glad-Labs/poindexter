"""``poindexter personas`` — thin Click wrapper over persona_service, tested
with the pool factory and the site-config loader patched out (no DB)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli import personas as cli
from poindexter.services.site_config import SiteConfig


@pytest.fixture
def harness(monkeypatch):
    """run_service executes the factory against a dummy pool
    SiteConfig is
    seeded
    SettingsService.set is recorded."""
    import asyncio

    sc = SiteConfig(initial_config={
        "media_default_persona": "presenter",
        "podcast_tts_voice": "bf_emma",
        "persona.presenter.display_name": "Presenter",
        "persona.presenter.voice_provider": "kokoro",
        "persona.presenter.voice_id": "",
        "persona.presenter.style_policy": "photoreal",
        "persona.presenter.enabled": "true",
    })
    settings = MagicMock()
    settings.set = AsyncMock()

    def fake_run_service(factory):
        return asyncio.run(factory(object()))

    async def fake_site_config(pool):
        return sc

    monkeypatch.setattr(cli, "run_service", fake_run_service)
    monkeypatch.setattr(cli, "_site_config", fake_site_config)
    monkeypatch.setattr(cli, "_settings_service", lambda pool: settings)
    return sc, settings


def test_list_shows_default_and_inherited_voice(harness):
    out = CliRunner().invoke(cli.personas_group, ["list"])
    assert out.exit_code == 0, out.output
    assert "presenter" in out.output and "inherits podcast_tts_voice" in out.output and "default" in out.output


def test_list_json(harness):
    out = CliRunner().invoke(cli.personas_group, ["list", "--json"])
    rows = json.loads(out.output)
    assert rows[0]["slug"] == "presenter" and rows[0]["default"] is True


def test_show_missing_persona_fails(harness):
    out = CliRunner().invoke(cli.personas_group, ["show", "ghost"])
    assert out.exit_code != 0 and "no persona" in out.output


def test_set_validates_through_the_service(harness):
    _sc, settings = harness
    ok = CliRunner().invoke(cli.personas_group, ["set", "presenter", "voice_id", "bf_isabella"])
    assert ok.exit_code == 0, ok.output
    assert settings.set.await_args.args[:2] == ("persona.presenter.voice_id", "bf_isabella")
    bad = CliRunner().invoke(cli.personas_group, ["set", "presenter", "style_policy", "anime"])
    assert bad.exit_code != 0 and "style_policy" in bad.output


def test_create_writes_the_field_family(harness):
    _sc, settings = harness
    out = CliRunner().invoke(cli.personas_group, [
        "create", "host", "--display-name", "Host", "--voice-id", "bm_george", "--style", "stylized",
    ])
    assert out.exit_code == 0, out.output
    keys = {c.args[0] for c in settings.set.await_args_list}
    assert {"persona.host.display_name", "persona.host.voice_id", "persona.host.style_policy", "persona.host.enabled"} <= keys


def test_portrait_stores_url_prompt_and_seed(harness):
    _sc, settings = harness
    with patch.object(cli.ps, "render_portrait", AsyncMock(return_value=("https://cdn/p.png", "Editorial photograph…", "/tmp/p.png"))):
        out = CliRunner().invoke(cli.personas_group, ["portrait", "presenter", "--seed", "21"])
    assert out.exit_code == 0, out.output
    written = {c.args[0]: c.args[1] for c in settings.set.await_args_list}
    assert written["persona.presenter.portrait_url"] == "https://cdn/p.png"
    assert written["persona.presenter.portrait_seed"] == "21"
    assert written["persona.presenter.portrait_prompt"].startswith("Editorial")
