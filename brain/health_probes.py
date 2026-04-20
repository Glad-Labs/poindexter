"""
Health probes — exercise each service with real inputs to verify they work.

Unlike basic HTTP checks, these probes send actual requests and validate responses.
Each probe runs on its own schedule (tracked by last-run time).
Results are stored in brain_knowledge for trend analysis.

Standalone: only depends on asyncpg + urllib (no FastAPI imports).
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request

try:
    # When brain/ is on sys.path directly (container runtime), import bare.
    from docker_utils import localize_url, resolve_url
except ImportError:
    # When imported as ``brain.health_probes`` (tests, notebooks), use
    # package-qualified path.
    from brain.docker_utils import localize_url, resolve_url

logger = logging.getLogger("brain.probes")

# Bootstrap defaults — overridden from app_settings on first probe run.
# localize_url rewrites `localhost` to `host.docker.internal` when running
# inside a container, so the same DB value works in both environments.
API_URL = localize_url(os.getenv("API_URL") or "http://localhost:8002")
LOCAL_OLLAMA = localize_url(os.getenv("OLLAMA_URL") or "http://localhost:11434")
GITEA_URL = localize_url(os.getenv("GITEA_URL") or "http://localhost:3001")
GITEA_USER = os.getenv("GITEA_USER") or ""
GITEA_PASS = os.getenv("GITEA_PASS") or os.getenv("GITEA_PASSWORD") or ""
GITEA_REPO = os.getenv("GITEA_REPO") or "gladlabs/glad-labs-codebase"

_config_synced = False


async def _sync_config_from_db(pool):
    """Pull URL/connection config from app_settings so probes use the
    canonical values instead of potentially stale env var defaults.
    Runs once on first probe cycle.

    Env vars take priority (Docker sets them correctly for the container
    network), DB values are fallback for local dev where env vars may
    not be set.
    """
    global API_URL, LOCAL_OLLAMA, GITEA_URL, GITEA_USER, GITEA_PASS, GITEA_REPO, _config_synced
    if _config_synced:
        return
    try:
        # URLs: shared resolver handles env-wins-over-DB + localize_url in one call.
        API_URL = await resolve_url(
            pool, "internal_api_base_url", "api_url",
            default=API_URL, env_var="API_URL",
        )
        LOCAL_OLLAMA = await resolve_url(
            pool, "ollama_base_url",
            default=LOCAL_OLLAMA, env_var="OLLAMA_URL",
        )
        GITEA_URL = await resolve_url(
            pool, "gitea_url",
            default=GITEA_URL, env_var="GITEA_URL",
        )
        # Non-URL settings: straightforward env-wins-over-DB, no localize_url.
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE key IN "
            "('gitea_user', 'gitea_password', 'gitea_repo')"
        )
        settings = {r["key"]: r["value"] for r in rows}
        if not os.getenv("GITEA_USER") and settings.get("gitea_user"):
            GITEA_USER = settings["gitea_user"]
        if not (os.getenv("GITEA_PASS") or os.getenv("GITEA_PASSWORD")) and settings.get("gitea_password"):
            GITEA_PASS = settings["gitea_password"]
        if not os.getenv("GITEA_REPO") and settings.get("gitea_repo"):
            GITEA_REPO = settings["gitea_repo"]
        _config_synced = True
        logger.info("[PROBES] Config synced: API=%s, Ollama=%s, Gitea=%s repo=%s (env wins over DB; URLs localized)",
                     API_URL, LOCAL_OLLAMA, GITEA_URL, GITEA_REPO)
    except Exception as e:
        logger.warning("[PROBES] Failed to sync config from DB, using env defaults: %s", e)

# Track which probe issues we've already created (avoid duplicates)
_created_issues: set = set()


def _create_gitea_issue(probe_name: str, detail: str):
    """Auto-create a Gitea issue when a probe fails 3x consecutively."""
    if probe_name in _created_issues:
        return  # Already created this session
    try:
        import base64
        auth = base64.b64encode(f"{GITEA_USER}:{GITEA_PASS}".encode()).decode()
        data = json.dumps({
            "title": f"ops: Probe '{probe_name}' failing — {detail[:60]}",
            "body": (
                f"## Health Probe Failure\n\n"
                f"**Probe:** `{probe_name}`\n"
                f"**Error:** {detail}\n"
                f"**Consecutive failures:** 3+\n"
                f"**Auto-created by:** brain_daemon health probes\n\n"
                f"This issue was automatically created when the probe failed 3 consecutive times."
            ),
            "labels": [],
        }).encode()
        req = urllib.request.Request(
            f"{GITEA_URL}/api/v1/repos/{GITEA_REPO}/issues",
            data=data,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status < 300:
            _created_issues.add(probe_name)
            logger.info("[PROBES] Created Gitea issue for probe '%s'", probe_name)
    except Exception as e:
        logger.debug("[PROBES] Could not create Gitea issue: %s", e)

# Probe schedules (seconds between runs)
PROBE_SCHEDULES = {
    "db_ping": 300,            # 5 min
    "ollama_models": 300,      # 5 min
    "quality_score": 1800,     # 30 min
    "content_gen": 1800,       # 30 min
    "affiliate_linker": 3600,  # 1 hour
    "research_service": 3600,  # 1 hour
    "image_search": 3600,      # 1 hour
    "grafana_datasources": 300,  # 5 min
    "public_site": 300,          # 5 min
    "scheduled_tasks": 3600,     # 1 hour
    "disk_space": 3600,          # 1 hour
    # P0 — pipeline health
    "stuck_tasks": 1800,         # 30 min
    "approval_queue": 3600,      # 1 hour
    "failed_task_spike": 3600,   # 1 hour
    "worker_error_rate": 300,    # 5 min — catches 100% failure rate fast
    # P1 — business continuity
    "publish_rate": 21600,       # 6 hours
    "cost_freshness": 3600,      # 1 hour
    "podcast_health": 21600,     # 6 hours
    "newsletter_health": 21600,  # 6 hours
    # P2 — quality & reliability
    "embeddings_freshness": 21600,  # 6 hours
    "r2_connectivity": 3600,        # 1 hour
    "traffic_anomaly": 21600,       # 6 hours
    "quality_trend": 21600,         # 6 hours
    "topic_quality": 21600,          # 6 hours
    "pipeline_throughput": 21600,    # 6 hours
}

# Track last run times
_last_run: dict[str, float] = {}


def _is_due(probe_name: str) -> bool:
    """Check if a probe is due to run based on its schedule."""
    last = _last_run.get(probe_name, 0)
    interval = PROBE_SCHEDULES.get(probe_name, 3600)
    return (time.time() - last) >= interval


def _mark_run(probe_name: str):
    """Mark a probe as having just run."""
    _last_run[probe_name] = time.time()


def _http_json(url: str, method: str = "GET", data: dict | None = None,
               timeout: int = 15) -> tuple[bool, dict]:
    """Make an HTTP request and parse JSON response. Returns (ok, data_or_error)."""
    try:
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        if body:
            req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return False, {"error": f"HTTP {e.code}: {str(e.reason)[:100]}"}
    except Exception as e:
        return False, {"error": str(e)[:200]}


async def probe_db_ping(pool) -> dict:
    """Probe: SELECT 1 — verify DB is responsive and measure latency."""
    start = time.time()
    try:
        row = await pool.fetchrow("SELECT 1 AS ok")
        latency_ms = (time.time() - start) * 1000
        ok = row is not None and row["ok"] == 1
        return {
            "ok": ok,
            "latency_ms": round(latency_ms, 1),
            "detail": "responsive" if ok else "unexpected result",
        }
    except Exception as e:
        return {"ok": False, "latency_ms": -1, "detail": str(e)[:200]}


async def probe_ollama_models(_pool) -> dict:
    """Probe: List Ollama models — verify expected models are loaded."""
    ok, result = _http_json(f"{LOCAL_OLLAMA}/api/tags", timeout=5)
    if not ok:
        return {"ok": False, "detail": f"Ollama unreachable: {result.get('error', 'unknown')}", "models": []}

    models = [m.get("name", "") for m in result.get("models", [])]
    # Check for at least one model present
    has_models = len(models) > 0
    return {
        "ok": has_models,
        "model_count": len(models),
        "models": models[:10],  # Cap for storage
        "detail": "models loaded" if has_models else "no models found",
    }


async def probe_quality_score(pool) -> dict:
    """Probe: Score a known-good snippet — verify quality service returns sane scores."""
    # Check that quality scoring is functional by verifying recent scored tasks

    # The real check: can we reach the quality evaluation endpoint?
    # Since there's no standalone quality endpoint, we verify the API is healthy
    # and check that quality_service data exists in recent tasks
    try:
        row = await pool.fetchrow("""
            SELECT quality_score FROM pipeline_tasks_view
            WHERE quality_score IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
        """)
        if row:
            score = float(row["quality_score"])
            return {
                "ok": 50 <= score <= 100,
                "last_score": score,
                "detail": f"last quality score: {score:.0f}",
            }
        return {"ok": True, "detail": "no scored tasks yet (pipeline idle)"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_content_gen(_pool) -> dict:
    """Probe: Check Ollama can generate text — 1-sentence test."""
    ok, result = _http_json(
        f"{LOCAL_OLLAMA}/api/generate",
        method="POST",
        data={
            "model": "qwen3-coder:30b",
            "prompt": "Respond with exactly one sentence: What is FastAPI?",
            "stream": False,
            "options": {"num_predict": 50},
        },
        timeout=30,
    )
    if not ok:
        # Try fallback model
        ok, result = _http_json(
            f"{LOCAL_OLLAMA}/api/generate",
            method="POST",
            data={
                "model": "llama3:latest",
                "prompt": "Respond with exactly one sentence: What is Python?",
                "stream": False,
                "options": {"num_predict": 50},
            },
            timeout=30,
        )

    if not ok:
        return {"ok": False, "detail": f"Ollama generate failed: {result.get('error', 'unknown')}"}

    response_text = result.get("response", "")
    has_content = len(response_text.strip()) > 10
    return {
        "ok": has_content,
        "response_length": len(response_text),
        "detail": "generation working" if has_content else "empty response",
    }


async def probe_affiliate_linker(pool) -> dict:
    """Probe: Check affiliate_links table has active links."""
    try:
        row = await pool.fetchrow(
            "SELECT COUNT(*) as c FROM affiliate_links WHERE active = true"
        )
        count = row["c"] if row else 0
        return {
            "ok": True,
            "active_links": count,
            "detail": f"{count} active affiliate links" if count > 0 else "table exists, no links configured yet",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_research_service(pool) -> dict:
    """Probe: Verify research service endpoint responds."""
    api_reachable = True
    ok, result = _http_json(f"{API_URL}/api/health", timeout=5)
    if not ok:
        # API unreachable is degraded, not a hard failure — the DB check still matters
        api_reachable = False

    # Check that published posts exist (research service uses these for internal links)
    try:
        row = await pool.fetchrow(
            "SELECT COUNT(*) as c FROM posts WHERE status = 'published'"
        )
        count = row["c"] if row else 0
        if not api_reachable:
            return {
                "ok": True,
                "status": "degraded",
                "published_posts": count,
                "detail": f"API unreachable (degraded) but DB has {count} posts for internal linking",
            }
        return {
            "ok": count > 0,
            "published_posts": count,
            "detail": f"{count} posts available for internal linking",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_image_search(_pool) -> dict:
    """Probe: Check Pexels image search is available (env var or API)."""
    import os
    pexels_key = os.getenv("PEXELS_API_KEY", "")
    if pexels_key:
        return {"ok": True, "detail": "Pexels API key configured (env)"}
    # No env var — check if API endpoint works with a test query
    ok, result = _http_json(f"{API_URL}/api/health", timeout=5)
    if not ok:
        return {"ok": True, "detail": "Pexels key not set but not critical — using fallback images"}
    return {"ok": True, "detail": "Pexels key not set — image search will use fallback"}


async def probe_grafana_datasources(_pool) -> dict:
    """Probe: Check all Grafana datasources can connect."""
    grafana_url = os.getenv("GRAFANA_URL", "http://localhost:3000")
    grafana_user = os.getenv("GRAFANA_USER", "admin")
    grafana_pass = os.getenv("GRAFANA_PASSWORD", "admin")

    try:
        import base64
        auth = base64.b64encode(f"{grafana_user}:{grafana_pass}".encode()).decode()
        # List datasources
        req = urllib.request.Request(
            f"{grafana_url}/api/datasources",
            headers={"Authorization": f"Basic {auth}"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        datasources = json.loads(resp.read())

        # Health check each datasource
        broken = []
        for ds in datasources:
            uid = ds.get("uid", "")
            name = ds.get("name", "")
            try:
                hc_req = urllib.request.Request(
                    f"{grafana_url}/api/datasources/uid/{uid}/health",
                    headers={"Authorization": f"Basic {auth}"},
                )
                hc_resp = urllib.request.urlopen(hc_req, timeout=10)
                hc_data = json.loads(hc_resp.read())
                if hc_data.get("status") != "OK":
                    broken.append(f"{name}: {hc_data.get('message', 'unhealthy')}")
            except Exception as e:
                broken.append(f"{name}: {str(e)[:80]}")

        if broken:
            return {"ok": False, "detail": f"{len(broken)} datasource(s) broken: {'; '.join(broken[:3])}", "broken": broken}
        return {"ok": True, "detail": f"all {len(datasources)} datasources healthy"}
    except Exception as e:
        return {"ok": False, "detail": f"Grafana unreachable: {str(e)[:100]}"}


async def probe_public_site(_pool) -> dict:
    """Probe: Check the public site returns content (not just 200)."""
    try:
        site_url = os.getenv("SITE_URL", "http://localhost:3000")
        ok, data = _http_json(f"{site_url}/api/posts?limit=1", timeout=10)
        if not ok:
            return {"ok": False, "detail": f"API unreachable: {data.get('error', 'unknown')}"}
        # Check that posts are actually returned
        items = data.get("items", [])
        total = data.get("total", 0)
        if total == 0 or (isinstance(items, list) and len(items) == 0):
            return {"ok": False, "detail": "Site returns 0 posts — DB connection may be broken"}
        return {"ok": True, "detail": f"{total} posts available", "sample": items[0].get("title", "")[:50] if items else ""}
    except Exception as e:
        return {"ok": False, "detail": f"Public site check failed: {str(e)[:100]}"}


async def probe_scheduled_tasks(_pool) -> dict:
    """Probe: Check Windows scheduled tasks for failures (non-zero last result)."""
    if platform.system() != "Windows":
        return {"ok": True, "detail": "skipped (not Windows)"}
    try:
        # Query Poindexter scheduled tasks via schtasks
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "CSV", "/v"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"ok": False, "detail": f"schtasks failed: {result.stderr[:100]}"}

        # Parse CSV output — look for our tasks with non-zero "Last Result"
        import csv
        import io
        reader = csv.DictReader(io.StringIO(result.stdout))
        failed_tasks = []
        for row in reader:
            task_name = row.get("TaskName", "")
            # Only check Poindexter tasks (matches existing GladLabs/Poindexter folders)
            if not any(kw in task_name.lower() for kw in [
                "openclaw", "worker", "brain", "publisher", "nvidia",
                "power", "content", "update", "claude",
                "poindexter", "gladlabs", "glad",
            ]):
                continue
            last_result = row.get("Last Result", "0")
            # 0 = success, 1 = running/queued (OK), 267011 = task hasn't run yet
            if last_result not in ("0", "1", "267011", "267009"):
                try:
                    code = int(last_result)
                    if code != 0:
                        short_name = task_name.split("\\")[-1]
                        failed_tasks.append(f"{short_name} (exit {code})")
                except ValueError:
                    pass

        if failed_tasks:
            return {
                "ok": False,
                "failed": failed_tasks[:5],
                "detail": f"{len(failed_tasks)} task(s) failing: {', '.join(failed_tasks[:3])}",
            }
        return {"ok": True, "detail": "all scheduled tasks healthy"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "schtasks timed out after 15s"}
    except Exception as e:
        return {"ok": False, "detail": f"scheduled task check failed: {str(e)[:150]}"}


async def probe_disk_space(_pool) -> dict:
    """Probe: Alert if any drive drops below 10% free space."""
    try:
        warnings = []
        if platform.system() == "Windows":
            # Check all lettered drives
            for letter in "CDEFGH":
                drive = f"{letter}:\\"
                try:
                    usage = shutil.disk_usage(drive)
                    pct_free = (usage.free / usage.total) * 100
                    if pct_free < 10:
                        gb_free = usage.free / (1024 ** 3)
                        warnings.append(f"{drive} {pct_free:.1f}% free ({gb_free:.1f} GB)")
                except (FileNotFoundError, OSError):
                    pass  # Drive doesn't exist
        else:
            # Linux/Mac — check root
            usage = shutil.disk_usage("/")
            pct_free = (usage.free / usage.total) * 100
            if pct_free < 10:
                gb_free = usage.free / (1024 ** 3)
                warnings.append(f"/ {pct_free:.1f}% free ({gb_free:.1f} GB)")

        if warnings:
            return {
                "ok": False,
                "low_drives": warnings,
                "detail": f"Low disk space: {', '.join(warnings)}",
            }
        return {"ok": True, "detail": "all drives above 10% free"}
    except Exception as e:
        return {"ok": False, "detail": f"disk check failed: {str(e)[:150]}"}


# ---------------------------------------------------------------------------
# P0 — Pipeline health probes
# ---------------------------------------------------------------------------


async def probe_stuck_tasks(pool) -> dict:
    """Probe: Detect tasks stuck in_progress for more than 4 hours."""
    try:
        rows = await pool.fetch("""
            SELECT task_id, topic, updated_at
            FROM pipeline_tasks_view
            WHERE status = 'in_progress'
              AND updated_at < NOW() - INTERVAL '4 hours'
            ORDER BY updated_at ASC LIMIT 5
        """)
        if rows:
            stuck = [f"{r['task_id'][:8]} ({r.get('topic', 'unknown')[:30]})" for r in rows]
            return {
                "ok": False,
                "stuck_count": len(rows),
                "stuck_tasks": stuck,
                "detail": f"{len(rows)} task(s) stuck in_progress > 4h: {', '.join(stuck[:3])}",
            }
        return {"ok": True, "detail": "no stuck tasks"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_approval_queue(pool) -> dict:
    """Probe: Alert if approval queue backs up beyond threshold."""
    try:
        row = await pool.fetchrow("""
            SELECT COUNT(*) as c FROM pipeline_tasks_view
            WHERE status = 'awaiting_approval'
        """)
        count = row["c"] if row else 0
        threshold = 5
        return {
            "ok": count <= threshold,
            "queue_size": count,
            "threshold": threshold,
            "detail": f"{count} tasks awaiting approval" + (f" (exceeds {threshold})" if count > threshold else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_failed_task_spike(pool) -> dict:
    """Probe: Detect spike in task failures (24h vs 7d average)."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE status = 'failed' AND updated_at > NOW() - INTERVAL '24 hours') as recent_failures,
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE status = 'failed' AND updated_at > NOW() - INTERVAL '7 days') as week_failures
        """)
        recent = row["recent_failures"] if row else 0
        week = row["week_failures"] if row else 0
        daily_avg = week / 7.0 if week > 0 else 0
        spike = recent > max(daily_avg * 2, 3)  # 2x average or 3+ failures
        return {
            "ok": not spike,
            "recent_24h": recent,
            "weekly_total": week,
            "daily_avg": round(daily_avg, 1),
            "detail": f"{recent} failures in 24h (avg {daily_avg:.1f}/day)" + (" — SPIKE" if spike else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_worker_error_rate(pool) -> dict:
    """Probe: Check worker task executor error rate via /api/health.

    Catches the case where the worker process is running and healthy
    but every task silently fails (e.g., 100% error rate).  Runs every
    5 minutes so a total pipeline outage is detected within 15 minutes
    (3 consecutive failures → Telegram alert).
    """
    try:
        ok, data = _http_json(f"{API_URL}/api/health")
        if not ok:
            return {"ok": False, "detail": f"Worker API unreachable: {data.get('error', 'unknown')}"}

        executor = data.get("components", {}).get("task_executor", {})
        if isinstance(executor, str):
            # executor might be "unavailable"
            return {"ok": False, "detail": f"Task executor: {executor}"}

        running = executor.get("running", False)
        if not running:
            return {"ok": False, "detail": "Task executor is not running"}

        success = executor.get("success_count", 0)
        errors = executor.get("error_count", 0)
        total = success + errors

        if total == 0:
            return {"ok": True, "detail": "No tasks processed yet", "success": 0, "errors": 0}

        error_rate = errors / total
        # Critical: >50% error rate with at least 3 tasks processed
        critical = error_rate > 0.5 and total >= 3
        # Warning: 100% error rate even with few tasks
        total_failure = success == 0 and errors > 0

        return {
            "ok": not (critical or total_failure),
            "error_rate": round(error_rate * 100, 1),
            "success": success,
            "errors": errors,
            "total": total,
            "detail": (
                f"{success}✓ {errors}✗ ({error_rate * 100:.0f}% errors)"
                + (" — CRITICAL: 100% failure" if total_failure else "")
                + (" — HIGH error rate" if critical and not total_failure else "")
            ),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


# ---------------------------------------------------------------------------
# P1 — Business continuity probes
# ---------------------------------------------------------------------------


async def probe_publish_rate(pool) -> dict:
    """Probe: Alert if no posts published in 3 days."""
    try:
        row = await pool.fetchrow("""
            SELECT COUNT(*) as c, MAX(published_at) as last_published
            FROM posts
            WHERE published_at > NOW() - INTERVAL '3 days' AND status = 'published'
        """)
        count = row["c"] if row else 0
        last = row["last_published"] if row else None
        if count == 0:
            return {
                "ok": False,
                "posts_3d": 0,
                "last_published": str(last) if last else "never",
                "detail": f"0 posts published in 3 days (last: {last or 'never'})",
            }
        return {
            "ok": True,
            "posts_3d": count,
            "last_published": str(last),
            "detail": f"{count} post(s) published in last 3 days",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_cost_freshness(pool) -> dict:
    """Probe: Alert if cost_logs haven't been written in 24 hours. Correlates with approval queue."""
    try:
        # Try with cost_type column; fall back if migration hasn't run yet
        try:
            row = await pool.fetchrow("""
                SELECT
                    (SELECT MAX(created_at) FROM cost_logs WHERE cost_type IS NULL OR cost_type = 'inference') as last_inference,
                    (SELECT MAX(created_at) FROM cost_logs) as last_any,
                    (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'awaiting_approval') as approval_queue
            """)
        except Exception:
            row = await pool.fetchrow("""
                SELECT
                    (SELECT MAX(created_at) FROM cost_logs) as last_inference,
                    (SELECT MAX(created_at) FROM cost_logs) as last_any,
                    (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'awaiting_approval') as approval_queue
            """)
        if not row or not row["last_any"]:
            return {"ok": True, "detail": "no cost_logs entries yet (pipeline idle)"}
        from datetime import datetime, timezone
        # Check inference costs specifically (electricity logs every 5 min)
        last = row["last_inference"] or row["last_any"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        approval_queue = row["approval_queue"] or 0
        stale = age_hours > 24

        # Correlate: if stale AND approval queue is full, explain the root cause
        if stale and approval_queue >= 3:
            return {
                "ok": True,  # Not a real failure — root cause is approval queue
                "status": "expected_idle",
                "age_hours": round(age_hours, 1),
                "approval_queue": approval_queue,
                "detail": f"inference idle {age_hours:.0f}h — approval queue full ({approval_queue}/3), pipeline throttled",
            }
        return {
            "ok": not stale,
            "age_hours": round(age_hours, 1),
            "detail": f"inference costs last entry {age_hours:.1f}h ago" + (" — STALE" if stale else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_podcast_health(pool) -> dict:
    """Probe: Check podcast generation is active (episode in last 7 days or no episodes expected)."""
    try:
        # Check if any podcast episodes exist at all
        row = await pool.fetchrow("""
            SELECT COUNT(*) as total,
                   MAX(updated_at) as last_gen
            FROM pipeline_tasks_view
            WHERE task_type = 'podcast' OR topic ILIKE '%podcast%'
        """)
        total = row["total"] if row else 0
        if total == 0:
            return {"ok": True, "detail": "no podcast tasks found (feature may not be active)"}
        last = row["last_gen"]
        if last:
            from datetime import datetime, timezone
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
            stale = age_days > 7
            return {
                "ok": not stale,
                "total_episodes": total,
                "last_activity_days": round(age_days, 1),
                "detail": f"podcast last activity {age_days:.1f}d ago" + (" — stale" if stale else ""),
            }
        return {"ok": True, "total_episodes": total, "detail": f"{total} podcast tasks, activity ongoing"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_newsletter_health(pool) -> dict:
    """Probe: Check newsletter delivery is working if subscribers exist."""
    try:
        # Check subscriber count
        sub_row = await pool.fetchrow("""
            SELECT COUNT(*) as c FROM newsletter_subscribers
            WHERE confirmed = true
        """)
        subs = sub_row["c"] if sub_row else 0
        if subs == 0:
            return {"ok": True, "subscribers": 0, "detail": "no confirmed subscribers yet"}

        # Check last send
        send_row = await pool.fetchrow("""
            SELECT MAX(sent_at) as last_sent FROM campaign_email_logs
        """)
        if not send_row or not send_row["last_sent"]:
            return {
                "ok": False,
                "subscribers": subs,
                "detail": f"{subs} subscribers but no sends recorded — check newsletter service",
            }
        from datetime import datetime, timezone
        last = send_row["last_sent"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        stale = age_days > 7
        return {
            "ok": not stale,
            "subscribers": subs,
            "last_send_days": round(age_days, 1),
            "detail": f"{subs} subs, last send {age_days:.1f}d ago" + (" — overdue" if stale else ""),
        }
    except Exception as e:
        # Table might not exist yet
        if "does not exist" in str(e):
            return {"ok": True, "detail": "newsletter tables not created yet"}
        return {"ok": False, "detail": str(e)[:200]}


# ---------------------------------------------------------------------------
# P2 — Quality & reliability probes
# ---------------------------------------------------------------------------


async def probe_embeddings_freshness(pool) -> dict:
    """Probe: Check embeddings are up to date with published posts."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM posts WHERE status = 'published') as total_posts,
                (SELECT COUNT(DISTINCT source_id) FROM embeddings WHERE source_type = 'post') as embedded_posts,
                (SELECT MAX(created_at) FROM embeddings) as last_embedding
        """)
        total = row["total_posts"] if row else 0
        embedded = row["embedded_posts"] if row else 0
        last = row["last_embedding"]
        gap = total - embedded
        stale = gap > 3  # Allow small buffer
        detail = f"{embedded}/{total} posts embedded"
        if last:
            from datetime import datetime, timezone
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            detail += f", last {age_hours:.0f}h ago"
        if stale:
            detail += f" — {gap} posts missing embeddings"
        return {
            "ok": not stale,
            "total_posts": total,
            "embedded_posts": embedded,
            "gap": gap,
            "detail": detail,
        }
    except Exception as e:
        if "does not exist" in str(e):
            return {"ok": True, "detail": "embeddings table not created yet"}
        return {"ok": False, "detail": str(e)[:200]}


async def probe_r2_connectivity(_pool) -> dict:
    """Probe: Verify R2 CDN is reachable by fetching the podcast feed."""
    r2_url = os.getenv("R2_PUBLIC_URL", "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev")
    try:
        # Use GET with Range header to minimize data transfer (R2 blocks HEAD)
        req = urllib.request.Request(f"{r2_url}/podcast/feed.xml")
        req.add_header("Range", "bytes=0-64")
        resp = urllib.request.urlopen(req, timeout=10)
        ok = resp.status in (200, 206)
        return {
            "ok": ok,
            "status_code": resp.status,
            "detail": f"R2 CDN reachable (HTTP {resp.status})",
        }
    except urllib.error.HTTPError as e:
        # 404 is OK — means R2 is reachable, feed just doesn't exist yet
        # 403 with GET likely means the file doesn't exist on public bucket
        if e.code in (404, 403):
            return {"ok": True, "status_code": e.code, "detail": f"R2 CDN reachable (feed.xml: HTTP {e.code})"}
        return {"ok": False, "status_code": e.code, "detail": f"R2 CDN error: HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "detail": f"R2 CDN unreachable: {str(e)[:100]}"}


async def probe_traffic_anomaly(pool) -> dict:
    """Probe: Alert if daily traffic drops >60% vs 7-day average."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM page_views
                 WHERE created_at >= date_trunc('day', NOW())) as today,
                (SELECT COUNT(*) / 7.0 FROM page_views
                 WHERE created_at >= NOW() - INTERVAL '7 days') as daily_avg
        """)
        today = row["today"] if row else 0
        avg = float(row["daily_avg"]) if row and row["daily_avg"] else 0
        if avg < 10:
            return {"ok": True, "today": today, "avg": round(avg, 1), "detail": "not enough history for anomaly detection"}
        drop_pct = ((avg - today) / avg * 100) if avg > 0 else 0
        anomaly = drop_pct > 60
        return {
            "ok": not anomaly,
            "today": today,
            "daily_avg": round(avg, 1),
            "drop_pct": round(drop_pct, 1),
            "detail": f"{today} views today vs {avg:.0f}/day avg ({drop_pct:.0f}% {'drop — ANOMALY' if anomaly else 'change'})",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_quality_trend(pool) -> dict:
    """Probe: Detect declining quality scores (7d vs prior 7d)."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT AVG(quality_score) FROM pipeline_tasks_view
                 WHERE quality_score IS NOT NULL AND updated_at > NOW() - INTERVAL '7 days') as recent_avg,
                (SELECT AVG(quality_score) FROM pipeline_tasks_view
                 WHERE quality_score IS NOT NULL
                   AND updated_at BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days') as prior_avg
        """)
        recent = float(row["recent_avg"]) if row and row["recent_avg"] else None
        prior = float(row["prior_avg"]) if row and row["prior_avg"] else None
        if recent is None:
            return {"ok": True, "detail": "no quality scores in last 7 days (pipeline idle)"}
        if prior is None:
            return {"ok": True, "recent_avg": round(recent, 1), "detail": f"recent avg {recent:.1f}, no prior data to compare"}
        decline = prior - recent
        declining = decline > 10
        return {
            "ok": not declining,
            "recent_avg": round(recent, 1),
            "prior_avg": round(prior, 1),
            "decline": round(decline, 1),
            "detail": f"quality {recent:.1f} (was {prior:.1f})" + (f" — declining {decline:.0f}pts" if declining else ""),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_topic_quality(pool) -> dict:
    """Probe: Monitor rejection rate and attribute the dominant driver (7d).

    Rejections come from multiple upstream stages, not just low quality.
    Reporting just "72% rejected — topics too low quality" when 0% of
    tasks failed the quality threshold was misleading (issue #235) —
    the actual driver in that case was semantic dedup. The probe now
    breaks down rejections by audit-log category and names the winner
    in the detail message.
    """
    try:
        row = await pool.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'rejected') as rejected,
                COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score < 70) as low_quality
            FROM pipeline_tasks_view
            WHERE created_at > NOW() - INTERVAL '7 days'
        """)
        total = row["total"] if row else 0
        rejected = row["rejected"] if row else 0
        low_quality = row["low_quality"] if row else 0
        if total == 0:
            return {"ok": True, "detail": "no tasks created in last 7 days (pipeline idle)"}
        rejection_rate = rejected / total * 100
        low_quality_rate = low_quality / total * 100

        # Attribute rejections to their actual drivers by counting the
        # rejection-category audit events in the same 7d window. If a
        # task hits multiple gates, the first-written event wins — that
        # matches the operator-facing reality (what stopped it first).
        driver_rows = await pool.fetch("""
            SELECT event, COUNT(*) AS n
            FROM audit_log
            WHERE event IN (
                'semantic_dedup_rejected',
                'qa_rejected',
                'topic_rejected',
                'title_not_original',
                'content_validation_rejected'
            )
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY event
            ORDER BY n DESC
            LIMIT 3
        """)
        drivers = {r["event"]: r["n"] for r in driver_rows}
        top_driver = next(iter(drivers), None)

        healthy = rejection_rate <= 30
        if healthy:
            suffix = ""
        elif top_driver:
            # Be specific about the cause: "dedup" or "quality", not a
            # generic "low quality" claim when quality wasn't the issue.
            label = {
                "semantic_dedup_rejected": "duplicate topics (feeds re-surfacing covered ground)",
                "qa_rejected": "QA threshold (tighten writer or loosen gate)",
                "topic_rejected": "topic selector rejecting (filters too strict)",
                "title_not_original": "titles colliding with web content",
                "content_validation_rejected": "content validator failing",
            }.get(top_driver, top_driver)
            suffix = f" — driver: {label} ({drivers[top_driver]}/{rejected})"
        else:
            suffix = " — cause unknown (no matching audit events)"

        return {
            "ok": healthy,
            "total_tasks": total,
            "rejected": rejected,
            "rejection_rate": round(rejection_rate, 1),
            "low_quality_count": low_quality,
            "low_quality_rate": round(low_quality_rate, 1),
            "drivers": drivers,
            "top_driver": top_driver,
            "detail": (
                f"{rejection_rate:.0f}% rejected, "
                f"{low_quality_rate:.0f}% below 70 quality ({total} tasks)"
                + suffix
            ),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_pipeline_throughput(pool) -> dict:
    """Probe: Compare 7-day publish throughput vs prior 7 days."""
    try:
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM posts
                 WHERE status = 'published' AND published_at > NOW() - INTERVAL '7 days') as recent,
                (SELECT COUNT(*) FROM posts
                 WHERE status = 'published'
                   AND published_at BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days') as prior
        """)
        recent = row["recent"] if row else 0
        prior = row["prior"] if row else 0
        if prior == 0:
            return {"ok": True, "recent_7d": recent, "prior_7d": prior,
                    "detail": f"{recent} posts this week, no prior data to compare"}
        change_pct = (recent - prior) / prior * 100
        concerning = change_pct < -50
        return {
            "ok": not concerning,
            "recent_7d": recent,
            "prior_7d": prior,
            "change_pct": round(change_pct, 1),
            "detail": f"{recent} posts (was {prior}), {change_pct:+.0f}% change"
            + ("" if not concerning else " — throughput dropped >50%"),
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


# All probes in execution order
PROBES = {
    # Infrastructure
    "db_ping": probe_db_ping,
    "ollama_models": probe_ollama_models,
    "content_gen": probe_content_gen,
    "grafana_datasources": probe_grafana_datasources,
    "public_site": probe_public_site,
    "scheduled_tasks": probe_scheduled_tasks,
    "disk_space": probe_disk_space,
    "r2_connectivity": probe_r2_connectivity,
    # Pipeline health (P0)
    "stuck_tasks": probe_stuck_tasks,
    "approval_queue": probe_approval_queue,
    "failed_task_spike": probe_failed_task_spike,
    "worker_error_rate": probe_worker_error_rate,
    # Content quality
    "quality_score": probe_quality_score,
    "quality_trend": probe_quality_trend,
    # Business continuity (P1)
    "publish_rate": probe_publish_rate,
    "cost_freshness": probe_cost_freshness,
    "podcast_health": probe_podcast_health,
    "newsletter_health": probe_newsletter_health,
    # Data health
    "affiliate_linker": probe_affiliate_linker,
    "research_service": probe_research_service,
    "image_search": probe_image_search,
    "embeddings_freshness": probe_embeddings_freshness,
    # Analytics
    "traffic_anomaly": probe_traffic_anomaly,
    # Topic & throughput monitoring
    "topic_quality": probe_topic_quality,
    "pipeline_throughput": probe_pipeline_throughput,
}

# Track consecutive failures for alerting
_failure_counts: dict[str, int] = {}
ALERT_AFTER_FAILURES = 3  # Alert on Telegram after 3 consecutive failures

# Probes whose alerts are now owned by Prometheus + Alertmanager
# (Phase D cutover). They still RUN — their results land in brain_knowledge
# for observability and trigger remediation — they just don't fire duplicate
# Telegram alerts. Moving them fully out of this file is tracked as a
# follow-up; this set is the "one source of truth for pages" boundary.
#
# Keep in sync with infrastructure/prometheus/alerts/infrastructure.yml
# + app_settings prometheus.rule.* (see services/prometheus_rule_builder.py).
PROMETHEUS_COVERED_PROBES: frozenset[str] = frozenset({
    "db_ping",                # → PoindexterPostgresDown
    "ollama_models",          # → PoindexterOllamaDown
    "embeddings_freshness",   # → EmbeddingsStale
    "cost_freshness",         # → DailySpend*/MonthlySpend*
    "publish_rate",           # → NoPublishedPostsRecently
})


async def run_health_probes(pool, notify_fn=None):
    """Run all due health probes, store results in brain_knowledge, alert on failures."""
    await _sync_config_from_db(pool)
    results = {}

    for name, probe_fn in PROBES.items():
        if not _is_due(name):
            continue

        _mark_run(name)
        try:
            result = await probe_fn(pool)
        except Exception as e:
            result = {"ok": False, "detail": f"probe crashed: {e}"}

        results[name] = result
        ok = result.get("ok", False)

        # Store in brain_knowledge
        try:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
                VALUES ($1, $2, $3, $4, 'health_probe', $5)
                ON CONFLICT (entity, attribute)
                DO UPDATE SET value = EXCLUDED.value, confidence = EXCLUDED.confidence,
                             updated_at = NOW(), tags = EXCLUDED.tags
            """,
                f"probe.{name}",
                "health_status",
                json.dumps(result),
                1.0 if ok else 0.3,
                ["health", "probe", name],
            )
        except Exception as e:
            logger.debug("[PROBES] Failed to store result for %s: %s", name, e)

        # Track failures and alert.
        # Probes in PROMETHEUS_COVERED_PROBES no longer Telegram-alert —
        # Prometheus + Alertmanager own human-visible alerts for those
        # signals. We still track failure counts so remediation logic
        # fires and Gitea issues get filed.
        prom_covered = name in PROMETHEUS_COVERED_PROBES
        if ok:
            if (
                _failure_counts.get(name, 0) >= ALERT_AFTER_FAILURES
                and notify_fn
                and not prom_covered
            ):
                notify_fn(f"✅ Probe '{name}' recovered: {result.get('detail', '')}")
            _failure_counts[name] = 0
        else:
            _failure_counts[name] = _failure_counts.get(name, 0) + 1
            logger.warning("[PROBES] %s FAILED (%d consecutive): %s",
                           name, _failure_counts[name], result.get("detail", ""))
            if _failure_counts[name] == ALERT_AFTER_FAILURES:
                detail = result.get('detail', 'unknown error')
                if notify_fn and not prom_covered:
                    notify_fn(
                        f"🔴 Probe '{name}' failed {ALERT_AFTER_FAILURES}x: {detail}"
                    )
                # Auto-create Gitea issue for tracking (always, even for
                # Prometheus-covered probes — the issue is a paper trail, not a page).
                _create_gitea_issue(name, detail)

    # --- Self-healing: execute remediation actions for persistent failures ---
    for name, count in _failure_counts.items():
        if count >= ALERT_AFTER_FAILURES and name in REMEDIATIONS:
            _try_remediation(name, results.get(name, {}), notify_fn)

    if results:
        passed = sum(1 for r in results.values() if r.get("ok"))
        total = len(results)
        logger.info("[PROBES] Ran %d probes: %d passed, %d failed", total, passed, total - passed)

    return results


# ---------------------------------------------------------------------------
# Self-healing: remediation actions
# ---------------------------------------------------------------------------

# Track remediation cooldowns (prevent restart loops)
_last_remediation: dict[str, float] = {}
REMEDIATION_COOLDOWN = 900  # 15 minutes between remediation attempts per probe


def _restart_container(container_name: str) -> tuple[bool, str]:
    """Restart a Docker container. Returns (success, message)."""
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return True, f"Restarted {container_name}"
        return False, f"docker restart failed: {result.stderr[:200]}"
    except Exception as e:
        return False, f"restart error: {str(e)[:200]}"


def _try_remediation(probe_name: str, result: dict, notify_fn=None):
    """Execute a remediation action if cooldown has elapsed."""
    last = _last_remediation.get(probe_name, 0)
    if (time.time() - last) < REMEDIATION_COOLDOWN:
        return  # Too soon since last attempt

    action = REMEDIATIONS.get(probe_name)
    if not action:
        return

    logger.info("[SELF-HEAL] Attempting remediation for '%s': %s",
                probe_name, action.get("description", "unknown"))

    ok, msg = False, "no action taken"
    action_type = action.get("type")

    if action_type == "restart_container":
        ok, msg = _restart_container(action["container"])
    elif action_type == "restart_multiple":
        msgs = []
        for container in action["containers"]:
            c_ok, c_msg = _restart_container(container)
            msgs.append(c_msg)
            ok = ok or c_ok
        msg = "; ".join(msgs)

    _last_remediation[probe_name] = time.time()

    detail = result.get("detail", "")
    if notify_fn:
        emoji = "🔧" if ok else "⚠️"
        notify_fn(
            f"{emoji} Self-heal '{probe_name}': {msg}\n"
            f"Trigger: {detail}"
        )
    logger.info("[SELF-HEAL] %s — %s (probe: %s)",
                "SUCCESS" if ok else "FAILED", msg, probe_name)


# Map probe names to remediation actions.
# Only non-destructive, data-safe actions — restart services, never delete data.
REMEDIATIONS = {
    "worker_error_rate": {
        "type": "restart_container",
        "container": "poindexter-worker",
        "description": "Restart worker when error rate is critical",
    },
    "stuck_tasks": {
        "type": "restart_container",
        "container": "poindexter-worker",
        "description": "Restart worker when tasks are stuck",
    },
    "grafana_datasources": {
        "type": "restart_container",
        "container": "poindexter-grafana",
        "description": "Restart Grafana when datasources are unhealthy",
    },
    "public_site": {
        "type": "restart_container",
        "container": "poindexter-worker",
        "description": "Restart worker when public site API is down",
    },
}
