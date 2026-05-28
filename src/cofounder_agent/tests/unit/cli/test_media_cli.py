"""Unit tests for ``poindexter media open``.

The dispatch keys on ``sys.platform`` so the test swaps the platform
value via ``monkeypatch.setattr`` and asserts the right OS branch fires.
``os.startfile`` (Windows-only attribute) is patched onto the ``os``
module so the test runs on any host.

The PODCAST_DIR / VIDEO_DIR imports are stubbed via ``sys.modules`` so
this test doesn't pull torch / ffmpeg helpers into the unit-test
process.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _stub_producing_services(tmp_path, monkeypatch):
    """Stub ``services.podcast_service`` / ``services.video_service`` to
    expose only the ``PODCAST_DIR`` / ``VIDEO_DIR`` constants the CLI
    needs.

    Real modules import torch + httpx-bound TTS clients; lazy-import in
    the CLI helper means stubbing the module satisfies the contract.
    """
    podcast_mod = types.ModuleType("services.podcast_service")
    podcast_mod.PODCAST_DIR = tmp_path / "podcast"  # type: ignore[attr-defined]
    (tmp_path / "podcast").mkdir(parents=True, exist_ok=True)

    video_mod = types.ModuleType("services.video_service")
    video_mod.VIDEO_DIR = tmp_path / "video"  # type: ignore[attr-defined]
    (tmp_path / "video").mkdir(parents=True, exist_ok=True)

    monkeypatch.setitem(sys.modules, "services.podcast_service", podcast_mod)
    monkeypatch.setitem(sys.modules, "services.video_service", video_mod)
    yield tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _import_media_module():
    """Import the CLI module fresh so the fixture's sys.modules stubs land."""
    # Drop cached imports so the test gets the stubbed modules.
    if "poindexter.cli.media" in sys.modules:
        del sys.modules["poindexter.cli.media"]
    from poindexter.cli import media as media_module
    return media_module


# ---------------------------------------------------------------------------
# OS dispatch — each platform routes through its native opener
# ---------------------------------------------------------------------------


def test_open_windows_uses_os_startfile(monkeypatch, runner, _stub_producing_services):
    """``sys.platform=='win32'`` → ``os.startfile`` fires."""
    media = _import_media_module()

    # Drop a file at the expected path so the existence check passes.
    post_id = "12345678-1234-1234-1234-123456789012"
    media_path = _stub_producing_services / "podcast" / f"{post_id}.mp3"
    media_path.write_bytes(b"fake mp3")

    monkeypatch.setattr(sys, "platform", "win32")
    # ``os.startfile`` is Windows-only; inject a Mock onto the os module
    # so the test runs on Linux / macOS hosts too.
    import os as _os
    mock_startfile = MagicMock()
    monkeypatch.setattr(_os, "startfile", mock_startfile, raising=False)

    with patch.object(media.subprocess, "run") as mock_run:
        result = runner.invoke(media.media_group, ["open", post_id, "podcast"])

    assert result.exit_code == 0, result.output
    mock_startfile.assert_called_once_with(str(media_path))
    mock_run.assert_not_called()


def test_open_macos_uses_open_command(monkeypatch, runner, _stub_producing_services):
    """``sys.platform=='darwin'`` → ``subprocess.run(['open', ...])`` fires."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-123456789012"
    media_path = _stub_producing_services / "video" / f"{post_id}.mp4"
    media_path.write_bytes(b"fake mp4")

    monkeypatch.setattr(sys, "platform", "darwin")

    with patch.object(media.subprocess, "run") as mock_run:
        result = runner.invoke(media.media_group, ["open", post_id, "video"])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    args = mock_run.call_args.args[0]
    assert args == ["open", str(media_path)]


def test_open_linux_uses_xdg_open(monkeypatch, runner, _stub_producing_services):
    """``sys.platform=='linux'`` → ``subprocess.run(['xdg-open', ...])``."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-123456789012"
    media_path = _stub_producing_services / "video" / f"{post_id}-short.mp4"
    media_path.write_bytes(b"fake short mp4")

    monkeypatch.setattr(sys, "platform", "linux")

    with patch.object(media.subprocess, "run") as mock_run:
        result = runner.invoke(media.media_group, ["open", post_id, "video_short"])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    args = mock_run.call_args.args[0]
    assert args == ["xdg-open", str(media_path)]


# ---------------------------------------------------------------------------
# Validation — bad post_id / missing file fail loud, no silent default
# ---------------------------------------------------------------------------


def test_open_invalid_post_id_raises_click_error(runner, _stub_producing_services):
    """A non-UUID post_id triggers Click's BadParameter handling."""
    media = _import_media_module()

    result = runner.invoke(media.media_group, ["open", "not-a-uuid", "podcast"])

    # Click returns exit_code=2 for BadParameter via UsageError.
    assert result.exit_code != 0
    assert "post_id must be a UUID" in result.output or "must be a UUID" in str(
        result.exception
    )


def test_open_missing_file_prints_clean_error_and_exits_nonzero(
    runner, _stub_producing_services,
):
    """Path doesn't exist → friendly error mentioning ``poindexter media pending``."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-aaaaaaaaaaaa"
    # No file written; default path won't exist.

    mock_startfile = MagicMock()
    # ``raising=False`` lets the patch attach to a host OS where the
    # attribute is missing (Linux / macOS); plain ``patch.object`` would
    # reject the missing attribute.
    media.os.startfile = mock_startfile  # type: ignore[attr-defined]
    with patch.object(media.subprocess, "run") as mock_run:
        result = runner.invoke(media.media_group, ["open", post_id, "podcast"])

    assert result.exit_code == 2
    assert "No file at" in result.output
    assert "poindexter media pending" in result.output
    mock_run.assert_not_called()
    mock_startfile.assert_not_called()


def test_open_invalid_medium_rejected_by_click(runner, _stub_producing_services):
    """``click.Choice`` rejects mediums outside ``_VALID_MEDIA``."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-123456789012"
    result = runner.invoke(media.media_group, ["open", post_id, "audio"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid choice" in result.output.lower()
