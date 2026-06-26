"""Unit tests for ``poindexter media open``.

The dispatch keys on ``sys.platform`` so the test swaps the platform
value via ``monkeypatch.setattr`` and asserts the right OS branch fires.
``os.startfile`` (Windows-only attribute) is patched onto the ``os``
module so the test runs on any host.

``media open`` now looks up ``media_assets.storage_path`` from the DB
and translates the container path to the host path, so tests mock
``_resolve_open_path`` rather than constructing the path themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _import_media_module():
    """Import the CLI module fresh each test."""
    if "poindexter.cli.media" in sys.modules:
        del sys.modules["poindexter.cli.media"]
    from poindexter.cli import media as media_module
    return media_module


# ---------------------------------------------------------------------------
# _translate_container_path — pure path translation
# ---------------------------------------------------------------------------


def test_translate_container_path_strips_prefix():
    media = _import_media_module()
    host_base = Path.home() / ".poindexter"

    result = media._translate_container_path(
        "/home/appuser/.poindexter/video/abc123_short.mp4"
    )
    assert result == host_base / "video" / "abc123_short.mp4"


def test_translate_container_path_passthrough_for_host_paths():
    """Paths that don't start with the container prefix are returned as-is."""
    media = _import_media_module()
    host_path = str(Path.home() / ".poindexter" / "podcast" / "abc.mp3")

    result = media._translate_container_path(host_path)
    assert result == Path(host_path)


# ---------------------------------------------------------------------------
# OS dispatch — each platform routes through its native opener
# ---------------------------------------------------------------------------


def test_open_windows_uses_os_startfile(monkeypatch, runner, tmp_path):
    """``sys.platform=='win32'`` → ``os.startfile`` fires."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-123456789012"
    media_path = tmp_path / "podcast" / f"{post_id}.mp3"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"fake mp3")

    monkeypatch.setattr(sys, "platform", "win32")
    import os as _os
    mock_startfile = MagicMock()
    monkeypatch.setattr(_os, "startfile", mock_startfile, raising=False)

    with patch.object(
        media, "_resolve_open_path",
        new=AsyncMock(return_value=(post_id, media_path)),
    ), patch.object(media.subprocess, "run") as mock_run:
        result = runner.invoke(media.media_group, ["open", post_id, "podcast"])

    assert result.exit_code == 0, result.output
    mock_startfile.assert_called_once_with(str(media_path))
    mock_run.assert_not_called()


def test_open_macos_uses_open_command(monkeypatch, runner, tmp_path):
    """``sys.platform=='darwin'`` → ``subprocess.run(['open', ...])`` fires."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-123456789012"
    media_path = tmp_path / "video" / f"{post_id}.mp4"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"fake mp4")

    monkeypatch.setattr(sys, "platform", "darwin")

    with patch.object(
        media, "_resolve_open_path",
        new=AsyncMock(return_value=(post_id, media_path)),
    ), patch.object(media.subprocess, "run") as mock_run:
        result = runner.invoke(media.media_group, ["open", post_id, "video"])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == ["open", str(media_path)]


def test_open_linux_uses_xdg_open(monkeypatch, runner, tmp_path):
    """``sys.platform=='linux'`` → ``subprocess.run(['xdg-open', ...])``."""
    media = _import_media_module()

    # Task-UUID-named short — the key scenario that was broken before the fix.
    task_uuid = "4a4b9054-3d65-46fc-a9f1-391c9d0e25ba"
    post_id = "12345678-1234-1234-1234-123456789012"
    media_path = tmp_path / "video" / f"{task_uuid}_short.mp4"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"fake short mp4")

    monkeypatch.setattr(sys, "platform", "linux")

    with patch.object(
        media, "_resolve_open_path",
        new=AsyncMock(return_value=(post_id, media_path)),
    ), patch.object(media.subprocess, "run") as mock_run:
        result = runner.invoke(media.media_group, ["open", post_id, "video_short"])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == ["xdg-open", str(media_path)]


# ---------------------------------------------------------------------------
# Validation — bad post_id / missing file fail loud, no silent default
# ---------------------------------------------------------------------------


def test_open_unresolvable_post_id_raises_click_error(runner):
    """A post_id that resolves to no post fails loud, never a silent
    'file not found'."""
    import click
    media = _import_media_module()

    with patch.object(
        media, "_resolve_open_path",
        new=AsyncMock(side_effect=click.BadParameter(
            "no post matches 'not-a-uuid'", param_hint="post_id",
        )),
    ):
        result = runner.invoke(media.media_group, ["open", "not-a-uuid", "podcast"])

    assert result.exit_code != 0
    assert "no post matches" in result.output or "no post matches" in str(
        result.exception
    )


def test_open_prefix_resolves_then_opens(monkeypatch, runner, tmp_path):
    """An 8-char prefix resolves to the full UUID via DB, then the file opens.

    Tests the DB lookup path inside ``_resolve_open_path``: prefix resolution
    via ``resolve_uuid_prefix`` + storage_path via the service layer.
    Mocks ``media_approval_service.get_asset_storage_path`` — the service
    boundary — rather than the raw pool.fetchrow call (which is an
    implementation detail of the service, not the CLI).
    """
    import types
    media = _import_media_module()

    full = "12345678-1234-1234-1234-123456789012"
    container_path = f"/home/appuser/.poindexter/podcast/{full}.mp3"
    expected_host = media._translate_container_path(container_path)
    expected_host.parent.mkdir(parents=True, exist_ok=True)
    expected_host.write_bytes(b"fake mp3")

    monkeypatch.setattr(sys, "platform", "win32")
    import os as _os
    mock_startfile = MagicMock()
    monkeypatch.setattr(_os, "startfile", mock_startfile, raising=False)

    pool = MagicMock()
    pool.close = AsyncMock()

    fake_service = types.ModuleType("services.media_approval_service")
    fake_service.get_asset_storage_path = AsyncMock(return_value=container_path)  # type: ignore[attr-defined]

    # `from services import media_approval_service` resolves via the package
    # attribute, not just sys.modules — patch both, same pattern as
    # TestDecideResolvesPrefix.
    import services as _services_pkg

    with patch.object(
        media, "_make_pool", new=AsyncMock(return_value=pool),
    ), patch.object(
        media, "resolve_uuid_prefix", new=AsyncMock(return_value=full),
    ), patch.dict(
        sys.modules, {"services.media_approval_service": fake_service},
    ), patch.object(
        _services_pkg, "media_approval_service", fake_service, create=True,
    ), patch.object(media.subprocess, "run"):
        result = runner.invoke(media.media_group, ["open", "12345678", "podcast"])

    assert result.exit_code == 0, result.output
    mock_startfile.assert_called_once_with(str(expected_host))
    fake_service.get_asset_storage_path.assert_awaited_once_with(pool, full, "podcast")


def test_open_no_asset_record_prints_clean_error(runner):
    """``None`` host_path (no media_assets row) → friendly error, exit 2."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-aaaaaaaaaaaa"

    with patch.object(
        media, "_resolve_open_path",
        new=AsyncMock(return_value=(post_id, None)),
    ):
        result = runner.invoke(media.media_group, ["open", post_id, "video"])

    assert result.exit_code == 2
    assert "poindexter media pending" in result.output


def test_open_missing_file_prints_clean_error_and_exits_nonzero(runner, tmp_path):
    """Path returned by DB doesn't exist on disk → friendly error, exit 2."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-aaaaaaaaaaaa"
    nonexistent = tmp_path / "video" / "missing.mp4"

    with patch.object(
        media, "_resolve_open_path",
        new=AsyncMock(return_value=(post_id, nonexistent)),
    ):
        result = runner.invoke(media.media_group, ["open", post_id, "podcast"])

    assert result.exit_code == 2
    assert "No file at" in result.output
    assert "poindexter media pending" in result.output


def test_open_invalid_medium_rejected_by_click(runner):
    """``click.Choice`` rejects mediums outside ``_VALID_MEDIA``."""
    media = _import_media_module()

    post_id = "12345678-1234-1234-1234-123456789012"
    result = runner.invoke(media.media_group, ["open", post_id, "audio"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid choice" in result.output.lower()


# ---------------------------------------------------------------------------
# Short post_id prefix resolution — operators paste the 8-char prefix that
# ``media pending`` prints, but media_approvals.post_id is a UUID column.
# Before the fix, asyncpg crashed encoding the prefix as a UUID.
# ---------------------------------------------------------------------------


def _make_pool_with(*, exact=None, prefix_rows=None):
    """asyncpg pool double: ``.acquire()`` yields a conn whose ``.fetchrow``
    returns ``exact`` (the exact-match branch) and ``.fetch`` returns
    ``prefix_rows`` (the LIKE branch)."""
    from contextlib import asynccontextmanager

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=exact)
    conn.fetch = AsyncMock(return_value=list(prefix_rows or []))

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


class TestDecideResolvesPrefix:
    def test_approve_resolves_prefix_before_decide(self, runner, monkeypatch):
        """``media approve <prefix> <medium>`` resolves to the full UUID and
        hands THAT to the service — never the bare prefix."""
        import types
        media = _import_media_module()
        full = "b938661c-1111-2222-3333-444455556666"

        pool = _make_pool_with(exact=None, prefix_rows=[{"post_id": full}])
        # pool.close() is awaited in _decide's finally block.
        pool.close = AsyncMock()

        async def _fake_pool():
            return pool

        monkeypatch.setattr(media, "_make_pool", _fake_pool)

        decide_calls: list[tuple] = []

        async def _fake_decide(_db, post_id, medium, **kwargs):
            decide_calls.append((post_id, medium, kwargs))

        fake_service = types.ModuleType("services.media_approval_service")
        fake_service.decide = _fake_decide  # type: ignore[attr-defined]
        monkeypatch.setitem(
            sys.modules, "services.media_approval_service", fake_service
        )
        # _decide does `from services import media_approval_service`; make the
        # attribute resolve to our fake on the parent package too.
        import services as _services_pkg
        monkeypatch.setattr(
            _services_pkg, "media_approval_service", fake_service, raising=False
        )

        result = runner.invoke(media.media_group, ["approve", "b938661c", "video"])

        assert result.exit_code == 0, result.output
        assert len(decide_calls) == 1
        # The service got the FULL uuid, not the 8-char prefix.
        assert decide_calls[0][0] == full
        assert decide_calls[0][1] == "video"
        assert "approved: video for post b938661c" in result.output
