"""
Glad Labs Daemon — single long-lived process that runs all background tasks.

Replaces separate Windows Scheduled Tasks with one process that handles:
- Auto-publisher (every 5 minutes)
- Content generator (every 8 hours)

Run alongside the worker (which handles content generation via Ollama).
The daemon handles the orchestration tasks that don't need GPU.

Usage:
    pythonw scripts/daemon.py          # Run windowless (background)
    python scripts/daemon.py           # Run with console output
    python scripts/daemon.py --once    # Run once and exit (for testing)
"""

import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime

# Add backend to path for content_validator
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cofounder_agent"))

LOG_FILE = os.path.join(os.path.expanduser("~"), ".gladlabs", "daemon.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("daemon")

API_URL = "https://cofounder-production.up.railway.app"
API_TOKEN = "REDACTED_API_TOKEN"
AUTH = f"Bearer {API_TOKEN}"

PUBLISH_INTERVAL = 300  # 5 minutes
GENERATE_INTERVAL = 28800  # 8 hours


def auto_publish():
    """Approve and publish all awaiting tasks."""
    from services.content_validator import validate_content

    published = 0
    rejected = 0

    for status in ["awaiting_approval", "approved"]:
        try:
            req = urllib.request.Request(
                f"{API_URL}/api/tasks?status={status}&limit=30",
                headers={"Authorization": AUTH},
            )
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            tasks = data.get("tasks", [])
        except Exception:
            continue

        for t in tasks:
            tid = t["task_id"]
            topic = t.get("topic", "")
            title = t.get("task_name", topic)

            # Fetch content for validation
            try:
                full = json.loads(urllib.request.urlopen(
                    urllib.request.Request(f"{API_URL}/api/tasks/{tid}", headers={"Authorization": AUTH}),
                    timeout=10,
                ).read())
                content = ""
                result = full.get("result")
                if isinstance(result, dict):
                    content = result.get("content", "")
                if not content:
                    content = full.get("content", "")
            except Exception:
                content = ""

            # Validate
            if content:
                validation = validate_content(title or "", content, topic)
                if not validation.passed:
                    logger.warning("REJECTED: %s — %s", topic[:50],
                                   "; ".join(i.description[:50] for i in validation.issues[:2]))
                    rejected += 1
                    continue

            # Approve + publish
            try:
                if status == "awaiting_approval":
                    urllib.request.urlopen(urllib.request.Request(
                        f"{API_URL}/api/tasks/{tid}/approve", method="POST",
                        headers={"Authorization": AUTH}), timeout=10)
                urllib.request.urlopen(urllib.request.Request(
                    f"{API_URL}/api/tasks/{tid}/publish", method="POST",
                    headers={"Authorization": AUTH}), timeout=10)
                published += 1
            except Exception:
                pass
            time.sleep(0.3)

    if published or rejected:
        logger.info("Published: %d, Rejected: %d", published, rejected)
    return published, rejected


def generate_content(count=3):
    """Generate content tasks from topic templates."""
    import random

    TEMPLATES = [
        "The Developer's Guide to {tech}",
        "{tech} vs {alt}: Which One Should You Choose?",
        "Why {domain} Teams Are Adopting {tech}",
        "Building Production-Ready {tech} Applications",
        "The Hidden Costs of {tech} Nobody Talks About",
        "{num} Mistakes Developers Make With {tech}",
        "The Business Case for {tech} in {domain}",
        "A Practical Introduction to {tech} for {domain}",
        "When to Use {tech} Instead of {alt}",
        "How {tech} Is Reshaping {domain} in 2026",
    ]
    TECHS = ["Local LLMs", "AI Agents", "RAG Pipelines", "FastAPI", "PostgreSQL",
             "Next.js", "Grafana", "Docker", "Prompt Engineering", "AI Orchestration",
             "Edge Computing", "GraphQL", "Redis", "CI/CD Pipelines", "Terraform"]
    ALTS = ["Cloud APIs", "REST APIs", "MySQL", "Django", "Manual Deployment", "SaaS Tools"]
    DOMAINS = ["Startups", "Content Creation", "DevOps", "Small Businesses", "SaaS Companies",
               "Solo Founders", "E-commerce", "Marketing"]
    NUMS = ["3", "5", "7", "10"]

    # Get recent topics to avoid duplicates
    recent = set()
    try:
        req = urllib.request.Request(f"{API_URL}/api/tasks?limit=50", headers={"Authorization": AUTH})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        recent = {t.get("topic", "").lower() for t in data.get("tasks", [])}
    except Exception:
        pass

    created = 0
    for _ in range(count * 3):
        if created >= count:
            break
        topic = random.choice(TEMPLATES).format(
            tech=random.choice(TECHS), alt=random.choice(ALTS),
            domain=random.choice(DOMAINS), num=random.choice(NUMS),
        )
        if topic.lower() in recent:
            continue
        try:
            payload = json.dumps({"task_name": f"Blog post: {topic}", "topic": topic,
                                  "category": "technology", "target_audience": "developers and founders"}).encode()
            urllib.request.urlopen(urllib.request.Request(
                f"{API_URL}/api/tasks", data=payload,
                headers={"Authorization": AUTH, "Content-Type": "application/json"}), timeout=10)
            created += 1
            recent.add(topic.lower())
            logger.info("Created task: %s", topic[:60])
        except Exception:
            pass

    return created


def main():
    one_shot = "--once" in sys.argv
    logger.info("Glad Labs Daemon starting (once=%s)", one_shot)

    last_publish = 0
    last_generate = 0

    while True:
        now = time.time()

        # Auto-publish check
        if now - last_publish >= PUBLISH_INTERVAL:
            try:
                auto_publish()
            except Exception as e:
                logger.error("Auto-publish error: %s", e)
            last_publish = now

        # Content generation check
        if now - last_generate >= GENERATE_INTERVAL:
            try:
                generate_content(3)
            except Exception as e:
                logger.error("Content generation error: %s", e)
            last_generate = now

        if one_shot:
            break

        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
