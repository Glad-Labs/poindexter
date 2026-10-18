#!/usr/bin/env python3
"""Suspend the PC through the operator's night window, and wake it at the end.

2026-10-18: the operator's box averaged 365 W around the clock (~$75/month at
$0.288/kWh) and does nothing useful overnight, so it sleeps 01:00-08:00.

Runs every 10 minutes from ``poindexter-night-sleep.timer`` and does nothing
outside the window. Inside it:

1. Defers while anything is in flight: a GPU lock holder, a pipeline task in
   ``in_progress``, a running deploy pass, or the operator's skip file
   (``~/.poindexter/no-sleep-tonight``). Past ``host_sleep_defer_limit_minutes``
   after the start it gives up for the night and records why.
2. Unloads every model from both GPUs first: the NVIDIA driver preserves VRAM
   across suspend by copying it to /var on the small root drive (up to 56 GB
   across the 5090 and 3090), so a loaded card makes suspend slow and can fill
   that drive.
3. Suspends to RAM with the RTC set for the wake time (``rtcwake``).
4. On resume: records the sleep, checks that every GPU container can still
   see its GPU and restarts any that cannot, and restarts the backup jobs so
   each runs at once. Their timers count only awake time, so without the
   restart a "daily" backup would drift seven hours later every night.

Everything is read from app_settings (``host_sleep_*``, ``operator_timezone``)
through the postgres container; the feature is off unless
``host_sleep_enabled`` is true. ``--dry-run`` prints the decision and acts on
nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PG_CONTAINER = "poindexter-postgres-local"
# Every GPU-lock holder stamps its connection "poindexter-gpu:<owner>:<phase>:..."
# (services.gpu_scheduler._holder_tag); any holder means work is on a card.
# Pinned to the scheduler by tests/unit/scripts/test_night_sleep.py.
GPU_HOLDER_PATTERN = "poindexter-gpu:%"
SOURCE = "scripts.linux.night_sleep"
SKIP_FILE = Path(os.environ.get("NIGHT_SLEEP_SKIP_FILE", str(Path.home() / ".poindexter" / "no-sleep-tonight")))

DEFAULTS = {
    "host_sleep_enabled": "false",
    "host_sleep_start": "01:00",
    "host_sleep_wake": "08:00",
    "host_sleep_defer_limit_minutes": "120",
    "host_sleep_resume_restart_containers": (
        "poindexter-backup-hourly,poindexter-backup-daily,poindexter-backup-volumes,poindexter-backup-offsite"
    ),
    "operator_timezone": "UTC",
    "ollama_base_url": "",
    "ollama_vision_base_url": "",
}


def log(msg: str) -> None:
    print(f"[night-sleep] {msg}", flush=True)


# --- pure decisions (unit-tested) --------------------------------------------


def _hhmm(value: str) -> tuple[int, int]:
    hours, minutes = str(value).strip().split(":")
    return int(hours), int(minutes)


def window(now: datetime, start: str, wake: str, defer_limit_minutes: int) -> dict:
    """Where ``now`` (tz-aware, operator time) sits relative to tonight's window.

    ``state`` is ``outside`` (not between the start and the wake), ``due``
    (inside, and still within the defer limit of the start) or ``too_late``
    (inside, but past the defer limit). ``start_at`` / ``wake_at`` are the
    window instants that apply. A wake at or before the start crosses midnight.
    """
    sh, sm = _hhmm(start)
    wh, wm = _hhmm(wake)
    start_at = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    if start_at > now:
        start_at -= timedelta(days=1)
    wake_at = start_at.replace(hour=wh, minute=wm)
    if wake_at <= start_at:
        wake_at += timedelta(days=1)
    if not start_at <= now < wake_at:
        return {"state": "outside", "start_at": start_at, "wake_at": wake_at}
    if now >= start_at + timedelta(minutes=defer_limit_minutes):
        return {"state": "too_late", "start_at": start_at, "wake_at": wake_at}
    return {"state": "due", "start_at": start_at, "wake_at": wake_at}


def host_url(url: str) -> str:
    """An Ollama base URL as the host reaches it (containers say host.docker.internal).

    Only http(s): the URL comes from app_settings and is opened with urllib,
    which would also open file:// and other schemes.
    """
    url = (url or "").strip().rstrip("/")
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return url.replace("host.docker.internal", "127.0.0.1")


# --- I/O ---------------------------------------------------------------------


def psql(sql: str, *args: str) -> str:
    """Run one statement in the postgres container; ``$1``... bind to ``args``.

    The SQL goes in on stdin: psql interpolates ``:'name'`` variables only in
    input it reads, never in ``-c``. Each value is its own argv element, so
    nothing passes through a shell.
    """
    for i in range(len(args), 0, -1):
        sql = sql.replace(f"${i}", f":'a{i}'")
    variables = [part for i, a in enumerate(args, 1) for part in ("-v", f"a{i}={a}")]
    cmd = ["sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -q -v ON_ERROR_STOP=1 "$@" -f -',
           "psql", *variables]
    out = subprocess.run(["docker", "exec", "-i", PG_CONTAINER, *cmd], input=sql, capture_output=True,
                         text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def read_settings() -> dict[str, str]:
    rows = psql(
        "SELECT key || '=' || value FROM app_settings WHERE key = ANY(string_to_array($1, ','))",
        ",".join(DEFAULTS),
    )
    got = dict(DEFAULTS)
    for line in rows.splitlines():
        key, _, value = line.partition("=")
        if key in got and value.strip():
            got[key] = value.strip()
    return got


def audit(event: str, details: dict, severity: str = "info") -> None:
    try:
        psql(
            "INSERT INTO audit_log (event_type, source, details, severity) VALUES ($1, $2, $3::jsonb, $4)",
            event, SOURCE, json.dumps(details, default=str), severity,
        )
    except Exception as exc:  # noqa: BLE001 — a lost audit row never blocks the sleep
        log(f"audit write failed for {event}: {exc}")


def slept_since(start_at: datetime) -> bool:
    n = psql(
        "SELECT count(*) FROM audit_log WHERE event_type IN ('host_sleep', 'host_sleep_skipped') "
        "AND timestamp >= $1::timestamptz",
        start_at.isoformat(),
    )
    return int(n or 0) > 0


def busy_reasons() -> list[str]:
    reasons: list[str] = []
    holders = psql(
        "SELECT string_agg(DISTINCT a.application_name, ', ') FROM pg_locks l "
        "JOIN pg_stat_activity a ON a.pid = l.pid WHERE l.locktype = 'advisory' AND l.granted "
        "AND a.application_name LIKE $1",
        GPU_HOLDER_PATTERN,
    )
    if holders:
        reasons.append(f"GPU lock held by {holders}")
    running = psql("SELECT count(*) FROM pipeline_tasks WHERE status = 'in_progress'")
    if int(running or 0):
        reasons.append(f"{running} pipeline task(s) in progress")
    deploy = subprocess.run(["systemctl", "is-active", "poindexter-deploy-sync.service"],
                            capture_output=True, text=True).stdout.strip()
    if deploy in ("active", "activating"):
        reasons.append("a deploy pass is running")
    if SKIP_FILE.exists():
        reasons.append(f"skip file {SKIP_FILE} is present")
    return reasons


def free_vram(settings: dict[str, str]) -> None:
    """Unload every model: media sidecars via the scheduler's ladder, then every
    Ollama model on every instance (the ladder skips the pinned judge)."""
    subprocess.run(
        ["docker", "exec", "poindexter-worker", "python", "-c",
         "import asyncio\n"
         "from poindexter.services.gpu_scheduler import gpu\n"
         "asyncio.run(gpu.reclaim_render_vram(include_ollama=False))"],
        capture_output=True, text=True, timeout=300,
    )
    for base in {host_url(settings["ollama_base_url"]) or "http://127.0.0.1:11434",
                 host_url(settings["ollama_vision_base_url"])} - {""}:
        try:
            with urllib.request.urlopen(f"{base}/api/ps", timeout=10) as resp:  # nosec B310 - host_url admits http(s) only
                models = [m["name"] for m in json.load(resp).get("models", [])]
            for name in models:
                req = urllib.request.Request(
                    f"{base}/api/generate", data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=60).read()  # nosec B310 - host_url admits http(s) only
                log(f"unloaded {name} from {base}")
        except Exception as exc:  # noqa: BLE001
            log(f"could not unload models at {base}: {exc}")


def gpu_containers() -> list[str]:
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    out = []
    for name in names:
        req = subprocess.run(["docker", "inspect", name, "--format", "{{json .HostConfig.DeviceRequests}}"],
                             capture_output=True, text=True).stdout.strip()
        if req and req != "null" and "gpu" in req.lower():
            out.append(name)
    return out


def after_resume(settings: dict[str, str], slept_at: datetime, wake_at: datetime) -> None:
    resumed_at = datetime.now(slept_at.tzinfo)
    audit("host_resume", {"slept_at": slept_at.isoformat(), "resumed_at": resumed_at.isoformat(),
                          "planned_wake": wake_at.isoformat()})
    time.sleep(45)
    restarted = []
    for name in gpu_containers():
        ok = subprocess.run(["docker", "exec", name, "nvidia-smi", "-L"], capture_output=True, timeout=60)
        if ok.returncode != 0:
            subprocess.run(["docker", "restart", name], capture_output=True, timeout=300)
            restarted.append(name)
    for name in [c.strip() for c in settings["host_sleep_resume_restart_containers"].split(",") if c.strip()]:
        subprocess.run(["docker", "restart", name], capture_output=True, timeout=300)
    audit("host_resume_checked", {"gpu_containers_restarted": restarted,
                                  "backup_jobs_restarted": settings["host_sleep_resume_restart_containers"]},
          severity="warning" if restarted else "info")
    log(f"resumed; GPU containers restarted: {restarted or 'none'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the decision, act on nothing")
    args = parser.parse_args()

    settings = read_settings()
    if settings["host_sleep_enabled"].lower() not in ("1", "true", "yes", "on"):
        log("host_sleep_enabled is off")
        return 0
    tz = ZoneInfo(settings["operator_timezone"])
    now = datetime.now(tz)
    win = window(now, settings["host_sleep_start"], settings["host_sleep_wake"],
                 int(settings["host_sleep_defer_limit_minutes"]))
    if win["state"] == "outside":
        log(f"outside the window ({settings['host_sleep_start']}-{settings['host_sleep_wake']})")
        return 0
    if slept_since(win["start_at"]):
        log("already slept or gave up tonight")
        return 0
    reasons = busy_reasons()
    if win["state"] == "too_late":
        audit("host_sleep_skipped", {"reasons": reasons, "window_start": win["start_at"].isoformat()},
              severity="warning")
        log(f"past the defer limit; staying up tonight ({'; '.join(reasons) or 'no reason recorded'})")
        return 0
    if reasons:
        log(f"deferring: {'; '.join(reasons)}")
        return 0
    if args.dry_run:
        log(f"would unload the GPUs and sleep until {win['wake_at'].isoformat()}")
        return 0

    free_vram(settings)
    slept_at = datetime.now(tz)
    audit("host_sleep", {"slept_at": slept_at.isoformat(), "planned_wake": win["wake_at"].isoformat()})
    log(f"suspending until {win['wake_at'].isoformat()}")
    result = subprocess.run(["rtcwake", "-m", "mem", "-t", str(int(win["wake_at"].timestamp()))],
                            capture_output=True, text=True)
    if result.returncode != 0:
        audit("host_sleep_failed", {"stderr": result.stderr.strip()[:500]}, severity="warning")
        log(f"rtcwake failed: {result.stderr.strip()}")
        return 1
    after_resume(settings, slept_at, win["wake_at"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
