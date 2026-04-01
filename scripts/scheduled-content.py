import os
"""Scheduled content generation — creates N content tasks per run.

Run via cron, Windows Task Scheduler, or manually:
  python scripts/scheduled-content.py --count 3

Picks topics from a rotating list of themes relevant to the Glad Labs brand.
Avoids duplicating recent topics by checking the last 50 content_tasks.
"""

import argparse
import json
import random
import sys
import urllib.request

# Ensure scripts/ is on sys.path so `from lib.…` works
sys.path.insert(0, os.path.dirname(__file__))
from lib.config import load_api_token  # noqa: E402
from lib.topic_dedup import fetch_existing_topics, is_too_similar, is_topic_duplicate_semantic  # noqa: E402

# Production API
API_URL = "https://cofounder-production.up.railway.app"
API_TOKEN = load_api_token()

# Topic templates — {angle} gets filled with a random perspective
TOPIC_TEMPLATES = [
    "{tech} in {domain}: What's Changed in 2026",
    "The Developer's Guide to {tech}",
    "{tech} vs {alt_tech}: Which One Should You Choose?",
    "Why {domain} Teams Are Adopting {tech}",
    "Building Production-Ready {tech} Applications",
    "The Hidden Costs of {tech} Nobody Talks About",
    "From Prototype to Production: Scaling {tech}",
    "{number} Mistakes Developers Make With {tech}",
    "The Business Case for {tech} in {domain}",
    "A Practical Introduction to {tech} for {domain}",
    "When to Use {tech} Instead of {alt_tech}",
    "Getting Started With {tech}: What You Need to Know",
]

TECHS = [
    "Local LLMs", "AI Agents", "RAG Pipelines", "Vector Databases",
    "FastAPI", "PostgreSQL", "Next.js", "Grafana", "Docker",
    "Prompt Engineering", "Fine-Tuning", "AI Orchestration",
    "Edge Computing", "WebAssembly", "GraphQL", "Kubernetes",
    "Terraform", "Redis", "Elasticsearch", "CI/CD Pipelines",
]

ALT_TECHS = [
    "Cloud APIs", "Monoliths", "REST APIs", "MySQL", "MongoDB",
    "Django", "Express", "Datadog", "Manual Deployment",
    "Pre-built Solutions", "SaaS Tools", "Serverless",
]

DOMAINS = [
    "Startups", "Content Creation", "DevOps", "Small Businesses",
    "SaaS Companies", "Solo Founders", "Enterprise", "Healthcare",
    "Finance", "Education", "E-commerce", "Marketing",
]

NUMBERS = ["3", "5", "7", "10"]
TIMEFRAMES = ["30 Days", "60 Days", "90 Days", "6 Months"]


def generate_topic() -> str:
    """Generate a random topic from templates."""
    template = random.choice(TOPIC_TEMPLATES)
    return template.format(
        tech=random.choice(TECHS),
        alt_tech=random.choice(ALT_TECHS),
        domain=random.choice(DOMAINS),
        number=random.choice(NUMBERS),
        timeframe=random.choice(TIMEFRAMES),
    )


def create_task(topic: str) -> bool:
    """Create a content task via the API."""
    try:
        payload = json.dumps({
            "task_name": f"Blog post: {topic}",
            "topic": topic,
            "category": "technology",
            "target_audience": "developers and founders",
        }).encode()
        req = urllib.request.Request(
            f"{API_URL}/api/tasks",
            data=payload,
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        print(f"  Created: {result.get('task_id', '?')[:8]} — {topic}")
        return True
    except Exception as e:
        print(f"  Failed: {topic} — {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate scheduled content tasks")
    parser.add_argument("--count", type=int, default=3, help="Number of tasks to create")
    parser.add_argument("--dry-run", action="store_true", help="Print topics without creating")
    args = parser.parse_args()

    existing = fetch_existing_topics(API_URL, f"Bearer {API_TOKEN}")
    created = 0
    attempts = 0
    max_attempts = args.count * 5  # Avoid infinite loop

    print(f"Generating {args.count} content tasks...")
    while created < args.count and attempts < max_attempts:
        topic = generate_topic()
        attempts += 1

        # Skip if too similar to existing tasks or published posts
        if is_too_similar(topic, existing):
            continue
        # Semantic check against published post embeddings in pgvector
        if is_topic_duplicate_semantic(topic):
            print(f"  [SEMANTIC DUP] {topic}")
            continue

        if args.dry_run:
            print(f"  [DRY RUN] {topic}")
            created += 1
        else:
            if create_task(topic):
                created += 1
                existing.add(topic)

    print(f"Done: {created}/{args.count} tasks created ({attempts} attempts)")


if __name__ == "__main__":
    main()
