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

# Production API
API_URL = "https://cofounder-production.up.railway.app"
API_TOKEN = os.getenv("GLADLABS_KEY", "")
if not API_TOKEN:
    # Try reading from OpenClaw workspace .env
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("GLADLABS_KEY="):
                API_TOKEN = _line.split("=", 1)[1].strip()

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


def _normalize_words(text: str) -> list:
    """Lowercase, strip punctuation, split into words."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).split()


def _get_ngrams(words: list, n: int) -> set:
    """Return set of n-grams (tuples of n consecutive words)."""
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _extract_template_base(topic: str) -> str:
    """Extract the template skeleton (first 4 + last 4 words).

    E.g. "The Hidden Costs of Local LLMs Nobody Talks About"
      -> "the hidden costs of ... nobody talks about"
    """
    words = _normalize_words(topic)
    if len(words) <= 6:
        return " ".join(words)
    return " ".join(words[:4]) + " ... " + " ".join(words[-4:])


def _is_too_similar(topic: str, existing_topics: set) -> bool:
    """Check if topic is too similar to any existing topic.

    Checks: exact match, 3+ consecutive shared words, same template skeleton.
    """
    topic_words = _normalize_words(topic)
    topic_ngrams = _get_ngrams(topic_words, 3)
    topic_base = _extract_template_base(topic)

    for existing in existing_topics:
        if topic.lower() == existing.lower():
            return True
        existing_words = _normalize_words(existing)
        existing_ngrams = _get_ngrams(existing_words, 3)
        if topic_ngrams & existing_ngrams:
            return True
        if _extract_template_base(existing) == topic_base:
            return True
    return False


def get_existing_topics() -> set:
    """Fetch recent task topics AND published post titles to avoid duplicates."""
    auth = f"Bearer {API_TOKEN}"
    topics = set()
    # Recent tasks
    try:
        req = urllib.request.Request(
            f"{API_URL}/api/tasks?limit=50",
            headers={"Authorization": auth},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for t in data.get("tasks", []):
            topic = t.get("topic", "")
            if topic:
                topics.add(topic)
    except Exception:
        pass
    # Published posts
    try:
        req = urllib.request.Request(
            f"{API_URL}/api/posts?limit=100",
            headers={"Authorization": auth},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for p in data.get("posts", data if isinstance(data, list) else []):
            title = p.get("title", "")
            if title:
                topics.add(title)
    except Exception:
        pass
    return topics


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

    existing = get_existing_topics()
    created = 0
    attempts = 0
    max_attempts = args.count * 5  # Avoid infinite loop

    print(f"Generating {args.count} content tasks...")
    while created < args.count and attempts < max_attempts:
        topic = generate_topic()
        attempts += 1

        # Skip if too similar to existing tasks or published posts
        if _is_too_similar(topic, existing):
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
