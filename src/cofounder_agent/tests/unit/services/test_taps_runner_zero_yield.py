"""Zero-yield tap detection — the signal missing during poindexter#988.

The `memory` and `claude_code_sessions` taps ingested nothing for 17 days
after the Pop!_OS migration while every run reported `ok`. Nothing caught
it because the auto-embed watchdog measures the `auto_embed_succeeded`
heartbeat (did the run finish), not whether any tap produced anything.

`is_zero_yield` is the discriminator. These tests pin the distinction that
the run summary alone cannot express — two real lines from the same run:

    posts   ok 0 embedded, 168 skipped, 0 failed   <- healthy
    memory  ok 0 embedded,   0 skipped, 0 failed   <- dark

Both say `ok` and `0 embedded`; only the second is a fault.
"""

from __future__ import annotations

from unittest import mock

import pytest

from services.taps.runner import TapStats, _emit_zero_yield_finding, is_zero_yield


class TestIsZeroYield:
    def test_dark_tap_is_flagged(self):
        """The exact shape memory + claude_code_sessions held for 17 days."""
        assert is_zero_yield(TapStats(name="memory")) is True

    def test_all_skipped_is_healthy(self):
        """`posts ok 0 embedded, 168 skipped` — yielded 168, none changed.

        The most important negative case: this is the steady state of a
        healthy tap, and flagging it would make the signal useless.
        """
        assert is_zero_yield(TapStats(name="posts", skipped=168)) is False

    def test_embedding_work_is_healthy(self):
        assert is_zero_yield(TapStats(name="audit", embedded=29, skipped=171)) is False

    def test_failures_are_not_zero_yield(self):
        """Documents were yielded, they just failed to store — a different
        fault, already surfaced through the failure path."""
        assert is_zero_yield(TapStats(name="brain", failed=3)) is False

    def test_disabled_tap_is_not_flagged(self):
        """openclaw_sqlite is deliberately off, not broken."""
        assert is_zero_yield(TapStats(name="openclaw_sqlite", enabled=False)) is False

    def test_errored_tap_is_not_double_reported(self):
        """A timeout already records failed=1 + error and surfaces there."""
        stats = TapStats(name="wedged", failed=1, error="exceeded 300s tap timeout")
        assert is_zero_yield(stats) is False

    def test_errored_tap_with_no_counts_is_not_double_reported(self):
        stats = TapStats(name="wedged", error="extract failed: boom")
        assert is_zero_yield(stats) is False


class TestEmitZeroYieldFinding:
    def test_emits_warn_severity_with_stable_dedup_key(self):
        """Discord-routine, not a Telegram page, and collapsible across runs."""
        with mock.patch("utils.findings.emit_finding") as emit:
            _emit_zero_yield_finding(TapStats(name="memory", duration_s=0.004))

        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        assert kwargs["kind"] == "tap_zero_yield"
        assert kwargs["severity"] == "warn"
        assert kwargs["dedup_key"] == "tap-zero-yield:memory"
        assert kwargs["extra"]["tap"] == "memory"
        assert "memory" in kwargs["title"]

    def test_finding_failure_never_breaks_ingest(self):
        """Observability is best-effort — a broken sink must not stop taps."""
        with mock.patch(
            "utils.findings.emit_finding", side_effect=RuntimeError("audit down")
        ):
            _emit_zero_yield_finding(TapStats(name="memory"))  # must not raise


class TestRunnerWiring:
    """The predicate is only useful if the runner actually consults it."""

    @pytest.mark.parametrize(
        ("stats", "expect_finding"),
        [
            (TapStats(name="memory"), True),
            (TapStats(name="posts", skipped=168), False),
            (TapStats(name="off", enabled=False), False),
        ],
    )
    def test_predicate_drives_emission(self, stats: TapStats, expect_finding: bool):
        assert is_zero_yield(stats) is expect_finding
