#!/usr/bin/env python3
"""Did GPU device-lock scoping actually reduce QA-rail skips?

One-off follow-up check for poindexter#3457. Scoping went live 2026-08-31
~01:05 UTC; at that moment the payoff was UNPROVABLE because the pipeline had
been idle for two days (0 tasks on 08-29/08-30, 1 on 08-31), so the headline
metric — `gpu_lock_timeout` — had already fallen to zero for reasons that had
nothing to do with the change.

This script answers the question honestly once traffic returns, and is careful
to say "still unproven" rather than claim a win from an idle window.

Why a local script and not a cloud routine: the evidence lives in the operator
box's Postgres (`audit_log` findings) and in the running scheduler. A cloud
agent gets a git checkout, not this data.

Run: python3 scripts/check_gpu_scoping_payoff.py [--quiet]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: When device scoping was enabled in app_settings (UTC).
#: A real tz-aware datetime, NOT a string: asyncpg binds timestamptz params by
#: type and rejects a str with DataError (a trap this repo has hit before).
CUTOVER = _dt.datetime(2026, 8, 31, 1, 5, tzinfo=_dt.UTC)

#: Below this many post-cutover tasks the window carries no information — say
#: so instead of reporting a flattering zero.
MIN_TASKS_FOR_A_VERDICT = 10


async def _gather() -> dict:
    """Counts either side of the cutover.

    Windows are passed as (lo, hi) BIND PARAMETERS rather than spliced into the
    SQL. That keeps the statements constant strings — no f-string SQL for
    bandit to flag, and nothing that could ever grow into an injection seam if
    a bound later came from somewhere less trusted than a module constant.
    """
    import asyncpg

    from brain.bootstrap import resolve_database_url

    now = _dt.datetime.now(_dt.UTC)
    after = (CUTOVER, now)
    before = (CUTOVER - _dt.timedelta(days=5), CUTOVER)

    TASKS = (
        "SELECT count(*) FROM pipeline_tasks "
        "WHERE created_at > $1 AND created_at <= $2"
    )
    FINDING = (
        "SELECT count(*) FROM audit_log WHERE event_type='finding' "
        "AND details->>'kind' = $3 AND timestamp > $1 AND timestamp <= $2"
    )
    FINDING_T = FINDING + " AND details->'extra'->>'timeout_s' = $4"

    conn = await asyncpg.connect(resolve_database_url())
    try:
        out: dict = {}
        for label, (lo, hi) in (("after", after), ("before", before)):
            out[f"tasks_{label}"] = await conn.fetchval(TASKS, lo, hi)
            out[f"skips_{label}"] = await conn.fetchval(
                FINDING_T, lo, hi, "gpu_lock_timeout", "45.0"
            )
            out[f"starve_{label}"] = await conn.fetchval(
                FINDING_T, lo, hi, "gpu_lock_timeout", "900"
            )
            out[f"degraded_{label}"] = await conn.fetchval(
                FINDING, lo, hi, "qa_rail_degraded"
            )
        for key in ("gpu_lock_per_device_enabled", "gpu_lock_node_id",
                    "gpu_lock_scopes", "ollama_gpu_indexes"):
            out[key] = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key=$1", key
            )
        return out
    finally:
        await conn.close()


def _per_task(n: int, tasks: int) -> str:
    return f"{n / tasks:.1f}/task" if tasks else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="skip the operator ping")
    args = ap.parse_args()

    d = asyncio.run(_gather())
    after, before = d["tasks_after"], d["tasks_before"]

    # Config sanity first: a "clean" metric means nothing if scoping got turned
    # off, or if the node id fell back and every caller is on the shared key.
    problems = []
    if (d.get("gpu_lock_per_device_enabled") or "").lower() != "true":
        problems.append("gpu_lock_per_device_enabled is NOT true — scoping is off")
    if not (d.get("gpu_lock_node_id") or "").strip():
        problems.append(
            "gpu_lock_node_id is EMPTY — in a container that fails closed to the "
            "shared key, so no split is in effect"
        )
    if (d.get("ollama_gpu_indexes") or "").strip() not in ("0", ""):
        problems.append(
            f"ollama_gpu_indexes={d['ollama_gpu_indexes']!r} — should be 0 while "
            f"ollama-primary is pinned to GPU 0"
        )

    lines = [
        f"tasks:    {before} before / {after} after cutover",
        f"rail skips (45s budget):  {before and d['skips_before']} -> {d['skips_after']}"
        f"   ({_per_task(d['skips_before'], before)} -> {_per_task(d['skips_after'], after)})",
        f"lock starvation (900s):   {d['starve_before']} -> {d['starve_after']}",
        f"qa_rail_degraded:         {d['degraded_before']} -> {d['degraded_after']}"
        f"   ({_per_task(d['degraded_before'], before)} -> {_per_task(d['degraded_after'], after)})",
    ]

    if problems:
        verdict = "CONFIG REGRESSED — the split is not actually in effect"
        lines += ["", *(f"  ! {p}" for p in problems)]
    elif after < MIN_TASKS_FOR_A_VERDICT:
        verdict = (
            f"STILL UNPROVEN — only {after} tasks since cutover "
            f"(need >={MIN_TASKS_FOR_A_VERDICT}). Zero skips in an idle window "
            f"is not evidence."
        )
    else:
        rate_b = d["skips_before"] / before if before else 0
        rate_a = d["skips_after"] / after
        if rate_a < rate_b * 0.5:
            verdict = f"WORKING — rail skips/task fell {rate_b:.1f} -> {rate_a:.1f}"
        elif rate_a > rate_b:
            verdict = f"WORSE — rail skips/task ROSE {rate_b:.1f} -> {rate_a:.1f}"
        else:
            verdict = f"NO CLEAR CHANGE — skips/task {rate_b:.1f} -> {rate_a:.1f}"

    body = "\n".join(lines)
    print(f"VERDICT: {verdict}\n\n{body}")

    if not args.quiet:
        try:
            from brain.operator_notifier import notify_operator

            notify_operator(
                f"GPU scoping follow-up: {verdict.split(' —')[0]}",
                f"{verdict}\n\n{body}\n\n"
                f"Full context: docs/superpowers/specs/"
                f"2026-08-28-gpu-lock-device-scoping-design.md",
                source="check_gpu_scoping_payoff",
                severity="warning" if problems else "info",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"(operator ping failed: {type(exc).__name__}: {exc})", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
