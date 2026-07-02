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


class TestCredentialRedaction:
    """Credential-shaped substrings in detail never leave the notifier."""

    def test_database_url_password_masked(self, isolated_notifier, capsys):
        operator_notifier.notify_operator(
            title="cannot start",
            detail="Tried postgresql://poindexter:hunter2@localhost:5432/db and failed",
            source="unit",
            severity="error",
        )
        err = capsys.readouterr().err
        assert "hunter2" not in err
        assert "postgresql://poindexter:***@localhost:5432/db" in err

    def test_key_value_secret_masked(self, isolated_notifier, capsys):
        operator_notifier.notify_operator(
            title="config problem",
            detail="api_key=sk-live-abc123 rejected; also password: swordfish",
            source="unit",
            severity="error",
        )
        err = capsys.readouterr().err
        assert "sk-live-abc123" not in err
        assert "swordfish" not in err

    def test_plain_prose_untouched(self, isolated_notifier, capsys):
        detail = "Run `poindexter setup` to create bootstrap.toml interactively."
        operator_notifier.notify_operator(
            title="plain", detail=detail, source="unit", severity="info",
        )
        assert detail in capsys.readouterr().err

    def test_redact_helper_handles_url_without_password(self):
        text = "see https://example.com/docs for the fix"
        assert operator_notifier._redact_credentials(text) == text


class TestPageCooldown:
    """Repeat-suppression gate (2026-07-01 alert-noise audit).

    Repeats of the same dedup key inside the cooldown window skip the
    external Telegram/Discord sends; critical always bypasses; a failed
    send never starts the window (the next cycle retries).
    """

    @pytest.fixture
    def cooled_notifier(self, monkeypatch, tmp_path):
        monkeypatch.setattr(operator_notifier, "_ALERTS_LOG", tmp_path / "alerts.log")
        monkeypatch.setattr(operator_notifier, "_PAGE_COOLDOWN_SECONDS", 3600)
        monkeypatch.setattr(operator_notifier, "_LAST_PAGED_AT", {})
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            monkeypatch.delenv(var, raising=False)
        sends: list[str] = []

        def _spy_discord(text):
            sends.append(text)
            return True, "discord"

        monkeypatch.setattr(operator_notifier, "_try_discord", _spy_discord)
        return sends, tmp_path

    def _page(self, severity="warning", dedup_key=None, title="probe fired"):
        return operator_notifier.notify_operator(
            title=title,
            detail="detail",
            source="unit-test",
            severity=severity,
            dedup_key=dedup_key,
        )

    def test_repeat_same_key_suppressed(self, cooled_notifier):
        sends, _ = cooled_notifier
        first = self._page(dedup_key="cond-a")
        second = self._page(dedup_key="cond-a")
        assert len(sends) == 1
        assert first["discord"] == "discord"
        assert second["discord"] == "suppressed (page cooldown)"
        assert second["telegram"] == "suppressed (page cooldown)"

    def test_default_key_is_source_and_title(self, cooled_notifier):
        sends, _ = cooled_notifier
        self._page(title="same title")
        self._page(title="same title")
        self._page(title="different title")
        assert len(sends) == 2

    def test_distinct_keys_not_suppressed(self, cooled_notifier):
        sends, _ = cooled_notifier
        self._page(dedup_key="cond-a")
        self._page(dedup_key="cond-b")
        assert len(sends) == 2

    def test_critical_always_bypasses(self, cooled_notifier):
        sends, _ = cooled_notifier
        self._page(severity="critical", dedup_key="cond-a")
        self._page(severity="critical", dedup_key="cond-a")
        assert len(sends) == 2

    def test_failed_send_does_not_start_window(self, monkeypatch, tmp_path):
        monkeypatch.setattr(operator_notifier, "_ALERTS_LOG", tmp_path / "alerts.log")
        monkeypatch.setattr(operator_notifier, "_PAGE_COOLDOWN_SECONDS", 3600)
        monkeypatch.setattr(operator_notifier, "_LAST_PAGED_AT", {})
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            monkeypatch.delenv(var, raising=False)
        attempts: list[str] = []

        def _failing_discord(text):
            attempts.append(text)
            return False, "discord send failed: boom"

        monkeypatch.setattr(operator_notifier, "_try_discord", _failing_discord)
        operator_notifier.notify_operator(
            title="t", detail="d", source="s", severity="warning",
            dedup_key="cond-a",
        )
        operator_notifier.notify_operator(
            title="t", detail="d", source="s", severity="warning",
            dedup_key="cond-a",
        )
        assert len(attempts) == 2, "failed send must not swallow the retry"

    def test_disabled_gate_never_suppresses(self, cooled_notifier, monkeypatch):
        sends, _ = cooled_notifier
        monkeypatch.setattr(operator_notifier, "_PAGE_COOLDOWN_SECONDS", 0)
        self._page(dedup_key="cond-a")
        self._page(dedup_key="cond-a")
        assert len(sends) == 2

    def test_suppressed_repeat_still_lands_in_alerts_log(self, cooled_notifier):
        sends, tmp_path = cooled_notifier
        self._page(dedup_key="cond-a")
        self._page(dedup_key="cond-a")
        log_text = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert log_text.count("probe fired") == 2
        assert "suppressed repeat" in log_text

    def test_set_page_cooldown_seconds_clamps_negative(self, monkeypatch):
        monkeypatch.setattr(operator_notifier, "_PAGE_COOLDOWN_SECONDS", 0)
        operator_notifier.set_page_cooldown_seconds(-5)
        assert operator_notifier._PAGE_COOLDOWN_SECONDS == 0
        operator_notifier.set_page_cooldown_seconds(1800)
        assert operator_notifier._PAGE_COOLDOWN_SECONDS == 1800
        operator_notifier.set_page_cooldown_seconds(0)
