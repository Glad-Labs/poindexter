"""integration_db: the zero-local-inference backfill zeroes phantom local
``cost_usd`` while leaving genuinely-paid cloud rows and the brain's measured
electricity rows untouched — and preserving ``electricity_kwh`` for attribution.

The harness (schema_loaded -> fixtures_loaded -> test_pool) runs the full
migration chain against a disposable ``poindexter_test_<hex>`` DB. ``cost_logs``
is empty when the chain runs, so this migration no-ops there; this test
hand-inserts the three canonical row shapes and re-runs the migration's ``up()``
to pin the backfill contract:

  * phantom local inference (a bare Ollama tag that collided with a hosted
    price) -> ``cost_usd`` zeroed, ``electricity_kwh`` preserved
  * the brain's measured PSU electricity row -> untouched (it is the bill)
  * a genuinely-paid cloud row -> untouched
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


def _load_migration():
    # parents: [0]=integration_db [1]=tests [2]=cofounder_agent
    mig_dir = Path(__file__).resolve().parents[2] / "services" / "migrations"
    matches = sorted(mig_dir.glob("*_zero_local_inference_cost_usd.py"))
    assert matches, "zero_local_inference_cost_usd migration file not found"
    path = matches[-1]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_backfill_zeros_local_keeps_paid_and_electricity(test_pool):
    mod = _load_migration()
    async with test_pool.acquire() as conn:
        # Clean any residue from a prior run of this test in the same session.
        await conn.execute("DELETE FROM cost_logs WHERE phase = 'backfill_test'")
        await conn.execute(
            """
            INSERT INTO cost_logs (phase, model, provider, cost_usd, electricity_kwh, cost_type)
            VALUES
              ('backfill_test', 'llama3.2:3b', 'litellm', 0.0135, 0.0001, 'inference'),
              ('backfill_test', 'psu', 'electricity', 0.02, NULL, 'electricity_active'),
              ('backfill_test', 'claude-haiku-4-5', 'anthropic', 0.03, NULL, 'inference')
            """
        )

    # Idempotent backfill: re-running picks up the freshly-inserted dirty rows.
    await mod.up(test_pool)

    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT provider,
                   cost_usd::float8        AS cost_usd,
                   electricity_kwh::float8 AS electricity_kwh
              FROM cost_logs
             WHERE phase = 'backfill_test'
            """
        )
        await conn.execute("DELETE FROM cost_logs WHERE phase = 'backfill_test'")

    by_provider = {r["provider"]: r for r in rows}

    # Phantom local row: API-axis dollars zeroed, electricity_kwh preserved
    # (the P5 savings view needs the kWh attribution).
    assert by_provider["litellm"]["cost_usd"] == 0.0
    assert by_provider["litellm"]["electricity_kwh"] == pytest.approx(0.0001)

    # The brain's measured electricity row IS the bill — never zeroed.
    assert by_provider["electricity"]["cost_usd"] == pytest.approx(0.02)

    # Genuinely-paid cloud row — untouched.
    assert by_provider["anthropic"]["cost_usd"] == pytest.approx(0.03)
