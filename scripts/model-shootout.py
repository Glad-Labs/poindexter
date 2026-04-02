"""Model Shootout — Compare multiple Ollama models on blog generation quality and cost.

For each of 3 topics, generates a blog post with every available model, then:
- Measures generation time, token counts, word count
- Runs QA scoring with gemma3:27b
- Calculates electricity cost and cloud-equivalent costs
- Tests hybrid configs (draft with model X, QA with model Y)

Results saved to ~/.gladlabs/model-shootout-results.json

Usage:
    python scripts/model-shootout.py
    python scripts/model-shootout.py --models gemma3:27b,qwen3:30b
    python scripts/model-shootout.py --qa-model gemma3:27b
    python scripts/model-shootout.py --skip-hybrid
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_URL = "http://127.0.0.1:11434"
ELECTRICITY_RATE_KWH = 0.14770  # $/kWh
GPU_WATTS = 250  # inference power draw
REQUEST_TIMEOUT = 900  # 15 min for full blog generation
QA_TIMEOUT = 300  # 5 min for QA

DEFAULT_MODELS = ["gemma3:27b", "glm-4.7-5090", "qwen3.5:35b", "qwen3:30b", "phi3", "llama3"]
DEFAULT_QA_MODEL = "gemma3:27b"

TOPICS = [
    {
        "topic": "Docker containers for startups",
        "keyword": "Docker containers",
        "audience": "startup founders and technical leads",
    },
    {
        "topic": "AI agent architectures",
        "keyword": "AI agents",
        "audience": "software engineers building AI products",
    },
    {
        "topic": "Local vs cloud AI",
        "keyword": "local AI",
        "audience": "tech-savvy business owners evaluating AI deployment",
    },
]

# Cloud pricing per 1M tokens (input / output)
CLOUD_PRICING = {
    "Haiku": {"input": 0.80, "output": 4.00},
    "Sonnet": {"input": 3.00, "output": 15.00},
    "GPT-4o-mini": {"input": 0.15, "output": 0.60},
}

GENERATION_SYSTEM = """You are an expert technical writer and professional blogger.
Write clear, engaging, well-structured content.
Format as Markdown with # for title and ## for sections.
Generate approximately 1500 words. Include practical examples and actionable insights.
Use creative, compelling section titles — never generic ones like "Introduction" or "Conclusion"."""

GENERATION_PROMPT_TEMPLATE = """Generate a comprehensive blog post about '{topic}'.
Target audience: {audience}.
Primary keyword: '{keyword}'.

Requirements:
1. Start with # Title on the first line
2. Include 3-5 main sections with ## subheadings
3. Write approximately 1500 words of substantive content
4. Include [IMAGE-1], [IMAGE-2] placeholders where visuals help
5. End with a compelling call-to-action
6. Do NOT fabricate statistics, quotes, or named sources

Write the complete blog post now."""

QA_SYSTEM = "You are a content quality reviewer. Evaluate blog posts for publication readiness."

QA_PROMPT_TEMPLATE = """Review this blog post for publication readiness.

TARGET AUDIENCE: {audience}
PRIMARY KEYWORD: '{keyword}'

---DRAFT---
{draft}
---END DRAFT---

Evaluate against these criteria:
1. Clarity: Is writing clear and easy to understand?
2. Tone: Professional and engaging for target audience?
3. Keyword integration: Primary keyword used naturally?
4. Structure: Clear headings, logical flow?
5. Value: Genuine insights and useful information?
6. Section titles: Creative and compelling? (Generic titles = -10 points)
7. Research depth: Demonstrates real knowledge?
8. Factual integrity: No fabricated stats, quotes, or sources? (Fabrication = score below 60)

GRADING SCALE (0-100):
- 85+: Excellent  - 75-84: Good  - 70-74: Needs work  - 60-69: Fair  - <60: Poor

Respond with ONLY valid JSON (no markdown):
{{"approved": true/false, "quality_score": NUMBER, "feedback": "2-3 sentences of specific feedback"}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def calc_electricity_cost(seconds: float) -> float:
    hours = seconds / 3600
    kwh = (GPU_WATTS / 1000) * hours
    return kwh * ELECTRICITY_RATE_KWH


def calc_cloud_cost(input_tokens: int, output_tokens: int, provider: str) -> float:
    pricing = CLOUD_PRICING[provider]
    return (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]


def count_words(text: str) -> int:
    return len(text.split())


def parse_qa_response(text: str) -> dict:
    """Try to extract JSON from QA model response."""
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON in the response
    for start_char in ["{", "```json\n{", "```\n{"]:
        idx = text.find(start_char)
        if idx >= 0:
            # Find the matching closing brace
            snippet = text[idx:]
            if snippet.startswith("```"):
                snippet = snippet.split("```")[1]
                if snippet.startswith("json\n"):
                    snippet = snippet[5:]
                elif snippet.startswith("\n"):
                    snippet = snippet[1:]
            brace_count = 0
            for i, c in enumerate(snippet):
                if c == "{":
                    brace_count += 1
                elif c == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        try:
                            return json.loads(snippet[: i + 1])
                        except json.JSONDecodeError:
                            break
    return {"approved": False, "quality_score": 0, "feedback": "Failed to parse QA response"}


# ---------------------------------------------------------------------------
# Ollama API
# ---------------------------------------------------------------------------
async def call_ollama(
    client: httpx.AsyncClient, model: str, system: str, prompt: str, timeout: int = REQUEST_TIMEOUT
) -> dict:
    payload = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
    }
    start = time.monotonic()
    try:
        resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        elapsed = time.monotonic() - start
        return {"error": f"Timeout after {elapsed:.1f}s", "elapsed": elapsed, "input_tokens": 0, "output_tokens": 0, "response": ""}
    except httpx.HTTPStatusError as e:
        elapsed = time.monotonic() - start
        return {"error": f"HTTP {e.response.status_code}", "elapsed": elapsed, "input_tokens": 0, "output_tokens": 0, "response": ""}
    except Exception as e:
        elapsed = time.monotonic() - start
        return {"error": str(e), "elapsed": elapsed, "input_tokens": 0, "output_tokens": 0, "response": ""}

    elapsed = time.monotonic() - start
    return {
        "error": None,
        "elapsed": elapsed,
        "input_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": data.get("eval_count", 0),
        "response": data.get("response", ""),
    }


async def check_available_models(client: httpx.AsyncClient, requested: list[str]) -> list[str]:
    """Return which requested models are actually available in Ollama."""
    try:
        resp = await client.get(f"{OLLAMA_URL}/api/tags", timeout=10)
        resp.raise_for_status()
        available = [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        print(f"ERROR: Cannot connect to Ollama at {OLLAMA_URL}: {e}")
        sys.exit(1)

    print(f"Ollama models installed: {available}")

    # Match requested models (partial match, e.g. "gemma3:27b" matches "gemma3:27b" or "gemma3:27b-fp16")
    matched = []
    for req in requested:
        found = [a for a in available if req in a or a.startswith(req.split(":")[0])]
        if found:
            # Prefer exact match
            exact = [a for a in found if a == req]
            matched.append(exact[0] if exact else found[0])
        else:
            print(f"  WARNING: '{req}' not found, skipping")

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for m in matched:
        if m not in seen:
            seen.add(m)
            deduped.append(m)

    return deduped


# ---------------------------------------------------------------------------
# Generation + QA pipeline
# ---------------------------------------------------------------------------
async def generate_and_score(
    client: httpx.AsyncClient,
    gen_model: str,
    qa_model: str,
    topic_info: dict,
) -> dict:
    """Generate a blog post and score it. Returns full result dict."""
    topic = topic_info["topic"]
    keyword = topic_info["keyword"]
    audience = topic_info["audience"]

    prompt = GENERATION_PROMPT_TEMPLATE.format(topic=topic, keyword=keyword, audience=audience)

    # Generate
    gen_result = await call_ollama(client, gen_model, GENERATION_SYSTEM, prompt)

    if gen_result["error"]:
        return {
            "gen_model": gen_model,
            "qa_model": qa_model,
            "topic": topic,
            "error": gen_result["error"],
            "gen_time_s": round(gen_result["elapsed"], 2),
            "gen_input_tokens": 0,
            "gen_output_tokens": 0,
            "word_count": 0,
            "quality_score": 0,
            "qa_approved": False,
            "qa_feedback": gen_result["error"],
            "qa_time_s": 0,
            "qa_input_tokens": 0,
            "qa_output_tokens": 0,
            "total_electricity_cost": round(calc_electricity_cost(gen_result["elapsed"]), 6),
        }

    draft = gen_result["response"]
    word_count = count_words(draft)
    gen_elec = calc_electricity_cost(gen_result["elapsed"])

    # QA
    qa_prompt = QA_PROMPT_TEMPLATE.format(audience=audience, keyword=keyword, draft=draft)
    qa_result = await call_ollama(client, qa_model, QA_SYSTEM, qa_prompt, timeout=QA_TIMEOUT)

    qa_elec = calc_electricity_cost(qa_result["elapsed"]) if not qa_result["error"] else 0
    total_elec = gen_elec + qa_elec

    if qa_result["error"]:
        qa_parsed = {"quality_score": 0, "approved": False, "feedback": qa_result["error"]}
    else:
        qa_parsed = parse_qa_response(qa_result["response"])

    # Cloud costs for the generation step
    cloud_costs = {}
    total_in = gen_result["input_tokens"] + (qa_result["input_tokens"] or 0)
    total_out = gen_result["output_tokens"] + (qa_result["output_tokens"] or 0)
    for provider in CLOUD_PRICING:
        cloud_costs[provider] = round(calc_cloud_cost(total_in, total_out, provider), 6)

    return {
        "gen_model": gen_model,
        "qa_model": qa_model,
        "topic": topic,
        "error": None,
        "gen_time_s": round(gen_result["elapsed"], 2),
        "gen_input_tokens": gen_result["input_tokens"],
        "gen_output_tokens": gen_result["output_tokens"],
        "word_count": word_count,
        "quality_score": qa_parsed.get("quality_score", 0),
        "qa_approved": qa_parsed.get("approved", False),
        "qa_feedback": qa_parsed.get("feedback", ""),
        "qa_time_s": round(qa_result["elapsed"], 2),
        "qa_input_tokens": qa_result["input_tokens"],
        "qa_output_tokens": qa_result["output_tokens"],
        "total_time_s": round(gen_result["elapsed"] + qa_result["elapsed"], 2),
        "total_electricity_cost": round(total_elec, 6),
        "cloud_equivalent": cloud_costs,
        "gen_tok_per_sec": round(gen_result["output_tokens"] / gen_result["elapsed"], 1) if gen_result["elapsed"] > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run_shootout(models: list[str], qa_model: str, skip_hybrid: bool = False):
    print("=" * 80)
    print("  MODEL SHOOTOUT")
    print(f"  Models: {', '.join(models)}")
    print(f"  QA Model: {qa_model}")
    print(f"  Topics: {len(TOPICS)}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()

    all_results = []
    hybrid_results = []

    async with httpx.AsyncClient() as client:
        available = await check_available_models(client, models)
        if not available:
            print("ERROR: No requested models are available.")
            sys.exit(1)
        print(f"Testing models: {available}")

        # Ensure QA model is available
        qa_available = await check_available_models(client, [qa_model])
        if not qa_available:
            print(f"ERROR: QA model '{qa_model}' not available.")
            sys.exit(1)
        qa_model_resolved = qa_available[0]
        print(f"QA model: {qa_model_resolved}")
        print()

        # ---------------------------------------------------------------
        # Phase 1: Single-model runs
        # ---------------------------------------------------------------
        total_runs = len(available) * len(TOPICS)
        run_num = 0

        for model in available:
            for topic_info in TOPICS:
                run_num += 1
                print(f"[{run_num}/{total_runs}] {model} -- \"{topic_info['topic']}\"")

                result = await generate_and_score(client, model, qa_model_resolved, topic_info)

                if result["error"]:
                    print(f"         ERROR: {result['error']}")
                else:
                    print(
                        f"         Words: {result['word_count']:,}  "
                        f"Score: {result['quality_score']}  "
                        f"Time: {result['total_time_s']:.0f}s  "
                        f"Speed: {result['gen_tok_per_sec']} tok/s  "
                        f"Cost: ${result['total_electricity_cost']:.6f}"
                    )

                all_results.append(result)
                print()

        # ---------------------------------------------------------------
        # Phase 2: Hybrid configs (draft with X, QA with Y)
        # ---------------------------------------------------------------
        if not skip_hybrid and len(available) >= 2:
            print()
            print("=" * 80)
            print("  HYBRID CONFIGURATIONS (draft model != QA model)")
            print("=" * 80)
            print()

            # Test: draft with each non-QA model, QA with each other model
            # Keep it bounded: only test pairs where draft != QA, use first topic only
            hybrid_topic = TOPICS[0]
            hybrid_pairs = []
            for draft_model in available:
                for review_model in available:
                    if draft_model != review_model:
                        hybrid_pairs.append((draft_model, review_model))

            for i, (draft_m, review_m) in enumerate(hybrid_pairs, 1):
                print(f"[Hybrid {i}/{len(hybrid_pairs)}] Draft: {draft_m} | QA: {review_m}")

                result = await generate_and_score(client, draft_m, review_m, hybrid_topic)
                result["config_type"] = "hybrid"

                if result["error"]:
                    print(f"         ERROR: {result['error']}")
                else:
                    print(
                        f"         Words: {result['word_count']:,}  "
                        f"Score: {result['quality_score']}  "
                        f"Time: {result['total_time_s']:.0f}s  "
                        f"Cost: ${result['total_electricity_cost']:.6f}"
                    )

                hybrid_results.append(result)
                print()

    # -----------------------------------------------------------------------
    # Summary tables
    # -----------------------------------------------------------------------
    print()
    print("=" * 110)
    print("  SINGLE-MODEL RESULTS")
    print("=" * 110)
    print()
    header = (
        f"{'Model':<20} {'Topic':<28} {'Score':>5} {'Words':>6} "
        f"{'Time':>6} {'tok/s':>6} {'Elec $':>10} {'Haiku $':>9} {'Sonnet $':>9} {'4o-m $':>9}"
    )
    print(header)
    print("-" * 110)

    for r in all_results:
        topic_short = r["topic"][:26]
        cloud = r.get("cloud_equivalent", {})
        print(
            f"{r['gen_model']:<20} {topic_short:<28} {r['quality_score']:>5} {r['word_count']:>6} "
            f"{r.get('total_time_s', r['gen_time_s']):>5.0f}s {r['gen_tok_per_sec']:>6.1f} "
            f"${r['total_electricity_cost']:>9.6f} "
            f"${cloud.get('Haiku', 0):>8.6f} "
            f"${cloud.get('Sonnet', 0):>8.6f} "
            f"${cloud.get('GPT-4o-mini', 0):>8.6f}"
        )

    # Per-model averages
    print()
    print("  PER-MODEL AVERAGES")
    print("-" * 90)
    print(f"{'Model':<20} {'Avg Score':>9} {'Avg Words':>10} {'Avg Time':>9} {'Avg tok/s':>10} {'Avg Cost':>10}")
    print("-" * 90)

    model_groups = {}
    for r in all_results:
        m = r["gen_model"]
        if m not in model_groups:
            model_groups[m] = []
        model_groups[m].append(r)

    for model, runs in model_groups.items():
        valid = [r for r in runs if not r["error"]]
        if not valid:
            print(f"{model:<20} {'ALL ERRORS':>9}")
            continue
        avg_score = sum(r["quality_score"] for r in valid) / len(valid)
        avg_words = sum(r["word_count"] for r in valid) / len(valid)
        avg_time = sum(r.get("total_time_s", r["gen_time_s"]) for r in valid) / len(valid)
        avg_tps = sum(r["gen_tok_per_sec"] for r in valid) / len(valid)
        avg_cost = sum(r["total_electricity_cost"] for r in valid) / len(valid)
        print(
            f"{model:<20} {avg_score:>9.1f} {avg_words:>10.0f} {avg_time:>8.0f}s "
            f"{avg_tps:>10.1f} ${avg_cost:>9.6f}"
        )

    # Hybrid results
    if hybrid_results:
        print()
        print("=" * 100)
        print("  HYBRID CONFIGURATION RESULTS")
        print("=" * 100)
        print()
        print(f"{'Draft Model':<20} {'QA Model':<20} {'Score':>5} {'Words':>6} {'Time':>6} {'Cost':>10}")
        print("-" * 100)
        for r in hybrid_results:
            print(
                f"{r['gen_model']:<20} {r['qa_model']:<20} {r['quality_score']:>5} "
                f"{r['word_count']:>6} {r.get('total_time_s', r['gen_time_s']):>5.0f}s "
                f"${r['total_electricity_cost']:>9.6f}"
            )

        # Best hybrid vs best single
        valid_single = [r for r in all_results if not r["error"] and r["quality_score"] > 0]
        valid_hybrid = [r for r in hybrid_results if not r["error"] and r["quality_score"] > 0]
        if valid_single and valid_hybrid:
            best_single = max(valid_single, key=lambda r: r["quality_score"])
            best_hybrid = max(valid_hybrid, key=lambda r: r["quality_score"])
            print()
            print(f"  Best single-model: {best_single['gen_model']} (score {best_single['quality_score']})")
            print(f"  Best hybrid:       {best_hybrid['gen_model']}+{best_hybrid['qa_model']} (score {best_hybrid['quality_score']})")

    # -----------------------------------------------------------------------
    # Save JSON
    # -----------------------------------------------------------------------
    report = {
        "shootout_timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "models_tested": list(model_groups.keys()),
            "qa_model": qa_model_resolved,
            "topics": TOPICS,
            "gpu_watts": GPU_WATTS,
            "electricity_rate_kwh": ELECTRICITY_RATE_KWH,
            "cloud_pricing_per_1M_tokens": CLOUD_PRICING,
        },
        "single_model_results": all_results,
        "hybrid_results": hybrid_results,
        "per_model_averages": {},
    }

    for model, runs in model_groups.items():
        valid = [r for r in runs if not r["error"]]
        if valid:
            report["per_model_averages"][model] = {
                "avg_quality_score": round(sum(r["quality_score"] for r in valid) / len(valid), 1),
                "avg_word_count": round(sum(r["word_count"] for r in valid) / len(valid)),
                "avg_time_s": round(sum(r.get("total_time_s", r["gen_time_s"]) for r in valid) / len(valid), 1),
                "avg_tok_per_sec": round(sum(r["gen_tok_per_sec"] for r in valid) / len(valid), 1),
                "avg_electricity_cost": round(sum(r["total_electricity_cost"] for r in valid) / len(valid), 6),
                "runs": len(valid),
                "errors": len(runs) - len(valid),
            }

    out_dir = Path.home() / ".gladlabs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "model-shootout-results.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print()
    print(f"  Results saved to: {out_path}")
    print(f"  Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Model Shootout")
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help=f"Comma-separated models (default: {','.join(DEFAULT_MODELS)})",
    )
    parser.add_argument("--qa-model", default=DEFAULT_QA_MODEL, help=f"QA reviewer model (default: {DEFAULT_QA_MODEL})")
    parser.add_argument("--skip-hybrid", action="store_true", help="Skip hybrid configuration tests")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    asyncio.run(run_shootout(models, args.qa_model, args.skip_hybrid))


if __name__ == "__main__":
    main()
