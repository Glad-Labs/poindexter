"""
Poindexter Daemon — single long-lived process that runs all background tasks.

Replaces separate Windows Scheduled Tasks with one process that handles:
- Auto-publisher (every 5 minutes)
- Content generator (every 8 hours)

Run alongside the worker (which handles content generation via Ollama).
The daemon handles the orchestration tasks that don't need GPU.

Usage:
    pythonw scripts/daemon.py          # Run windowless (background)
    python scripts/daemon.py           # Run with console output
    python scripts/daemon.py --once    # Run once and exit (for testing)

Worker-API authentication (Glad-Labs/poindexter#248):
    Uses ``ScriptsOAuthClient`` from ``_oauth_helper`` — picks OAuth
    client credentials when configured, falls back to the legacy static
    Bearer (``api_token``). Run ``poindexter auth migrate-scripts`` to
    provision the OAuth path.
"""

import asyncio
import logging
import os
import subprocess
import sys
import time

# Ensure scripts/ is on sys.path so `from _oauth_helper import …` works
sys.path.insert(0, os.path.dirname(__file__))

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
from _oauth_helper import oauth_client_from_bootstrap_only  # noqa: E402

LOG_FILE = os.path.join(os.path.expanduser("~"), ".poindexter", "daemon.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# Set up the "daemon" logger with its own file handler that won't be blown away
# by any subsequent root logger reconfiguration.
logger = logging.getLogger("daemon")
logger.setLevel(logging.INFO)
_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_file_handler)
if sys.stdout is not None and not sys.stdout.name == os.devnull:
    logger.addHandler(logging.StreamHandler(sys.stdout))

API_URL = os.getenv("POINDEXTER_API_URL", "http://localhost:8002")

# OAuth client + event loop — the daemon runs sync logic on a single
# global asyncio loop so the cached JWT survives across cycles. Each
# cycle's API calls are issued on this loop via run_until_complete.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_oauth_client = _loop.run_until_complete(
    oauth_client_from_bootstrap_only(base_url=API_URL),
)


def _api_get(path: str, timeout: float = 30) -> dict:
    """Sync wrapper around the OAuth client's GET — used by the daemon's
    sync control loop. Returns parsed JSON; raises on HTTP errors."""
    async def _impl():
        resp = await _oauth_client.get(path, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    return _loop.run_until_complete(_impl())


def _api_post(path: str, json_data: dict | None = None, timeout: float = 30) -> dict:
    async def _impl():
        kwargs = {"timeout": timeout}
        if json_data is not None:
            kwargs["json"] = json_data
        resp = await _oauth_client.post(path, **kwargs)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {}
    return _loop.run_until_complete(_impl())


def _api_patch(path: str, json_data: dict, timeout: float = 30) -> dict:
    async def _impl():
        resp = await _oauth_client.patch(path, json=json_data, timeout=timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {}
    return _loop.run_until_complete(_impl())

# ---------------------------------------------------------------------------
# Dynamic settings — loaded from API, refreshed periodically
# ---------------------------------------------------------------------------
_SETTINGS_DEFAULTS = {
    "auto_publish_threshold": 75,
    "daily_spend_limit": 5.0,
    "publish_interval": 300,       # 5 minutes
    "generate_interval": 28800,    # 8 hours
    "sync_interval": 900,          # 15 minutes
    "content_gen_count": 3,
}
_cached_settings: dict = {}
_settings_fetched_at: float = 0
_SETTINGS_REFRESH = 300  # re-fetch every 5 minutes


def _load_settings():
    """Fetch settings from the API and cache them.  Falls back to defaults."""
    global _cached_settings, _settings_fetched_at
    try:
        resp = _api_get("/api/settings", timeout=10)
        # API may return a dict or a list of {key, value} rows
        if isinstance(resp, list):
            _cached_settings = {item["key"]: item["value"] for item in resp}
        elif isinstance(resp, dict):
            _cached_settings = resp
        else:
            _cached_settings = {}
        _settings_fetched_at = time.time()
        logger.info("Settings loaded from API (%d keys)", len(_cached_settings))
    except Exception as e:
        logger.warning("Could not load settings from API, using defaults: %s", e)
        # Keep stale cache if we had one; otherwise empty → defaults kick in


def _setting(key: str):
    """Return a setting value, refreshing from the API when stale."""
    if time.time() - _settings_fetched_at > _SETTINGS_REFRESH:
        _load_settings()
    raw = _cached_settings.get(key, _SETTINGS_DEFAULTS[key])
    # Coerce to same type as the default
    default = _SETTINGS_DEFAULTS[key]
    try:
        if isinstance(default, float):
            return float(raw)
        if isinstance(default, int):
            return int(float(raw))  # int(float()) handles "75.0" strings
    except (ValueError, TypeError):
        return default
    return raw


OPPORTUNISTIC_INTERVAL = 120  # 2 minutes — check for idle GPU work (not worth making configurable)


def auto_publish():
    """Approve and publish awaiting tasks that pass quality gates.

    Quality gates (all must pass):
    1. Programmatic content validator — rejects hallucinations, fabricated claims
    2. QA score threshold — only publishes content scoring >= MIN_PUBLISH_SCORE
       (pipeline multi-model QA already ran; this is a safety net)
    """
    MIN_PUBLISH_SCORE = _setting("auto_publish_threshold")

    # 0 means auto-publish is disabled — hold everything for manual approval
    if MIN_PUBLISH_SCORE <= 0:
        logger.debug("Auto-publish disabled (threshold=0). Skipping publish cycle.")
        return

    published = 0
    rejected = 0
    held = 0  # Tasks held for manual review (below threshold but not rejected)

    for status in ["awaiting_approval", "approved"]:
        try:
            data = _api_get(f"/api/tasks?status={status}&limit=30", timeout=10)
            tasks = data.get("tasks", [])
        except Exception as _e:
            logger.warning("API request failed: %s", _e)
            continue

        for t in tasks:
            tid = t["task_id"]
            topic = t.get("topic", "")
            title = t.get("task_name", topic)

            # Fetch full task for content and QA score
            try:
                full = _api_get(f"/api/tasks/{tid}", timeout=10)
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
            result_raw = full.get("result")
            result_data = result_raw if isinstance(result_raw, dict) else {}
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
                    _api_post(f"/api/tasks/{tid}/approve", timeout=30)
                _api_post(f"/api/tasks/{tid}/publish", timeout=30)
                published += 1
                logger.info("PUBLISHED: %s (QA: %.0f)", topic[:50], qa_score)
            except Exception as _e:
                logger.warning("Approve/publish failed: %s", _e)
            time.sleep(0.3)

    if published or rejected or held:
        logger.info("Published: %d, Rejected: %d, Held for review: %d", published, rejected, held)
    return published, rejected


def generate_content(count=3):
    """Request ``count`` new content tasks via the worker's auto-topic feature.

    The legacy hardcoded-template path that used to live here pulled in
    a ``lib.topic_dedup`` module that no longer exists; the worker's
    own TopicDiscovery (HN / Dev.to / DuckDuckGo + semantic dedup) is a
    superset of what this script used to do, so we just request N
    auto-topic tasks and let the worker handle dedup + selection.
    """
    created = 0
    for _ in range(count):
        try:
            _api_post(
                "/api/tasks",
                json_data={"topic": "auto", "task_type": "blog_post"},
                timeout=30,
            )
            created += 1
        except Exception as _e:
            logger.warning("Task creation failed: %s", _e)
    if created:
        logger.info("Requested %d auto-topic tasks", created)
    return created


def get_gpu_utilization():
    """Get current GPU utilization percentage (0-100)."""
    try:
        kwargs = {}
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, **kwargs
        )
        return int(result.stdout.strip()) if result.returncode == 0 else -1
    except Exception:
        return -1


def get_pending_task_count():
    """Check how many tasks are waiting in the queue."""
    try:
        data = _api_get("/api/tasks?status=pending&limit=1", timeout=10)
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
    logger.info("GPU idle (%d%%), looking for opportunistic work...", gpu_util)

    # Priority 1: Re-score held posts that have old quality scores
    # These were scored before calibration and might now pass the threshold
    try:
        data = _api_get("/api/tasks?status=awaiting_approval&limit=50", timeout=10)
        held_tasks = data.get("tasks", [])
        low_scored = [t for t in held_tasks if (t.get("quality_score") or 0) < 75]

        if low_scored:
            # Pick one to re-evaluate — create a lightweight re-score task
            task = low_scored[0]
            logger.info("[OPPORTUNISTIC] Re-scoring held post: %s (current: %s)",
                        task.get("topic", "?")[:40], task.get("quality_score"))
            # Reset to pending so the worker re-processes with updated scoring
            try:
                _api_patch(
                    f"/api/tasks/{task['task_id']}",
                    json_data={"status": "pending"},
                    timeout=10,
                )
                logger.info("[OPPORTUNISTIC] Task reset to pending for re-scoring")
                return
            except Exception as e:
                logger.debug("Re-score reset failed: %s", e)
    except Exception:
        pass

    # Priority 2: Pre-generate content if we're running low
    try:
        data = _api_get("/api/tasks?status=awaiting_approval&limit=1", timeout=10)
        ready_count = len(data.get("tasks", []))

        if ready_count < 3:
            logger.info("[OPPORTUNISTIC] Content buffer low (%d), requesting topic via API", ready_count)
            # Use the API's auto-topic feature instead of hardcoded templates.
            # This triggers TopicDiscovery which pulls from HN/Dev.to/DuckDuckGo.
            try:
                _api_post(
                    "/api/tasks",
                    json_data={"topic": "auto", "task_type": "blog_post"},
                    timeout=30,
                )
            except Exception as e:
                logger.debug("[OPPORTUNISTIC] Auto-topic request failed: %s", e)
            return
        else:
            logger.debug("[OPPORTUNISTIC] Buffer has %d posts, skipping generation", ready_count)
            return
    except Exception:
        pass

    # GPU is idle but nothing to do — that's fine
    logger.debug("GPU idle (%d%%) but no opportunistic work available", gpu_util)


# NOTE: run_db_sync() previously ran bidirectional sync between local Postgres
# and a Railway-hosted cloud copy. Railway has been decommissioned — the daemon
# no longer has a remote target to sync with. The SyncService class is kept in
# services/ for when a new cloud target is wired up (Neon, Supabase, etc.), but
# the daemon no longer runs sync on its schedule.


def main():
    one_shot = "--once" in sys.argv
    logger.info("Glad Labs Daemon starting (once=%s)", one_shot)

    # Load configurable settings from API at startup
    _load_settings()

    last_publish = 0
    last_generate = 0
    last_opportunistic = 0
    last_sync = 0

    while not _shutdown:
        now = time.time()

        # Auto-publish check
        if now - last_publish >= _setting("publish_interval"):
            try:
                pub, rej = auto_publish()
                logger.info("Publish cycle done (published=%d, rejected=%d)", pub, rej)
            except Exception as e:
                logger.error("Auto-publish error: %s", e)
            for h in logging.getLogger().handlers:
                h.flush()
            last_publish = now

        # Content generation check (with cost guard)
        if now - last_generate >= _setting("generate_interval"):
            # Check daily cost before creating more tasks
            # Each task can cost $0.50-5.00 if it hits cloud models
            try:
                cost_check = _api_get("/api/metrics/costs/today", timeout=10)
                daily_spend = cost_check.get("total_cost", 0) or 0
                spend_limit = _setting("daily_spend_limit")
                if daily_spend >= spend_limit:
                    logger.warning("COST GUARD: Daily spend $%.2f >= $%.2f — skipping content gen", daily_spend, spend_limit)
                    last_generate = now
                    continue
            except Exception as _e:
                logger.warning("Cost check failed: %s", _e)  # If cost API unavailable, proceed with generation (Ollama is free)

            try:
                generate_content(int(_setting("content_gen_count")))
            except Exception as e:
                logger.error("Content generation error: %s", e)
            last_generate = now

        # Opportunistic GPU work — use idle compute productively
        if now - last_opportunistic >= OPPORTUNISTIC_INTERVAL:
            try:
                run_opportunistic_task()
            except Exception as e:
                logger.warning("Opportunistic task error: %s", e)
            last_opportunistic = now

        # Periodic maintenance — run openclaw doctor to heal degraded channels
        # (Telegram 409, WhatsApp disconnect). Cadence reuses the former sync interval.
        if now - last_sync >= _setting("sync_interval"):
            try:
                import subprocess as _sp
                _kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
                _result = _sp.run(["openclaw", "doctor", "--fix"], capture_output=True, text=True, timeout=30, **_kwargs)
                if _result.returncode != 0:
                    logger.warning("[OPENCLAW] doctor --fix failed: %s", _result.stdout[-200:])
            except Exception as _e:
                logger.debug("[OPENCLAW] doctor not available: %s", _e)

            last_sync = now

        if one_shot:
            break

        time.sleep(60)  # Check every minute

    logger.info("Daemon shutting down")


_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Shutdown signal received (signal %d)", signum)
    _shutdown = True


if __name__ == "__main__":
    import signal as _signal
    _signal.signal(_signal.SIGINT, _handle_signal)
    _signal.signal(_signal.SIGTERM, _handle_signal)
    main()
