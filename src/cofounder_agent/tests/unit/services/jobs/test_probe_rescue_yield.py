"""Unit tests for ProbeRescueYieldJob (poindexter#986)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.jobs.probe_rescue_yield import ProbeRescueYieldJob
from services.site_config import SiteConfig


class _FakePool:
    def __init__(self, attempts: int, rescued: int) -> None:
        self._row = {"attempts": attempts, "rescued": rescued}
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
class TestProbeRescueYield:
    async def test_zero_yield_past_min_attempts_emits_finding(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_rescue_yield.emit_finding",
            lambda **kw: findings.append(kw),
        )
        result = await ProbeRescueYieldJob().run(_FakePool(12, 0), _cfg())
        assert result.ok is True
        assert result.changes_made == 1
        assert [f["kind"] for f in findings] == ["qa_rescue_yield_zero"]
        assert "0-for-12" in findings[0]["title"]
        assert result.metrics["rescue_attempts"] == 12
        assert result.metrics["rescued"] == 0

    async def test_nonzero_yield_is_quiet_with_metrics(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_rescue_yield.emit_finding",
            lambda **kw: findings.append(kw),
        )
        result = await ProbeRescueYieldJob().run(_FakePool(10, 3), _cfg())
        assert result.ok is True
        assert result.changes_made == 0
        assert findings == []
        assert result.metrics == {
            "rescue_attempts": 10, "rescued": 3,
            "yield_pct": 30.0, "window_days": 14,
        }

    async def test_below_min_attempts_never_pages(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_rescue_yield.emit_finding",
            lambda **kw: findings.append(kw),
        )
        # 0-for-3 is not yet a streak worth paging about (default min 8).
        result = await ProbeRescueYieldJob().run(_FakePool(3, 0), _cfg())
        assert result.ok is True
        assert findings == []
        assert "below min_attempts" in result.detail

    async def test_window_setting_reaches_query(self):
        pool = _FakePool(0, 0)
        await ProbeRescueYieldJob().run(
            pool, _cfg(qa_rescue_yield_window_days="30"),
        )
        assert pool.seen_args == (30,)

    async def test_disabled_via_setting_skips(self):
        result = await ProbeRescueYieldJob().run(
            _FakePool(50, 0), _cfg(qa_rescue_yield_probe_enabled="false"),
        )
        assert result.ok is True
        assert "disabled" in result.detail

    async def test_no_pool_not_ok(self):
        result = await ProbeRescueYieldJob().run(None, _cfg())
        assert result.ok is False
