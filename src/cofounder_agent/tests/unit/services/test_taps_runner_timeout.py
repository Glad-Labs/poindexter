"""Unit tests for the per-tap timeout guard in ``services.taps.runner.run_all``.

``run_all`` iterates every registered Tap *sequentially* and there is no outer
deadline on the standalone ``auto-embed`` sidecar (it loops
``while true; python auto-embed.py; sleep 3600`` with no kill). A single Tap that
hangs on a stalled Ollama embed call or a wedged DB query would therefore freeze
the entire embedding pipeline until a human restarts the container — embeddings
silently stop updating.

These tests lock the bound: each ``run_tap`` is wrapped in a per-tap timeout
(``tap_run_timeout_seconds``, DB-tunable). A tap that exceeds it is cancelled,
recorded as a failed tap *with a reason* (feedback_no_silent_defaults), and the
run continues to the remaining taps.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.taps import runner as runner_mod

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeSiteConfig:
    """Hermetic stand-in so run_all's best-effort config read does no DB IO."""

    def __init__(self, pool=None):
        pass

    async def load(self, pool):
        return None

    def get_int(self, key, default):
        return default


def _patch_discovery(monkeypatch, taps):
    """Make run_all see exactly ``taps`` and nothing from the real registry."""
    monkeypatch.setattr(runner_mod, "get_taps", lambda: list(taps))
    monkeypatch.setattr(runner_mod, "get_core_samples", lambda: {"taps": []})
    monkeypatch.setattr("services.site_config.SiteConfig", _FakeSiteConfig)


class TestPerTapTimeout:
    async def test_hanging_tap_is_cancelled_and_run_continues(self, monkeypatch):
        """A tap whose run_tap never returns is bounded; later taps still run."""
        taps = [SimpleNamespace(name="hangs"), SimpleNamespace(name="healthy")]
        _patch_discovery(monkeypatch, taps)

        async def fake_run_tap(tap, pool, mem, *, max_chars=None, dedup_batch_size=None):
            if tap.name == "hangs":
                await asyncio.Event().wait()  # never resolves
            return runner_mod.TapStats(name=tap.name, embedded=1)

        monkeypatch.setattr(runner_mod, "run_tap", fake_run_tap)

        summary = await runner_mod.run_all(
            MagicMock(), MagicMock(), tap_timeout_s=0.05
        )

        by_name = {t.name: t for t in summary.taps}
        # The hung tap is recorded as failed, with a timeout reason.
        assert by_name["hangs"].failed == 1
        assert "timeout" in (by_name["hangs"].error or "").lower()
        # The run did NOT die on the hang — the next tap still executed.
        assert by_name["healthy"].embedded == 1
        assert summary.total_failed == 1
        assert summary.total_embedded == 1

    async def test_healthy_taps_unaffected_by_the_guard(self, monkeypatch):
        """With no hang, the timeout wrapper is transparent — normal stats flow."""
        taps = [SimpleNamespace(name="a"), SimpleNamespace(name="b")]
        _patch_discovery(monkeypatch, taps)

        async def fake_run_tap(tap, pool, mem, *, max_chars=None, dedup_batch_size=None):
            return runner_mod.TapStats(name=tap.name, embedded=2, skipped=1)

        monkeypatch.setattr(runner_mod, "run_tap", fake_run_tap)

        summary = await runner_mod.run_all(
            MagicMock(), MagicMock(), tap_timeout_s=30
        )

        assert summary.total_embedded == 4
        assert summary.total_skipped == 2
        assert summary.total_failed == 0
        assert all(t.error is None for t in summary.taps)
