"""Regression tests: emoji/symbol CLI output must not crash on legacy-codepage
consoles (e.g. Windows ``cp1252``).

Commands like ``poindexter tasks rebuild-images`` print success/failure
glyphs (``✅ ❌ ⚠ ✋ →``) via ``click.secho``/``click.echo``. Click's Windows
ANSI-color wrapper (colorama) ultimately writes through the process's real
``sys.stdout``/``sys.stderr`` text streams, which on a plain Windows terminal
default to the legacy ``cp1252`` codepage rather than UTF-8. Any glyph outside
that codepage's repertoire raises ``UnicodeEncodeError`` and kills the CLI
mid-command — even after the underlying HTTP call already succeeded.

The fix reconfigures stdout/stderr to UTF-8 (``errors="replace"``) once at CLI
import (covers ``--help`` on the root group) and again at the top of the root
``main()`` callback (covers every subcommand — and is what makes this
testable, since ``CliRunner`` substitutes its own stdout/stderr per
invocation). ``CliRunner(charset="cp1252")`` substitutes real
``io.TextIOWrapper`` objects using that charset (``errors="strict"`` for
stdout), faithfully reproducing the crash in-process without needing an
actual Windows console.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli import _encoding as enc
from poindexter.cli.app import main as cli_main


def _fake_client(result):
    """AsyncMock WorkerClient whose json_or_raise yields ``result``."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = MagicMock()
    client.get.return_value = MagicMock()
    client.json_or_raise.return_value = result
    return client


@pytest.mark.unit
class TestEnsureUtf8Streams:
    def test_reconfigures_stdout_and_stderr_to_utf8_replace(self, monkeypatch):
        calls: list[tuple[str, dict]] = []

        class _FakeStream:
            def reconfigure(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr(enc.sys, "stdout", _FakeStream())
        monkeypatch.setattr(enc.sys, "stderr", _FakeStream())

        enc.ensure_utf8_streams()

        assert calls == [
            {"encoding": "utf-8", "errors": "replace"},
            {"encoding": "utf-8", "errors": "replace"},
        ]

    def test_skips_streams_without_reconfigure(self, monkeypatch):
        # e.g. a plain io.StringIO some harnesses substitute for stdout — must
        # not raise AttributeError.
        monkeypatch.setattr(enc.sys, "stdout", io.StringIO())
        monkeypatch.setattr(enc.sys, "stderr", io.StringIO())

        enc.ensure_utf8_streams()  # must not raise


@pytest.mark.unit
class TestCliRootAppliesEncodingFix:
    def test_root_group_reconfigures_before_dispatch(self, monkeypatch):
        # Mirrors TestCliRootAppliesSwitch in test_pipeline_resume_event_loop.py:
        # the root callback must apply the fix before dispatching to any
        # subcommand, including a bare `--help`.
        from poindexter.cli import app as app_mod

        called: list[bool] = []
        monkeypatch.setattr(app_mod, "ensure_utf8_streams", lambda: called.append(True))
        monkeypatch.setattr("poindexter.cli._bootstrap.ensure_secret_key", lambda: None)

        result = CliRunner().invoke(app_mod.main, ["costs", "--help"])

        assert result.exit_code == 0, result.output
        assert called == [True]


@pytest.mark.unit
class TestRebuildImagesSurvivesLegacyCodepage:
    """The exact reported repro: ``poindexter tasks rebuild-images <id>`` on a
    plain (non-UTF-8) Windows console."""

    def test_success_message_does_not_crash_on_cp1252_console(self, monkeypatch):
        monkeypatch.setattr("poindexter.cli._bootstrap.ensure_secret_key", lambda: None)
        client = _fake_client(
            {
                "ok": True,
                "task_id": "rebuild-task-123",
                "detail": "image rebuild queued as task rebuild-task-123",
            },
        )
        runner = CliRunner(charset="cp1252")
        with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
            result = runner.invoke(cli_main, ["tasks", "rebuild-images", "abc123"])

        assert result.exception is None, repr(result.exception)
        assert result.exit_code == 0, result.output

    def test_failure_message_does_not_crash_on_cp1252_console(self, monkeypatch):
        monkeypatch.setattr("poindexter.cli._bootstrap.ensure_secret_key", lambda: None)
        client = _fake_client(
            {
                "ok": False,
                "detail": "rebuild-images requires an awaiting_approval draft",
            },
        )
        runner = CliRunner(charset="cp1252")
        with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
            result = runner.invoke(cli_main, ["tasks", "rebuild-images", "abc123"])

        # Exits 1 (rebuild not queued) but must not crash with UnicodeEncodeError.
        assert result.exit_code == 1
        assert not isinstance(result.exception, UnicodeEncodeError)
