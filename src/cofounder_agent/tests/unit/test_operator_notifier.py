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

from brain import operator_notifier  # noqa: E402


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


class TestSeverityRouting:
    """Per feedback_telegram_vs_discord: Telegram = critical/error only,
    Discord = all severities. info/warning must NOT phone-ping the operator.

    Added 2026-05-20 after PR #485 fixed the Discord webhook hydration and
    immediately surfaced spam-to-Telegram on routine probe warnings.
    """

    @pytest.fixture
    def with_external_channels_configured(self, monkeypatch, tmp_path):
        """Like ``isolated_notifier`` but with non-empty TG + Discord env so
        the channel-attempt code path actually fires (instead of skipping
        on missing config)."""
        monkeypatch.setattr(operator_notifier, "_ALERTS_LOG", tmp_path / "alerts.log")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "FAKE-BOT-TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("DISCORD_OPS_WEBHOOK_URL", "https://example.invalid/webhook")
        return tmp_path

    @pytest.mark.parametrize("severity", ["info", "warning"])
    def test_telegram_skipped_for_low_severity(
        self, with_external_channels_configured, severity, monkeypatch,
    ):
        """info + warning must NEVER reach _try_telegram even with env set."""
        called = {"telegram": 0}

        def _spy_telegram(text):
            called["telegram"] += 1
            return False, "should not be called"

        monkeypatch.setattr(operator_notifier, "_try_telegram", _spy_telegram)
        results = operator_notifier.notify_operator(
            title="routine probe noise",
            detail="some probe is sad",
            source="probe_x",
            severity=severity,
        )
        assert called["telegram"] == 0, (
            f"_try_telegram should not be called for severity={severity}"
        )
        assert "skipped" in results["telegram"].lower()

    @pytest.mark.parametrize("severity", ["error", "critical"])
    def test_telegram_attempted_for_high_severity(
        self, with_external_channels_configured, severity, monkeypatch,
    ):
        """error + critical must attempt Telegram so the operator phone-pings."""
        called = {"telegram": 0}

        def _spy_telegram(text):
            called["telegram"] += 1
            return True, "telegram"

        monkeypatch.setattr(operator_notifier, "_try_telegram", _spy_telegram)
        operator_notifier.notify_operator(
            title="real failure",
            detail="something broke",
            source="probe_x",
            severity=severity,
        )
        assert called["telegram"] == 1, (
            f"_try_telegram MUST be called once for severity={severity}"
        )

    def test_discord_attempted_for_all_severities(
        self, with_external_channels_configured, monkeypatch,
    ):
        """Discord is the spam channel — every severity goes there."""
        called = {"discord": 0}

        def _spy_discord(text):
            called["discord"] += 1
            return True, "discord"

        monkeypatch.setattr(operator_notifier, "_try_discord", _spy_discord)
        for sev in ("info", "warning", "error", "critical"):
            operator_notifier.notify_operator(
                title="t", detail="d", source="s", severity=sev,
            )
        assert called["discord"] == 4


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
