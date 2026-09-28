"""``services.taps.runner`` hands each tap the run's ``SiteConfig``.

``MemoryFilesTap`` and the Claude Code sessions tap read app-level settings
(``claude_projects_dir``, ``openclaw_memory_dir``, ``shared_context_dir``) from
``config["_site_config"]``, the DI seam the other plugin dispatchers seed (the
scheduler for jobs, the external-tap handlers for topic sources). Those reads
were added in May 2026 (glad-labs-stack#330) but the tap runner was never
taught to seed the key, so on the auto-embed sidecar the fallback could not run.
Every one of those settings was a dead read: setting it changed nothing, and no
error said so.

These tests drive the real ``run_all`` -> ``run_tap`` -> ``PluginConfig.load``
path. Only the embed-and-store tail is stubbed, because the seam under test is
what ``tap.extract`` receives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.plugins.config import PluginConfig
from poindexter.services.site_config import SiteConfig
from poindexter.services.taps import runner as runner_mod
from poindexter.services.taps.memory import MemoryFilesTap

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _empty_home(monkeypatch, tmp_path_factory) -> None:
    """No test here may read the developer's real home directory.

    Before the seam was wired, the end-to-end test below ignored its setting and
    fell through to the memory tap's built-in defaults, ingesting whatever
    directories the machine running it happened to have.
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))


class _RecordingTap:
    """A tap that records the config ``extract`` was handed and yields nothing."""

    name = "recorder"
    interval_seconds = 0

    def __init__(self) -> None:
        self.seen: dict[str, Any] | None = None

    async def extract(self, pool: Any, config: dict[str, Any]):
        self.seen = config
        return
        yield  # pragma: no cover - makes this an async generator


def _pool(*, tap_row: dict[str, Any] | None = None, settings: dict[str, str] | None = None):
    """A pool serving ``SiteConfig.load``, ``PluginConfig.load`` and the flush."""
    rows = [
        {"key": k, "value": v, "deprecated": False, "superseded_by": None}
        for k, v in (settings or {}).items()
    ]
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 0")
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchval = AsyncMock(return_value=json.dumps(tap_row) if tap_row is not None else None)
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


def _run_only(monkeypatch, *taps: Any) -> None:
    monkeypatch.setattr(runner_mod, "get_taps", lambda: list(taps))
    monkeypatch.setattr(runner_mod, "get_core_samples", lambda: {"taps": []})


class TestRunTapSeedsSiteConfig:
    async def test_the_tap_receives_the_site_config(self):
        tap = _RecordingTap()
        site_config = SiteConfig(initial_config={"shared_context_dir": "/notes"})

        await runner_mod.run_tap(tap, _pool(), MagicMock(), site_config=site_config)

        assert tap.seen is not None
        assert tap.seen["_site_config"] is site_config

    async def test_the_row_config_still_reaches_the_tap(self):
        tap = _RecordingTap()
        pool = _pool(tap_row={"enabled": True, "config": {"memory_scope_allowlist": "a,b"}})

        await runner_mod.run_tap(tap, pool, MagicMock(), site_config=SiteConfig())

        assert tap.seen is not None
        assert tap.seen["memory_scope_allowlist"] == "a,b"

    async def test_without_a_site_config_the_row_config_is_passed_as_is(self):
        """Back-compat: a caller that passes no site_config sees no new key."""
        tap = _RecordingTap()
        pool = _pool(tap_row={"enabled": True, "config": {"memory_scope_allowlist": "a"}})

        await runner_mod.run_tap(tap, pool, MagicMock())

        assert tap.seen == {"memory_scope_allowlist": "a"}

    async def test_the_persisted_plugin_config_is_not_mutated(self, monkeypatch):
        """``PluginConfig.save`` json-dumps ``config``; a SiteConfig in it would crash."""
        cfg = PluginConfig(plugin_type="tap", name="recorder", config={"k": "v"})
        monkeypatch.setattr(PluginConfig, "load", AsyncMock(return_value=cfg))
        tap = _RecordingTap()

        await runner_mod.run_tap(tap, _pool(), MagicMock(), site_config=SiteConfig())

        assert cfg.config == {"k": "v"}
        assert tap.seen is not None and "_site_config" in tap.seen
        json.dumps(cfg.config)  # would raise TypeError if the SiteConfig had leaked in

    async def test_a_stored_key_cannot_shadow_the_reserved_one(self):
        tap = _RecordingTap()
        pool = _pool(tap_row={"enabled": True, "config": {"_site_config": "junk"}})
        site_config = SiteConfig()

        await runner_mod.run_tap(tap, pool, MagicMock(), site_config=site_config)

        assert tap.seen is not None
        assert tap.seen["_site_config"] is site_config


class TestRunAllSeedsSiteConfig:
    async def test_every_tap_gets_the_pass_site_config(self, monkeypatch):
        tap = _RecordingTap()
        _run_only(monkeypatch, tap)

        await runner_mod.run_all(_pool(), MagicMock())

        assert tap.seen is not None
        assert isinstance(tap.seen["_site_config"], SiteConfig)

    async def test_it_is_the_instance_the_pass_loaded_from_the_database(self, monkeypatch):
        """The tap sees the tunables the run just loaded, not an empty SiteConfig."""
        tap = _RecordingTap()
        _run_only(monkeypatch, tap)
        pool = _pool(settings={"shared_context_dir": "/from/the/database"})

        await runner_mod.run_all(pool, MagicMock())

        assert tap.seen is not None
        assert tap.seen["_site_config"].get("shared_context_dir") == "/from/the/database"

    async def test_a_failed_site_config_load_still_runs_the_tap(self, monkeypatch):
        """No SiteConfig means no seam key, and the tap still runs on its defaults."""
        tap = _RecordingTap()
        _run_only(monkeypatch, tap)
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=ConnectionResetError("reset by peer"))

        summary = await runner_mod.run_all(pool, MagicMock())

        assert [t.name for t in summary.taps] == ["recorder"]
        assert tap.seen is not None


class TestASettingReachesTheMemoryTap:
    """The end-to-end claim: setting the key in ``app_settings`` changes what is ingested."""

    async def test_shared_context_dir_set_in_app_settings_is_ingested(
        self, monkeypatch, tmp_path: Path
    ):
        notes = tmp_path / "team-notes"
        notes.mkdir()
        (notes / "handoff.md").write_text("# Handoff\nship it\n", encoding="utf-8")
        stored: list[str] = []

        async def _store(mem, pool, doc, **_kw):
            stored.append(doc.source_id)
            return "embedded"

        monkeypatch.setattr(runner_mod, "_store_document", _store)
        monkeypatch.setattr(
            runner_mod, "_batch_existing_chunk0_hashes_for", AsyncMock(return_value={})
        )
        _run_only(monkeypatch, MemoryFilesTap())
        pool = _pool(
            # The other two sources are pinned off so the test never reads the
            # developer's real home; the shared dir comes from app_settings only.
            tap_row={
                "enabled": True,
                "config": {"claude_projects_dir": "__skip__", "openclaw_memory_dir": "__skip__"},
            },
            settings={
                "shared_context_dir": str(notes),
                "tap_interval_enforcement_enabled": "false",
            },
        )

        await runner_mod.run_all(pool, MagicMock())

        assert stored == ["shared-context/handoff.md"]

    async def test_the_tap_config_still_beats_the_setting(self, monkeypatch, tmp_path: Path):
        from_setting = tmp_path / "from-setting"
        from_setting.mkdir()
        (from_setting / "setting.md").write_text("from the setting", encoding="utf-8")
        from_tap = tmp_path / "from-tap"
        from_tap.mkdir()
        (from_tap / "tap.md").write_text("from the tap row", encoding="utf-8")
        stored: list[str] = []

        async def _store(mem, pool, doc, **_kw):
            stored.append(doc.source_id)
            return "embedded"

        monkeypatch.setattr(runner_mod, "_store_document", _store)
        monkeypatch.setattr(
            runner_mod, "_batch_existing_chunk0_hashes_for", AsyncMock(return_value={})
        )
        _run_only(monkeypatch, MemoryFilesTap())
        pool = _pool(
            tap_row={
                "enabled": True,
                "config": {
                    "claude_projects_dir": "__skip__",
                    "openclaw_memory_dir": "__skip__",
                    "shared_context_dir": str(from_tap),
                },
            },
            settings={
                "shared_context_dir": str(from_setting),
                "tap_interval_enforcement_enabled": "false",
            },
        )

        await runner_mod.run_all(pool, MagicMock())

        assert stored == ["shared-context/tap.md"]
