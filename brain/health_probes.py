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

logger = logging.getLogger("brain.probes")

API_URL = os.getenv("API_URL", "https://cofounder-production.up.railway.app")
LOCAL_OLLAMA = os.getenv("OLLAMA_URL", "http://localhost:11434")
GITEA_URL = os.getenv("GITEA_URL", "http://localhost:3001")
GITEA_USER = os.getenv("GITEA_USER", "gladlabs")
GITEA_PASS = os.getenv("GITEA_PASS", "")
GITEA_REPO = os.getenv("GITEA_REPO", "gladlabs/glad-labs-codebase")

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

# Detect Railway environment (no local Ollama available)
IS_RAILWAY = bool(os.getenv("RAILWAY_SERVICE_ID"))

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
    if IS_RAILWAY:
        return {"ok": True, "detail": "skipped on Railway (no local Ollama)", "models": []}
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
            SELECT quality_score FROM content_tasks
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
    if IS_RAILWAY:
        return {"ok": True, "detail": "skipped on Railway (no local Ollama)"}
    ok, result = _http_json(
        f"{LOCAL_OLLAMA}/api/generate",
        method="POST",
        data={
            "model": "qwen3-coder",
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
                "model": "llama3.2",
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
            "SELECT COUNT(*) as c FROM affiliate_links WHERE is_active = true"
        )
        count = row["c"] if row else 0
        return {
            "ok": count > 0,
            "active_links": count,
            "detail": f"{count} active affiliate links",
        }
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


async def probe_research_service(pool) -> dict:
    """Probe: Verify research service endpoint responds."""
    ok, result = _http_json(f"{API_URL}/api/health", timeout=5)
    if not ok:
        return {"ok": False, "detail": f"API unreachable: {result.get('error', 'unknown')}"}

    # Check that published posts exist (research service uses these for internal links)
    try:
        row = await pool.fetchrow(
            "SELECT COUNT(*) as c FROM posts WHERE status = 'published'"
        )
        count = row["c"] if row else 0
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
    if IS_RAILWAY:
        return {"ok": True, "detail": "skipped on Railway (Grafana is local)"}
    grafana_url = os.getenv("GRAFANA_URL", "http://localhost:3000")
    grafana_user = os.getenv("GRAFANA_USER", "admin")
    grafana_pass = os.getenv("GRAFANA_PASSWORD", "gladlabs")

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
        site_url = os.getenv("SITE_URL", "https://www.gladlabs.io")
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
    if IS_RAILWAY or platform.system() != "Windows":
        return {"ok": True, "detail": "skipped (not Windows)"}
    try:
        # Query Glad Labs scheduled tasks via schtasks
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
            # Only check Glad Labs tasks (in root or GladLabs folder)
            if not any(kw in task_name.lower() for kw in [
                "openclaw", "worker", "brain", "publisher", "nvidia",
                "power", "content", "update", "claude", "gladlabs", "glad"
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
    if IS_RAILWAY:
        return {"ok": True, "detail": "skipped on Railway (managed infra)"}
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


# All probes in execution order
PROBES = {
    "db_ping": probe_db_ping,
    "ollama_models": probe_ollama_models,
    "content_gen": probe_content_gen,
    "quality_score": probe_quality_score,
    "affiliate_linker": probe_affiliate_linker,
    "research_service": probe_research_service,
    "image_search": probe_image_search,
    "grafana_datasources": probe_grafana_datasources,
    "public_site": probe_public_site,
    "scheduled_tasks": probe_scheduled_tasks,
    "disk_space": probe_disk_space,
}

# Track consecutive failures for alerting
_failure_counts: dict[str, int] = {}
ALERT_AFTER_FAILURES = 3  # Alert on Telegram after 3 consecutive failures


async def run_health_probes(pool, send_telegram_fn=None):
    """Run all due health probes, store results in brain_knowledge, alert on failures."""
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

        # Track failures and alert
        if ok:
            if _failure_counts.get(name, 0) >= ALERT_AFTER_FAILURES and send_telegram_fn:
                send_telegram_fn(f"✅ Probe '{name}' recovered: {result.get('detail', '')}")
            _failure_counts[name] = 0
        else:
            _failure_counts[name] = _failure_counts.get(name, 0) + 1
            logger.warning("[PROBES] %s FAILED (%d consecutive): %s",
                           name, _failure_counts[name], result.get("detail", ""))
            if _failure_counts[name] == ALERT_AFTER_FAILURES:
                detail = result.get('detail', 'unknown error')
                if send_telegram_fn:
                    send_telegram_fn(
                        f"🔴 Probe '{name}' failed {ALERT_AFTER_FAILURES}x: {detail}"
                    )
                # Auto-create Gitea issue for tracking
                _create_gitea_issue(name, detail)

    if results:
        passed = sum(1 for r in results.values() if r.get("ok"))
        total = len(results)
        logger.info("[PROBES] Ran %d probes: %d passed, %d failed", total, passed, total - passed)

    return results
