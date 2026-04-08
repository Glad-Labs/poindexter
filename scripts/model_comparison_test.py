"""
Model Shootout v2 — Direct Ollama comparison, no pipeline overhead.

Generates content directly via Ollama API, QA scores with a separate model,
tracks tokens/sec, electricity cost, and cloud cost equivalent.
Based on Matt's original model-shootout.py approach.

Usage: python scripts/model_comparison_test.py
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
RESULTS_PATH = os.path.expanduser("~/.gladlabs/model-shootout-results.json")

# Models to test (must be installed in Ollama)
MODELS = [
    # Fits in 32GB VRAM (fast)
    "qwen3.5:35b",
    "qwen3:30b",
    "gemma3:27b",
    "gemma3:27b-it-qat",
    "glm-4.7-5090:latest",
    "gpt-oss:20b",
    "phi4:14b",
    # Spills to CPU (slower but larger)
    "llama3.3:70b",
    "qwen2.5:72b",
]

# Separate QA model (should be different from the writer for unbiased scoring)
QA_MODEL = "gemma3:27b"

# Test topics — varied to expose model strengths/weaknesses
TOPICS = [
    {
        "topic": "Building a Self-Hosted AI Content Pipeline on Consumer Hardware",
        "keyword": "self-hosted AI",
        "audience": "developers and hardware enthusiasts",
    },
    {
        "topic": "Why Edge Computing Will Replace Cloud for Real-Time AI",
        "keyword": "edge computing AI",
        "audience": "technical decision-makers evaluating infrastructure",
    },
    {
        "topic": "The Complete Guide to Running 30B+ Parameter Models on a Single GPU",
        "keyword": "large language models GPU",
        "audience": "ML engineers and AI hobbyists",
    },
]

# Cost calculation
GPU_WATTS = 250
ELECTRICITY_RATE_KWH = 0.29  # Matt's rate
CLOUD_PRICING_PER_1M = {
    "Haiku": {"input": 0.8, "output": 4.0},
    "Sonnet": {"input": 3.0, "output": 15.0},
    "GPT-4o-mini": {"input": 0.15, "output": 0.6},
}

SYSTEM_PROMPT = """You are an expert technical writer for Glad Labs, a technology blog covering AI/ML, hardware, and gaming. Write engaging, well-structured blog posts with:
- A compelling introduction that hooks the reader
- Clear section headers (## format)
- Practical examples and actionable advice
- A strong conclusion with takeaways
- Target length: 1200-1500 words
- Tone: authoritative but conversational
- Do NOT reference cloud AI APIs (OpenAI, Anthropic, etc.) — we run everything locally
- Do NOT fabricate statistics, quotes, or internal links"""


async def generate(client: httpx.AsyncClient, model: str, topic: dict) -> dict:
    """Generate content with a model and return metrics."""
    prompt = f"Write a blog post about: {topic['topic']}\nTarget audience: {topic['audience']}\nPrimary keyword: {topic['keyword']}"

    # Unload any loaded models first to get clean timing
    try:
        ps = await client.get(f"{OLLAMA_URL}/api/ps")
        for m in ps.json().get("models", []):
            await client.post(f"{OLLAMA_URL}/api/generate",
                              json={"model": m["name"], "keep_alive": 0}, timeout=10)
    except Exception:
        pass

    start = time.time()
    try:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.7, "num_ctx": 8192, "num_gpu": 99},
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e)[:200], "gen_time_s": time.time() - start}

    elapsed = time.time() - start
    content = data.get("message", {}).get("content", "")
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)
    word_count = len(content.split())
    tok_per_sec = output_tokens / elapsed if elapsed > 0 else 0

    return {
        "content": content,
        "gen_time_s": round(elapsed, 2),
        "gen_input_tokens": input_tokens,
        "gen_output_tokens": output_tokens,
        "word_count": word_count,
        "gen_tok_per_sec": round(tok_per_sec, 1),
        "error": None,
    }


async def qa_review(client: httpx.AsyncClient, content: str, topic: dict) -> dict:
    """Score content with the QA model."""
    qa_prompt = f"""Rate this blog post on a scale of 0-100. Consider:
- Relevance to topic "{topic['topic']}" and audience "{topic['audience']}"
- Writing quality, structure, and engagement
- Factual accuracy (no fabricated stats/quotes)
- Keyword integration ("{topic['keyword']}")
- Actionable value for the reader

Respond with ONLY a JSON object: {{"score": <0-100>, "approved": <true/false>, "feedback": "<1-2 sentences>"}}

BLOG POST:
{content[:6000]}"""

    start = time.time()
    try:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": QA_MODEL,
                "messages": [{"role": "user", "content": qa_prompt}],
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 8192, "num_gpu": 99},
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"quality_score": 0, "qa_approved": False, "qa_feedback": f"QA failed: {e}",
                "qa_time_s": time.time() - start}

    elapsed = time.time() - start
    raw = data.get("message", {}).get("content", "")
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)

    # Parse JSON from response
    try:
        # Handle markdown code blocks
        if "```" in raw:
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        result = json.loads(raw)
    except Exception:
        result = {"score": 50, "approved": False, "feedback": raw[:200]}

    return {
        "quality_score": result.get("score", 50),
        "qa_approved": result.get("approved", False),
        "qa_feedback": result.get("feedback", ""),
        "qa_time_s": round(elapsed, 2),
        "qa_input_tokens": input_tokens,
        "qa_output_tokens": output_tokens,
    }


def calc_costs(gen_time_s, input_tokens, output_tokens):
    """Calculate electricity and cloud equivalent costs."""
    kwh = (GPU_WATTS * gen_time_s) / (1000 * 3600)
    electricity = kwh * ELECTRICITY_RATE_KWH
    cloud = {}
    for name, prices in CLOUD_PRICING_PER_1M.items():
        cloud[name] = round(
            (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000, 6
        )
    return round(electricity, 6), cloud


async def main():
    total = len(MODELS) * len(TOPICS)
    current = 0
    results = []

    async with httpx.AsyncClient(timeout=300) as client:
        # Verify Ollama is up
        try:
            await client.get(f"{OLLAMA_URL}/api/tags")
        except Exception:
            print(f"ERROR: Ollama not reachable at {OLLAMA_URL}")
            return

        for model in MODELS:
            for topic in TOPICS:
                current += 1
                print(f"\n[{current}/{total}] {model} -- {topic['topic'][:50]}")

                # Generate
                gen = await generate(client, model, topic)
                if gen.get("error") or "word_count" not in gen:
                    err = gen.get("error", "Unknown error — no content returned")
                    print(f"  FAILED: {str(err)[:80]}")
                    results.append({
                        "gen_model": model, "qa_model": QA_MODEL,
                        "topic": topic["topic"], "error": str(err),
                        "gen_time_s": gen.get("gen_time_s", 0),
                    })
                    continue

                print(f"  Generated: {gen['word_count']} words, {gen['gen_output_tokens']} tokens, "
                      f"{gen['gen_tok_per_sec']} tok/s, {gen['gen_time_s']}s")

                # QA
                qa = await qa_review(client, gen["content"], topic)
                print(f"  QA: {qa['quality_score']}/100 ({'PASS' if qa['qa_approved'] else 'FAIL'}) "
                      f"-- {qa.get('qa_feedback', '')[:80]}")

                # Costs
                electricity, cloud = calc_costs(
                    gen["gen_time_s"], gen["gen_input_tokens"], gen["gen_output_tokens"]
                )

                total_time = gen["gen_time_s"] + qa["qa_time_s"]
                result = {
                    "gen_model": model,
                    "qa_model": QA_MODEL,
                    "topic": topic["topic"],
                    "error": None,
                    "gen_time_s": gen["gen_time_s"],
                    "gen_input_tokens": gen["gen_input_tokens"],
                    "gen_output_tokens": gen["gen_output_tokens"],
                    "word_count": gen["word_count"],
                    "quality_score": qa["quality_score"],
                    "qa_approved": qa["qa_approved"],
                    "qa_feedback": qa["qa_feedback"],
                    "qa_time_s": qa["qa_time_s"],
                    "qa_input_tokens": qa.get("qa_input_tokens", 0),
                    "qa_output_tokens": qa.get("qa_output_tokens", 0),
                    "total_time_s": round(total_time, 2),
                    "total_electricity_cost": electricity,
                    "cloud_equivalent": cloud,
                    "gen_tok_per_sec": gen["gen_tok_per_sec"],
                }
                results.append(result)

    # Summary table
    print(f"\n{'=' * 110}")
    print(f"{'Model':<25} {'Topic':<40} {'Q':>3} {'Words':>5} {'tok/s':>6} {'Time':>6} {'Elec$':>8} {'Haiku$':>8}")
    print("-" * 110)
    for r in results:
        if r.get("error"):
            print(f"{r['gen_model']:<25} {r['topic'][:38]:<40} {'ERR':>3} {'--':>5} {'--':>6} {r.get('gen_time_s',0):>5.0f}s {'--':>8} {'--':>8}")
        else:
            print(f"{r['gen_model']:<25} {r['topic'][:38]:<40} {r['quality_score']:>3} {r['word_count']:>5} "
                  f"{r['gen_tok_per_sec']:>5.0f} {r['total_time_s']:>5.0f}s "
                  f"${r['total_electricity_cost']:.4f} ${r['cloud_equivalent']['Haiku']:.4f}")

    # Model averages
    print(f"\n{'=' * 110}")
    print("MODEL AVERAGES:")
    print(f"{'Model':<25} {'Avg Q':>5} {'Avg Words':>9} {'Avg tok/s':>9} {'Avg Time':>9} {'Pass Rate':>9}")
    print("-" * 110)
    for model in MODELS:
        mr = [r for r in results if r["gen_model"] == model and not r.get("error")]
        if not mr:
            print(f"{model:<25} {'--':>5} {'--':>9} {'--':>9} {'--':>9} 0/{len([r for r in results if r['gen_model']==model])}")
            continue
        avg_q = sum(r["quality_score"] for r in mr) / len(mr)
        avg_w = sum(r["word_count"] for r in mr) / len(mr)
        avg_t = sum(r["gen_tok_per_sec"] for r in mr) / len(mr)
        avg_time = sum(r["total_time_s"] for r in mr) / len(mr)
        passed = sum(1 for r in mr if r["qa_approved"])
        print(f"{model:<25} {avg_q:>5.1f} {avg_w:>9.0f} {avg_t:>8.0f} {avg_time:>8.0f}s {passed}/{len(mr)}")

    # Save results
    output = {
        "shootout_timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "models_tested": MODELS,
            "qa_model": QA_MODEL,
            "topics": TOPICS,
            "gpu_watts": GPU_WATTS,
            "electricity_rate_kwh": ELECTRICITY_RATE_KWH,
            "cloud_pricing_per_1M_tokens": CLOUD_PRICING_PER_1M,
        },
        "single_model_results": results,
    }
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
