#!/usr/bin/env python3
"""Overnight stress test for the LangGraph canonical_blog cutover.

Dispatches N tasks (default 25, configurable via --count) with topics
sampled from the brand-keyword whitelist so they pass the off-brand
filter, then polls until every task reaches a terminal state.

Reports:
  - Per-task: terminal status + duration + error_message preview
  - Aggregate: pass/fail counts, mean duration, failure modes
  - Audit-log scan: rag_engine_fallback / qa_reviewer_failure /
    qa_pass_completed counts during the run

Designed to run unattended overnight. The worker daemon picks up the
tasks at its normal poll cadence; this script is purely an
orchestrator + reporter.

Safe to interrupt — re-running will dispatch fresh tasks; in-flight
ones from the previous run keep going to completion.

Usage:
  python scripts/stress_test_canonical_blog.py --count 25
  python scripts/stress_test_canonical_blog.py --count 50 --max-wait 7200
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python scripts/stress_test_canonical_blog.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "cofounder_agent"))

import asyncpg  # noqa: E402

# Brand-aligned topics — every one hits at least one keyword in
# TopicDiscovery._BRAND_KEYWORDS so the off-brand filter doesn't kill
# the run before the LangGraph chain gets a chance.
TOPICS = [
    "Why local Ollama inference beats cloud LLMs for content pipelines",
    "Vector database tradeoffs: pgvector vs Qdrant for self-hosted RAG",
    "Fine-tuning vs prompt engineering — when each actually pays off",
    "How to monitor GPU temps and VRAM usage on a homelab AI rig",
    "FastAPI vs Next.js for solo-founder SaaS backends",
    "Self-hosting Grafana + Prometheus on a single Postgres instance",
    "Docker compose patterns for indie hacker AI stacks",
    "Quantization tradeoffs: Q4_K_M vs Q8 for local LLM inference",
    "Why content automation needs human-in-the-loop approval gates",
    "Building a headless CMS with FastAPI and pgvector",
    "Token throughput benchmarks: RTX 5090 vs M3 Ultra vs cloud GPUs",
    "The case for owning your data — vendor lock-in in AI infrastructure",
    "How LoRA fine-tuning actually works (not just the marketing version)",
    "Rust vs Go for high-throughput LLM inference servers",
    "Stable Diffusion XL on a single 5090 — is it actually worth it?",
    "Open source AI agents: LangGraph vs AutoGen vs CrewAI",
    "Encryption-at-rest patterns for self-hosted Postgres",
    "Why Cloudflare Workers are eating Lambda's lunch for AI inference",
    "Indie hacker stack: TypeScript + FastAPI + pgvector + SDXL",
    "How to set up a local AI dev loop without OpenAI API keys",
    "Side-project SaaS economics — when to bootstrap vs raise",
    "Self-hosted observability: Tempo + Loki + Pyroscope on Docker",
    "Embedding models compared: nomic-embed-text vs bge-small vs OpenAI ada",
    "Why context window length matters less than retrieval quality",
    "Solo-founder AI infrastructure: what actually scales",
    "Postgres as the spinal cord — sharing state across AI services",
    "Headless content automation: bypassing the WordPress trap",
    "Diffusion model training on consumer hardware in 2026",
    "Prompt engineering hygiene for autonomous content pipelines",
    "Open-source LLM observability: Langfuse vs LangSmith",
    "Quantum computing in cybersecurity — beyond the buzzword",
    "Edge AI: running LLMs on Raspberry Pi 5 + Jetson Orin",
    "Why SDXL is finally good enough to replace stock photo APIs",
    "Postgres LISTEN/NOTIFY patterns for AI worker coordination",
    "RAG quality metrics that actually predict downstream output",
]


def _today_topic_pool(count: int) -> list[str]:
    """Pick `count` topics, suffixed so duplicates don't dedupe."""
    suffix = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    pool: list[str] = []
    for i, t in enumerate(TOPICS):
        if i >= count:
            break
        pool.append(f"{t} ({suffix} #{i+1})")
    return pool


async def _dispatch(pool, topics: list[str]) -> list[str]:
    """Insert tasks via the production tasks_db.add_task path so they
    pick up the default_template_slug routing. Returns task_ids."""
    from services.tasks_db import TasksDatabase

    db = TasksDatabase(pool)
    task_ids: list[str] = []
    for topic in topics:
        task_id = await db.add_task({
            "task_name": topic[:120],
            "topic": topic,
            "task_type": "blog_post",
            "status": "pending",
            "category": "ai_ml",
            "primary_keyword": topic.split()[0].lower(),
            "target_audience": "indie devs running AI stacks",
            "target_length": 1200,
            "style": "technical",
            "tone": "candid",
        })
        task_ids.append(task_id)
        print(f"  dispatched {task_id}  topic={topic[:60]!r}")
    return task_ids


async def _poll(
    pool, task_ids: list[str], max_wait_s: int,
) -> dict[str, dict]:
    """Poll until every task reaches a terminal state or timeout."""
    terminal = {"awaiting_approval", "completed", "failed", "rejected", "rejected_final"}
    started = time.time()
    last_print = 0.0
    results: dict[str, dict] = {}

    while True:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT task_id, status, stage, error_message, "
                "  extract(epoch from (now() - created_at))::int AS age_s "
                "FROM pipeline_tasks "
                "WHERE task_id = ANY($1::text[])",
                task_ids,
            )

        active = []
        for r in rows:
            if r["status"] in terminal:
                if r["task_id"] not in results:
                    results[r["task_id"]] = {
                        "status": r["status"],
                        "stage": r["stage"],
                        "duration_s": r["age_s"],
                        "error": (r["error_message"] or "")[:200],
                    }
            else:
                active.append((r["task_id"][:8], r["status"], r["stage"]))

        if not active:
            return results

        now = time.time()
        if now - last_print > 30:
            done = len(task_ids) - len(active)
            print(
                f"  [{now-started:.0f}s] {done}/{len(task_ids)} done · "
                f"active stages: "
                + ", ".join(f"{tid}:{st}" for tid, _s, st in active[:5])
                + ("..." if len(active) > 5 else "")
            )
            last_print = now

        if now - started > max_wait_s:
            print(f"\nTIMEOUT after {max_wait_s}s — {len(active)} tasks still active")
            for tid, st, stage in active:
                results[tid] = {
                    "status": f"timeout ({st})",
                    "stage": stage,
                    "duration_s": int(now - started),
                    "error": "stress harness timeout",
                }
            return results

        await asyncio.sleep(15)


async def _audit_scan(pool, since: datetime) -> dict[str, int]:
    """Count interesting audit_log events that fired during the run."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT event_type, COUNT(*)::int AS n "
            "FROM audit_log WHERE timestamp >= $1 "
            "GROUP BY event_type ORDER BY n DESC",
            since,
        )
    return {r["event_type"]: r["n"] for r in rows}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=25,
                        help="Number of tasks to dispatch (max %d)" % len(TOPICS))
    parser.add_argument("--max-wait", type=int, default=7200,
                        help="Max wait per run in seconds (default 2h)")
    parser.add_argument("--dsn", default=(
        "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
    ))
    args = parser.parse_args()

    count = min(args.count, len(TOPICS))
    started_at = datetime.now(timezone.utc)

    print(f"=== stress test starting at {started_at.isoformat()} ===")
    print(f"  count    : {count}")
    print(f"  max_wait : {args.max_wait}s")
    print()

    pool = await asyncpg.create_pool(args.dsn, min_size=1, max_size=4)

    print("=== dispatching ===")
    topics = _today_topic_pool(count)
    task_ids = await _dispatch(pool, topics)

    print(f"\n=== polling {len(task_ids)} tasks ===")
    results = await _poll(pool, task_ids, args.max_wait)

    print(f"\n=== results ===")
    by_status: dict[str, int] = {}
    durations: list[int] = []
    failures: list[tuple[str, str]] = []
    for tid, r in results.items():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        durations.append(r["duration_s"])
        if r["status"] in ("failed", "rejected", "rejected_final") and r["error"]:
            failures.append((tid[:8], r["error"]))

    for status, n in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"  {status:<28} {n}")

    if durations:
        durations.sort()
        mid = durations[len(durations) // 2]
        p95 = durations[int(len(durations) * 0.95)]
        print(f"\n  median duration : {mid}s")
        print(f"  p95 duration    : {p95}s")
        print(f"  total elapsed   : {(datetime.now(timezone.utc) - started_at).total_seconds():.0f}s")

    if failures:
        print(f"\n=== failure samples ({min(5, len(failures))}/{len(failures)}) ===")
        for tid, err in failures[:5]:
            print(f"  {tid}: {err}")

    print("\n=== audit_log scan ===")
    counts = await _audit_scan(pool, started_at)
    notable = [
        "rag_engine_fallback",
        "qa_reviewer_failure",
        "qa_pass_completed",
        "auto_publish_gate",
        "task_started",
        "template_completed",
    ]
    for ev in notable:
        if ev in counts:
            marker = " ⚠️" if ev in ("rag_engine_fallback", "qa_reviewer_failure") and counts[ev] > 0 else ""
            print(f"  {ev:<28} {counts[ev]}{marker}")

    if any(counts.get(ev, 0) > 0 for ev in ("rag_engine_fallback", "qa_reviewer_failure")):
        print("\n  ⚠️  Loud-fallback events fired — open the QA Rails dashboard:")
        print("      http://localhost:3000/d/qa-rails")

    await pool.close()
    print("\n=== done ===")


if __name__ == "__main__":
    asyncio.run(main())
