#!/usr/bin/env python3
"""Suspend the PC through the operator's night window, and wake it at the end.

2026-10-18: the operator's box averaged 365 W around the clock (~$75/month at
$0.288/kWh) and does nothing useful overnight, so it sleeps 01:00-08:00.

Runs every 10 minutes from ``poindexter-night-sleep.timer`` and does nothing
outside the window. Inside it:

1. Defers while anything is in flight: a GPU lock holder, a pipeline task in
   ``in_progress``, a running deploy pass, a CI job on this machine's
   self-hosted runners, or the operator's skip file
   (``~/.poindexter/no-sleep-tonight``). Past ``host_sleep_defer_limit_minutes``
   after the start it gives up for the night and records why.
2. Unloads every model from both GPUs first: the NVIDIA driver preserves VRAM
   across suspend by copying it to /var on the small root drive (up to 56 GB
   across the 5090 and 3090), so a loaded card makes suspend slow and can fill
   that drive.
   It then waits for the cards to settle: the 2026-10-18 test suspend was
   aborted because an Ollama runner told to unload seconds earlier was still
   releasing 21 GB on the 3090, stuck in an uninterruptible exit, and the
   kernel's process freezer gave up after 20 s.
3. Arms the RTC alarm (``rtcwake -m no``) and suspends through
   ``systemctl suspend``, so the NVIDIA suspend hooks run. Writing
   ``/sys/power/state`` directly (``rtcwake -m mem``) skips them.
4. On resume: brings the desktop back first (below), records the sleep,
   checks that every GPU container can still
   see its GPU and restarts any that cannot, and restarts the backup jobs so
   each runs at once. Their timers count only awake time, so without the
   restart a "daily" backup would drift seven hours later every night.

The desktop: on 2026-10-18 a test sleep resumed every service but not the
screens. The COSMIC compositor logged "Unable to load output config: Failed to
reset config" and never took the displays back, so the monitors stayed on the
kernel console. Switching to another virtual console and back made it
reclaim them with the session and its apps intact. So on resume the script
cycles the graphical session's VT and checks the compositor's log for that
error after the switch, retrying once (``host_sleep_resume_vt_cycle``).

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
    "host_sleep_resume_vt_cycle": "true",
    "host_sleep_settle_tolerance_mib": "64",
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


def ci_jobs_running() -> int:
    """GitHub Actions jobs running on this machine's self-hosted runners.

    A runner (host or container; container processes are visible from the
    host) starts a ``Runner.Worker`` only for the length of a job. 2026-10-19:
    a test-backend job started at 01:40 and the box slept under it at 01:41,
    failing the PR's required check."""
    # By process name (-x), not command line: a shell whose command text
    # mentions the worker would otherwise count as a job.
    out = subprocess.run(["pgrep", "-c", "-x", "Runner.Worker"], capture_output=True, text=True).stdout
    return int(out.strip() or 0)


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
    jobs = ci_jobs_running()
    if jobs:
        reasons.append(f"{jobs} CI job(s) running on this machine's runners")
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


def _ollama_loaded(settings: dict[str, str]) -> bool:
    for base in {host_url(settings["ollama_base_url"]) or "http://127.0.0.1:11434",
                 host_url(settings["ollama_vision_base_url"])} - {""}:
        try:
            with urllib.request.urlopen(f"{base}/api/ps", timeout=10) as resp:  # nosec B310 - host_url admits http(s) only
                if json.load(resp).get("models"):
                    return True
        except Exception as exc:  # noqa: BLE001 — an unreachable instance holds nothing we can see
            log(f"could not read loaded models at {base}: {exc}")
    return False


def _gpu_memory_used() -> tuple[int, ...]:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=30).stdout
    return tuple(int(v) for v in out.split() if v.strip().isdigit())


def wait_gpus_settled(settings: dict[str, str], timeout_s: float = 180.0, interval_s: float = 5.0) -> bool:
    """True once no Ollama model is loaded and GPU memory holds still across
    three readings ``interval_s`` apart: the runners have finished exiting.

    "Still" is within ``host_sleep_settle_tolerance_mib`` per GPU. The first
    night (2026-10-19) demanded identical readings, and the 5090's desktop
    memory moved by a few dozen MiB between them (3474 vs 3437), so the sleep
    deferred twice for nothing. A runner releasing a model moves gigabytes."""
    tolerance = int(settings.get("host_sleep_settle_tolerance_mib", "64") or 64)
    deadline = time.monotonic() + timeout_s
    last: tuple[int, ...] | None = None
    stable = 0
    while time.monotonic() < deadline:
        used = _gpu_memory_used()
        still = last is not None and len(used) == len(last) and all(
            abs(a - b) <= tolerance for a, b in zip(used, last)
        )
        stable = stable + 1 if (still and not _ollama_loaded(settings)) else 0
        if stable >= 2:
            log(f"GPUs settled at {used} MiB")
            return True
        last = used
        time.sleep(interval_s)
    log(f"GPUs did not settle within {timeout_s:.0f}s (last {last} MiB)")
    return False


def _suspended_seconds() -> float:
    """CLOCK_BOOTTIME counts suspend and CLOCK_MONOTONIC does not; their gap is
    the total time this boot has spent asleep."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


def suspend_until(wake_at: datetime, start_timeout_s: float = 180.0) -> bool:
    """Arm the RTC for ``wake_at``, suspend through systemd, and return once the
    machine has slept and woken. False if it never went to sleep."""
    subprocess.run(["rtcwake", "-m", "no", "-t", str(int(wake_at.timestamp()))],
                   check=True, capture_output=True, text=True)
    asleep_before = _suspended_seconds()
    result = subprocess.run(["systemctl", "suspend", "-i"], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"systemctl suspend failed: {result.stderr.strip()}")
        subprocess.run(["rtcwake", "-m", "disable"], capture_output=True)
        return False
    deadline = time.monotonic() + start_timeout_s
    while time.monotonic() < deadline:
        time.sleep(5)
        if _suspended_seconds() - asleep_before > 15:
            return True
    subprocess.run(["rtcwake", "-m", "disable"], capture_output=True)
    return False


# What the COSMIC compositor logs when it fails to take the displays back.
DISPLAY_FAILURE_PATTERNS = ("Failed to reset config", "Unable to load output config")


def graphical_vt(wait_s: float = 60.0, interval_s: float = 5.0) -> int | None:
    """The virtual console of the local graphical session, if any.

    Not filtered on ``Active``: right after resume, when the compositor has
    lost the displays, logind can report the session inactive (the
    2026-10-18 retest found no session 10 s after wake and skipped the
    switch). An active session is preferred; any local one will do. Polls
    while logind has nothing to report yet."""
    deadline = time.monotonic() + wait_s
    while True:
        found: list[tuple[bool, int]] = []
        ids = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True).stdout
        for sid in [line.split()[0] for line in ids.splitlines() if line.strip()]:
            props = dict(
                line.split("=", 1)
                for line in subprocess.run(
                    ["loginctl", "show-session", sid, "-p", "Type", "-p", "VTNr", "-p", "Active", "-p", "Remote"],
                    capture_output=True, text=True,
                ).stdout.splitlines()
                if "=" in line
            )
            vt = props.get("VTNr", "0")
            if props.get("Type") in ("wayland", "x11") and props.get("Remote") != "yes" and vt.isdigit() and int(vt) > 0:
                found.append((props.get("Active") == "yes", int(vt)))
        if found:
            return max(found)[1]
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval_s)


def display_failed_since(since: datetime) -> bool:
    """Whether the compositor has logged a failure to take the displays since ``since``."""
    log = subprocess.run(
        ["journalctl", "--since", since.strftime("%Y-%m-%d %H:%M:%S"), "-o", "cat", "--no-pager"],
        capture_output=True, text=True,
    ).stdout
    return any(p in log for p in DISPLAY_FAILURE_PATTERNS)


def recover_display(settle_s: float = 8.0, wait_s: float = 60.0) -> dict:
    """Switch the graphical session's console away and back so the compositor
    reclaims the screens; retry once if it logs the failure again."""
    vt = graphical_vt(wait_s=wait_s)
    if vt is None:
        return {"vt": None, "ok": None, "cycles": 0}
    other = 2 if vt != 2 else 3
    for cycle in (1, 2):
        mark = datetime.now()
        subprocess.run(["chvt", str(other)], capture_output=True)
        time.sleep(3)
        subprocess.run(["chvt", str(vt)], capture_output=True)
        time.sleep(settle_s)
        if not display_failed_since(mark):
            return {"vt": vt, "ok": True, "cycles": cycle}
    return {"vt": vt, "ok": False, "cycles": 2}


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
    display = {"vt": None, "ok": None, "cycles": 0}
    if settings.get("host_sleep_resume_vt_cycle", "true").lower() in ("1", "true", "yes", "on"):
        time.sleep(10)
        display = recover_display()
        log(f"display: {display}")
    audit("host_resume", {"slept_at": slept_at.isoformat(), "resumed_at": resumed_at.isoformat(),
                          "planned_wake": wake_at.isoformat(), "display": display},
          severity="warning" if display.get("ok") is False else "info")
    time.sleep(35)
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
    if not wait_gpus_settled(settings):
        log("deferring: the GPUs are still releasing memory")
        return 0
    # The settle wait can take minutes; anything that started meanwhile (the
    # 2026-10-19 CI job began in that gap) defers to the next tick.
    reasons = busy_reasons()
    if reasons:
        log(f"deferring: {'; '.join(reasons)} (started while the GPUs settled)")
        return 0
    slept_at = datetime.now(tz)
    audit("host_sleep", {"slept_at": slept_at.isoformat(), "planned_wake": win["wake_at"].isoformat()})
    log(f"suspending until {win['wake_at'].isoformat()}")
    if not suspend_until(win["wake_at"]):
        audit("host_sleep_failed", {"slept_at": slept_at.isoformat()}, severity="warning")
        log("the machine did not go to sleep; see journalctl -k for the PM: lines")
        return 1
    after_resume(settings, slept_at, win["wake_at"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
