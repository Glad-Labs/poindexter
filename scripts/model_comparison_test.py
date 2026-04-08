"""
Model Comparison Test — runs the same topic through different models
and compares quality, speed, and content issues.

Usage: python scripts/model_comparison_test.py
"""
import asyncio
import asyncpg
import json
import time
import uuid

DB_URL = "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain"

MODELS = [
    "ollama/qwen3.5:35b",
    "ollama/qwen3:30b",
    "ollama/gemma3:27b",
    "ollama/glm-4.7-5090:latest",
    "ollama/gpt-oss:20b",
]

TOPIC = "Building a Self-Hosted AI Content Pipeline on Consumer Hardware"
RUNS_PER_MODEL = 2
POLL_INTERVAL = 15  # seconds
MAX_WAIT = 600  # 10 min max per task


async def set_writer_model(pool, model: str):
    """No-op — model is now injected per-task via model_selections column."""
    print(f"  [CONFIG] Model for next task: {model}")


async def create_task(pool, model: str, run: int) -> str:
    """Create a pending content task with a specific model forced via model_selections."""
    task_id = str(uuid.uuid4())
    # Strip ollama/ prefix for model_selections — content router parses "draft" key
    model_name = model.removeprefix("ollama/")
    meta = json.dumps({"test_model": model, "test_run": run, "test_batch": "model_comparison_v1"})
    model_sel = json.dumps({"draft": model_name})
    await pool.execute(
        """INSERT INTO content_tasks (task_id, task_type, content_type, status, topic, category,
               target_audience, metadata, model_selections, created_at, updated_at)
           VALUES ($1, 'blog_post', 'blog_post', 'pending', $2, 'ai-ml',
               'developers and hardware enthusiasts', $3::jsonb, $4::jsonb, NOW(), NOW())""",
        task_id, TOPIC, meta, model_sel,
    )
    return task_id


async def wait_for_completion(pool, task_id: str) -> dict:
    """Poll until task leaves pending/in_progress."""
    start = time.time()
    while time.time() - start < MAX_WAIT:
        row = await pool.fetchrow(
            """SELECT status, title, quality_score, content, error_message,
                      model_used, models_used_by_phase::text, actual_cost
               FROM content_tasks WHERE task_id = $1""",
            task_id,
        )
        status = row["status"]
        if status not in ("pending", "in_progress"):
            elapsed = time.time() - start
            content = row["content"] or ""
            has_cloud_ref = "OpenAI" in content or "Anthropic" in content
            has_hallucinated = any(
                phrase in content.lower()
                for phrase in ["\bour guide", "our article on", "our post on", "we discussed in our"]
            )
            return {
                "status": status,
                "title": row["title"],
                "quality": row["quality_score"],
                "content_len": len(content),
                "model_used": row["model_used"],
                "elapsed_s": round(elapsed, 1),
                "cost": float(row["actual_cost"] or 0),
                "cloud_api_refs": has_cloud_ref,
                "hallucinated_links": has_hallucinated,
                "error": row["error_message"],
            }
        await asyncio.sleep(POLL_INTERVAL)
    return {"status": "timeout", "elapsed_s": MAX_WAIT, "error": "Timed out waiting"}


async def main():
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    # Cancel any leftover active tasks
    await pool.execute(
        """UPDATE content_tasks SET status='cancelled', updated_at=NOW()
           WHERE status IN ('pending', 'in_progress', 'awaiting_approval')"""
    )

    results = []
    total = len(MODELS) * RUNS_PER_MODEL
    current = 0

    for model in MODELS:
        await set_writer_model(pool, model)
        # Give worker a moment to pick up new config
        await asyncio.sleep(3)

        for run in range(1, RUNS_PER_MODEL + 1):
            current += 1
            print(f"\n[{current}/{total}] {model} run {run}")
            task_id = await create_task(pool, model, run)
            print(f"  Task: {task_id[:8]}... waiting for completion")

            result = await wait_for_completion(pool, task_id)
            result["model_config"] = model
            result["run"] = run
            results.append(result)

            print(f"  Status: {result['status']} | Q: {result.get('quality', '--')} | "
                  f"{result.get('content_len', 0)} chars | {result['elapsed_s']}s | "
                  f"Model used: {result.get('model_used', '--')}")
            if result.get("cloud_api_refs"):
                print(f"  ⚠️  Cloud API references detected!")
            if result.get("hallucinated_links"):
                print(f"  ⚠️  Hallucinated internal links detected!")
            if result.get("error"):
                print(f"  ❌ Error: {result['error'][:100]}")

    # Summary table
    print("\n" + "=" * 100)
    print(f"{'Model':<30} {'Run':>3} {'Status':<12} {'Quality':>7} {'Chars':>6} {'Time':>7} {'Issues'}")
    print("-" * 100)
    for r in results:
        issues = []
        if r.get("cloud_api_refs"):
            issues.append("cloud-api")
        if r.get("hallucinated_links"):
            issues.append("hallucinated")
        if r.get("error"):
            issues.append("error")
        print(f"{r['model_config']:<30} {r['run']:>3} {r['status']:<12} "
              f"{r.get('quality', '--'):>7} {r.get('content_len', 0):>6} "
              f"{r['elapsed_s']:>6.0f}s {', '.join(issues) or 'clean'}")

    # Averages by model
    print("\n" + "=" * 100)
    print("AVERAGES BY MODEL:")
    print(f"{'Model':<30} {'Avg Q':>7} {'Avg Chars':>10} {'Avg Time':>10} {'Success':>8}")
    print("-" * 100)
    for model in MODELS:
        model_results = [r for r in results if r["model_config"] == model]
        successful = [r for r in model_results if r["status"] not in ("failed", "timeout")]
        if successful:
            avg_q = sum(r.get("quality", 0) or 0 for r in successful) / len(successful)
            avg_chars = sum(r.get("content_len", 0) for r in successful) / len(successful)
            avg_time = sum(r["elapsed_s"] for r in successful) / len(successful)
            print(f"{model:<30} {avg_q:>7.1f} {avg_chars:>10.0f} {avg_time:>9.0f}s "
                  f"{len(successful)}/{len(model_results)}")
        else:
            print(f"{model:<30} {'--':>7} {'--':>10} {'--':>10} 0/{len(model_results)}")

    # Save results to file
    with open("scripts/model_comparison_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nResults saved to scripts/model_comparison_results.json")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
