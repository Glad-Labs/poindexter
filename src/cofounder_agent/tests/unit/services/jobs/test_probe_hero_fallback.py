"""Unit tests for ProbeHeroFallbackJob (poindexter#992).

Per-shot ``hero_render_fallback`` findings are info-level, so a total hero
outage is invisible in aggregate — the 2026-08 one ran four days and the
operator noticed the missing motion before any alert did. These pin the
aggregate watchdog that closes that gap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.jobs.probe_hero_fallback import ProbeHeroFallbackJob
from services.site_config import SiteConfig


class _FakePool:
    def __init__(self, fallbacks: int, pieces: int, top_reason: str | None) -> None:
        self._row = {
            "fallbacks": fallbacks,
            "pieces": pieces,
            "top_reason": top_reason,
        }
        self.seen_args: tuple | None = None

    def acquire(self):  # type: ignore[no-untyped-def]
        pool = self

        class _Acq:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                conn = AsyncMock()

                async def _fetchrow(_sql, *args):  # type: ignore[no-untyped-def]
                    pool.seen_args = args
                    return pool._row

                conn.fetchrow = _fetchrow
                return conn

            async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
                return False

        return _Acq()


def _cfg(**overrides: str) -> dict:
    return {"_site_config": SiteConfig(initial_config=dict(overrides))}


@pytest.mark.unit
class TestProbeHeroFallback:
    async def test_cluster_emits_finding_with_reason(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_hero_fallback.emit_finding",
            lambda **kw: findings.append(kw),
        )
        pool = _FakePool(10, 1, "wan provider returned no result")

        result = await ProbeHeroFallbackJob().run(pool, _cfg())

        assert result.ok and result.changes_made == 1
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "hero_fallback_streak"
        assert f["severity"] == "warn"
        # the reason must reach the operator — it is what distinguishes an
        # OOM from a dead server from an upstream substitution
        assert "wan provider returned no result" in f["body"]
        assert f["extra"]["hero_fallbacks"] == 10
        assert f["extra"]["pieces_affected"] == 1

    async def test_below_threshold_is_silent(self, monkeypatch):
        """One-off fallbacks are normal — the still is a good outcome."""
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_hero_fallback.emit_finding",
            lambda **kw: findings.append(kw),
        )
        pool = _FakePool(2, 1, "wan provider returned no result")

        result = await ProbeHeroFallbackJob().run(pool, _cfg())

        assert result.ok and result.changes_made == 0
        assert findings == []

    async def test_metrics_returned_even_when_healthy(self, monkeypatch):
        """Hero health must be graphable while it is fine, not only when it
        breaks — the job_run audit sink reads JobResult.metrics."""
        monkeypatch.setattr(
            "services.jobs.probe_hero_fallback.emit_finding", lambda **kw: None,
        )
        result = await ProbeHeroFallbackJob().run(_FakePool(0, 0, None), _cfg())

        assert result.metrics["hero_fallbacks"] == 0
        assert result.metrics["window_hours"] == 24

    async def test_thresholds_are_operator_tunable(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_hero_fallback.emit_finding",
            lambda **kw: findings.append(kw),
        )
        pool = _FakePool(4, 2, "CUDA out of memory")

        result = await ProbeHeroFallbackJob().run(
            pool,
            _cfg(hero_fallback_min_count="5", hero_fallback_window_hours="6"),
        )

        assert result.changes_made == 0 and findings == []
        assert pool.seen_args == ("hero_render_fallback", 6)  # window threaded

    async def test_disabled_via_settings(self, monkeypatch):
        monkeypatch.setattr(
            "services.jobs.probe_hero_fallback.emit_finding",
            lambda **kw: pytest.fail("must not emit when disabled"),
        )
        result = await ProbeHeroFallbackJob().run(
            _FakePool(99, 9, "x"), _cfg(hero_fallback_probe_enabled="false"),
        )
        assert result.ok and result.changes_made == 0

    async def test_no_pool_fails_loud(self):
        result = await ProbeHeroFallbackJob().run(None, _cfg())
        assert result.ok is False

    async def test_missing_reason_does_not_crash(self, monkeypatch):
        """A fallback row written before the reason field existed must still
        produce a usable page."""
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_hero_fallback.emit_finding",
            lambda **kw: findings.append(kw),
        )
        await ProbeHeroFallbackJob().run(_FakePool(5, 3, None), _cfg())

        assert "(no reason recorded)" in findings[0]["body"]
