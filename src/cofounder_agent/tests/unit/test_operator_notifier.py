"""Unit tests for brain/operator_notifier.py (#198).

No network allowed — Telegram/Discord calls are patched to a stub. The
alerts log is redirected to a tmp_path so we don't pollute the user's
real ~/.poindexter/alerts.log.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# brain/ lives at the repo root; walk up until we find it.
_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "brain" / "operator_notifier.py").is_file():
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
        break

from brain import operator_notifier


@pytest.fixture
def isolated_notifier(monkeypatch, tmp_path):
    """Redirect alerts.log and clear external-channel env vars."""
    monkeypatch.setattr(operator_notifier, "_ALERTS_LOG", tmp_path / "alerts.log")
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DISCORD_OPS_WEBHOOK_URL",
        "DISCORD_LAB_LOGS_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


class TestNotifyChannels:
    def test_stderr_always_written(self, isolated_notifier, capsys):
        operator_notifier.notify_operator(
            title="test-title",
            detail="test-detail",
            source="unit-test",
            severity="info",
        )
        err = capsys.readouterr().err
        assert "test-title" in err
        assert "test-detail" in err
        assert "unit-test" in err

    def test_alerts_log_written(self, isolated_notifier):
        operator_notifier.notify_operator(
            title="hello",
            detail="world",
            source="unit",
            severity="error",
        )
        log_path = isolated_notifier / "alerts.log"
        assert log_path.is_file()
        content = log_path.read_text(encoding="utf-8")
        assert "hello" in content
        assert "world" in content

    def test_telegram_skipped_when_unset(self, isolated_notifier):
        results = operator_notifier.notify_operator(
            title="x", detail="y", source="u", severity="error",
        )
        assert "TELEGRAM_BOT_TOKEN" in results["telegram"]

    def test_discord_skipped_when_unset(self, isolated_notifier):
        results = operator_notifier.notify_operator(
            title="x", detail="y", source="u", severity="error",
        )
        assert "DISCORD" in results["discord"]


class TestSeverityEmoji:
    @pytest.mark.parametrize(
        "sev,expected_glyphs",
        [
            ("info", "ℹ️"),
            ("warning", "⚠️"),
            ("error", "🔴"),
            ("critical", "🚨"),
        ],
    )
    def test_emoji_per_severity(self, isolated_notifier, capsys, sev, expected_glyphs):
        operator_notifier.notify_operator(
            title="t", detail="d", source="s", severity=sev,
        )
        err = capsys.readouterr().err
        assert expected_glyphs in err


class TestDoesNotRaise:
    def test_never_raises_even_when_everything_fails(self, monkeypatch, tmp_path, capsys):
        """The whole point of notify_operator is graceful degradation."""
        # Point alerts log at a read-only path. Note: alerts log failure
        # should be recovered silently, not propagated.
        bogus = tmp_path / "nonexistent-dir" / "alerts.log"
        # Make the parent unwritable by NOT creating it — mkdir in the
        # notifier will succeed here, so force a failure by monkeypatching
        # Path.mkdir to raise.
        monkeypatch.setattr(operator_notifier, "_ALERTS_LOG", bogus)

        original_mkdir = Path.mkdir

        def _failing_mkdir(self, *a, **kw):
            if self == bogus.parent:
                raise PermissionError("stub: unwritable")
            return original_mkdir(self, *a, **kw)

        monkeypatch.setattr(Path, "mkdir", _failing_mkdir)

        # Should not raise:
        result = operator_notifier.notify_operator(
            title="t", detail="d", source="s", severity="critical",
        )
        assert "failed" in result["alerts_log"]
