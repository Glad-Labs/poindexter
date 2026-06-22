# Cost Control P1 — Honest Attribution Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cost_logs` an honest ledger — local calls cost `$0` in API terms, electricity is sourced from the brain's measured power (with an estimate fallback), and every consumer reads one `cost_ledger.get_spend()` seam — so the phone/dashboard show real API-vs-electricity numbers instead of the blended `$42.58`.

**Architecture:** A write-time invariant (local rows write `cost_usd=0`, keeping `electricity_kwh` for attribution) plus one read seam (`services/cost_ledger.py`) that splits spend into `api_usd` / `electricity_usd` / `total_usd`. A backfill migration cleans history. The MCP `get_budget` view and the `get_budget_status` advisory both reroute onto the seam; the Cost dashboard splits its blended stat.

**Tech Stack:** Python 3 / asyncio, asyncpg, FastAPI, Prefect, pytest, PostgreSQL (`cost_logs`), Grafana (provisioned JSON dashboards).

**Spec:** [`docs/superpowers/specs/2026-06-21-cost-control-attribution-design.md`](../specs/2026-06-21-cost-control-attribution-design.md) — this plan implements **Phase 1 only**. P2 (gate), P3 (throttle), P4 (anomaly), P5 (savings) are separate plans that depend on this one.

## Global Constraints

- **Write invariant:** any _local_ row (`not _is_paid_llm_call`, or `is_local=True`) writes `cost_usd = 0.0`. `electricity_kwh` is still populated (attribution only).
- **No renames:** do NOT rename `cost_type` values or the existing `daily_spend_limit_usd` / `monthly_spend_limit_usd` keys (backcompat).
- **New settings live in `settings_defaults.py` only** — both the `DEFAULTS` dict (string value) and the `METADATA` dict (`owner` / `value_type`). NEVER seed settings in a migration file.
- **`app_settings.value` is `NOT NULL`**; `''` is the unset sentinel.
- **Fail-loud, no silent defaults**; **tests + docs ship with every change**.
- **Async everywhere**; never block the event loop.
- **Test runner:** `cd src/cofounder_agent && poetry run pytest <path> -q`.

---

## File Structure

| File                                                                                       | Responsibility                                                      | Action                                         |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- | ---------------------------------------------- |
| `src/cofounder_agent/services/cost_ledger.py`                                              | The one read seam: `SpendBreakdown` + `get_spend()`                 | **Create**                                     |
| `src/cofounder_agent/services/llm_providers/dispatcher.py`                                 | Stop writing per-call electricity dollars onto local inference rows | **Modify** (`_record_dispatch_cost`, ~466-488) |
| `src/cofounder_agent/services/cost_guard.py`                                               | `record_usage` local branch writes `$0`                             | **Modify** (~778-788)                          |
| `src/cofounder_agent/services/cost_aggregation_service.py`                                 | `get_spend_totals` + `get_budget_status` reroute to the ledger      | **Modify**                                     |
| `src/cofounder_agent/services/settings_defaults.py`                                        | Two new electricity-ledger keys                                     | **Modify** (`DEFAULTS` ~113, `METADATA` ~1171) |
| `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_zero_local_inference_cost_usd.py` | Backfill: zero historical local `cost_usd`                          | **Create**                                     |
| `infrastructure/grafana/dashboards/<cost-analytics>.json`                                  | Split blended spend stat → API $ / Electricity $ + source badge     | **Modify**                                     |
| `tests/unit/services/test_cost_ledger.py`                                                  | Ledger unit tests                                                   | **Create**                                     |
| `tests/unit/services/test_llm_providers_dispatcher.py`                                     | Phantom canary                                                      | **Modify**                                     |
| `tests/unit/services/test_cost_guard.py`                                                   | `record_usage` local-$0                                             | **Modify**                                     |
| `tests/unit/services/test_cost_aggregation_service.py`                                     | reroute + unify tests                                               | **Modify**                                     |
| `tests/integration_db/test_zero_local_inference_backfill.py`                               | Backfill behavior                                                   | **Create**                                     |

---

## Task 1: Write invariant — local calls cost `$0`

**Files:**

- Modify: `src/cofounder_agent/services/llm_providers/dispatcher.py:466-488`
- Modify: `src/cofounder_agent/services/cost_guard.py:778-788`
- Test: `tests/unit/services/test_llm_providers_dispatcher.py`, `tests/unit/services/test_cost_guard.py`

**Interfaces:**

- Consumes: nothing (first task).
- Produces: the invariant every later task relies on — a local `cost_logs` row has `cost_usd == 0` and `electricity_kwh` populated. No signature changes.

- [ ] **Step 1: Write the failing canary test (dispatcher)**

Add to `tests/unit/services/test_llm_providers_dispatcher.py`:

```python
import pytest
from services.llm_providers import dispatcher


@pytest.mark.asyncio
async def test_local_dispatch_records_zero_cost_usd(monkeypatch):
    """Phantom canary: a LOCAL call must log cost_usd=0 (electricity_kwh kept).

    Guards against a bare local tag (e.g. 'llama3.2:3b') re-acquiring a
    hosted price from litellm.model_cost — the 2026-06-21 phantom bug.
    """
    captured = {}

    class _Conn:
        async def execute(self, sql, *args):
            # cost_logs INSERT positional args: cost_usd is arg[7], electricity_kwh arg[11]
            captured["cost_usd"] = args[7]
            captured["electricity_kwh"] = args[11]

    class _Pool:
        def acquire(self):
            class _Cm:
                async def __aenter__(self_):
                    return _Conn()
                async def __aexit__(self_, *a):
                    return False
            return _Cm()

    class _Result:
        raw = {"response_cost": 0.0135}   # phantom hosted price litellm would stamp
        prompt_tokens = 100
        completion_tokens = 50
        total_tokens = 150

    await dispatcher._record_dispatch_cost(
        pool=_Pool(),
        provider=type("P", (), {"name": "litellm"})(),
        model="llama3.2:3b",          # bare local tag — NOT paid
        result=_Result(),
        task_id=None,
        phase="test",
        duration_ms=1000,
        success=True,
        provider_config={},
    )

    assert captured["cost_usd"] == 0.0, "local call must record $0 API cost"
    assert captured["electricity_kwh"] is not None, "electricity_kwh must be kept"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_llm_providers_dispatcher.py::test_local_dispatch_records_zero_cost_usd -q`
Expected: FAIL — `cost_usd` is `~0.00007` (the `kwh_to_usd` value), not `0.0`.

- [ ] **Step 3: Fix the dispatcher local branch**

In `dispatcher.py::_record_dispatch_cost`, the local-electricity block currently re-sets `cost_usd` from kWh. Change it to keep `electricity_kwh` but leave `cost_usd` at `0`:

```python
        # Local calls then attribute electricity for the dashboard/savings view,
        # but cost_usd STAYS 0 — the API axis is paid-cloud only, and the
        # electricity BILL comes from the brain's measured power rows, not
        # per-call estimates (see cost-control attribution spec, P1 invariant).
        electricity_kwh: float | None = None
        if cost_usd == 0.0:
            try:
                from services.cost_guard import CostGuard

                site_config = None
                try:
                    from services.integrations.shared_context import get_site_config
                    site_config = get_site_config()
                except Exception:  # noqa: BLE001
                    pass
                guard = CostGuard(pool=pool, site_config=site_config)
                electricity_kwh = guard.estimate_local_kwh(duration_ms=duration_ms)
                # NOTE: do NOT set cost_usd = kwh_to_usd(...) here — that conflated
                # electricity onto the API axis and double-counted the brain's
                # measured power.
            except Exception:  # noqa: BLE001 — best-effort attribution
                pass
```

- [ ] **Step 4: Run the canary to confirm it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_llm_providers_dispatcher.py::test_local_dispatch_records_zero_cost_usd -q`
Expected: PASS.

- [ ] **Step 5: Write the failing test for `record_usage` (compositor path)**

Add to `tests/unit/services/test_cost_guard.py`:

```python
@pytest.mark.asyncio
async def test_record_usage_local_writes_zero_cost():
    """is_local=True must record cost_usd=0, keeping electricity_kwh."""
    captured = {}

    class _Pool:
        async def execute(self, sql, *args):
            captured["cost_usd"] = float(args[7])
            captured["electricity_kwh"] = args[10]

    from services.cost_guard import CostGuard
    guard = CostGuard(pool=_Pool(), site_config=None)
    returned = await guard.record_usage(
        provider="compositor.ffmpeg_local",
        model="h264",
        duration_ms=5000,
        is_local=True,
    )
    assert returned == 0.0
    assert captured["cost_usd"] == 0.0
    assert captured["electricity_kwh"] is not None
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_guard.py::test_record_usage_local_writes_zero_cost -q`
Expected: FAIL — `cost_usd` is the `kwh_to_usd` value, not `0.0`.

- [ ] **Step 7: Fix `record_usage` local branch**

In `cost_guard.py::record_usage`, change the `is_local` cost branch:

```python
        if cost_usd is None:
            if is_local:
                # Local cost on the API axis is $0 — electricity is tracked via
                # electricity_kwh (attribution) + the brain's measured rows
                # (the bill). Do NOT bill per-call electricity onto cost_usd
                # (cost-control attribution spec, P1 invariant).
                cost_usd = 0.0
            else:
                cost_usd = await self.estimate_cost(
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
```

- [ ] **Step 8: Run both test files**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_guard.py tests/unit/services/test_llm_providers_dispatcher.py -q`
Expected: PASS (no regressions — existing electricity_kwh assertions still hold).

- [ ] **Step 9: Commit**

```bash
git add src/cofounder_agent/services/llm_providers/dispatcher.py \
        src/cofounder_agent/services/cost_guard.py \
        src/cofounder_agent/tests/unit/services/test_llm_providers_dispatcher.py \
        src/cofounder_agent/tests/unit/services/test_cost_guard.py
git commit -m "fix(cost): local calls record \$0 API cost (phantom kill + write invariant)"
```

---

## Task 2: `cost_ledger.get_spend()` — the read seam (measured electricity)

**Files:**

- Create: `src/cofounder_agent/services/cost_ledger.py`
- Test: `tests/unit/services/test_cost_ledger.py`

**Interfaces:**

- Consumes: Task 1's invariant (local rows `cost_usd=0`).
- Produces: `SpendBreakdown(api_usd, electricity_usd, total_usd, electricity_source, electricity_coverage_pct, by_type)` and `async def get_spend(pool, *, window="day", strict=False) -> SpendBreakdown`. Task 3 extends electricity; Tasks 5/6 consume this.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/test_cost_ledger.py`:

```python
import pytest
from services import cost_ledger


class _Pool:
    """Stub asyncpg pool returning canned fetchval/fetchrow per-SQL."""
    def __init__(self, api=0.0, electricity=0.0, by_type=None):
        self._api = api
        self._electricity = electricity
        self._by_type = by_type or []

    async def fetchval(self, sql, *args):
        if "NOT LIKE 'electricity%'" in sql:
            return self._api
        if "LIKE 'electricity%'" in sql:
            return self._electricity
        return 0.0

    async def fetch(self, sql, *args):
        return self._by_type


@pytest.mark.asyncio
async def test_get_spend_splits_axes():
    pool = _Pool(api=0.0, electricity=34.25)
    b = await cost_ledger.get_spend(pool, window="month")
    assert b.api_usd == 0.0
    assert b.electricity_usd == 34.25
    assert b.total_usd == 34.25


@pytest.mark.asyncio
async def test_get_spend_strict_raises_on_db_error():
    class _BadPool:
        async def fetchval(self, sql, *args):
            raise RuntimeError("db down")
        async def fetch(self, sql, *args):
            raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await cost_ledger.get_spend(_BadPool(), window="day", strict=True)


@pytest.mark.asyncio
async def test_get_spend_swallows_db_error_when_not_strict():
    class _BadPool:
        async def fetchval(self, sql, *args):
            raise RuntimeError("db down")
        async def fetch(self, sql, *args):
            raise RuntimeError("db down")

    b = await cost_ledger.get_spend(_BadPool(), window="day", strict=False)
    assert b.api_usd == 0.0 and b.electricity_usd == 0.0
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_ledger.py -q`
Expected: FAIL — `ModuleNotFoundError: services.cost_ledger`.

- [ ] **Step 3: Create the ledger module (measured electricity only)**

Create `src/cofounder_agent/services/cost_ledger.py`:

```python
"""Single read seam for cost_logs spend — splits the API and electricity axes.

Replaces N hand-rolled SUM(cost_usd) queries (cost_guard, get_spend_totals,
get_budget_status, detect_anomalies) that each disagreed. Relies on the P1
write invariant: a LOCAL inference/media row has cost_usd=0, so the api axis
("everything not electricity") sums only genuinely-paid cloud spend without an
in-SQL locality heuristic. Electricity is the brain's measured PSU rows
(cost_type LIKE 'electricity%'); Task 3 adds the estimate fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Window = Literal["day", "month"]

_WINDOW_SQL = {
    "day": "created_at >= date_trunc('day', NOW())",
    "month": "created_at >= date_trunc('month', NOW())",
}

# nosec B608 justification: window clause is a hardcoded literal keyed by an
# enum, never user input.
_API_SQL = (
    "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
    "WHERE COALESCE(cost_type, 'inference') NOT LIKE 'electricity%' AND {w}"
)
_ELEC_SQL = (
    "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
    "WHERE cost_type LIKE 'electricity%' AND {w}"
)
_BYTYPE_SQL = (
    "SELECT COALESCE(cost_type, 'inference') AS t, COALESCE(SUM(cost_usd), 0) AS v "
    "FROM cost_logs WHERE {w} GROUP BY 1"
)


@dataclass
class SpendBreakdown:
    api_usd: float = 0.0
    electricity_usd: float = 0.0
    total_usd: float = 0.0
    electricity_source: Literal["measured", "estimated", "mixed", "none"] = "none"
    electricity_coverage_pct: float = 0.0
    by_type: dict[str, float] = field(default_factory=dict)


async def get_spend(
    pool: Any, *, window: Window = "day", strict: bool = False,
) -> SpendBreakdown:
    """Return the spend breakdown for ``window`` ('day' | 'month').

    ``strict=True`` re-raises on DB error (fail-closed callers like the gate);
    default swallows to a zeroed breakdown (fail-open callers like the throttle
    and dashboards).
    """
    w = _WINDOW_SQL[window]
    try:
        api = float(await pool.fetchval(_API_SQL.format(w=w)) or 0.0)  # nosec B608
        electricity = float(await pool.fetchval(_ELEC_SQL.format(w=w)) or 0.0)  # nosec B608
        rows = await pool.fetch(_BYTYPE_SQL.format(w=w))  # nosec B608
    except Exception:
        if strict:
            raise
        return SpendBreakdown()

    by_type = {r["t"]: float(r["v"] or 0.0) for r in rows}
    source: Literal["measured", "estimated", "mixed", "none"] = (
        "measured" if electricity > 0 else "none"
    )
    return SpendBreakdown(
        api_usd=api,
        electricity_usd=electricity,
        total_usd=api + electricity,
        electricity_source=source,
        electricity_coverage_pct=100.0 if electricity > 0 else 0.0,
        by_type=by_type,
    )
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_ledger.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/cost_ledger.py \
        src/cofounder_agent/tests/unit/services/test_cost_ledger.py
git commit -m "feat(cost): add cost_ledger.get_spend read seam (api/electricity split)"
```

---

## Task 3: Electricity measured-primary + estimate-fallback

**Files:**

- Modify: `src/cofounder_agent/services/cost_ledger.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (`DEFAULTS` ~113, `METADATA` ~1171)
- Test: `tests/unit/services/test_cost_ledger.py`

**Interfaces:**

- Consumes: Task 2's `get_spend`.
- Produces: `get_spend` now sets `electricity_source` ∈ `measured|estimated|none` and a real `electricity_coverage_pct`; when measured coverage is below the configured floor it falls back to `SUM(electricity_kwh) × electricity_rate_kwh`.

- [ ] **Step 1: Add the two settings keys (DEFAULTS)**

In `settings_defaults.py`, under the `# ----- Cost / billing -----` block (~line 115), add:

```python
    # Electricity ledger: prefer the brain's measured PSU rows; fall back to
    # per-call kWh estimates for windows the measured feed didn't cover
    # (HX1500i sampling has been flaky). A sample "covers" up to
    # *_gap_minutes after it; below *_min_coverage_pct → estimated.
    'electricity_measured_min_coverage_pct': '80',
    'electricity_source_gap_minutes': '15',
```

- [ ] **Step 2: Add the two settings keys (METADATA)**

In `settings_defaults.py`, under the `# ----- Cost guard ... -----` METADATA block (~line 1173), add:

```python
    'electricity_measured_min_coverage_pct': {'owner': 'cost_ledger', 'value_type': 'float'},
    'electricity_source_gap_minutes': {'owner': 'cost_ledger', 'value_type': 'integer'},
```

- [ ] **Step 3: Write the failing fallback test**

Add to `tests/unit/services/test_cost_ledger.py`:

```python
@pytest.mark.asyncio
async def test_electricity_falls_back_to_estimate_when_measured_sparse():
    """No measured rows but per-call kWh present → estimated source."""
    class _Pool:
        async def fetchval(self, sql, *args):
            if "NOT LIKE 'electricity%'" in sql:
                return 0.0           # api
            if "cost_type LIKE 'electricity%'" in sql:
                return 0.0           # measured electricity $ — none
            if "SUM(electricity_kwh)" in sql:
                return 10.0          # 10 kWh attributed on local rows
            if "COUNT(*)" in sql and "electricity%" in sql:
                return 0             # zero measured samples → 0% coverage
            return 0.0
        async def fetch(self, sql, *args):
            return []

    b = await cost_ledger.get_spend(
        _Pool(), window="day",
        site_config=_FakeConfig({"electricity_rate_kwh": "0.2579",
                                 "electricity_measured_min_coverage_pct": "80"}),
    )
    assert b.electricity_source == "estimated"
    assert round(b.electricity_usd, 4) == round(10.0 * 0.2579, 4)


class _FakeConfig:
    def __init__(self, d): self._d = d
    def get(self, k, default=None): return self._d.get(k, default)
```

- [ ] **Step 4: Run to confirm it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_ledger.py::test_electricity_falls_back_to_estimate_when_measured_sparse -q`
Expected: FAIL — `get_spend` has no `site_config` param / no fallback.

- [ ] **Step 5: Implement coverage + fallback**

Update `cost_ledger.py` — add `site_config` param and the fallback path:

```python
_MEASURED_COVERAGE_SQL = (
    "SELECT COUNT(*) FROM cost_logs "
    "WHERE cost_type LIKE 'electricity%' AND {w}"
)
_EST_KWH_SQL = (
    "SELECT COALESCE(SUM(electricity_kwh), 0) FROM cost_logs "
    "WHERE COALESCE(cost_type,'inference') NOT LIKE 'electricity%' AND {w}"
)
# Expected samples per window if the brain wrote one electricity row every
# gap_minutes; used to turn a raw measured-row count into a coverage %.
_WINDOW_MINUTES = {"day": 24 * 60, "month": 30 * 24 * 60}


async def get_spend(pool, *, window="day", strict=False, site_config=None):
    w = _WINDOW_SQL[window]
    try:
        api = float(await pool.fetchval(_API_SQL.format(w=w)) or 0.0)  # nosec B608
        measured = float(await pool.fetchval(_ELEC_SQL.format(w=w)) or 0.0)  # nosec B608
        samples = int(await pool.fetchval(_MEASURED_COVERAGE_SQL.format(w=w)) or 0)  # nosec B608
        rows = await pool.fetch(_BYTYPE_SQL.format(w=w))  # nosec B608
    except Exception:
        if strict:
            raise
        return SpendBreakdown()

    def _cfg(key, default):
        return float(site_config.get(key, default)) if site_config else float(default)

    gap_min = _cfg("electricity_source_gap_minutes", 15.0)
    min_cov = _cfg("electricity_measured_min_coverage_pct", 80.0)
    expected = max(1.0, _WINDOW_MINUTES[window] / max(1.0, gap_min))
    coverage = min(100.0, 100.0 * samples / expected)

    if coverage >= min_cov and measured > 0:
        electricity, source = measured, "measured"
    else:
        try:
            est_kwh = float(await pool.fetchval(_EST_KWH_SQL.format(w=w)) or 0.0)  # nosec B608
        except Exception:
            est_kwh = 0.0
        rate = _cfg("electricity_rate_kwh", 0.16)
        electricity = est_kwh * rate
        source = "estimated" if electricity > 0 else "none"

    by_type = {r["t"]: float(r["v"] or 0.0) for r in rows}
    return SpendBreakdown(
        api_usd=api, electricity_usd=electricity, total_usd=api + electricity,
        electricity_source=source, electricity_coverage_pct=round(coverage, 1),
        by_type=by_type,
    )
```

- [ ] **Step 6: Run the full ledger suite**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_ledger.py -q`
Expected: PASS (existing measured tests + the new fallback test; measured tests pass because their stub returns `electricity>0` and the coverage stub defaults make `coverage>=80`). If a measured test now needs a sample count, add `if "COUNT(*)" ...: return 9999` to its stub so coverage reads 100%.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/cost_ledger.py \
        src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/tests/unit/services/test_cost_ledger.py
git commit -m "feat(cost): measured-primary electricity with estimate fallback"
```

---

## Task 4: Backfill migration — zero historical local `cost_usd`

**Files:**

- Create: `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_zero_local_inference_cost_usd.py`
- Test: `tests/integration_db/test_zero_local_inference_backfill.py`

**Interfaces:**

- Consumes: nothing (data-only mutation).
- Produces: history matching the invariant — local inference/media rows have `cost_usd=0`, `electricity_kwh` preserved; electricity + genuinely-paid rows untouched.

- [ ] **Step 1: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "zero local inference cost_usd"`
This creates a timestamped file with the runner interface (`up`/`down`).

- [ ] **Step 2: Write the integration test first**

Create `tests/integration_db/test_zero_local_inference_backfill.py`:

```python
import importlib, pathlib, pytest

pytestmark = pytest.mark.integration_db


def _load_migration():
    d = pathlib.Path(__file__).resolve().parents[2] / "services" / "migrations"
    path = next(d.glob("*_zero_local_inference_cost_usd.py"))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_backfill_zeros_local_keeps_paid_and_electricity(db_pool):
    await db_pool.execute("DELETE FROM cost_logs WHERE phase = 'backfill_test'")
    # phantom local, electricity (brain), genuinely-paid cloud
    await db_pool.execute(
        "INSERT INTO cost_logs (phase, model, provider, cost_usd, electricity_kwh, cost_type) "
        "VALUES "
        "('backfill_test','llama3.2:3b','litellm',0.0135,0.0001,'inference'),"
        "('backfill_test','psu','electricity',0.02,NULL,'electricity_active'),"
        "('backfill_test','claude-haiku-4-5','anthropic',0.03,NULL,'inference')"
    )
    mod = _load_migration()
    await mod.up(db_pool)

    fetched = await db_pool.fetch(
        "SELECT provider, cost_usd, electricity_kwh FROM cost_logs "
        "WHERE phase='backfill_test'"
    )
    rows = {r["provider"]: r for r in fetched}

    assert float(rows["litellm"]["cost_usd"]) == 0.0          # phantom zeroed
    assert rows["litellm"]["electricity_kwh"] is not None      # attribution kept
    assert float(rows["electricity"]["cost_usd"]) == 0.02      # bill untouched
    assert float(rows["anthropic"]["cost_usd"]) == 0.03        # paid untouched
    await db_pool.execute("DELETE FROM cost_logs WHERE phase = 'backfill_test'")
```

- [ ] **Step 3: Run to confirm it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_zero_local_inference_backfill.py -q`
Expected: FAIL — `up()` is the empty generated stub, so `litellm.cost_usd` is still `0.0135`.

- [ ] **Step 4: Implement the migration**

Replace the generated `up`/`down` body:

```python
async def up(pool) -> None:
    # Dry-run visibility: how many rows will change (logged, not gated).
    n = await pool.fetchval(
        """
        SELECT COUNT(*) FROM cost_logs
        WHERE COALESCE(cost_type,'inference') NOT LIKE 'electricity%'
          AND cost_usd > 0
          AND provider NOT IN ('anthropic','openai','gemini','openrouter')
          AND (provider IN ('ollama','ollama_native','litellm')
               OR model !~ '^(anthropic|openai|gemini|openrouter)/')
        """
    )
    import logging
    logging.getLogger(__name__).info(
        "[backfill] zeroing cost_usd on %s local cost_logs rows", n,
    )
    await pool.execute(
        """
        UPDATE cost_logs SET cost_usd = 0
        WHERE COALESCE(cost_type,'inference') NOT LIKE 'electricity%'
          AND cost_usd > 0
          AND provider NOT IN ('anthropic','openai','gemini','openrouter')
          AND (provider IN ('ollama','ollama_native','litellm')
               OR model !~ '^(anthropic|openai|gemini|openrouter)/')
        """
    )


async def down(pool) -> None:
    # Forward-only: reverting would re-introduce phantom dollars. Documented no-op.
    return None
```

- [ ] **Step 5: Run to confirm it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_zero_local_inference_backfill.py -q`
Expected: PASS.

- [ ] **Step 6: Verify idempotency + fresh-DB apply**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_smoke.py && python scripts/ci/migrations_lint.py`
Expected: both clean (the `UPDATE` no-ops on the empty fresh DB; runner interface valid).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/migrations/*_zero_local_inference_cost_usd.py \
        src/cofounder_agent/tests/integration_db/test_zero_local_inference_backfill.py
git commit -m "feat(cost): backfill migration zeroes historical phantom cost_usd"
```

---

## Task 5: Reroute `get_spend_totals` (the phone `get_budget`) onto the ledger

**Files:**

- Modify: `src/cofounder_agent/services/cost_aggregation_service.py:590-600`
- Test: `tests/unit/services/test_cost_aggregation_service.py`

**Interfaces:**

- Consumes: Task 2/3 `cost_ledger.get_spend`.
- Produces: `get_spend_totals(pool)` returns a backward-compatible superset — keeps `monthly_total_usd` / `daily_total_usd` (now `= total_usd`), ADDS `api_usd` / `electricity_usd` / `electricity_source` per window.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/services/test_cost_aggregation_service.py`:

```python
@pytest.mark.asyncio
async def test_get_spend_totals_returns_split(monkeypatch):
    from services import cost_aggregation_service as cas
    from services.cost_ledger import SpendBreakdown

    async def fake_get_spend(pool, *, window="day", strict=False, site_config=None):
        if window == "month":
            return SpendBreakdown(api_usd=0.0, electricity_usd=34.25, total_usd=34.25,
                                  electricity_source="measured")
        return SpendBreakdown(api_usd=0.0, electricity_usd=1.10, total_usd=1.10,
                              electricity_source="measured")

    monkeypatch.setattr(cas.cost_ledger, "get_spend", fake_get_spend)
    out = await cas.get_spend_totals(object())
    assert out["monthly_total_usd"] == 34.25   # backcompat key
    assert out["daily_total_usd"] == 1.10
    assert out["monthly_api_usd"] == 0.0
    assert out["monthly_electricity_usd"] == 34.25
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_aggregation_service.py::test_get_spend_totals_returns_split -q`
Expected: FAIL — current `get_spend_totals` returns only the two blended keys via raw SQL.

- [ ] **Step 3: Reroute `get_spend_totals`**

Replace the `get_spend_totals` function (and its `_SPEND_*_SQL` constants become unused — delete them):

```python
from services import cost_ledger  # add at top of module


async def get_spend_totals(pool: Any) -> dict[str, float]:
    """Current month + day spend from the cost ledger (honest split).

    Backward-compatible superset: ``monthly_total_usd`` / ``daily_total_usd``
    stay (now = total_usd) so the MCP get_budget tool keeps working; the
    api/electricity split + source are added for the phone/dashboard.
    """
    month = await cost_ledger.get_spend(pool, window="month")
    day = await cost_ledger.get_spend(pool, window="day")
    return {
        "monthly_total_usd": month.total_usd,
        "daily_total_usd": day.total_usd,
        "monthly_api_usd": month.api_usd,
        "monthly_electricity_usd": month.electricity_usd,
        "daily_api_usd": day.api_usd,
        "daily_electricity_usd": day.electricity_usd,
        "electricity_source": month.electricity_source,
    }
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_aggregation_service.py::test_get_spend_totals_returns_split -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/cost_aggregation_service.py \
        src/cofounder_agent/tests/unit/services/test_cost_aggregation_service.py
git commit -m "feat(cost): get_budget (phone) shows api/electricity split via ledger"
```

---

## Task 6: Unify `get_budget_status` onto the ledger (drop hardcoded `$150`)

**Files:**

- Modify: `src/cofounder_agent/services/cost_aggregation_service.py:374-510`
- Test: `tests/unit/services/test_cost_aggregation_service.py`

**Interfaces:**

- Consumes: `cost_ledger.get_spend`, `app_settings.monthly_spend_limit_usd`.
- Produces: `get_budget_status` is advisory-only and reads its budget + spend from the ledger/app_settings — no `$150` literal. Enforcement stays solely in `cost_guard` (P2).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/services/test_cost_aggregation_service.py`:

```python
@pytest.mark.asyncio
async def test_budget_status_reads_app_settings_not_150(monkeypatch):
    from services import cost_aggregation_service as cas
    from services.cost_ledger import SpendBreakdown

    async def fake_get_spend(pool, *, window="day", strict=False, site_config=None):
        return SpendBreakdown(api_usd=5.0, electricity_usd=0.0, total_usd=5.0)

    monkeypatch.setattr(cas.cost_ledger, "get_spend", fake_get_spend)
    svc = cas.CostAggregationService(db_service=_FakeDb(monthly_cap="10.0"))
    status = await svc.get_budget_status()
    assert status["monthly_budget"] == 10.0     # from app_settings, NOT 150
    assert status["amount_spent"] == 5.0
    assert status["percent_used"] == 50.0
```

(Use the file's existing `_FakeDb`/pool fixture pattern; have it return `'10.0'` for `monthly_spend_limit_usd`.)

- [ ] **Step 2: Run to confirm it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_aggregation_service.py::test_budget_status_reads_app_settings_not_150 -q`
Expected: FAIL — `monthly_budget` defaults to `150.0`.

- [ ] **Step 3: Rewrite `get_budget_status` to read the ledger + app_settings**

Change the signature default and the spend source. Replace the hardcoded default and the inline `SUM(cost_usd)` query:

```python
    async def get_budget_status(self, monthly_budget: float | None = None) -> dict[str, Any]:
        if not self.db or not self.db.pool:
            return self._get_empty_budget_status(monthly_budget or 0.0)
        # Budget from app_settings (the cost_guard cap), NOT a hardcoded literal.
        if monthly_budget is None:
            raw = await self.db.pool.fetchval(
                "SELECT value FROM app_settings WHERE key = 'monthly_spend_limit_usd'"
            )
            monthly_budget = float(raw) if raw not in (None, "") else 0.0
        from services import cost_ledger
        month = await cost_ledger.get_spend(self.db.pool, window="month")
        amount_spent = month.total_usd
        # ... existing days/burn/projection/alerts arithmetic unchanged ...
```

Delete the `self.monthly_budget = 150.0` line in `__init__` and any remaining `150.0` literal in `get_budget_status` / `_get_empty_summary` (replace summary's budget with `0.0` or a settings read; the summary's `monthly_budget` is display-only).

- [ ] **Step 4: Run the cost-aggregation suite**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_aggregation_service.py -q`
Expected: PASS. Update any existing test that asserted the `150.0` default to pass an explicit budget or the settings stub.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/cost_aggregation_service.py \
        src/cofounder_agent/tests/unit/services/test_cost_aggregation_service.py
git commit -m "refactor(cost): get_budget_status advisory-only, reads ledger + app_settings"
```

---

## Task 7: Dashboard — split the blended spend stat (API $ vs Electricity $)

**Files:**

- Modify: the Cost & Analytics dashboard JSON under `infrastructure/grafana/dashboards/` (locate with the glob below)
- Verify: visual (Grafana renders the split)

**Interfaces:**

- Consumes: the `cost_type` taxonomy + write invariant (so the SQL below reads honest numbers).
- Produces: operator-visible proof that the blended `$42.58` splits into `~$0 API / $34 electricity`.

- [ ] **Step 1: Locate the dashboard + the blended panel**

Run: `ls infrastructure/grafana/dashboards/ | grep -i cost`
Open the matched JSON and find the panel whose `rawSql` sums `cost_logs.cost_usd` without splitting by `cost_type` (the blended "spend" stat).

- [ ] **Step 2: Replace with two stat panels (API $ / Electricity $ this month)**

API $ this month:

```sql
SELECT COALESCE(SUM(cost_usd),0) AS "API $ (month)"
FROM cost_logs
WHERE COALESCE(cost_type,'inference') NOT LIKE 'electricity%'
  AND created_at >= date_trunc('month', NOW());
```

Electricity $ this month:

```sql
SELECT COALESCE(SUM(cost_usd),0) AS "Electricity $ (month)"
FROM cost_logs
WHERE cost_type LIKE 'electricity%'
  AND created_at >= date_trunc('month', NOW());
```

Keep the panel `datasource` (Postgres) identical to the panel you replaced. Use two `stat` panels (or one `stat` with two targets), colorblind-safe (no red/green-only encoding — use labels + neutral thresholds per `feedback_visual_verification`).

- [ ] **Step 3: Validate the JSON**

Run: `python -c "import json,sys; json.load(open([p for p in __import__('glob').glob('infrastructure/grafana/dashboards/*ost*.json')][0])); print('ok')"`
Expected: `ok` (no JSON syntax error).

- [ ] **Step 4: Reload Grafana provisioning + verify visually**

Run: `docker compose restart poindexter-grafana`
Then open `http://localhost:3000/d/cost...` and confirm the two stats render — API ≈ `$0`, Electricity ≈ the month's real power figure. (This is the acceptance evidence from the spec's verification plan.)

- [ ] **Step 5: Commit**

```bash
git add infrastructure/grafana/dashboards/*.json
git commit -m "feat(cost): dashboard splits blended spend into API \$ vs electricity \$"
```

---

## Verification (end-to-end, after all tasks)

- [ ] **Full unit suite for touched areas:**

```bash
cd src/cofounder_agent && poetry run pytest \
  tests/unit/services/test_cost_ledger.py \
  tests/unit/services/test_cost_guard.py \
  tests/unit/services/test_llm_providers_dispatcher.py \
  tests/unit/services/test_cost_aggregation_service.py -q
```

- [ ] **Migration smoke + lint:** `python scripts/ci/migrations_smoke.py && python scripts/ci/migrations_lint.py`

- [ ] **Re-run the audit reconciliation (the spec's acceptance test)** against prod `cost_logs`:

```sql
SELECT
  ROUND(SUM(cost_usd) FILTER (WHERE COALESCE(cost_type,'inference') NOT LIKE 'electricity%')::numeric,2) AS api_usd,
  ROUND(SUM(cost_usd) FILTER (WHERE cost_type LIKE 'electricity%')::numeric,2) AS electricity_usd
FROM cost_logs WHERE created_at >= date_trunc('month', NOW());
```

Expected after backfill: `api_usd ≈ 0.00` (no real cloud spend enabled), `electricity_usd ≈` the month's real power bill — the blended `$42.58` is gone.

- [ ] **Phone check:** call the MCP `get_budget` tool → response now has `monthly_api_usd` / `monthly_electricity_usd` / `electricity_source`, not a single blended number.

## Self-Review (completed at write time)

- **Spec coverage (P1):** write invariant → Task 1; `cost_ledger` seam → Task 2; measured/fallback electricity + 2 keys → Task 3; backfill → Task 4; phone `get_budget` reroute → Task 5; budget-system unification → Task 6; dashboard split → Task 7. All P1 bullets covered. (P2–P5 are out of scope by design — separate plans.)
- **Placeholder scan:** the migration filename uses the `YYYYMMDD_HHMMSS` convention (resolved by `new-migration.py` in Task 4 Step 1); the dashboard filename is resolved by glob in Task 7 Step 1 (the JSON is generated/large — the actionable content, the SQL, is concrete). No `TODO`/`TBD`/"handle edge cases" steps.
- **Type consistency:** `SpendBreakdown` fields (`api_usd`, `electricity_usd`, `total_usd`, `electricity_source`, `electricity_coverage_pct`, `by_type`) and `get_spend(pool, *, window, strict, site_config)` are defined in Task 2/3 and consumed identically in Tasks 5/6. The `cost_logs` INSERT positional indices used in Task 1 tests match `cost_guard.record` / `_record_dispatch_cost` column order.
