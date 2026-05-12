"""
Unit tests for the structlog + stdlib secret-redaction processor.

Closes audit-2026-05-12 P1 #12 — `services/logger_config.py` previously
configured structlog with no redaction step, so any caller doing
`logger.info("loaded", token=tok)` would leak the secret to Loki + disk.

These tests exercise the processor function directly with synthetic
event_dicts; they do NOT touch structlog's global configuration.
"""

from __future__ import annotations

import logging

import services.logger_config as lc


# ---------------------------------------------------------------------------
# KEY-based redaction
# ---------------------------------------------------------------------------


class TestKeyBasedRedaction:
    def test_top_level_token_key_masked(self):
        out = lc.redact_secrets(None, "info", {"event": "loaded", "token": "abc123"})
        assert out["token"] == lc.REDACTED_VALUE
        # Non-secret fields preserved.
        assert out["event"] == "loaded"

    def test_password_api_key_authorization_all_masked(self):
        event = {
            "password": "hunter2",
            "api_key": "ak_live_xxx",
            "api-key": "ak_live_yyy",
            "authorization": "anything",
            "cookie": "session=abc",
            "client_secret": "cs_xxx",
            "signing_key": "sk_xxx",
            "access_key": "AKIA...",
            "x-api-key": "xax_xxx",
        }
        out = lc.redact_secrets(None, "info", dict(event))
        for k in event:
            assert out[k] == lc.REDACTED_VALUE, f"key {k!r} not redacted"

    def test_suffix_match_discord_ops_webhook_url(self):
        # `discord_ops_webhook_url` matches via the `webhook_url`
        # alternative *and* the dedicated `discord_ops_webhook` alternative.
        out = lc.redact_secrets(
            None,
            "info",
            {"discord_ops_webhook_url": "https://discord.com/api/webhooks/xxx"},
        )
        assert out["discord_ops_webhook_url"] == lc.REDACTED_VALUE

    def test_indexnow_key_masked(self):
        out = lc.redact_secrets(
            None, "info", {"indexnow_key": "abcd1234deadbeef"}
        )
        assert out["indexnow_key"] == lc.REDACTED_VALUE

    def test_bearer_key_name_masked(self):
        out = lc.redact_secrets(None, "info", {"bearer": "anything"})
        assert out["bearer"] == lc.REDACTED_VALUE

    def test_dsn_key_masked(self):
        # `sentry_dsn` matches via the `dsn` alternative.
        out = lc.redact_secrets(
            None, "info", {"sentry_dsn": "https://abc@sentry.io/1"}
        )
        assert out["sentry_dsn"] == lc.REDACTED_VALUE

    def test_x_revalidate_secret_masked(self):
        out = lc.redact_secrets(
            None, "info", {"x_revalidate_secret": "supersecret"}
        )
        assert out["x_revalidate_secret"] == lc.REDACTED_VALUE
        out2 = lc.redact_secrets(
            None, "info", {"x-revalidate-secret": "supersecret"}
        )
        assert out2["x-revalidate-secret"] == lc.REDACTED_VALUE


# ---------------------------------------------------------------------------
# Nested + list redaction (recursion)
# ---------------------------------------------------------------------------


class TestNestedRedaction:
    def test_nested_dict_authorization_masked(self):
        event = {
            "event": "webhook_received",
            "data": {"authorization": "Bearer xyz", "url": "https://safe"},
        }
        out = lc.redact_secrets(None, "info", event)
        # Inner authorization key got the mask.
        assert out["data"]["authorization"] == lc.REDACTED_VALUE
        # Sibling non-secret key passed through.
        assert out["data"]["url"] == "https://safe"

    def test_list_of_dicts_headers_pattern(self):
        # The "headers = [{name, value}]" pattern is common in webhook
        # logging. The "name": "X-Api-Key" entry is just metadata — the
        # leaky one is "value". With current key-based rules, the
        # dict-level keys are "name" and "value", neither of which match.
        # That's WHY we have value-shape detection. The value here doesn't
        # match a prefix either — but if it's a Bearer header the value
        # detector catches it. Confirm both behaviours.

        # Case A: explicit X-Api-Key as dict KEY (the common nested pattern).
        event = {
            "headers": {"X-Api-Key": "ak_live_xxx", "Content-Type": "json"},
        }
        out = lc.redact_secrets(None, "info", event)
        assert out["headers"]["X-Api-Key"] == lc.REDACTED_VALUE
        assert out["headers"]["Content-Type"] == "json"

        # Case B: list-of-dicts shape.
        event2 = {
            "headers": [
                {"name": "X-Api-Key", "value": "ak_live_xxx"},
                {"name": "Content-Type", "value": "application/json"},
            ]
        }
        out2 = lc.redact_secrets(None, "info", event2)
        # The "value" entry next to "name": "X-Api-Key" is a header value
        # that the operator likely doesn't want logged. We don't have a
        # cross-field rule for that pair (would require key+sibling-key
        # inspection). We DO catch it if the value-shape is bearer-like.
        # For now: confirm the structure is preserved and at minimum
        # the non-secret-named row is preserved.
        assert out2["headers"][1] == {
            "name": "Content-Type",
            "value": "application/json",
        }

    def test_deeply_nested_secret_masked(self):
        event = {
            "outer": {
                "middle": {
                    "inner": {"token": "leak_me"},
                }
            }
        }
        out = lc.redact_secrets(None, "info", event)
        assert out["outer"]["middle"]["inner"]["token"] == lc.REDACTED_VALUE


# ---------------------------------------------------------------------------
# Value-shape detection
# ---------------------------------------------------------------------------


class TestValueShapeDetection:
    def test_bearer_value_in_generic_key_masked(self):
        # `data` is not a secret-name; only the VALUE shape gives it away.
        out = lc.redact_secrets(
            None, "info", {"data": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"}
        )
        assert out["data"] == lc.REDACTED_VALUE

    def test_langfuse_public_key_masked_by_shape(self):
        out = lc.redact_secrets(
            None, "info", {"loaded": "pk-lf-abc123def456"}
        )
        assert out["loaded"] == lc.REDACTED_VALUE

    def test_langfuse_secret_key_masked_by_shape(self):
        out = lc.redact_secrets(
            None, "info", {"loaded": "sk-lf-abc123def456"}
        )
        assert out["loaded"] == lc.REDACTED_VALUE

    def test_poindexter_token_masked_by_shape(self):
        out = lc.redact_secrets(
            None, "info", {"loaded": "pdx_a1b2c3d4e5"}
        )
        assert out["loaded"] == lc.REDACTED_VALUE

    def test_envelope_encrypted_value_masked_by_shape(self):
        # plugins/secrets.py wraps secrets in `enc:v1:...` — even though
        # they're already encrypted, no reason to dump the ciphertext
        # to logs.
        out = lc.redact_secrets(
            None, "info", {"raw_setting": "enc:v1:gAAAAA..."}
        )
        assert out["raw_setting"] == lc.REDACTED_VALUE


# ---------------------------------------------------------------------------
# False positives
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    def test_count_int_not_masked(self):
        out = lc.redact_secrets(None, "info", {"count": 42})
        assert out["count"] == 42

    def test_url_string_not_masked(self):
        out = lc.redact_secrets(
            None, "info", {"url": "https://gladlabs.io/post/abc"}
        )
        assert out["url"] == "https://gladlabs.io/post/abc"

    def test_normal_message_not_masked(self):
        out = lc.redact_secrets(
            None, "info", {"event": "task_started", "task_id": "t-123"}
        )
        assert out["event"] == "task_started"
        assert out["task_id"] == "t-123"

    def test_nested_non_secret_preserved(self):
        event = {
            "request": {"path": "/api/foo", "method": "GET", "status": 200}
        }
        out = lc.redact_secrets(None, "info", event)
        assert out["request"] == {"path": "/api/foo", "method": "GET", "status": 200}

    def test_none_and_empty_string_passed_through(self):
        out = lc.redact_secrets(
            None, "info", {"event": "boot", "extra": None, "note": ""}
        )
        assert out["extra"] is None
        assert out["note"] == ""


# ---------------------------------------------------------------------------
# Robustness — exceptions don't crash the logger
# ---------------------------------------------------------------------------


class TestRedactorRobustness:
    def test_circular_reference_does_not_recurse_infinitely(self, capsys):
        # Build a self-referential dict. The depth cap (5 levels) should
        # cut the recursion off — the redactor returns the dict (degraded
        # redaction is better than infinite recursion).
        a: dict = {"event": "loop"}
        a["self"] = a
        out = lc.redact_secrets(None, "info", a)
        # The outer dict should still be returned (no crash).
        assert isinstance(out, dict)
        assert out["event"] == "loop"

    def test_unhashable_objects_in_dict_dont_crash(self):
        class Weird:
            def __repr__(self):
                raise RuntimeError("explode on repr")

        event = {"obj": Weird(), "token": "abc"}
        # Must not raise.
        out = lc.redact_secrets(None, "info", event)
        # Token still got masked even though Weird is alongside.
        assert out["token"] == lc.REDACTED_VALUE

    def test_exception_in_walk_falls_back_to_original_dict(self, monkeypatch, capsys):
        # Force _redact_walk to raise. The top-level handler should catch
        # it, write a warning to stderr, and return the dict unchanged.
        def boom(*_args, **_kwargs):
            raise RuntimeError("synthetic explosion")

        monkeypatch.setattr(lc, "_redact_walk", boom)
        original = {"event": "x", "data": {"nested": "value"}}
        out = lc.redact_secrets(None, "info", dict(original))
        # The processor returned without raising.
        assert out == original
        captured = capsys.readouterr()
        assert "secret-redaction processor failed" in captured.err

    def test_depth_cap_prevents_runaway(self):
        # Build a 20-deep nested dict ending in a secret. The cap is 5,
        # so beyond level 5 the secret won't get masked — but we MUST
        # not crash, and the outer levels MUST still be processed.
        event: dict = {"token": "outermost_secret"}
        current = event
        for i in range(20):
            current["nested"] = {}
            current = current["nested"]
        current["token"] = "deeply_nested_secret"

        out = lc.redact_secrets(None, "info", event)
        # Outer token always masked (level 0).
        assert out["token"] == lc.REDACTED_VALUE
        # Function returned a dict without raising — that's the bar.
        assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# Stdlib SecretRedactionFilter
# ---------------------------------------------------------------------------


class TestStdlibFilter:
    def _make_record(self, **extras) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        for k, v in extras.items():
            setattr(record, k, v)
        return record

    def test_filter_masks_extra_token_attr(self):
        f = lc.SecretRedactionFilter()
        record = self._make_record(token="leak_me", count=5)
        assert f.filter(record) is True
        assert record.token == lc.REDACTED_VALUE
        assert record.count == 5

    def test_filter_masks_bearer_value_in_generic_attr(self):
        f = lc.SecretRedactionFilter()
        record = self._make_record(data="Bearer xyz")
        f.filter(record)
        assert record.data == lc.REDACTED_VALUE

    def test_filter_masks_args_tuple(self):
        f = lc.SecretRedactionFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="header was %s",
            args=("Bearer abc",),
            exc_info=None,
        )
        f.filter(record)
        assert record.args == (lc.REDACTED_VALUE,)

    def test_filter_masks_dict_args(self):
        f = lc.SecretRedactionFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="loaded %(token)s",
            args={"token": "abc", "count": 1},
            exc_info=None,
        )
        f.filter(record)
        assert record.args["token"] == lc.REDACTED_VALUE
        assert record.args["count"] == 1

    def test_filter_does_not_touch_internal_attrs(self):
        f = lc.SecretRedactionFilter()
        record = self._make_record(token="leak_me")
        original_msg = record.msg
        original_levelname = record.levelname
        f.filter(record)
        # Internal LogRecord attrs are untouched.
        assert record.msg == original_msg
        assert record.levelname == original_levelname

    def test_filter_returns_true_even_on_exception(self, monkeypatch, capsys):
        # Force an internal failure — the filter must still return True
        # (otherwise the record gets dropped, which is worse than
        # logging it unredacted).
        def boom(*_a, **_kw):
            raise RuntimeError("synthetic")

        monkeypatch.setattr(lc, "_redact_walk", boom)
        f = lc.SecretRedactionFilter()
        record = self._make_record(payload={"nested": "value"})
        result = f.filter(record)
        assert result is True
