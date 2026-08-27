"""Host-RAM recycle watch for the queue-less GPU model sidecars.

The generalisation of ``comfyui_ram_watch`` (poindexter#3360) forced by the
**2026-08-27 hard-reboot freeze**. That probe works and stays — but it is
hardcoded to ``poindexter-comfyui``, and ComfyUI was *not* what filled the
swap that day. The four sidecars that did had no watcher at all:

    chatterbox     1.2 GB RSS +  8.9 GB swap = 10.1 GB
    wan-server     0.01        +  7.6        =  7.6
    speaches       0.6         +  7.4        =  8.0
    stable-audio   0.01        +  3.9        =  3.9
                                              ------
                                              ~29 GB parked while DORMANT

With swap 100% full, one Ollama model load tipped the box into a reclaim
livelock that no OOM killer could break (see
docs/operations/host-oom-protection.md). systemd-oomd now guarantees the box
stays reachable; this probe attacks the cause, so oomd rarely has to fire.

**Why this can't just reuse the ComfyUI probe:** that one proves idleness with
``GET /queue``. These four expose only ``/health`` — liveness, not idleness —
so the idle proof had to be rebuilt from two gates:

1. **The GPU advisory lock is free** (``sidecar_ram_recycle_require_gpu_lock_free``,
   default on). Every GPU session in the stack holds
   ``pg_advisory_lock(7_777_777_777)`` for its duration (gpu_scheduler.py), so
   a free lock means no inference is in flight anywhere — one authoritative
   system-wide signal rather than four bespoke ones.
2. **The container's CPU is below the idle threshold.** The target-specific
   gate; catches work that never took the GPU lock (a direct HTTP call to a
   sidecar, a warm-up, a model load).

Gate 1 is global and therefore blunt — it blocks a chatterbox recycle while
ComfyUI renders, which are unrelated. Sampled during a busy render window on
2026-08-27 the lock was free only **7% of the time**, so under sustained load
(exactly when footprints grow) this probe can defer for hours. It still
defaults ON, because gate 2 alone is not sufficient: a sidecar blocked on a
CUDA sync mid-inference sits under the CPU threshold and looks idle, and
restarting it there kills a live render. Turn gate 1 off only if the Findings
board shows chronic deferral while swap climbs.

Whatever gates run are evaluated immediately before the restart, and
**anything unprovable counts as busy** — a failed lock query or an unreadable
CPU stat defers the recycle. That is the poindexter#3094 posture: bouncing a
working renderer is strictly worse than carrying a fat one for another cycle.

At most ONE container is recycled per cycle (the fattest over-watermark one)
to bound the blast radius; cooldowns are per-container so each recovers
independently.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

try:  # Flat import when brain/ is on sys.path (container runtime).
    from ram_recycle_common import (
        coerce_bool,
        coerce_float,
        coerce_int,
        emit_finding,
        read_container_cpu_percent,
        read_container_main_rss_swap_gb,
        read_setting,
        restart_container,
    )
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.ram_recycle_common import (
        coerce_bool,
        coerce_float,
        coerce_int,
        emit_finding,
        read_container_cpu_percent,
        read_container_main_rss_swap_gb,
        read_setting,
        restart_container,
    )

logger = logging.getLogger("brain.sidecar_ram_watch")

ENABLED_KEY = "sidecar_ram_recycle_enabled"
TARGETS_KEY = "sidecar_ram_recycle_targets"
COOLDOWN_MINUTES_KEY = "sidecar_ram_recycle_cooldown_minutes"
CPU_IDLE_PERCENT_KEY = "sidecar_ram_recycle_cpu_idle_percent"
REQUIRE_GPU_LOCK_FREE_KEY = "sidecar_ram_recycle_require_gpu_lock_free"

DEFAULT_ENABLED = True
# `container:watermark_gb` CSV. Every watermark must sit BELOW that sidecar's
# 2026-08-27 footprint — otherwise this probe watches the incident it was
# written for and does nothing. Incident vs watermark:
#
#     chatterbox    10.1 GB  >  6    speaches      8.0 GB  >  6
#     wan-server     7.6 GB  >  6    stable-audio  3.9 GB  >  3
#
# stable-audio is 3, not 4: at 4 its own incident footprint (3.9) would have
# stayed under the line. Pinned by test_incident_footprints_would_have_tripped.
# Idle working sets are ~0.04-1.2 GB, so there is ample headroom above normal.
DEFAULT_TARGETS = (
    "poindexter-chatterbox:6,"
    "poindexter-speaches:6,"
    "poindexter-wan-server:6,"
    "poindexter-stable-audio:3"
)
DEFAULT_COOLDOWN_MINUTES = 60
# Measured idle draw for these four is 0.09-1.21%; 5% leaves headroom for
# background threads without reading active inference as idle.
DEFAULT_CPU_IDLE_PERCENT = 5.0
# On by default: the CPU check alone can read a CUDA-blocked sidecar as idle.
# See _prove_idle for the responsiveness trade-off this controls.
DEFAULT_REQUIRE_GPU_LOCK_FREE = True

# gpu_scheduler.GPU_ADVISORY_LOCK_KEY. Duplicated rather than imported: the
# brain daemon deliberately does not import the worker package (it runs on
# asyncpg alone). Pinned against the real constant by a unit test.
GPU_ADVISORY_LOCK_KEY = 7_777_777_777

_SOURCE = "brain.sidecar_ram_watch"
_RECYCLED_KIND = "sidecar_ram_recycled"
_FAILED_KIND = "sidecar_ram_recycle_failed"

# Per-container cooldown stamps — persist across cycles so a watermark set
# below a sidecar's healthy working set can't bounce it every brain cycle.
_last_recycle_monotonic: dict[str, float] = {}


def _reset_recycle_state() -> None:
    """Test helper — wipe the cross-cycle cooldown stamps."""
    _last_recycle_monotonic.clear()


def parse_targets(raw: Any) -> list[tuple[str, float]]:
    """``"a:6, b:4"`` -> ``[("a", 6.0), ("b", 4.0)]``.

    Malformed entries are skipped with a warning rather than failing the whole
    probe — one fat-fingered entry should not disarm the other three. An entry
    with no watermark is skipped too: defaulting it would invent a threshold
    the operator never chose (feedback_no_silent_defaults).
    """
    out: list[tuple[str, float]] = []
    for chunk in str(raw or "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        if ":" not in entry:
            logger.warning(
                "[SIDECAR_RAM] target %r has no :watermark_gb — skipping", entry
            )
            continue
        name, _, wm = entry.rpartition(":")
        name = name.strip()
        try:
            watermark = float(wm.strip())
        except (TypeError, ValueError):
            logger.warning(
                "[SIDECAR_RAM] target %r watermark unparseable — skipping", entry
            )
            continue
        if not name or watermark <= 0:
            logger.warning("[SIDECAR_RAM] target %r invalid — skipping", entry)
            continue
        out.append((name, watermark))
    return out


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": coerce_bool(
            await read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED
        ),
        "targets": parse_targets(
            await read_setting(pool, TARGETS_KEY, DEFAULT_TARGETS)
        ),
        "cooldown_minutes": coerce_int(
            await read_setting(pool, COOLDOWN_MINUTES_KEY, DEFAULT_COOLDOWN_MINUTES),
            DEFAULT_COOLDOWN_MINUTES,
        ),
        "cpu_idle_percent": coerce_float(
            await read_setting(pool, CPU_IDLE_PERCENT_KEY, DEFAULT_CPU_IDLE_PERCENT),
            DEFAULT_CPU_IDLE_PERCENT,
        ),
        "require_gpu_lock_free": coerce_bool(
            await read_setting(pool, REQUIRE_GPU_LOCK_FREE_KEY, "true"),
            DEFAULT_REQUIRE_GPU_LOCK_FREE,
        ),
    }


# --- idle gates --------------------------------------------------------------


async def gpu_lock_held(pool: Any) -> bool | None:
    """True = a GPU session is in flight, False = free, None = unknown.

    ``pg_advisory_lock(bigint)`` splits the key across ``pg_locks.classid``
    (high 32 bits) and ``objid`` (low 32 bits) with ``objsubid = 1``; the shift
    reassembles it rather than hardcoding the halves, which would silently stop
    matching if the key ever changed.
    """
    try:
        held = await pool.fetchval(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_locks"
            "  WHERE locktype = 'advisory' AND objsubid = 1"
            "    AND ((classid::bigint << 32) | objid::bigint) = $1"
            ")",
            GPU_ADVISORY_LOCK_KEY,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SIDECAR_RAM] GPU lock query failed: %s", exc)
        return None
    return bool(held)


async def _prove_idle(
    pool: Any,
    container: str,
    *,
    cpu_idle_percent: float,
    require_gpu_lock_free: bool,
    gpu_lock_fn: Callable[[], Awaitable[bool | None]],
    cpu_fn: Callable[[str], float | None],
) -> tuple[bool, str]:
    """``(is_idle, why_not)``. Unprovable counts as NOT idle, by design.

    The GPU gate is GLOBAL and therefore strict: it blocks a chatterbox
    recycle while ComfyUI renders, even though those are unrelated. Measured
    on 2026-08-27 during a busy render window, the lock was free only **7%**
    of the time — strict enough that during a sustained busy stretch (exactly
    when footprints grow) the probe can defer for hours.

    It defaults on anyway, because the per-container CPU check alone is not
    sufficient: a sidecar blocked on a CUDA sync mid-inference can sit under
    the CPU threshold and look idle, and restarting it there kills a live
    render. `require_gpu_lock_free=false` trades that safety for
    responsiveness — reach for it only if the Findings board shows this probe
    chronically deferring while swap climbs.
    """
    if require_gpu_lock_free:
        held = await gpu_lock_fn()
        if held is None:
            return False, "GPU lock state unknown"
        if held:
            return False, "a GPU session holds the scheduler lock"
    cpu = await asyncio.to_thread(cpu_fn, container)
    if cpu is None:
        return False, f"{container} CPU unreadable"
    if cpu >= cpu_idle_percent:
        return False, f"{container} CPU {cpu:.1f}% >= {cpu_idle_percent:g}%"
    return True, ""


# --- the probe ---------------------------------------------------------------


async def run_sidecar_ram_watch_probe(
    pool: Any,
    *,
    gpu_lock_fn: Callable[[], Awaitable[bool | None]] | None = None,
    cpu_fn: Callable[[str], float | None] | None = None,
    mem_fn: Callable[[str], tuple[float, float] | None] | None = None,
    restart_fn: Callable[[str], tuple[bool, str]] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Single cycle of the sidecar host-RAM recycle watch."""
    gpu_lock_fn = gpu_lock_fn or (lambda: gpu_lock_held(pool))
    cpu_fn = cpu_fn or read_container_cpu_percent
    mem_fn = mem_fn or read_container_main_rss_swap_gb
    restart_fn = restart_fn or restart_container
    now_fn = now_fn or time.monotonic

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "detail": f"{ENABLED_KEY}=false"}

    targets = config["targets"]
    if not targets:
        return {
            "ok": True,
            "status": "no_targets",
            "detail": f"{TARGETS_KEY} is empty or unparseable — nothing watched",
        }

    cooldown_minutes = int(config["cooldown_minutes"])
    cpu_idle_percent = float(config["cpu_idle_percent"])

    # Measure every target first, then act on the single fattest offender.
    over: list[tuple[float, str, float, float, float]] = []
    skipped: list[str] = []
    for container, watermark_gb in targets:
        stamp = _last_recycle_monotonic.get(container)
        if stamp is not None and cooldown_minutes > 0:
            since_s = now_fn() - stamp
            if since_s < cooldown_minutes * 60.0:
                skipped.append(
                    f"{container}: cooldown ({since_s / 60.0:.0f}m < "
                    f"{cooldown_minutes}m)"
                )
                continue
        mem = await asyncio.to_thread(mem_fn, container)
        if mem is None:
            # Not a failure of the probe: these sidecars are profile-gated and
            # simply absent on most installs.
            skipped.append(f"{container}: footprint unreadable (down?)")
            continue
        rss_gb, swap_gb = mem
        footprint_gb = rss_gb + swap_gb
        if footprint_gb < watermark_gb:
            continue
        over.append((footprint_gb, container, rss_gb, swap_gb, watermark_gb))

    if not over:
        return {
            "ok": True,
            "status": "below_watermark",
            "detail": (
                f"{len(targets)} target(s) under watermark"
                + (f"; {'; '.join(skipped)}" if skipped else "")
            ),
            "targets": len(targets),
            "skipped": skipped,
        }

    over.sort(reverse=True)
    footprint_gb, container, rss_gb, swap_gb, watermark_gb = over[0]

    # Prove idle IMMEDIATELY before acting — never on the stale read above.
    idle, why_not = await _prove_idle(
        pool,
        container,
        cpu_idle_percent=cpu_idle_percent,
        require_gpu_lock_free=bool(config["require_gpu_lock_free"]),
        gpu_lock_fn=gpu_lock_fn,
        cpu_fn=cpu_fn,
    )
    if not idle:
        return {
            "ok": True,
            "status": "busy",
            "detail": (
                f"{container} at {footprint_gb:.1f} GB is over its "
                f"{watermark_gb:g} GB watermark but is not provably idle "
                f"({why_not}) — deferred to the next cycle"
            ),
            "container": container,
            "footprint_gb": round(footprint_gb, 2),
        }

    ok, msg = await asyncio.to_thread(restart_fn, container)
    if not ok:
        detail = (
            f"{container} RSS+swap at {footprint_gb:.1f} GB (>= "
            f"{watermark_gb:g} GB watermark) but the recycle restart failed: {msg}"
        )
        logger.warning("[SIDECAR_RAM] %s", detail)
        await emit_finding(
            pool,
            source=_SOURCE,
            kind=_FAILED_KIND,
            severity="warn",
            title=f"Sidecar RAM recycle failed — {container}",
            body=(
                f"{detail}. The sidecar keeps its footprint until a restart "
                f"succeeds; check docker socket access from the brain "
                f"container. The probe retries every cycle."
            ),
            dedup_key=f"{_FAILED_KIND}:{container}",
            extra={
                "container": container,
                "error": msg,
                "rss_gb": round(rss_gb, 2),
                "swap_gb": round(swap_gb, 2),
                "footprint_gb": round(footprint_gb, 2),
                "watermark_gb": watermark_gb,
            },
        )
        return {"ok": False, "status": "restart_failed", "detail": msg}

    _last_recycle_monotonic[container] = now_fn()
    detail = (
        f"recycled {container} at {footprint_gb:.1f} GB RSS+swap "
        f"(rss {rss_gb:.1f} + swap {swap_gb:.1f}, watermark {watermark_gb:g})"
    )
    logger.info("[SIDECAR_RAM] %s", detail)
    await emit_finding(
        pool,
        source=_SOURCE,
        kind=_RECYCLED_KIND,
        severity="info",
        title=(
            f"Sidecar RAM recycled: {container} {footprint_gb:.1f} GB "
            f"(rss {rss_gb:.1f} + swap {swap_gb:.1f})"
        ),
        body=(
            f"{container} crossed its {watermark_gb:g} GB RSS+swap watermark "
            f"while provably idle (GPU scheduler lock free AND container CPU "
            f"under {cpu_idle_percent:g}%, both re-checked immediately before "
            f"the restart), so the brain restarted it to return the memory to "
            f"the host. Weights lazy-reload on next use. This is the cause-side "
            f"companion to systemd-oomd (2026-08-27 freeze). Tune via "
            f"app_settings.{TARGETS_KEY} / {COOLDOWN_MINUTES_KEY} / "
            f"{CPU_IDLE_PERCENT_KEY}; disable via {ENABLED_KEY}."
        ),
        dedup_key=f"{_RECYCLED_KIND}:{container}",
        extra={
            "container": container,
            "rss_gb": round(rss_gb, 2),
            "swap_gb": round(swap_gb, 2),
            "footprint_gb": round(footprint_gb, 2),
            "watermark_gb": watermark_gb,
        },
    )
    return {
        "ok": True,
        "status": "recycled",
        "detail": detail,
        "container": container,
        "rss_gb": round(rss_gb, 2),
        "swap_gb": round(swap_gb, 2),
        "footprint_gb": round(footprint_gb, 2),
        "watermark_gb": watermark_gb,
    }


class SidecarRamWatchProbe:
    """Probe-Protocol wrapper (mirrors ComfyUIRamWatchProbe)."""

    name: str = "sidecar_ram_watch"
    description: str = (
        "Watches the queue-less GPU sidecars' PID-1 RSS+swap and `docker "
        "restart`s the fattest over-watermark one — only when the GPU "
        "scheduler lock is free AND its CPU is idle, both re-checked "
        "immediately before the restart. Emits sidecar_ram_recycled (info)."
    )
    interval_seconds: int = 300

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_sidecar_ram_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", summary.get("status", "")),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
