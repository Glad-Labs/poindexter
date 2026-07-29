"""Unit tests for `poindexter media tts-bakeoff` — mocked renders, no sidecars."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

# Bind `poindexter.cli.media` at RUN TIME inside each test — never at module
# top. Sibling tests (test_media_cli.py's `_import_media_module`,
# test_media_decide_prefix.py) `del sys.modules["poindexter.cli.media"]` to
# inject a fake `media_approval_service`. A top-level
# `from ...media import media_group` would capture the PRE-eviction module
# instance; a later `patch("poindexter.cli.media....")` would then patch a
# DIFFERENT instance than the command runs from, the patch would silently
# no-op, and the CLI would hit the real DB (asyncpg :5432). Importing the
# module object at run time and using `patch.object(media_mod, ...)` +
# `media_mod.media_group` keeps patch-target and command on one instance
# regardless of test order.


@pytest.mark.unit
class TestTtsBakeoff:
    def test_renders_each_engine_and_writes_manifest(self, tmp_path):
        from poindexter.cli import media as media_mod

        async def _fake_render_one(engine, text, voice, out_path, site_config, **_):
            out_path.write_bytes(f"{engine}-audio".encode())
            return {"engine": engine, "path": str(out_path),
                    "bytes": out_path.stat().st_size, "duration_s": 12}

        with patch.object(media_mod, "_render_bakeoff_engine",
                          new=AsyncMock(side_effect=_fake_render_one)), \
             patch.object(media_mod, "_make_bakeoff_site_config",
                          new=AsyncMock(return_value=object())):
            runner = CliRunner()
            result = runner.invoke(media_mod.media_group, [
                "tts-bakeoff",
                "--engines", "speaches,chatterbox",
                "--out-dir", str(tmp_path),
            ])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "speaches.mp3").read_bytes() == b"speaches-audio"
        assert (tmp_path / "chatterbox.mp3").read_bytes() == b"chatterbox-audio"
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert {row["engine"] for row in manifest} == {"speaches", "chatterbox"}

    def test_one_dead_engine_does_not_abort_the_others(self, tmp_path):
        from poindexter.cli import media as media_mod

        async def _fake_render_one(engine, text, voice, out_path, site_config, **_):
            if engine == "chatterbox":
                raise RuntimeError("sidecar down")
            out_path.write_bytes(b"ok")
            return {"engine": engine, "path": str(out_path), "bytes": 2, "duration_s": 1}

        with patch.object(media_mod, "_render_bakeoff_engine",
                          new=AsyncMock(side_effect=_fake_render_one)), \
             patch.object(media_mod, "_make_bakeoff_site_config",
                          new=AsyncMock(return_value=object())):
            runner = CliRunner()
            result = runner.invoke(media_mod.media_group, [
                "tts-bakeoff", "--engines", "speaches,chatterbox", "--out-dir", str(tmp_path),
            ])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "speaches.mp3").exists()      # good engine still rendered
        assert not (tmp_path / "chatterbox.mp3").exists()  # failed engine skipped
        assert "sidecar down" in result.output              # failure surfaced

    def test_make_bakeoff_site_config_falls_back_when_db_unreachable(self):
        """Offline bake-off: a down/absent DB yields a default SiteConfig, not a crash.

        The command docstring promises it "does NOT touch ... the DB"; every
        setting it reads has an inline default, so the DB load is best-effort.
        """
        from poindexter.cli import media as media_mod

        with patch.object(media_mod, "_make_pool",
                          new=AsyncMock(side_effect=OSError("connection refused"))):
            cfg = asyncio.run(media_mod._make_bakeoff_site_config())

        # A usable SiteConfig: an unset key returns the caller-supplied default.
        assert cfg.get("plugin.tts_provider.chatterbox.exaggeration",
                       "sentinel-default") == "sentinel-default"

    def test_engine_config_uses_host_published_url_not_docker_internal(self):
        """The host-run bake-off must reach engines at their published host ports,
        not the docker-internal `<service>:8000` the in-container pipeline uses."""
        from poindexter.cli.media import _bakeoff_base_url, _bakeoff_engine_config

        class _SC:
            def get(self, k, d=None):
                return {"plugin.tts_provider.chatterbox.exaggeration": "0.7"}.get(k, d)

        cfg = _bakeoff_engine_config("chatterbox", _SC(), "localhost")
        assert cfg["base_url"] == "http://localhost:8011/v1"  # NOT chatterbox:8000
        assert cfg["model"] == "chatterbox"
        assert cfg["exaggeration"] == "0.7"
        # --host override flows through for other registered engines too
        # (e.g. a tailnet IP; doc-range IP here) — not hardcoded to chatterbox.
        assert _bakeoff_base_url("speaches", "198.51.100.10") == "http://198.51.100.10:8001/v1"

    def test_engine_config_forwards_chatterbox_audio_prompt_path(self):
        """A pinned voice-clone reference must reach the bake-off render too —
        the README's ``poindexter media tts-bakeoff --engines chatterbox``
        verification step is only meaningful if the CLI forwards the same
        setting the live podcast pipeline reads."""
        from poindexter.cli.media import _bakeoff_engine_config

        class _SC:
            def get(self, k, d=None):
                return {
                    "plugin.tts_provider.chatterbox.audio_prompt_path":
                        "/app/voices/podcast-voice.wav",
                }.get(k, d)

        cfg = _bakeoff_engine_config("chatterbox", _SC(), "localhost")
        assert cfg["audio_prompt_path"] == "/app/voices/podcast-voice.wav"

    def test_engine_config_forwards_chatterbox_atempo(self):
        """Pace must reach the bake-off render too. atempo is applied by the
        live pipeline, so omitting it here makes the preview systematically
        FASTER than the episode it is supposed to be previewing — the same
        honesty requirement audio_prompt_path has."""
        from poindexter.cli.media import _bakeoff_engine_config

        class _SC:
            def get(self, k, d=None):
                return {
                    "plugin.tts_provider.chatterbox.atempo": "0.92",
                }.get(k, d)

        cfg = _bakeoff_engine_config("chatterbox", _SC(), "localhost")
        assert cfg["atempo"] == "0.92"
