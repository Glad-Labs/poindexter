"""Unit tests for ``services/integrations/telegram_cli_passthrough.py``.

Covers each safety branch:

- Non-/cli messages return None (no-op).
- Bare ``/cli`` with no args returns None (no-op).
- Unauthorized chat_id is rejected silently (returns None — no leakage).
- Kill-switch (``telegram_cli_enabled=false``) replies with a denial.
- Allowlist gate rejects unknown subcommands.
- Hard-deny tokens (``rm``, ``--force``, ``mcp``) are rejected anywhere.
- ``settings set <secret-key>`` is rejected.
- Allowed command dispatches to the runner + replies with output.
- Output truncation respects ``telegram_cli_max_output_chars``.
- Timeout is rendered with a clear message.
- Audit row is written for both invocations and denials.
- Audit can be disabled via ``telegram_cli_audit_logged=false``.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import telegram_cli_passthrough as tcp


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeSiteConfig:
    """Minimal SiteConfig stand-in — sync .get(key, default) only."""

    def __init__(self, **values: str):
        # Default to "the system is configured for Matt's chat 12345".
        self._values: dict[str, str] = {
            "telegram_chat_id": "12345",
            "telegram_cli_enabled": "true",
            "telegram_cli_safe_commands": (
                "post,settings,validators,auth,check_health,"
                "get_post_count,health,version"
            ),
            "telegram_cli_max_output_chars": "3500",
            "telegram_cli_timeout_seconds": "30",
            "telegram_cli_audit_logged": "true",
        }
        self._values.update(values)

    def get(self, key: str, default: Any = "") -> Any:
        if key in self._values:
            return self._values[key]
        return default


class _FakeAuditLogger:
    def __init__(self):
        self.rows: list[dict[str, Any]] = []

    async def log(
        self,
        event_type: str,
        source: str,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        self.rows.append({
            "event_type": event_type,
            "source": source,
            "details": dict(details or {}),
            "severity": severity,
        })


def _make_runner(
    *,
    output: str = "ok",
    exit_code: int = 0,
    duration_s: float = 0.05,
    timed_out: bool = False,
):
    """Build an injectable runner that returns a canned ``_RunResult``."""
    captured: dict[str, Any] = {}

    async def _runner(args: list[str], timeout_s: int) -> tcp._RunResult:
        captured["args"] = args
        captured["timeout_s"] = timeout_s
        return tcp._RunResult(
            exit_code=exit_code,
            output=output,
            duration_s=duration_s,
            timed_out=timed_out,
        )

    _runner.captured = captured  # type: ignore[attr-defined]
    return _runner


# ---------------------------------------------------------------------------
# 1. Non-/cli messages are passthrough no-ops.
# ---------------------------------------------------------------------------


class TestNonCliMessages:
    @pytest.mark.asyncio
    async def test_plain_text_returns_none(self):
        reply = await tcp.handle_cli_message(
            "hello there",
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is None

    @pytest.mark.asyncio
    async def test_other_slash_command_returns_none(self):
        reply = await tcp.handle_cli_message(
            "/health",
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is None

    @pytest.mark.asyncio
    async def test_clipped_prefix_returns_none(self):
        # `/clitter` looks like /cli but is its own word — must NOT match.
        reply = await tcp.handle_cli_message(
            "/clitter foo",
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is None

    @pytest.mark.asyncio
    async def test_bare_cli_no_args_returns_none(self):
        # `/cli` alone (no whitespace + args) is treated as a non-trigger.
        reply = await tcp.handle_cli_message(
            "/cli",
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is None


# ---------------------------------------------------------------------------
# 2. Authentication.
# ---------------------------------------------------------------------------


class TestAuth:
    @pytest.mark.asyncio
    async def test_unauthorized_chat_id_silent_reject(self):
        """Unknown chat_id MUST get None back — never reveal /cli exists."""
        audit = _FakeAuditLogger()
        reply = await tcp.handle_cli_message(
            "/cli check_health",
            "99999",  # not the configured chat
            site_config=_FakeSiteConfig(telegram_chat_id="12345"),
            audit_logger=audit,
        )
        assert reply is None
        # No audit row either — silent reject is silent.
        assert audit.rows == []

    @pytest.mark.asyncio
    async def test_missing_chat_id_setting_silent_reject(self):
        """If telegram_chat_id is empty, EVERY caller is unauthorized."""
        reply = await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(telegram_chat_id=""),
        )
        assert reply is None


# ---------------------------------------------------------------------------
# 3. Kill switch.
# ---------------------------------------------------------------------------


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_disabled_replies_with_explanation(self):
        audit = _FakeAuditLogger()
        reply = await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(telegram_cli_enabled="false"),
            audit_logger=audit,
        )
        assert reply is not None
        assert "disabled" in reply.text.lower()
        # Denial should still be audited.
        assert any(r["event_type"] == "telegram_cli_denied" for r in audit.rows)


# ---------------------------------------------------------------------------
# 4. Allowlist gate.
# ---------------------------------------------------------------------------


class TestAllowlist:
    @pytest.mark.asyncio
    async def test_unknown_subcommand_rejected(self):
        reply = await tcp.handle_cli_message(
            "/cli reformat-everything",
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is not None
        assert "not on the safe list" in reply.text

    @pytest.mark.asyncio
    async def test_allowlist_is_configurable(self):
        """Operator can override the allowlist via app_settings."""
        runner = _make_runner(output="ran")
        reply = await tcp.handle_cli_message(
            "/cli topics list",
            "12345",
            site_config=_FakeSiteConfig(
                telegram_cli_safe_commands="topics,post",
            ),
            runner=runner,
        )
        assert reply is not None
        assert "exit=0" in reply.text


# ---------------------------------------------------------------------------
# 5. Hard-deny tokens.
# ---------------------------------------------------------------------------


class TestDenyTokens:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [
        "/cli rm -rf /",
        "/cli post drop",
        "/cli settings delete some_key",
        "/cli post approve abc --force",
        "/cli mcp ping",
    ])
    async def test_deny_tokens_rejected(self, bad):
        reply = await tcp.handle_cli_message(
            bad,
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is not None
        # Either deny-token reason or, if subcommand is also unknown,
        # allowlist reason. Both are denials, both are correct outcomes.
        assert "denied" in reply.text.lower()

    @pytest.mark.asyncio
    async def test_deny_token_blocks_even_allowlisted_top(self):
        """`post` is allowlisted but `--force` anywhere kills the request."""
        reply = await tcp.handle_cli_message(
            "/cli post approve abc --force",
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is not None
        assert "deny list" in reply.text


# ---------------------------------------------------------------------------
# 6. Secret-write guard.
# ---------------------------------------------------------------------------


class TestSecretWriteGuard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", [
        "telegram_bot_token",
        "openai_api_key",
        "smtp_password",
        "database_url",
        "vercel_secret",
    ])
    async def test_settings_set_secret_key_rejected(self, key):
        reply = await tcp.handle_cli_message(
            f"/cli settings set {key} hunter2",
            "12345",
            site_config=_FakeSiteConfig(),
        )
        assert reply is not None
        assert "secret" in reply.text.lower()

    @pytest.mark.asyncio
    async def test_settings_set_plain_key_allowed(self):
        runner = _make_runner(output="updated")
        reply = await tcp.handle_cli_message(
            "/cli settings set pipeline_writer_model glm-4.7-5090",
            "12345",
            site_config=_FakeSiteConfig(),
            runner=runner,
        )
        assert reply is not None
        assert "exit=0" in reply.text

    @pytest.mark.asyncio
    async def test_settings_get_always_allowed(self):
        runner = _make_runner(output="value: glm-4.7-5090")
        reply = await tcp.handle_cli_message(
            "/cli settings get pipeline_writer_model",
            "12345",
            site_config=_FakeSiteConfig(),
            runner=runner,
        )
        assert reply is not None
        assert "exit=0" in reply.text
        assert "glm-4.7-5090" in reply.text


# ---------------------------------------------------------------------------
# 7. Successful dispatch.
# ---------------------------------------------------------------------------


class TestSuccessfulDispatch:
    @pytest.mark.asyncio
    async def test_runner_receives_tokenized_args(self):
        runner = _make_runner(output="all good")
        reply = await tcp.handle_cli_message(
            "/cli post show 8f8227ae",
            "12345",
            site_config=_FakeSiteConfig(),
            runner=runner,
        )
        assert reply is not None
        assert runner.captured["args"] == ["post", "show", "8f8227ae"]
        assert "all good" in reply.text

    @pytest.mark.asyncio
    async def test_quoted_arg_preserved(self):
        runner = _make_runner(output="rejected")
        reply = await tcp.handle_cli_message(
            '/cli post reject 8f8227ae --reason "bad title"',
            "12345",
            site_config=_FakeSiteConfig(),
            runner=runner,
        )
        assert reply is not None
        # shlex tokenization keeps the quoted phrase as one arg.
        assert "bad title" in runner.captured["args"]

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_still_replies(self):
        runner = _make_runner(output="nope", exit_code=2)
        reply = await tcp.handle_cli_message(
            "/cli post show abc",
            "12345",
            site_config=_FakeSiteConfig(),
            runner=runner,
        )
        assert reply is not None
        assert "exit=2" in reply.text
        assert "nope" in reply.text


# ---------------------------------------------------------------------------
# 8. Output truncation.
# ---------------------------------------------------------------------------


class TestOutputTruncation:
    @pytest.mark.asyncio
    async def test_long_output_truncated(self):
        big = "x" * 10_000
        runner = _make_runner(output=big)
        reply = await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(telegram_cli_max_output_chars="500"),
            runner=runner,
        )
        assert reply is not None
        # Truncation marker present.
        assert "output truncated" in reply.text
        # Total length under the configured cap (with small margin for header).
        assert len(reply.text) <= 800  # cap + header + marker headroom

    @pytest.mark.asyncio
    async def test_short_output_not_truncated(self):
        runner = _make_runner(output="tiny")
        reply = await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(telegram_cli_max_output_chars="3500"),
            runner=runner,
        )
        assert reply is not None
        assert "truncated" not in reply.text
        assert "tiny" in reply.text


# ---------------------------------------------------------------------------
# 9. Timeout.
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timed_out_flag_renders_clear_message(self):
        runner = _make_runner(output="partial...", timed_out=True, duration_s=30.0)
        reply = await tcp.handle_cli_message(
            "/cli post show abc",
            "12345",
            site_config=_FakeSiteConfig(telegram_cli_timeout_seconds="30"),
            runner=runner,
        )
        assert reply is not None
        assert "timed out" in reply.text.lower()

    @pytest.mark.asyncio
    async def test_timeout_setting_passed_to_runner(self):
        runner = _make_runner()
        await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(telegram_cli_timeout_seconds="7"),
            runner=runner,
        )
        assert runner.captured["timeout_s"] == 7


# ---------------------------------------------------------------------------
# 10. Audit logging.
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_invocation_audited(self):
        audit = _FakeAuditLogger()
        runner = _make_runner(output="ok")
        await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(),
            audit_logger=audit,
            runner=runner,
        )
        assert len(audit.rows) == 1
        row = audit.rows[0]
        assert row["event_type"] == "telegram_cli_invoked"
        assert row["source"] == "telegram_cli"
        assert row["details"]["chat_id"] == "12345"
        assert "check_health" in row["details"]["command"]
        assert row["details"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_denial_audited(self):
        audit = _FakeAuditLogger()
        await tcp.handle_cli_message(
            "/cli rm -rf /",
            "12345",
            site_config=_FakeSiteConfig(),
            audit_logger=audit,
        )
        assert len(audit.rows) == 1
        assert audit.rows[0]["event_type"] == "telegram_cli_denied"
        assert audit.rows[0]["severity"] == "warning"
        assert "deny" in audit.rows[0]["details"]["reason"].lower()

    @pytest.mark.asyncio
    async def test_audit_disabled_writes_nothing(self):
        audit = _FakeAuditLogger()
        runner = _make_runner(output="ok")
        await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(telegram_cli_audit_logged="false"),
            audit_logger=audit,
            runner=runner,
        )
        assert audit.rows == []

    @pytest.mark.asyncio
    async def test_audit_logger_none_doesnt_crash(self):
        runner = _make_runner(output="ok")
        reply = await tcp.handle_cli_message(
            "/cli check_health",
            "12345",
            site_config=_FakeSiteConfig(),
            audit_logger=None,
            runner=runner,
        )
        assert reply is not None
        assert "exit=0" in reply.text


# ---------------------------------------------------------------------------
# 11. Setting parsers — defensive coverage.
# ---------------------------------------------------------------------------


class TestSettingParsers:
    def test_int_setting_falls_back_on_garbage(self):
        cfg = _FakeSiteConfig(telegram_cli_timeout_seconds="not-a-number")
        assert tcp._int_setting(cfg, "telegram_cli_timeout_seconds", 30) == 30

    def test_bool_setting_accepts_common_truthy(self):
        for v in ("true", "True", "1", "yes", "on"):
            cfg = _FakeSiteConfig(telegram_cli_enabled=v)
            assert tcp._bool_setting(cfg, "telegram_cli_enabled", False) is True

    def test_bool_setting_accepts_common_falsy(self):
        for v in ("false", "False", "0", "no", "off"):
            cfg = _FakeSiteConfig(telegram_cli_enabled=v)
            assert tcp._bool_setting(cfg, "telegram_cli_enabled", True) is False

    def test_csv_setting_normalizes_whitespace_and_case(self):
        cfg = _FakeSiteConfig(
            telegram_cli_safe_commands="  Post , Settings ,VALIDATORS  ",
        )
        result = tcp._csv_setting(
            cfg, "telegram_cli_safe_commands", "x",
        )
        assert result == frozenset({"post", "settings", "validators"})


# ---------------------------------------------------------------------------
# 12. Format helpers.
# ---------------------------------------------------------------------------


class TestFormatters:
    def test_format_reply_no_output(self):
        result = tcp._RunResult(
            exit_code=0, output="", duration_s=0.1, timed_out=False,
        )
        out = tcp._format_reply(result, max_chars=3500)
        assert "no output" in out
        assert "exit=0" in out

    def test_format_denial_includes_reason(self):
        msg = tcp._format_denial("widget broke")
        assert "widget broke" in msg
        assert msg.startswith("/cli denied")

    def test_safe_command_line_strips_prefix_and_caps_length(self):
        # 600-char body — safe_command_line caps at 500.
        body = "x" * 600
        out = tcp._safe_command_line(f"/cli {body}")
        assert len(out) == 500
        assert not out.startswith("/cli")
