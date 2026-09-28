"""``services.taps.runner.run_all`` stamps the settings each pass reads.

``run_all``'s caller is the auto-embed sidecar, a process of its own that runs
no flush job. ``run_all`` builds its own ``SiteConfig`` for the ``tap_*``
tunables, so until 2026-09-28 those reads died with the pass: all six
``tap_*`` keys read as never-read on prod while the sidecar read them every
hour, and ``ProbeZeroReaderSettingsJob`` listed them as orphan candidates
(poindexter#756). The pass now flushes before it returns.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services import settings_read_sink
from poindexter.services.taps import runner as runner_mod

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_TAP_KEYS = {
    "tap_chunk_max_chars",
    "tap_run_timeout_seconds",
    "tap_dedup_batch_size",
    "tap_zero_yield_finding_enabled",
    "tap_interval_enforcement_enabled",
    "tap_interval_grace_seconds",
}


def _recording_pool() -> tuple[Any, list[list[str]]]:
    """A pool serving SiteConfig.load and recording the telemetry UPDATE."""
    stamped: list[list[str]] = []

    async def execute(_sql: str, keys: list[str], _restamp: int) -> str:
        stamped.append(list(keys))
        return f"UPDATE {len(keys)}"

    conn = AsyncMock()
    conn.execute = execute
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])  # SiteConfig.load: nothing tuned
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, stamped


def _no_taps(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "get_taps", lambda: [])
    monkeypatch.setattr(runner_mod, "get_core_samples", lambda: {"taps": []})


async def test_a_pass_stamps_every_tap_setting_it_read(monkeypatch):
    _no_taps(monkeypatch)
    pool, stamped = _recording_pool()

    await runner_mod.run_all(pool, MagicMock())

    assert len(stamped) == 1
    assert _TAP_KEYS <= set(stamped[0])


async def test_a_pinned_timeout_is_not_read_so_not_stamped(monkeypatch):
    _no_taps(monkeypatch)
    pool, stamped = _recording_pool()

    await runner_mod.run_all(pool, MagicMock(), tap_timeout_s=5)

    assert _TAP_KEYS - {"tap_run_timeout_seconds"} <= set(stamped[0])
    assert "tap_run_timeout_seconds" not in stamped[0]


async def test_a_taps_own_sink_reads_are_stamped_too(monkeypatch):
    """A tap reading through SettingsService records into the process-wide
    sink; the pass's flush drains that as well."""
    _no_taps(monkeypatch)
    pool, stamped = _recording_pool()
    settings_read_sink.record_read("github_issues_repos")

    await runner_mod.run_all(pool, MagicMock())

    assert "github_issues_repos" in stamped[0]


async def test_a_failed_stamp_never_breaks_the_pass(monkeypatch):
    _no_taps(monkeypatch)
    pool, _ = _recording_pool()
    pool.acquire = MagicMock(side_effect=ConnectionResetError("reset by peer"))

    summary = await runner_mod.run_all(pool, MagicMock())

    assert summary.taps == []
