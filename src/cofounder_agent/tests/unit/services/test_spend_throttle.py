"""Unit tests for services.spend_throttle (P3 — spend throttle).

The throttle defers NEW work when total_usd (paid API + measured electricity)
crosses a soft budget. It mirrors ``services.pipeline_throttle``'s shape
(module-singleton + ``get_state()`` + monotonic accounting + fail-open) but is
keyed on money instead of approval-queue depth.

``cost_ledger.get_spend`` is monkeypatched to inject canned spend; ``SiteConfig``
is real (``initial_config``) so the ``get_bool`` / ``get_float`` coercion is
exercised for real rather than mocked.
"""
from __future__ import annotations

import pytest

from services import cost_ledger, spend_throttle
from services.cost_ledger import SpendBreakdown
from services.site_config import SiteConfig


@pytest.fixture(autouse=True)
def _reset_throttle():
    spend_throttle.reset_for_tests()
    yield
    spend_throttle.reset_for_tests()


def _cfg(**over) -> SiteConfig:
    base = {
        "cost_throttle_enabled": "true",
        "cost_throttle_daily_budget_usd": "3.00",
        "cost_throttle_monthly_budget_usd": "60.00",
        "cost_throttle_resume_buffer_pct": "10",
    }
    base.update({k: str(v) for k, v in over.items()})
    return SiteConfig(initial_config=base)


def _patch_spend(monkeypatch, *, daily_total: float, monthly_total: float) -> None:
    async def _fake(pool, *, window="day", strict=False, site_config=None):
        return SpendBreakdown(total_usd=daily_total if window == "day" else monthly_total)

    monkeypatch.setattr(cost_ledger, "get_spend", _fake)


class _Pool:
    """Truthy pool stand-in; get_spend is patched so it's never actually used."""


# ---------------------------------------------------------------------------
# Core ceiling behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_throttled_within_budget(monkeypatch):
    _patch_spend(monkeypatch, daily_total=1.0, monthly_total=10.0)
    d = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    assert d.throttled is False
    assert d.ceiling is None


@pytest.mark.asyncio
async def test_daily_ceiling_trips(monkeypatch):
    _patch_spend(monkeypatch, daily_total=3.5, monthly_total=10.0)
    d = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    assert d.throttled is True
    assert d.ceiling == "daily"
    assert d.daily_total_usd == 3.5
    assert d.daily_budget_usd == 3.0


@pytest.mark.asyncio
async def test_monthly_ceiling_trips(monkeypatch):
    _patch_spend(monkeypatch, daily_total=1.0, monthly_total=62.0)
    d = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    assert d.throttled is True
    assert d.ceiling == "monthly"
    assert d.monthly_total_usd == 62.0


@pytest.mark.asyncio
async def test_monthly_takes_precedence_over_daily(monkeypatch):
    # Both axes crossed; the monthly backstop (no hysteresis, lasts till
    # rollover) is the more binding one, so it's reported.
    _patch_spend(monkeypatch, daily_total=5.0, monthly_total=70.0)
    d = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    assert d.throttled is True
    assert d.ceiling == "monthly"


# ---------------------------------------------------------------------------
# Escape hatches — a budget <= 0 disables that axis; master switch off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_budget_zero_disables_daily(monkeypatch):
    _patch_spend(monkeypatch, daily_total=999.0, monthly_total=1.0)
    d = await spend_throttle.should_throttle(
        _Pool(), site_config=_cfg(cost_throttle_daily_budget_usd="0")
    )
    assert d.throttled is False


@pytest.mark.asyncio
async def test_monthly_budget_zero_disables_monthly(monkeypatch):
    _patch_spend(monkeypatch, daily_total=1.0, monthly_total=999.0)
    d = await spend_throttle.should_throttle(
        _Pool(), site_config=_cfg(cost_throttle_monthly_budget_usd="0")
    )
    assert d.throttled is False


@pytest.mark.asyncio
async def test_master_switch_off_never_throttles(monkeypatch):
    _patch_spend(monkeypatch, daily_total=999.0, monthly_total=999.0)
    d = await spend_throttle.should_throttle(
        _Pool(), site_config=_cfg(cost_throttle_enabled="false")
    )
    assert d.throttled is False


# ---------------------------------------------------------------------------
# Fail-open — a dead DB / no pool must never become a content outage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fails_open_on_ledger_error(monkeypatch):
    async def _boom(pool, *, window="day", strict=False, site_config=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(cost_ledger, "get_spend", _boom)
    d = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    assert d.throttled is False


@pytest.mark.asyncio
async def test_fails_open_when_no_pool(monkeypatch):
    _patch_spend(monkeypatch, daily_total=999.0, monthly_total=999.0)
    d = await spend_throttle.should_throttle(None, site_config=_cfg())
    assert d.throttled is False


@pytest.mark.asyncio
async def test_fails_open_on_bad_config(monkeypatch):
    # Fail-open must cover config/arithmetic errors, not just the DB read. A
    # malformed setting (or any getter that raises / returns a non-number) must
    # NOT crash the new-work seam. Regression: an earlier cut guarded only the
    # get_spend read, so a non-numeric budget blew up the ceiling comparison and
    # propagated out of the flow (surfaced by a MagicMock site_config in the
    # Prefect-flow Sentry tests).
    _patch_spend(monkeypatch, daily_total=999.0, monthly_total=999.0)

    class _BadConfig:
        def get_bool(self, key, default=False):
            return True

        def get_float(self, key, default=0.0):
            raise ValueError(f"malformed setting {key!r}")

    d = await spend_throttle.should_throttle(_Pool(), site_config=_BadConfig())
    assert d.throttled is False
    assert d.ceiling is None


# ---------------------------------------------------------------------------
# Daily hysteresis — engage at budget, release only below budget*(1-buffer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_hysteresis_holds_then_releases(monkeypatch):
    cfg = _cfg(cost_throttle_daily_budget_usd="3.00",
               cost_throttle_resume_buffer_pct="10")  # release band = $2.70

    # 1) cross the cap → engage.
    _patch_spend(monkeypatch, daily_total=3.1, monthly_total=1.0)
    assert (await spend_throttle.should_throttle(_Pool(), site_config=cfg)).throttled is True

    # 2) drop into the hysteresis band ($2.70–$3.00) → STAYS engaged (no chatter).
    _patch_spend(monkeypatch, daily_total=2.85, monthly_total=1.0)
    assert (await spend_throttle.should_throttle(_Pool(), site_config=cfg)).throttled is True

    # 3) drop below the release threshold ($2.70) → releases.
    _patch_spend(monkeypatch, daily_total=2.60, monthly_total=1.0)
    assert (await spend_throttle.should_throttle(_Pool(), site_config=cfg)).throttled is False


@pytest.mark.asyncio
async def test_monthly_has_no_hysteresis(monkeypatch):
    cfg = _cfg(cost_throttle_monthly_budget_usd="60.00",
               cost_throttle_resume_buffer_pct="10")
    # Engage on the monthly backstop.
    _patch_spend(monkeypatch, daily_total=0.5, monthly_total=61.0)
    assert (await spend_throttle.should_throttle(_Pool(), site_config=cfg)).ceiling == "monthly"
    # Any monthly value below budget releases immediately — no band, because a
    # month-to-date SUM only rises; a drop only happens at month rollover.
    _patch_spend(monkeypatch, daily_total=0.5, monthly_total=59.9)
    assert (await spend_throttle.should_throttle(_Pool(), site_config=cfg)).throttled is False


# ---------------------------------------------------------------------------
# Observability — get_state() snapshot + engage finding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_state_reflects_active_and_budgets(monkeypatch):
    _patch_spend(monkeypatch, daily_total=3.5, monthly_total=10.0)
    await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    st = spend_throttle.get_state()
    assert st["active"] is True
    assert st["ceiling"] == "daily"
    assert st["daily_total_usd"] == 3.5
    assert st["daily_budget_usd"] == 3.0
    assert st["monthly_budget_usd"] == 60.0
    assert st["total_seconds"] >= 0.0


@pytest.mark.asyncio
async def test_emits_finding_once_on_engage(monkeypatch):
    _patch_spend(monkeypatch, daily_total=3.5, monthly_total=10.0)
    calls: list[dict] = []
    monkeypatch.setattr(spend_throttle, "emit_finding", lambda **kw: calls.append(kw))
    # First check engages → one finding.
    await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    # Second check while STILL engaged → no new finding (dedup via transition).
    await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    assert len(calls) == 1
    assert calls[0]["kind"] == "spend_throttle_engaged"
    assert calls[0]["severity"] == "warn"
    assert calls[0]["source"] == "spend_throttle"


@pytest.mark.asyncio
async def test_reset_for_tests_clears_state(monkeypatch):
    _patch_spend(monkeypatch, daily_total=3.5, monthly_total=10.0)
    await spend_throttle.should_throttle(_Pool(), site_config=_cfg())
    assert spend_throttle.get_state()["active"] is True
    spend_throttle.reset_for_tests()
    assert spend_throttle.get_state()["active"] is False


# ---------------------------------------------------------------------------
# Idle-electricity exclusion (2026-07-31)
#
# The throttle gates *controllable* spend. Idle draw happens whether or not the
# pipeline runs, so counting it made the cap measure "was the machine on" — and
# because deferring work can't reduce it, once idle alone crossed the ceiling
# the pipeline stayed throttled until rollover. Observed on prod: $61.06 monthly
# against a $60 cap, of which $41.73 was idle and only $11.37 real cloud spend.
# ---------------------------------------------------------------------------


def _patch_spend_with_idle(
    monkeypatch, *, daily_total: float, monthly_total: float,
    daily_idle: float = 0.0, monthly_idle: float = 0.0,
) -> None:
    async def _fake(pool, *, window="day", strict=False, site_config=None):
        if window == "day":
            return SpendBreakdown(
                total_usd=daily_total, idle_electricity_usd=daily_idle,
            )
        return SpendBreakdown(
            total_usd=monthly_total, idle_electricity_usd=monthly_idle,
        )

    monkeypatch.setattr(cost_ledger, "get_spend", _fake)


@pytest.mark.asyncio
async def test_idle_electricity_alone_does_not_engage_monthly(monkeypatch):
    """The prod incident, replayed: total over cap but only because of idle."""
    _patch_spend_with_idle(
        monkeypatch, daily_total=0.5, monthly_total=61.06, monthly_idle=41.73,
    )
    decision = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())

    assert decision.throttled is False, (
        "idle electricity pushed the raw total over the cap, but controllable "
        "spend was only $19.33 — the pipeline must keep working"
    )


@pytest.mark.asyncio
async def test_controllable_spend_still_engages_monthly(monkeypatch):
    """Excluding idle must not defang the ceiling on real spend."""
    _patch_spend_with_idle(
        monkeypatch, daily_total=0.5, monthly_total=101.0, monthly_idle=40.0,
    )
    decision = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())

    assert decision.throttled is True
    assert decision.ceiling == "monthly"


@pytest.mark.asyncio
async def test_idle_excluded_from_daily_axis_too(monkeypatch):
    """Both ceilings read the same controllable total."""
    _patch_spend_with_idle(
        monkeypatch, daily_total=3.4, monthly_total=5.0, daily_idle=1.5,
    )
    decision = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())

    assert decision.throttled is False, "daily controllable spend was $1.90"


@pytest.mark.asyncio
async def test_active_electricity_stays_counted(monkeypatch):
    """Only IDLE is excluded. Active draw is caused by running work, so
    deferring work genuinely reduces it and it must still trip the cap."""
    # $61 total with no idle component => all of it is API + active electricity.
    _patch_spend_with_idle(
        monkeypatch, daily_total=0.5, monthly_total=61.0, monthly_idle=0.0,
    )
    decision = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())

    assert decision.throttled is True
    assert decision.ceiling == "monthly"


@pytest.mark.asyncio
async def test_setting_restores_legacy_inclusive_sum(monkeypatch):
    """Backcompat escape hatch — the pre-2026-07-31 behaviour on demand."""
    _patch_spend_with_idle(
        monkeypatch, daily_total=0.5, monthly_total=61.06, monthly_idle=41.73,
    )
    decision = await spend_throttle.should_throttle(
        _Pool(),
        site_config=_cfg(cost_throttle_count_idle_electricity="true"),
    )

    assert decision.throttled is True
    assert decision.ceiling == "monthly"


@pytest.mark.asyncio
async def test_reported_total_is_the_throttled_on_total(monkeypatch):
    """The decision must report the number it actually judged, or the operator
    reads a total that doesn't explain the verdict."""
    _patch_spend_with_idle(
        monkeypatch, daily_total=2.0, monthly_total=61.06,
        daily_idle=0.5, monthly_idle=41.73,
    )
    decision = await spend_throttle.should_throttle(_Pool(), site_config=_cfg())

    assert decision.monthly_total_usd == pytest.approx(19.33, abs=0.01)
    assert decision.daily_total_usd == pytest.approx(1.5, abs=0.01)
