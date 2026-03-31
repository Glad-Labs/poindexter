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

# pythonw.exe sets stdout/stderr to None — redirect to devnull before any imports
# that might trigger warnings (e.g., pydantic) writing to stderr
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Add backend to path for content_validator
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cofounder_agent"))

# Import content_validator early — its import chain triggers configure_standard_logging()
# which reconfigures the root logger. We must let that run BEFORE setting up our own handler.
from services.content_validator import validate_content  # noqa: E402

LOG_FILE = os.path.join(os.path.expanduser("~"), ".gladlabs", "daemon.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# Set up the "daemon" logger with its own file handler that won't be blown away
# by any subsequent root logger reconfiguration.
logger = logging.getLogger("daemon")
logger.setLevel(logging.INFO)
_file_handler = logging.FileHandler(LOG_FILE)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_file_handler)
if sys.stdout is not None and not sys.stdout.name == os.devnull:
    logger.addHandler(logging.StreamHandler(sys.stdout))

API_URL = "https://cofounder-production.up.railway.app"
API_TOKEN = os.getenv("GLADLABS_KEY", "")
if not API_TOKEN:
    # Try reading from OpenClaw workspace .env
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("GLADLABS_KEY="):
                API_TOKEN = _line.split("=", 1)[1].strip()
AUTH = f"Bearer {API_TOKEN}"

PUBLISH_INTERVAL = 300  # 5 minutes
GENERATE_INTERVAL = 28800  # 8 hours


def auto_publish():
    """Approve and publish awaiting tasks that pass quality gates.

    Quality gates (all must pass):
    1. Programmatic content validator — rejects hallucinations, fabricated claims
    2. QA score threshold — only publishes content scoring >= MIN_PUBLISH_SCORE
       (pipeline multi-model QA already ran; this is a safety net)
    """
    MIN_PUBLISH_SCORE = 80  # Only auto-publish high-quality content

    published = 0
    rejected = 0
    held = 0  # Tasks held for manual review (below threshold but not rejected)

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

            # Fetch full task for content and QA score
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
                full = {}

            # Gate 1: Programmatic content validation
            if content:
                validation = validate_content(title or "", content, topic)
                if not validation.passed:
                    logger.warning("REJECTED: %s — %s", topic[:50],
                                   "; ".join(i.description[:50] for i in validation.issues[:2]))
                    rejected += 1
                    continue

            # Gate 2: QA score threshold — only auto-publish high scorers
            # quality_score is a top-level field on the task response;
            # qa_final_score lives inside result dict (when multi-model QA ran)
            qa_score = full.get("quality_score", 0) or 0
            result_data = full.get("result") if isinstance(full.get("result"), dict) else {}
            qa_final = result_data.get("qa_final_score", 0) or 0
            # Use the higher of the two scores (multi-model QA or quality eval)
            qa_score = max(qa_score, qa_final)
            if qa_score < MIN_PUBLISH_SCORE:
                if qa_score > 0:
                    logger.info("HELD: %s — QA score %.0f < %d (needs manual review)",
                                topic[:50], qa_score, MIN_PUBLISH_SCORE)
                    held += 1
                continue

            # Both gates passed — approve + publish
            try:
                if status == "awaiting_approval":
                    urllib.request.urlopen(urllib.request.Request(
                        f"{API_URL}/api/tasks/{tid}/approve", method="POST",
                        headers={"Authorization": AUTH}), timeout=10)
                urllib.request.urlopen(urllib.request.Request(
                    f"{API_URL}/api/tasks/{tid}/publish", method="POST",
                    headers={"Authorization": AUTH}), timeout=10)
                published += 1
                logger.info("PUBLISHED: %s (QA: %.0f)", topic[:50], qa_score)
            except Exception:
                pass
            time.sleep(0.3)

    if published or rejected or held:
        logger.info("Published: %d, Rejected: %d, Held for review: %d", published, rejected, held)
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
                pub, rej = auto_publish()
                logger.info("Publish cycle done (published=%d, rejected=%d)", pub, rej)
            except Exception as e:
                logger.error("Auto-publish error: %s", e)
            for h in logging.getLogger().handlers:
                h.flush()
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
