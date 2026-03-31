"""Pipeline Benchmark — A/B test models and configs for content quality.

Extends the existing model_benchmarking.ps1 (throughput) with quality scoring.
Runs the same topics through different model/config variants and compares:
- Quality score (0-100)
- Generation time
- Cost (for cloud models)
- Content length
- QA pass rate

Results stored in DB (content_tasks with experiment metadata) and CSV.

Usage:
    python scripts/pipeline_benchmark.py                    # Run default benchmark
    python scripts/pipeline_benchmark.py --models glm-4.7-5090,qwen3.5  # Specific models
    python scripts/pipeline_benchmark.py --cloud            # Include cloud models
    python scripts/pipeline_benchmark.py --topics 5         # Number of test topics
    python scripts/pipeline_benchmark.py --dry-run          # Show plan without running
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

API_URL = "https://cofounder-production.up.railway.app"
OLLAMA_URL = "http://127.0.0.1:11434"

# Load API token
API_TOKEN = os.getenv("GLADLABS_KEY", "")
if not API_TOKEN:
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("GLADLABS_KEY="):
                API_TOKEN = _line.split("=", 1)[1].strip()
AUTH = f"Bearer {API_TOKEN}"

# Test topics — same across all variants for fair comparison
DEFAULT_TOPICS = [
    "Building a REST API with FastAPI and PostgreSQL",
    "Local LLMs vs Cloud APIs: A Cost Analysis for Startups",
    "Docker Containers for Solo Developers: What You Actually Need",
    "Monitoring Your Infrastructure with Grafana: A Practical Guide",
    "Why RAG Pipelines Are Replacing Traditional Search",
]

# Model variants to test
LOCAL_MODELS = [
    "glm-4.7-5090:latest",
    "qwen3.5:latest",
    "qwen3-coder:30b",
    "gemma3:27b",
    "phi3:14b",
]

CLOUD_MODELS = [
    # These use the model router's cloud provider integration
    # Requires API keys in OpenClaw .env
    "anthropic/claude-haiku-4-5",
    "google/gemini-2.0-flash",
]


def get_available_ollama_models():
    """Get list of models currently loaded in Ollama."""
    try:
        data = json.loads(urllib.request.urlopen(
            f"{OLLAMA_URL}/api/tags", timeout=5
        ).read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def create_benchmark_task(topic, model, experiment_id, variant_label):
    """Create a content task tagged with benchmark metadata."""
    payload = json.dumps({
        "task_name": f"[BENCH-{experiment_id[:8]}] {topic}",
        "topic": topic,
        "category": "technology",
        "target_audience": "developers and founders",
        "task_type": "blog_post",
        "quality_preference": "balanced",
        "models_by_phase": {"draft": model, "refine": model},
        "metadata": {
            "experiment_id": experiment_id,
            "variant": variant_label,
            "model": model,
            "benchmark": True,
            "created_at": datetime.utcnow().isoformat(),
        },
    }).encode()

    req = urllib.request.Request(
        f"{API_URL}/api/tasks",
        data=payload,
        headers={"Authorization": AUTH, "Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return resp.get("task_id", resp.get("id"))


def poll_task(task_id, timeout=600, interval=10):
    """Poll task until completion or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(
                f"{API_URL}/api/tasks/{task_id}",
                headers={"Authorization": AUTH},
            )
            task = json.loads(urllib.request.urlopen(req, timeout=10).read())
            status = task.get("status", "")
            if status in ("awaiting_approval", "published", "completed", "rejected"):
                return task
            if status == "failed":
                return task
        except Exception:
            pass
        time.sleep(interval)
    return None


def extract_results(task):
    """Extract benchmark metrics from a completed task."""
    if not task:
        return {"status": "timeout", "quality_score": 0, "duration_s": 0}

    status = task.get("status", "unknown")
    quality = task.get("quality_score", 0) or 0
    model_used = task.get("model_used", "unknown")
    content = task.get("content", "")
    word_count = len(content.split()) if content else 0

    created = task.get("created_at", "")
    updated = task.get("updated_at", "")
    duration = 0
    if created and updated:
        try:
            from datetime import datetime as dt
            t1 = dt.fromisoformat(created.replace("Z", "+00:00"))
            t2 = dt.fromisoformat(updated.replace("Z", "+00:00"))
            duration = (t2 - t1).total_seconds()
        except Exception:
            pass

    cost = task.get("actual_cost") or task.get("estimated_cost") or 0

    # Get QA details from result or task_metadata
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    qa_score = result.get("qa_final_score", 0) or 0
    qa_reviews = result.get("qa_reviews", [])

    return {
        "status": status,
        "quality_score": quality,
        "qa_score": qa_score,
        "model_used": model_used,
        "word_count": word_count,
        "duration_s": round(duration, 1),
        "cost_usd": float(cost or 0),
        "qa_reviews": qa_reviews,
    }


def run_benchmark(topics, models, experiment_id, cloud=False, dry_run=False):
    """Run a full benchmark across topics and models."""
    if cloud:
        models = models + CLOUD_MODELS

    available = get_available_ollama_models()
    print(f"Available Ollama models: {len(available)}")
    print(f"Benchmark models: {models}")
    print(f"Topics: {len(topics)}")
    print(f"Total tasks: {len(topics) * len(models)}")
    print(f"Experiment ID: {experiment_id}")
    print()

    if dry_run:
        for topic in topics:
            for model in models:
                variant = model.split("/")[-1].split(":")[0]
                print(f"  [DRY RUN] {variant:20s} | {topic[:60]}")
        return []

    results = []
    for topic in topics:
        print(f"\n--- Topic: {topic[:60]} ---")
        topic_results = {}

        # Create all variant tasks for this topic
        for model in models:
            variant = model.split("/")[-1].split(":")[0]
            print(f"  Creating task for {variant}...", end=" ", flush=True)
            try:
                task_id = create_benchmark_task(topic, model, experiment_id, variant)
                print(f"task={task_id[:8]}")
                topic_results[variant] = {"task_id": task_id, "model": model}
            except Exception as e:
                print(f"FAILED: {e}")
                topic_results[variant] = {"task_id": None, "model": model, "error": str(e)}

        # Poll all tasks for this topic
        print("  Waiting for results...")
        for variant, info in topic_results.items():
            if not info.get("task_id"):
                results.append({
                    "experiment_id": experiment_id,
                    "topic": topic,
                    "variant": variant,
                    "model": info["model"],
                    "status": "create_failed",
                    "quality_score": 0,
                    "qa_score": 0,
                    "word_count": 0,
                    "duration_s": 0,
                    "cost_usd": 0,
                })
                continue

            print(f"  Polling {variant}...", end=" ", flush=True)
            task = poll_task(info["task_id"], timeout=300)
            metrics = extract_results(task)
            print(f"score={metrics['quality_score']} status={metrics['status']} "
                  f"words={metrics['word_count']} time={metrics['duration_s']}s")

            results.append({
                "experiment_id": experiment_id,
                "topic": topic,
                "variant": variant,
                "model": info["model"],
                "task_id": info["task_id"],
                **metrics,
            })

    return results


def print_summary(results):
    """Print a summary comparison table."""
    if not results:
        return

    # Group by variant
    variants = {}
    for r in results:
        v = r["variant"]
        if v not in variants:
            variants[v] = {"scores": [], "durations": [], "words": [], "costs": [], "passed": 0, "total": 0}
        variants[v]["scores"].append(r["quality_score"])
        variants[v]["durations"].append(r["duration_s"])
        variants[v]["words"].append(r["word_count"])
        variants[v]["costs"].append(r["cost_usd"])
        variants[v]["total"] += 1
        if r["status"] in ("awaiting_approval", "published", "completed"):
            variants[v]["passed"] += 1

    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS SUMMARY")
    print("=" * 80)
    print(f"{'Variant':<25} {'Avg Score':>10} {'Avg Time':>10} {'Avg Words':>10} {'Pass Rate':>10} {'Avg Cost':>10}")
    print("-" * 80)

    ranked = sorted(variants.items(), key=lambda x: sum(x[1]["scores"]) / max(len(x[1]["scores"]), 1), reverse=True)
    for variant, data in ranked:
        avg_score = sum(data["scores"]) / max(len(data["scores"]), 1)
        avg_time = sum(data["durations"]) / max(len(data["durations"]), 1)
        avg_words = sum(data["words"]) / max(len(data["words"]), 1)
        avg_cost = sum(data["costs"]) / max(len(data["costs"]), 1)
        pass_rate = data["passed"] / max(data["total"], 1) * 100

        print(f"{variant:<25} {avg_score:>10.1f} {avg_time:>9.1f}s {avg_words:>10.0f} {pass_rate:>9.0f}% ${avg_cost:>9.4f}")

    print("=" * 80)


def save_results(results, experiment_id):
    """Save results to CSV."""
    if not results:
        return

    out_dir = os.path.join(os.path.dirname(__file__))
    out_path = os.path.join(out_dir, f"benchmark_{experiment_id[:8]}.csv")

    fieldnames = ["experiment_id", "topic", "variant", "model", "task_id",
                  "status", "quality_score", "qa_score", "model_used",
                  "word_count", "duration_s", "cost_usd"]

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"\nResults saved to {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Pipeline content quality benchmark")
    parser.add_argument("--models", type=str, default=None,
                        help="Comma-separated model names (default: top 5 local)")
    parser.add_argument("--cloud", action="store_true",
                        help="Include cloud models (Claude Haiku, Gemini Flash)")
    parser.add_argument("--topics", type=int, default=3,
                        help="Number of test topics (default: 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without creating tasks")
    parser.add_argument("--experiment-id", type=str, default=None,
                        help="Custom experiment ID (default: auto-generated)")
    args = parser.parse_args()

    experiment_id = args.experiment_id or f"bench-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    if args.models:
        models = [m.strip() for m in args.models.split(",")]
    else:
        # Default: top performers from existing benchmarks
        models = LOCAL_MODELS[:3]  # glm-4.7-5090, qwen3.5, qwen3-coder

    topics = DEFAULT_TOPICS[:args.topics]

    print(f"Pipeline Quality Benchmark — {experiment_id}")
    print(f"Testing {len(models)} models x {len(topics)} topics = {len(models) * len(topics)} tasks")
    print()

    results = run_benchmark(topics, models, experiment_id, cloud=args.cloud, dry_run=args.dry_run)

    if results:
        print_summary(results)
        save_results(results, experiment_id)


if __name__ == "__main__":
    main()
