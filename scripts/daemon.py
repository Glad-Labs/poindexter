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
import subprocess
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
OPPORTUNISTIC_INTERVAL = 120  # 2 minutes — check for idle GPU work


def auto_publish():
    """Approve and publish awaiting tasks that pass quality gates.

    Quality gates (all must pass):
    1. Programmatic content validator — rejects hallucinations, fabricated claims
    2. QA score threshold — only publishes content scoring >= MIN_PUBLISH_SCORE
       (pipeline multi-model QA already ran; this is a safety net)
    """
    MIN_PUBLISH_SCORE = 75  # Auto-publish quality content (recalibrated scoring)

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
        except Exception as _e:
            logger.debug("API request failed: %s", _e)
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
            except Exception as _e:
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
            except Exception as _e:
                logger.debug("Operation failed: %s", _e)
            time.sleep(0.3)

    if published or rejected or held:
        logger.info("Published: %d, Rejected: %d, Held for review: %d", published, rejected, held)
    return published, rejected


def _normalize_words(text):
    """Lowercase, strip punctuation, split into words."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).split()


def _get_ngrams(words, n):
    """Return set of n-grams (tuples of n consecutive words)."""
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _extract_template_base(topic):
    """Extract the template pattern from a topic, ignoring fill-in values.

    E.g. "The Hidden Costs of Local LLMs Nobody Talks About"
      -> "the hidden costs of ... nobody talks about"

    Returns the first 4 and last 4 words joined, which captures the template
    skeleton while ignoring the variable middle.
    """
    words = _normalize_words(topic)
    if len(words) <= 6:
        return " ".join(words)
    return " ".join(words[:4]) + " ... " + " ".join(words[-4:])


def _is_too_similar(topic, existing_topics):
    """Check if topic is too similar to any existing topic.

    Similarity checks:
    1. Exact match (case-insensitive)
    2. Shares 3+ consecutive words with an existing topic
    3. Same template skeleton (first 4 + last 4 words match)
    """
    topic_words = _normalize_words(topic)
    topic_ngrams = _get_ngrams(topic_words, 3)
    topic_base = _extract_template_base(topic)

    for existing in existing_topics:
        existing_lower = existing.lower()
        # Exact match
        if topic.lower() == existing_lower:
            return True
        existing_words = _normalize_words(existing)
        # 3+ consecutive word overlap
        existing_ngrams = _get_ngrams(existing_words, 3)
        shared = topic_ngrams & existing_ngrams
        if len(shared) >= 1:
            return True
        # Same template skeleton
        if _extract_template_base(existing) == topic_base:
            return True
    return False


def _fetch_existing_topics():
    """Fetch both recent task topics AND published post titles for dedup."""
    topics = set()
    # Recent tasks (pending, in-progress, awaiting approval, etc.)
    try:
        req = urllib.request.Request(f"{API_URL}/api/tasks?limit=50", headers={"Authorization": AUTH})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for t in data.get("tasks", []):
            topic = t.get("topic", "")
            if topic:
                topics.add(topic)
    except Exception:
        pass
    # Published posts — check titles to avoid duplicating already-published content
    try:
        req = urllib.request.Request(f"{API_URL}/api/posts?limit=100", headers={"Authorization": AUTH})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for p in data.get("posts", data if isinstance(data, list) else []):
            title = p.get("title", "")
            if title:
                topics.add(title)
    except Exception:
        pass
    return topics


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

    # Get recent topics AND published post titles to avoid duplicates
    existing = _fetch_existing_topics()

    created = 0
    for _ in range(count * 3):
        if created >= count:
            break
        topic = random.choice(TEMPLATES).format(
            tech=random.choice(TECHS), alt=random.choice(ALTS),
            domain=random.choice(DOMAINS), num=random.choice(NUMS),
        )
        if _is_too_similar(topic, existing):
            continue
        try:
            payload = json.dumps({"task_name": f"Blog post: {topic}", "topic": topic,
                                  "category": "technology", "target_audience": "developers and founders"}).encode()
            urllib.request.urlopen(urllib.request.Request(
                f"{API_URL}/api/tasks", data=payload,
                headers={"Authorization": AUTH, "Content-Type": "application/json"}), timeout=10)
            created += 1
            existing.add(topic)
            logger.info("Created task: %s", topic[:60])
        except Exception as _e:
            logger.debug("Operation failed: %s", _e)

    return created


def get_gpu_utilization():
    """Get current GPU utilization percentage (0-100)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        return int(result.stdout.strip()) if result.returncode == 0 else -1
    except Exception:
        return -1


def get_pending_task_count():
    """Check how many tasks are waiting in the queue."""
    try:
        req = urllib.request.Request(
            f"{API_URL}/api/tasks?status=pending&limit=1",
            headers={"Authorization": AUTH},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return len(data.get("tasks", []))
    except Exception:
        return -1


def run_opportunistic_task():
    """Pick and run an opportunistic task when GPU is idle.

    Priority order (highest value first):
    1. Re-score held posts with updated quality service
    2. Generate content for next batch (pre-generate)
    3. Run benchmark on untested model variant

    Only runs when GPU < 10% utilization and no pending tasks in queue.
    """
    gpu_util = get_gpu_utilization()
    if gpu_util < 0:
        return  # Can't read GPU, skip

    if gpu_util >= 10:
        return  # GPU is busy, don't compete

    pending = get_pending_task_count()
    if pending > 0:
        return  # Worker has tasks to process, don't create more load

    # GPU is idle and no tasks pending — find productive work
    logger.info("🔋 GPU idle (%d%%), looking for opportunistic work...", gpu_util)

    # Priority 1: Re-score held posts that have old quality scores
    # These were scored before calibration and might now pass the threshold
    try:
        req = urllib.request.Request(
            f"{API_URL}/api/tasks?status=awaiting_approval&limit=50",
            headers={"Authorization": AUTH},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        held_tasks = data.get("tasks", [])
        low_scored = [t for t in held_tasks if (t.get("quality_score") or 0) < 75]

        if low_scored:
            # Pick one to re-evaluate — create a lightweight re-score task
            task = low_scored[0]
            logger.info("🔄 [OPPORTUNISTIC] Re-scoring held post: %s (current: %s)",
                        task.get("topic", "?")[:40], task.get("quality_score"))
            # Reset to pending so the worker re-processes with updated scoring
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"{API_URL}/api/tasks/{task['task_id']}",
                    data=json.dumps({"status": "pending"}).encode(),
                    headers={"Authorization": AUTH, "Content-Type": "application/json"},
                    method="PATCH",
                ), timeout=10)
                logger.info("🔄 [OPPORTUNISTIC] Task reset to pending for re-scoring")
                return
            except Exception as e:
                logger.debug("Re-score reset failed: %s", e)
    except Exception:
        pass

    # Priority 2: Pre-generate content if we're running low
    try:
        req = urllib.request.Request(
            f"{API_URL}/api/tasks?status=awaiting_approval&limit=1",
            headers={"Authorization": AUTH},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        ready_count = len(data.get("tasks", []))

        if ready_count < 10:
            logger.info("🔋 [OPPORTUNISTIC] Content buffer low (%d), pre-generating 1 task", ready_count)
            generate_content(1)
            return
    except Exception:
        pass

    # GPU is idle but nothing to do — that's fine
    logger.debug("GPU idle (%d%%) but no opportunistic work available", gpu_util)


def main():
    one_shot = "--once" in sys.argv
    logger.info("Glad Labs Daemon starting (once=%s)", one_shot)

    last_publish = 0
    last_generate = 0
    last_opportunistic = 0

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

        # Content generation check (with cost guard)
        if now - last_generate >= GENERATE_INTERVAL:
            # Check daily cost before creating more tasks
            # Each task can cost $0.50-5.00 if it hits cloud models
            try:
                cost_check = json.loads(urllib.request.urlopen(
                    urllib.request.Request(f"{API_URL}/api/metrics/costs/today",
                                          headers={"Authorization": AUTH}),
                    timeout=10,
                ).read())
                daily_spend = cost_check.get("total_cost", 0) or 0
                if daily_spend >= 5.0:
                    logger.warning("COST GUARD: Daily spend $%.2f >= $5.00 — skipping content gen", daily_spend)
                    last_generate = now
                    continue
            except Exception as _e:
                logger.debug("Operation failed: %s", _e)  # If cost API unavailable, proceed with generation (Ollama is free)

            try:
                generate_content(3)
            except Exception as e:
                logger.error("Content generation error: %s", e)
            last_generate = now

        # Opportunistic GPU work — use idle compute productively
        if now - last_opportunistic >= OPPORTUNISTIC_INTERVAL:
            try:
                run_opportunistic_task()
            except Exception as e:
                logger.debug("Opportunistic task error: %s", e)
            last_opportunistic = now

        if one_shot:
            break

        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
