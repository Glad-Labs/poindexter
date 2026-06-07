"""Cost/energy eval harness — validate cost-tier routing with measured data (#530).

Sweeps cost tiers (or explicit models) against a FIXED set of benchmark
prompts and, for every (model × prompt × repeat) run, measures:

  - tokens/second        (throughput)
  - joules/token         (real GPU watts from Prometheus × duration / tokens)
  - electricity_kwh      (measured watts when available, else cost_guard estimate)
  - cost_usd             (LiteLLM response_cost / cost_lookup for cloud;
                          kwh_to_usd for local)
  - quality_score        (optional, --score-quality; 0-100 via UnifiedQualityService)

Each run is persisted as one row in ``bench_run_results`` so the Grafana
cost dashboard reads it like the other cost panels. The point: replace
gut-feel cost-tier routing with measured intelligence-per-watt.

This is a ONE-OFF operator tool, run manually:

    poetry run python scripts/bench/eval_cost_tiers.py --tiers standard --repeat 1
    poetry run python scripts/bench/eval_cost_tiers.py --models ollama/gemma3:27b --score-quality
    poetry run python scripts/bench/eval_cost_tiers.py --tiers free,budget,standard,premium --repeat 2

Per ``feedback_no_dummy_data``: GPU watts are real or NULL. When Prometheus
is unreachable, ``gpu_watts_avg`` is recorded NULL and electricity falls
back to the static-TDP estimate — the NULL is the signal that the row used
the estimate path, not a measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path


def _ensure_paths() -> None:
    """Make services.* + brain.* importable without a poetry shell."""
    here = Path(__file__).resolve().parent
    # scripts/bench/ → repo root is three up.
    repo = here.parent.parent
    for p in (repo, repo / "src" / "cofounder_agent"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


_ensure_paths()


# FIXED, deterministic prompts so cross-model comparison is fair. Varying
# lengths (short factual / medium explain / long generate) exercise both
# the prefill and decode sides of the energy curve.
BENCHMARK_PROMPTS: list[dict] = [
    {
        "label": "short_factual",
        "messages": [
            {"role": "user", "content": "What is the capital of France? Answer in one word."},
        ],
    },
    {
        "label": "medium_explain",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Explain how a CPU cache improves performance. "
                    "Keep it to a single clear paragraph."
                ),
            },
        ],
    },
    {
        "label": "long_generate",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write an engaging introductory paragraph for a blog post about "
                    "the trade-offs between running large language models locally on a "
                    "consumer GPU versus calling a hosted cloud API. Aim for a confident, "
                    "concrete voice."
                ),
            },
        ],
    },
]


async def _resolve_pool():
    import asyncpg
    from brain.bootstrap import resolve_database_url

    dsn = resolve_database_url()
    if not dsn:
        raise SystemExit(
            "No DSN configured — set DATABASE_URL or write bootstrap.toml"
        )
    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)


async def _setting(pool, key: str, default: str) -> str:
    try:
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", key,
            )
    except Exception:
        return default
    if val is None or not str(val).strip():
        return default
    return str(val).strip()


def _is_local(model: str, provider_name: str) -> bool:
    """Local backend = zero token cost, GPU-electricity cost instead."""
    from services.cost_lookup import _is_local_route

    if _is_local_route(model):
        return True
    pl = (provider_name or "").lower()
    return pl.startswith("ollama") or pl in {"local", "vllm", "llama_cpp", "localai"}


async def _maybe_quality_score(text: str, prompt_label: str) -> float | None:
    """Score the completion 0-100 via UnifiedQualityService (pattern-based).

    Best-effort — any failure returns None so the sweep continues. Uses a
    minimal SiteConfig (defaults) since the harness runs outside the worker.
    """
    if not text or not text.strip():
        return None
    try:
        from modules.content.quality_service import get_quality_service
        from services.quality_models import EvaluationMethod
        from services.site_config import SiteConfig

        svc = get_quality_service(site_config=SiteConfig())
        assessment = await svc.evaluate(
            text,
            context={"topic": prompt_label},
            method=EvaluationMethod.PATTERN_BASED,
            store_result=False,
        )
        return round(float(assessment.overall_score), 2)
    except Exception as exc:  # noqa: BLE001
        print(f"    [warn] quality scoring failed: {exc}")
        return None


async def _run_one(
    pool,
    *,
    model: str,
    tier: str | None,
    prompt: dict,
    prometheus_url: str,
    score_quality: bool,
) -> dict | None:
    """Run a single (model × prompt) call and return the measured row dict.

    Returns None on dispatch failure (logged) so the sweep keeps going.
    """
    from services import energy_bench
    from services.cost_guard import CostGuard
    from services.cost_lookup import estimate_cost as cloud_estimate_cost
    from services.llm_providers.dispatcher import dispatch_complete, get_provider

    effective_tier = tier or "standard"
    task_id = f"bench_{uuid.uuid4().hex[:12]}"

    start = time.monotonic()
    start_ts = time.time()
    try:
        result = await dispatch_complete(
            pool,
            prompt["messages"],
            model,
            tier=effective_tier,
            task_id=task_id,
            phase="bench_harness",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    [skip] {model} / {prompt['label']}: dispatch failed: {exc}")
        return None
    duration_ms = int((time.monotonic() - start) * 1000)
    end_ts = time.time()

    prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(result, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(result, "total_tokens", 0) or 0) or (
        prompt_tokens + completion_tokens
    )

    # Provider name (for local/cloud classification + the persisted row).
    try:
        provider = await get_provider(pool, effective_tier)
        provider_name = getattr(provider, "name", "unknown")
    except Exception:
        provider_name = "unknown"

    is_local = _is_local(model, provider_name)

    # GPU watts — real measurement for local calls (cloud runs off-box, so
    # local Prometheus has nothing to attribute → None → estimate fallback).
    gpu_watts = None
    if is_local:
        gpu_watts = await energy_bench.measure_gpu_watts(prometheus_url, start_ts, end_ts)

    guard = CostGuard(pool=pool)

    # electricity_kwh: measured watts when available, else cost_guard estimate.
    if gpu_watts is not None:
        electricity_kwh = (gpu_watts * (duration_ms / 1000.0)) / 3_600_000.0
    elif is_local:
        electricity_kwh = guard.estimate_local_kwh(duration_ms=duration_ms)
    else:
        electricity_kwh = await guard.estimate_cloud_kwh(
            provider=provider_name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    # cost_usd: prefer LiteLLM's stamped response_cost; else cloud rate
    # lookup (cloud) or electricity bill (local).
    cost_usd = None
    raw = getattr(result, "raw", {}) or {}
    if isinstance(raw, dict) and raw.get("response_cost") is not None:
        try:
            cost_usd = float(raw["response_cost"])
        except (TypeError, ValueError):
            cost_usd = None
    if cost_usd is None:
        if is_local:
            cost_usd = guard.kwh_to_usd(electricity_kwh)
        else:
            cost_usd = cloud_estimate_cost(model, prompt_tokens, completion_tokens)

    jpt = energy_bench.joules_per_token(gpu_watts, duration_ms, total_tokens)
    tps = energy_bench.tokens_per_second(total_tokens, duration_ms)

    quality_score = None
    if score_quality:
        text = getattr(result, "text", None) or getattr(result, "content", None) or ""
        quality_score = await _maybe_quality_score(text, prompt["label"])

    return {
        "model": model,
        "tier": tier,
        "provider": provider_name,
        "prompt_label": prompt["label"],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
        "gpu_watts_avg": gpu_watts,
        "electricity_kwh": electricity_kwh,
        "cost_usd": cost_usd,
        "quality_score": quality_score,
        "joules_per_token": jpt,
        "tokens_per_second": tps,
    }


async def _persist(pool, row: dict) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bench_run_results (
                model, tier, provider, prompt_label,
                prompt_tokens, completion_tokens, total_tokens, duration_ms,
                gpu_watts_avg, electricity_kwh, cost_usd, quality_score,
                joules_per_token, tokens_per_second
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
            )
            """,
            row["model"], row["tier"], row["provider"], row["prompt_label"],
            row["prompt_tokens"], row["completion_tokens"], row["total_tokens"],
            row["duration_ms"], row["gpu_watts_avg"], row["electricity_kwh"],
            row["cost_usd"], row["quality_score"],
            row["joules_per_token"], row["tokens_per_second"],
        )


def _print_summary(rows: list[dict]) -> None:
    if not rows:
        print("\nNo successful runs to summarize.")
        return

    # Aggregate by (model, tier).
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["model"], r["tier"]), []).append(r)

    def _avg(vals: list) -> float | None:
        nums = [v for v in vals if v is not None]
        return sum(nums) / len(nums) if nums else None

    def _fmt(v, spec: str) -> str:
        return format(v, spec) if v is not None else "—"

    header = (
        f"{'model':<28} {'tier':<9} {'tok/s':>8} {'J/token':>9} "
        f"{'$/1k_tok':>11} {'quality':>8} {'$/qpt':>11}"
    )
    print("\n" + "=" * len(header))
    print("Cost-Tier Benchmark summary")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for (model, tier), grp in sorted(groups.items()):
        tps = _avg([g["tokens_per_second"] for g in grp])
        jpt = _avg([g["joules_per_token"] for g in grp])
        qual = _avg([g["quality_score"] for g in grp])
        # $/1k tokens averaged per-run (cost / total_tokens × 1000).
        per_1k = _avg(
            [
                (g["cost_usd"] / g["total_tokens"] * 1000.0)
                for g in grp
                if g["cost_usd"] is not None and g["total_tokens"]
            ]
        )
        # $/quality-point.
        per_qpt = _avg(
            [
                (g["cost_usd"] / g["quality_score"])
                for g in grp
                if g["cost_usd"] is not None and g["quality_score"]
            ]
        )
        print(
            f"{model[:28]:<28} {(tier or '—'):<9} "
            f"{_fmt(tps, '8.1f')} {_fmt(jpt, '9.4f')} "
            f"{_fmt(per_1k, '11.6f')} {_fmt(qual, '8.1f')} {_fmt(per_qpt, '11.6f')}"
        )
    print("=" * len(header))


async def _amain(args: argparse.Namespace) -> int:
    pool = await _resolve_pool()
    try:
        prometheus_url = args.prometheus_url or await _setting(
            pool, "bench_prometheus_url", "http://localhost:9091"
        )
        if args.repeat is not None:
            repeat = args.repeat
        else:
            repeat = int(await _setting(pool, "bench_default_prompt_count", "3"))

        # Build the (model, tier) work list.
        work: list[tuple[str, str | None]] = []
        if args.models:
            for m in [s.strip() for s in args.models.split(",") if s.strip()]:
                work.append((m, None))
        else:
            tiers = [s.strip() for s in args.tiers.split(",") if s.strip()]
            from services.llm_providers.dispatcher import resolve_tier_model

            for tier in tiers:
                try:
                    model = await resolve_tier_model(pool, tier)
                except Exception as exc:  # noqa: BLE001
                    print(f"[skip] tier {tier!r}: {exc}")
                    continue
                work.append((model, tier))

        if not work:
            print("Nothing to benchmark — no resolvable models/tiers.")
            return 1

        print(
            f"Benchmarking {len(work)} model(s) × {len(BENCHMARK_PROMPTS)} prompt(s) "
            f"× {repeat} repeat(s); prometheus={prometheus_url}; "
            f"score_quality={args.score_quality}"
        )

        rows: list[dict] = []
        for model, tier in work:
            label = f"{model}" + (f" (tier={tier})" if tier else "")
            print(f"\n>> {label}")
            for prompt in BENCHMARK_PROMPTS:
                for i in range(repeat):
                    row = await _run_one(
                        pool,
                        model=model,
                        tier=tier,
                        prompt=prompt,
                        prometheus_url=prometheus_url,
                        score_quality=args.score_quality,
                    )
                    if row is None:
                        continue
                    try:
                        await _persist(pool, row)
                    except Exception as exc:  # noqa: BLE001
                        print(f"    [warn] persist failed: {exc}")
                    watts = (
                        f"{row['gpu_watts_avg']:.0f}W"
                        if row["gpu_watts_avg"] is not None
                        else "est"
                    )
                    print(
                        f"    {prompt['label']}#{i + 1}: "
                        f"{row['total_tokens']}tok {row['duration_ms']}ms "
                        f"{watts} "
                        f"jpt={row['joules_per_token'] if row['joules_per_token'] is not None else '—'}"
                    )
                    rows.append(row)

        _print_summary(rows)
        return 0
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cost/energy eval harness for cost-tier routing validation (#530)",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--tiers",
        default="standard",
        help="Comma-separated cost tiers to sweep (free,budget,standard,premium). "
        "Each resolves to its configured model. Default: standard.",
    )
    g.add_argument(
        "--models",
        default=None,
        help="Comma-separated explicit model ids to benchmark instead of tiers.",
    )
    p.add_argument(
        "--repeat",
        type=int,
        default=None,
        help="Times to run each prompt per model. Default: bench_default_prompt_count.",
    )
    p.add_argument(
        "--score-quality",
        action="store_true",
        help="Score each completion 0-100 via UnifiedQualityService.",
    )
    p.add_argument(
        "--prometheus-url",
        default=None,
        help="Prometheus base URL. Default: app_settings.bench_prometheus_url.",
    )
    return p


def main() -> int:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    args = _build_parser().parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
