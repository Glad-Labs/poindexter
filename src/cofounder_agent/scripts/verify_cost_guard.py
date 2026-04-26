"""One-shot verification that the unified cost+electricity tracking
writes both columns end to end.

Insert two synthetic rows (one local Ollama-style, one cloud Gemini-style)
via :class:`CostGuard.record_usage`, then read them back and print the
result. Used to validate the GH#cost-elec-unify change without waiting
for a real pipeline cycle.
"""

import asyncio
import os

import asyncpg


async def main() -> None:
    dsn = os.environ.get(
        "DATABASE_URL",
        "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
    )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)

    from services.cost_guard import CostGuard

    guard = CostGuard(pool=pool)

    await guard.record_usage(
        provider="ollama_native",
        model="gemma3:27b",
        prompt_tokens=120,
        completion_tokens=80,
        phase="test_synthetic_post_refactor",
        task_id="cost-guard-self-test",
        success=True,
        duration_ms=1500,
        is_local=True,
    )
    await guard.record_usage(
        provider="gemini",
        model="gemini-2.5-flash",
        prompt_tokens=120,
        completion_tokens=80,
        phase="test_synthetic_post_refactor",
        task_id="cost-guard-self-test",
        success=True,
        duration_ms=800,
    )

    rows = await pool.fetch(
        """
        SELECT provider, model, input_tokens, output_tokens,
               ROUND(cost_usd::numeric, 8) AS cost_usd,
               ROUND(electricity_kwh::numeric, 10) AS electricity_kwh,
               duration_ms
        FROM cost_logs
        WHERE task_id = $1
        ORDER BY created_at DESC
        LIMIT 5
        """,
        "cost-guard-self-test",
    )
    print("=== self-test rows ===")
    for r in rows:
        print(dict(r))

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
