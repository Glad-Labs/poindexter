"""Pipeline Cost Audit — Measure real token counts and costs for every pipeline step.

Calls Ollama directly with representative prompts for each pipeline step:
  research -> generation -> QA review -> SEO title -> SEO description -> SEO keywords -> embedding

Measures real token counts, timing, and electricity cost. Compares against
Haiku, Sonnet, and GPT-4o-mini pricing.

Results saved to ~/.gladlabs/cost-audit-report.json

Usage:
    python scripts/pipeline-cost-audit.py
    python scripts/pipeline-cost-audit.py --model gemma3:27b
    python scripts/pipeline-cost-audit.py --topic "Building REST APIs with FastAPI"
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
DEFAULT_MODEL = "gemma3:27b"
REQUEST_TIMEOUT = 600  # 10 min per step (thinking models need time)

# Cloud pricing per 1M tokens (input / output)
CLOUD_PRICING = {
    "Haiku (claude-3.5-haiku)": {"input": 0.80, "output": 4.00},
    "Sonnet (claude-3.5-sonnet)": {"input": 3.00, "output": 15.00},
    "GPT-4o-mini": {"input": 0.15, "output": 0.60},
}

# ---------------------------------------------------------------------------
# Representative prompts for each pipeline step
# ---------------------------------------------------------------------------
TOPIC = "Docker containers for startups"
PRIMARY_KEYWORD = "Docker containers"
TARGET_AUDIENCE = "startup founders and technical leads"

SIMULATED_RESEARCH = """
Key findings from web research:
- Docker adoption among startups grew 35% in 2025
- Container orchestration reduces deployment time by 60-80%
- Average startup saves $2,000-5,000/month on infrastructure with containers
- Kubernetes adoption plateaued; simpler tools like Docker Compose gaining ground
- Security remains top concern: 40% of container images have known vulnerabilities
- Serverless containers (AWS Fargate, Cloud Run) popular for small teams
- Development environment consistency is #1 cited benefit
Sources: Docker 2025 Survey, CNCF Annual Report, Hacker News discussions
"""

SIMULATED_BLOG_POST = """# Why Your Startup Should Bet on Docker Containers in 2026

## The $5,000/Month Infrastructure Mistake Most Startups Make

Every startup burns cash on infrastructure they don't need. Virtual machines sitting idle at 15% utilization, manual deployments that eat engineering hours, and "works on my machine" bugs that slow down your entire team. Docker containers solve all three problems, and the numbers prove it.

Container adoption among startups grew 35% in 2025 alone. That's not hype — it's pragmatism. When you're running lean, every dollar and every engineering hour counts.

## From Chaos to Consistency: How Containers Transform Your Dev Workflow

The single biggest benefit of Docker containers isn't deployment — it's development environment consistency. When your entire stack runs in containers, new engineers go from "setting up their environment" for two days to `docker compose up` in two minutes.

Here's what that looks like in practice. Your application, database, cache layer, and message queue all defined in a single `docker-compose.yml` file. Every developer runs the exact same versions, the exact same configuration, the exact same everything.

[IMAGE-1]

This consistency eliminates an entire category of bugs. No more "it works on my machine." No more debugging version mismatches. No more maintaining a 47-page setup guide that's always out of date.

### The Compose-First Approach

For startups, Docker Compose is the sweet spot. Kubernetes is powerful but complex — it's designed for organizations running hundreds of services across multiple data centers. If you're a team of 3-15 engineers, Compose gives you 90% of the benefits at 10% of the complexity.

Your `docker-compose.yml` becomes your infrastructure documentation. New team member? Read the compose file. Want to know what services your app depends on? Read the compose file. Need to replicate production locally? You guessed it.

## Cutting Your Cloud Bill Without Cutting Corners

Containers pack more workloads onto fewer servers. Where a VM might run one application, a container host runs ten. This density translates directly to cost savings.

Startups using containers report saving between $2,000 and $5,000 per month on infrastructure costs. For a seed-stage company, that's runway. That's another month of building before you need to fundraise.

[IMAGE-2]

The savings come from three places:
- **Resource density**: Pack more services per server
- **Faster scaling**: Scale up in seconds, not minutes
- **Development velocity**: Ship faster, break less

Serverless container platforms like AWS Fargate and Google Cloud Run take this further. You pay only for the compute time you actually use. No idle VMs burning cash at 3 AM.

## The Security Question Nobody Wants to Answer

Here's the uncomfortable truth: roughly 40% of container images contain known vulnerabilities. Containers aren't automatically secure — they require deliberate security practices.

For startups, this means three non-negotiable practices:
1. **Use official base images** and keep them updated
2. **Scan images in CI/CD** with tools like Trivy or Snyk
3. **Never run containers as root** in production

The good news? Container security is more automatable than VM security. A single CI pipeline check catches vulnerabilities before they reach production.

## Your First Week With Docker: A Practical Roadmap

Don't try to containerize everything at once. Start with your development environment, then expand to staging, then production. Here's a realistic timeline:

**Day 1-2**: Write Dockerfiles for your main application and its dependencies. Get `docker compose up` working locally.

**Day 3-4**: Add your database, cache, and any other services to the compose file. Ensure data persistence with named volumes.

**Day 5**: Set up a CI/CD pipeline that builds and tests your containers automatically.

**Week 2**: Deploy containers to staging. Use the same images you tested locally.

**Week 3+**: Gradually migrate production services to containers, starting with stateless services.

This incremental approach minimizes risk. Each step is reversible. You're not betting the company on a big-bang migration.

## Ready to Make the Switch?

Docker containers aren't just a technology choice — they're an operational philosophy. Build once, run anywhere. Define infrastructure as code. Automate everything you can.

For startups, the calculus is simple: containers save money, save time, and reduce bugs. The 35% adoption growth in 2025 reflects a market that's figured this out.

Start with `docker compose up`. Everything else follows from there.

**Related: [[Getting Started with CI/CD]], [[Cloud Cost Optimization for Startups]]**
"""

# Pipeline step definitions: (name, system_prompt, user_prompt)
def build_pipeline_steps():
    """Build representative prompts for each pipeline step."""
    return [
        {
            "name": "research",
            "description": "Analyze search results for content research",
            "system": "You are a research analyst. Analyze search results and extract key findings.",
            "prompt": f"""Analyze the following search results for: "{TOPIC}"

Depth Level: standard

---SEARCH RESULTS---
Docker adoption among startups grew 35% in 2025. Container orchestration reduces deployment time by 60-80%. Average startup saves $2,000-5,000/month on infrastructure with containers. Kubernetes adoption plateaued; simpler tools like Docker Compose gaining ground. Security remains top concern: 40% of container images have known vulnerabilities. Serverless containers (AWS Fargate, Cloud Run) popular for small teams. Development environment consistency is #1 cited benefit. Sources: Docker 2025 Survey, CNCF Annual Report, Hacker News discussions, TechCrunch container analysis.
---END RESULTS---

Base your analysis ONLY on the search results provided.

Provide a structured analysis with:
1. Key Findings (5-10 main points)
2. Current Trends (from sources)
3. Important Statistics (with sources if available)
4. Recommended Sources

OUTPUT FORMAT (Valid JSON only, no markdown):
{{"key_points": [...], "trends": [...], "statistics": [...], "sources": [...]}}""",
        },
        {
            "name": "generation",
            "description": "Generate blog post draft from research",
            "system": "You are an expert technical writer and professional blogger. Write clear, engaging content for startup founders. Generate approximately 2000 words. Format as Markdown.",
            "prompt": f"""Generate a comprehensive, well-structured blog post draft about '{TOPIC}'.
The target audience is {TARGET_AUDIENCE}.
The primary keyword to focus on is '{PRIMARY_KEYWORD}'.

CRITICAL REQUIREMENTS:
1. Start with a Markdown heading (# Title) on the first line
2. Include 3-5 main sections with creative ## subheadings
3. Write FULL CONTENT to reach approximately 2000 words
4. Include [IMAGE-1], [IMAGE-2] where visuals would enhance content

RESEARCH CONTEXT:
{SIMULATED_RESEARCH}

Ensure the tone is professional and engaging. Include practical examples.""",
        },
        {
            "name": "qa_review",
            "description": "QA review for publication readiness",
            "system": "You are a content quality reviewer. Evaluate blog posts for publication readiness.",
            "prompt": f"""Review this blog post for publication readiness.

TARGET AUDIENCE: {TARGET_AUDIENCE}
PRIMARY KEYWORD: '{PRIMARY_KEYWORD}'

---DRAFT---
{SIMULATED_BLOG_POST}
---END DRAFT---

Evaluate against these criteria:
1. Clarity 2. Tone 3. Keyword integration 4. Structure 5. Value 6. Section titles 7. Research depth 8. Factual integrity

GRADING SCALE (0-100):
- 85+: Excellent  - 75-84: Good  - 70-74: Needs work  - <60: Poor

Respond with ONLY valid JSON:
{{"approved": true/false, "quality_score": NUMBER, "feedback": "specific feedback"}}""",
        },
        {
            "name": "seo_title",
            "description": "Generate SEO-optimized title",
            "system": "You are an SEO specialist.",
            "prompt": f"""Generate a professional, SEO-optimized blog title.

Content: {SIMULATED_BLOG_POST[:500]}
Primary Keyword: {PRIMARY_KEYWORD}

REQUIREMENTS:
- Maximum 60 characters
- Include primary keyword naturally
- Be specific and descriptive
- Professional tone

Generate ONLY the title, nothing else. No quotes, no explanation.""",
        },
        {
            "name": "seo_description",
            "description": "Generate SEO meta description",
            "system": "You are an SEO specialist.",
            "prompt": f"""Generate a compelling SEO meta description for search results.

Title: Why Your Startup Should Bet on Docker Containers in 2026
Content (first 400 chars): {SIMULATED_BLOG_POST[:400]}

REQUIREMENTS:
- Maximum 155 characters
- Include primary keyword naturally
- Include call-to-action
- Compelling and click-worthy

Generate ONLY the description, nothing else.""",
        },
        {
            "name": "seo_keywords",
            "description": "Extract SEO keywords from content",
            "system": "You are an SEO specialist.",
            "prompt": f"""Extract 5-7 SEO keywords from this content.

Title: Why Your Startup Should Bet on Docker Containers in 2026
Content (first 500 chars): {SIMULATED_BLOG_POST[:500]}

REQUIREMENTS:
- 5-7 keywords total, comma-separated
- Most important keywords first
- Mix of short-tail and long-tail

Generate ONLY the comma-separated list, nothing else.""",
        },
        {
            "name": "embedding",
            "description": "Generate embedding vector (simulated with short completion)",
            "system": "You are a text summarizer.",
            "prompt": f"""Summarize this blog post in exactly one sentence for semantic indexing:

{SIMULATED_BLOG_POST[:1000]}

Generate ONLY the one-sentence summary.""",
        },
    ]


# ---------------------------------------------------------------------------
# Ollama API calls
# ---------------------------------------------------------------------------
async def call_ollama(client: httpx.AsyncClient, model: str, system: str, prompt: str) -> dict:
    """Call Ollama /api/generate and return token counts + timing."""
    payload = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
    }
    start = time.monotonic()
    try:
        resp = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        elapsed = time.monotonic() - start
        return {
            "error": f"Timeout after {elapsed:.1f}s",
            "elapsed": elapsed,
            "input_tokens": 0,
            "output_tokens": 0,
            "response": "",
        }
    except httpx.HTTPStatusError as e:
        elapsed = time.monotonic() - start
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            "elapsed": elapsed,
            "input_tokens": 0,
            "output_tokens": 0,
            "response": "",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "error": str(e),
            "elapsed": elapsed,
            "input_tokens": 0,
            "output_tokens": 0,
            "response": "",
        }

    elapsed = time.monotonic() - start

    # Ollama returns prompt_eval_count (input) and eval_count (output)
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)

    return {
        "error": None,
        "elapsed": elapsed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "response": data.get("response", ""),
        "total_duration_ns": data.get("total_duration", 0),
        "load_duration_ns": data.get("load_duration", 0),
        "prompt_eval_duration_ns": data.get("prompt_eval_duration", 0),
        "eval_duration_ns": data.get("eval_duration", 0),
    }


def calc_electricity_cost(seconds: float) -> float:
    """Calculate electricity cost for GPU inference time."""
    hours = seconds / 3600
    kwh = (GPU_WATTS / 1000) * hours
    return kwh * ELECTRICITY_RATE_KWH


def calc_cloud_cost(input_tokens: int, output_tokens: int, provider: str) -> float:
    """Calculate what the same tokens would cost on a cloud provider."""
    pricing = CLOUD_PRICING[provider]
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------
async def run_audit(model: str, topic: str):
    print("=" * 70)
    print(f"  PIPELINE COST AUDIT")
    print(f"  Model: {model}")
    print(f"  Topic: {topic}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    steps = build_pipeline_steps()
    results = []
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "total_time_s": 0,
        "total_electricity_cost": 0,
    }

    async with httpx.AsyncClient() as client:
        # Verify Ollama is running
        try:
            health = await client.get(f"{OLLAMA_URL}/api/tags", timeout=10)
            health.raise_for_status()
            available = [m["name"] for m in health.json().get("models", [])]
            print(f"Ollama is running. Available models: {len(available)}")
            if not any(model in m for m in available):
                print(f"WARNING: Model '{model}' may not be available. Available: {available}")
            print()
        except Exception as e:
            print(f"ERROR: Cannot connect to Ollama at {OLLAMA_URL}: {e}")
            sys.exit(1)

        for i, step in enumerate(steps, 1):
            step_name = step["name"]
            print(f"[{i}/{len(steps)}] Running: {step_name} ({step['description']})")
            print(f"         Prompt length: ~{len(step['prompt'])} chars")

            result = await call_ollama(client, model, step["system"], step["prompt"])

            if result["error"]:
                print(f"         ERROR: {result['error']}")
            else:
                tok_per_sec = result["output_tokens"] / result["elapsed"] if result["elapsed"] > 0 else 0
                elec_cost = calc_electricity_cost(result["elapsed"])
                print(f"         Input:  {result['input_tokens']:,} tokens")
                print(f"         Output: {result['output_tokens']:,} tokens")
                print(f"         Time:   {result['elapsed']:.1f}s ({tok_per_sec:.1f} tok/s)")
                print(f"         Cost:   ${elec_cost:.6f} electricity")

                totals["input_tokens"] += result["input_tokens"]
                totals["output_tokens"] += result["output_tokens"]
                totals["total_tokens"] += result["input_tokens"] + result["output_tokens"]
                totals["total_time_s"] += result["elapsed"]
                totals["total_electricity_cost"] += elec_cost
            print()

            results.append({
                "step": step_name,
                "description": step["description"],
                "model": model,
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "total_tokens": result["input_tokens"] + result["output_tokens"],
                "time_s": round(result["elapsed"], 2),
                "electricity_cost": round(calc_electricity_cost(result["elapsed"]), 6),
                "error": result["error"],
                "response_preview": result["response"][:200] if result["response"] else "",
            })

    # Cloud cost comparison
    cloud_costs = {}
    for provider in CLOUD_PRICING:
        cloud_costs[provider] = round(
            calc_cloud_cost(totals["input_tokens"], totals["output_tokens"], provider), 6
        )

    # ---------------------------------------------------------------------------
    # Print summary table
    # ---------------------------------------------------------------------------
    print()
    print("=" * 90)
    print("  RESULTS SUMMARY")
    print("=" * 90)
    print()
    header = f"{'Step':<18} {'In Tok':>8} {'Out Tok':>8} {'Total':>8} {'Time':>7} {'Elec $':>10} {'Error'}"
    print(header)
    print("-" * 90)
    for r in results:
        err = "YES" if r["error"] else ""
        print(
            f"{r['step']:<18} {r['input_tokens']:>8,} {r['output_tokens']:>8,} "
            f"{r['total_tokens']:>8,} {r['time_s']:>6.1f}s ${r['electricity_cost']:>9.6f} {err}"
        )
    print("-" * 90)
    print(
        f"{'TOTAL':<18} {totals['input_tokens']:>8,} {totals['output_tokens']:>8,} "
        f"{totals['total_tokens']:>8,} {totals['total_time_s']:>6.1f}s "
        f"${totals['total_electricity_cost']:>9.6f}"
    )
    print()

    # Cloud comparison table
    print("  CLOUD COST COMPARISON (same token counts)")
    print("-" * 50)
    print(f"  {'Provider':<30} {'Cost':>12}")
    print("-" * 50)
    print(f"  {'Local (' + model + ')':<30} ${totals['total_electricity_cost']:>11.6f}")
    for provider, cost in cloud_costs.items():
        ratio = cost / totals["total_electricity_cost"] if totals["total_electricity_cost"] > 0 else 0
        print(f"  {provider:<30} ${cost:>11.6f}  ({ratio:.1f}x local)")
    print()

    # Derived stats
    if totals["total_time_s"] > 0:
        avg_tok_s = totals["output_tokens"] / totals["total_time_s"]
        print(f"  Average throughput: {avg_tok_s:.1f} tok/s")
    print(f"  GPU power assumed:  {GPU_WATTS}W")
    print(f"  Electricity rate:   ${ELECTRICITY_RATE_KWH}/kWh")
    print()

    # ---------------------------------------------------------------------------
    # Save JSON report
    # ---------------------------------------------------------------------------
    report = {
        "audit_timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "topic": TOPIC,
        "gpu_watts": GPU_WATTS,
        "electricity_rate_kwh": ELECTRICITY_RATE_KWH,
        "steps": results,
        "totals": {
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "total_tokens": totals["total_tokens"],
            "total_time_s": round(totals["total_time_s"], 2),
            "total_electricity_cost": round(totals["total_electricity_cost"], 6),
        },
        "cloud_comparison": cloud_costs,
        "cloud_pricing_per_1M_tokens": CLOUD_PRICING,
    }

    out_dir = Path.home() / ".gladlabs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cost-audit-report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Report saved to: {out_path}")
    print()

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline Cost Audit")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default: {DEFAULT_MODEL})")
    parser.add_argument("--topic", default=TOPIC, help="Blog topic to test")
    args = parser.parse_args()

    asyncio.run(run_audit(args.model, args.topic))


if __name__ == "__main__":
    main()
