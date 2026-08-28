"""ComfyUI host-RAM recycle watch (the RAM twin of the VRAM ghost class).

The ComfyUI render sidecar's main python (port 8188) accumulates host
memory across renders — observed 2026-08-26 at a 28.6 GB footprint
(13.9 GB RSS + 14.8 GB swap), filling the box's 47 GB swap. The
GPUScheduler reclaim ladder's ``_unload_comfyui`` rung (``POST /free``)
drops the loaded models and frees VRAM, but nothing recycles the
PROCESS — and per the #999 ghost lesson
(docs/architecture/video-render-vram-gate.md), only a process exit
returns that memory. A queue-idle ``docker restart poindexter-comfyui``
returned ~30 GB instantly; the sidecar lazy-reloads weights on the next
render, so an idle recycle costs one cold load and nothing else.

The worker container has no docker API access, so this lives brain-side
(the brain runs with the docker socket for exactly this class of
self-heal — see ``postiz_queue_watch.py``, whose shape this mirrors).

Per cycle:
1. ``comfyui_ram_recycle_enabled=false`` => disabled no-op.
2. ``GET {video_comfyui_server_url}/queue`` unreachable => no-op. The
   profile-gated sidecar simply isn't up on most installs (opt-in
   ``--profile comfyui``); and a hung-but-running sidecar is deliberately
   out of scope — this probe only ever restarts a sidecar it can VERIFY
   idle (poindexter#3094 posture: an unprovable queue is treated as
   busy, because bouncing a working renderer is worse than a fat one).
3. Anything in ``queue_running``/``queue_pending`` => busy no-op.
4. Within ``comfyui_ram_recycle_cooldown_minutes`` of the last recycle
   => cooldown no-op (restart-loop protection against a watermark set
   below the sidecar's healthy working set, mirroring
   docker_port_forward_probe's restart-cap posture).
5. PID 1's RSS+swap (``docker exec`` + ``/proc/1/status``) below
   ``comfyui_ram_recycle_watermark_gb`` => below-watermark no-op.
6. Above watermark: RE-CHECK ``/queue`` immediately before acting (a
   render may have been enqueued since step 3) — busy/unknown => defer
   to the next cycle.
7. ``docker restart poindexter-comfyui``. Success => emit a
   ``comfyui_ram_recycled`` finding (``info`` — Findings-board
   visibility, never pages) carrying the reclaimed footprint. Failure
   => a ``comfyui_ram_recycle_failed`` finding (``warn`` — routes per
   the ``findings.<kind>`` policy) so a broken lever can't fail silent.

Design parity with postiz_queue_watch: DB-configurable via app_settings,
injectable ``queue_fn`` / ``mem_fn`` / ``restart_fn`` / ``now_fn`` seams
for unit tests, module-level cooldown state, Probe-Protocol wrapper.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

# Shared with sidecar_ram_watch (poindexter#3360 generalisation): both probes
# read a container's PID-1 footprint, restart it, and record the outcome the
# same way — only the IDLE PROOF differs, and that stays per-module.
try:  # Flat import when brain/ is on sys.path (container runtime).
    from docker_utils import resolve_url
    from ram_recycle_common import (
        coerce_bool as _coerce_bool,
    )
    from ram_recycle_common import (
        coerce_float as _coerce_float,
    )
    from ram_recycle_common import (
        coerce_int as _coerce_int,
    )
    from ram_recycle_common import (
        emit_finding as _emit_finding_common,
    )
    from ram_recycle_common import (
        read_container_main_rss_swap_gb as _read_container_main_rss_swap_gb,
    )
    from ram_recycle_common import (
        read_setting as _read_setting,
    )
    from ram_recycle_common import (
        restart_container as _restart_comfyui_container,
    )
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.docker_utils import resolve_url
    from brain.ram_recycle_common import (
        coerce_bool as _coerce_bool,
    )
    from brain.ram_recycle_common import (
        coerce_float as _coerce_float,
    )
    from brain.ram_recycle_common import (
        coerce_int as _coerce_int,
    )
    from brain.ram_recycle_common import (
        emit_finding as _emit_finding_common,
    )
    from brain.ram_recycle_common import (
        read_container_main_rss_swap_gb as _read_container_main_rss_swap_gb,
    )
    from brain.ram_recycle_common import (
        read_setting as _read_setting,
    )
    from brain.ram_recycle_common import (
        restart_container as _restart_comfyui_container,
    )

logger = logging.getLogger("brain.comfyui_ram_watch")

ENABLED_KEY = "comfyui_ram_recycle_enabled"
WATERMARK_GB_KEY = "comfyui_ram_recycle_watermark_gb"
COOLDOWN_MINUTES_KEY = "comfyui_ram_recycle_cooldown_minutes"
# Shared with the worker's comfyui video provider + reclaim rung, so the
# probe watches the exact server the renders use.
SERVER_URL_KEY = "video_comfyui_server_url"

DEFAULT_ENABLED = True
# Sits in the VALLEY of a trimodal distribution, not at a round number.
# Measured over 7 days (2,001 samples of rss+swap, 2 GB bins):
#
#     0-4 GB   ###############  fresh / just-recycled   (31%)
#     4-12 GB  ##                                        (9%)
#    12-16 GB  ###############  normal post-render set  (32%)
#    16-18 GB  #                <-- the valley, 1.3%
#    20-30 GB  ##############   accumulated             (23%)
#
# 16 GB is the top of the normal working set and the floor of the valley, so
# an ordinary post-render footprint (12-16 GB) is left alone while genuine
# accumulation (20 GB+) is caught 4 GB earlier than the previous 20.0.
#
# Why it was lowered (2026-08-27): comfyui alone reached 29.4 GB, 63% of the
# host's 47 GB swap, on a box that hard-froze from swap exhaustion that day.
# The probe was already helping — time above 20 GB fell from 42-67%/day
# pre-probe to 1.6% on 08-27 — but stack#3409 removed ~10 spurious container
# recreations per week (a watchdog running `up -d` from the wrong checkout)
# that had been incidentally clearing this process for free. With that crutch
# gone, accumulation is expected to show up more, not less.
#
# Do NOT lower this into the 12-16 GB mode: the probe only recycles a
# verifiably idle queue, so it would not interrupt a render, but it WOULD
# recycle after most render batches and charge a cold model reload for the
# next one. The 60-minute cooldown caps the damage at one reload per hour,
# which is the backstop rather than the plan.
DEFAULT_WATERMARK_GB = 16.0
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_SERVER_URL = "http://comfyui:8188"

_CONTAINER = "poindexter-comfyui"
_SOURCE = "brain.comfyui_ram_watch"
_RECYCLED_KIND = "comfyui_ram_recycled"
_FAILED_KIND = "comfyui_ram_recycle_failed"
_API_TIMEOUT_SECONDS = 10

# Module-level cooldown stamp — persists across cycles so a low-set
# watermark can't bounce the sidecar every 5-minute brain cycle.
_last_recycle_monotonic: float | None = None


def _reset_recycle_state() -> None:
    """Test helper — wipe the cross-cycle cooldown stamp."""
    global _last_recycle_monotonic
    _last_recycle_monotonic = None


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": _coerce_bool(
            await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED
        ),
        "watermark_gb": _coerce_float(
            await _read_setting(pool, WATERMARK_GB_KEY, DEFAULT_WATERMARK_GB),
            DEFAULT_WATERMARK_GB,
        ),
        "cooldown_minutes": _coerce_int(
            await _read_setting(
                pool, COOLDOWN_MINUTES_KEY, DEFAULT_COOLDOWN_MINUTES
            ),
            DEFAULT_COOLDOWN_MINUTES,
        ),
    }


# --- ComfyUI queue state -----------------------------------------------------


async def _queue_busy(pool: Any) -> bool | None:
    """True = busy, False = verifiably idle, None = unreachable.

    Unparseable/odd JSON counts as busy — skipping one recycle is cheaper
    than killing a render we couldn't see (same posture as
    ``GPUScheduler._unload_comfyui``, poindexter#3094).
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx ships in the brain image
        logger.warning("[COMFYUI_RAM] httpx unavailable — cannot query ComfyUI")
        return None

    base = (
        await resolve_url(pool, SERVER_URL_KEY, default=DEFAULT_SERVER_URL)
    ).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_API_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{base}/queue")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        # silent-ok: the profile-gated sidecar being down is the common case
        # (opt-in `--profile comfyui`), not a fault — same posture as
        # GPUScheduler._unload_comfyui. The caller maps None to a
        # cycle-visible "unreachable" status, so this is not invisible.
        logger.debug("[COMFYUI_RAM] queue read failed (sidecar offline?): %s", exc)
        return None
    if not isinstance(data, dict):
        return True
    return bool(
        (data.get("queue_running") or []) or (data.get("queue_pending") or [])
    )


# --- finding emission --------------------------------------------------------


async def _emit_finding(
    pool: Any,
    *,
    kind: str,
    severity: str,
    title: str,
    body: str,
    dedup_key: str,
    extra: dict[str, Any],
) -> None:
    """Bind this module's ``source`` onto the shared finding writer."""
    await _emit_finding_common(
        pool,
        source=_SOURCE,
        kind=kind,
        severity=severity,
        title=title,
        body=body,
        dedup_key=dedup_key,
        extra=extra,
    )


# --- the probe ---------------------------------------------------------------


async def run_comfyui_ram_watch_probe(
    pool: Any,
    *,
    queue_fn: Callable[[], Awaitable[bool | None]] | None = None,
    mem_fn: Callable[[str], tuple[float, float] | None] | None = None,
    restart_fn: Callable[[str], tuple[bool, str]] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Single cycle of the ComfyUI host-RAM recycle watch.

    Args:
        pool: asyncpg pool for app_settings + audit_log.
        queue_fn: ``() -> True(busy) | False(idle) | None(unreachable)`` —
            defaults to the live ``GET /queue`` read. Called twice on the
            recycle path: once to qualify, once IMMEDIATELY before the
            restart (#3094 posture).
        mem_fn: ``(container) -> (rss_gb, swap_gb) | None`` — defaults to
            the ``docker exec … /proc/1/status`` read. Offloaded via
            ``asyncio.to_thread`` so the blocking subprocess never stalls
            the brain event loop.
        restart_fn: ``(container) -> (ok, msg)`` — defaults to the real
            ``docker restart``, also offloaded via ``asyncio.to_thread``.
        now_fn: monotonic clock for the cooldown stamp — defaults to
            ``time.monotonic``.
    """
    global _last_recycle_monotonic
    queue_fn = queue_fn or (lambda: _queue_busy(pool))
    mem_fn = mem_fn or _read_container_main_rss_swap_gb
    restart_fn = restart_fn or _restart_comfyui_container
    now_fn = now_fn or time.monotonic

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "detail": f"{ENABLED_KEY}=false"}

    busy = await queue_fn()
    if busy is None:
        return {
            "ok": True,
            "status": "unreachable",
            "detail": (
                "ComfyUI /queue unreachable — sidecar down or the opt-in "
                "profile isn't up; nothing to recycle"
            ),
        }
    if busy:
        return {
            "ok": True,
            "status": "busy",
            "detail": "render running/pending — declining (#3094 posture)",
        }

    cooldown_minutes = int(config["cooldown_minutes"])
    if _last_recycle_monotonic is not None and cooldown_minutes > 0:
        since_s = now_fn() - _last_recycle_monotonic
        if since_s < cooldown_minutes * 60.0:
            return {
                "ok": True,
                "status": "cooldown",
                "detail": (
                    f"last recycle {since_s / 60.0:.0f}m ago < "
                    f"{cooldown_minutes}m cooldown"
                ),
            }

    mem = await asyncio.to_thread(mem_fn, _CONTAINER)
    if mem is None:
        return {
            "ok": False,
            "status": "stats_failed",
            "detail": (
                "queue answered but the RSS+swap read failed — probe is "
                "blind to the footprint (docker exec access?)"
            ),
        }
    rss_gb, swap_gb = mem
    footprint_gb = rss_gb + swap_gb
    watermark_gb = float(config["watermark_gb"])
    if footprint_gb < watermark_gb:
        return {
            "ok": True,
            "status": "below_watermark",
            "detail": (
                f"{footprint_gb:.1f} GB (rss {rss_gb:.1f} + swap "
                f"{swap_gb:.1f}) < {watermark_gb:g} GB"
            ),
            "rss_gb": round(rss_gb, 2),
            "swap_gb": round(swap_gb, 2),
            "footprint_gb": round(footprint_gb, 2),
            "watermark_gb": watermark_gb,
        }

    # Above watermark. Re-check the queue IMMEDIATELY before acting: a
    # render may have been enqueued while we read /proc (#3094 — never
    # bounce a working renderer; an unknown queue counts as busy).
    busy = await queue_fn()
    if busy is None or busy:
        return {
            "ok": True,
            "status": "busy_at_recheck",
            "detail": (
                "queue became busy/unknown between qualify and restart — "
                "deferred to the next cycle"
            ),
        }

    ok, msg = await asyncio.to_thread(restart_fn, _CONTAINER)
    if not ok:
        detail = (
            f"ComfyUI RSS+swap at {footprint_gb:.1f} GB (>= {watermark_gb:g} "
            f"GB watermark) but the recycle restart failed: {msg}"
        )
        logger.warning("[COMFYUI_RAM] %s", detail)
        await _emit_finding(
            pool,
            kind=_FAILED_KIND,
            severity="warn",
            title="ComfyUI RAM recycle failed — docker restart error",
            body=(
                f"{detail}. The sidecar keeps its footprint until a restart "
                f"succeeds; check docker socket access from the brain "
                f"container. The probe retries every cycle."
            ),
            dedup_key=f"{_FAILED_KIND}:{_CONTAINER}",
            extra={
                "container": _CONTAINER,
                "error": msg,
                "rss_gb": round(rss_gb, 2),
                "swap_gb": round(swap_gb, 2),
                "footprint_gb": round(footprint_gb, 2),
                "watermark_gb": watermark_gb,
            },
        )
        return {"ok": False, "status": "restart_failed", "detail": msg}

    _last_recycle_monotonic = now_fn()
    detail = (
        f"recycled {_CONTAINER} at {footprint_gb:.1f} GB RSS+swap "
        f"(rss {rss_gb:.1f} + swap {swap_gb:.1f}, watermark {watermark_gb:g})"
    )
    logger.info("[COMFYUI_RAM] %s", detail)
    await _emit_finding(
        pool,
        kind=_RECYCLED_KIND,
        severity="info",
        title=(
            f"ComfyUI RAM recycled: {footprint_gb:.1f} GB "
            f"(rss {rss_gb:.1f} + swap {swap_gb:.1f})"
        ),
        body=(
            f"The ComfyUI sidecar's main python crossed the "
            f"{watermark_gb:g} GB RSS+swap watermark with a verifiably "
            f"idle queue (checked twice, immediately before the restart — "
            f"poindexter#3094 posture), so the brain restarted "
            f"{_CONTAINER} to return the memory to the host. Weights "
            f"lazy-reload on the next render. Tune via app_settings."
            f"{WATERMARK_GB_KEY} / {COOLDOWN_MINUTES_KEY}; disable via "
            f"{ENABLED_KEY}."
        ),
        dedup_key=f"{_RECYCLED_KIND}:{_CONTAINER}",
        extra={
            "container": _CONTAINER,
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
        "rss_gb": round(rss_gb, 2),
        "swap_gb": round(swap_gb, 2),
        "footprint_gb": round(footprint_gb, 2),
        "watermark_gb": watermark_gb,
    }


class ComfyUIRamWatchProbe:
    """Probe-Protocol wrapper (mirrors PostizQueueWatchProbe)."""

    name: str = "comfyui_ram_watch"
    description: str = (
        "Watches the ComfyUI render sidecar's PID-1 RSS+swap and `docker "
        "restart`s poindexter-comfyui above the watermark — only with a "
        "verifiably idle /queue, re-checked immediately before the restart "
        "(#3094 posture). Emits comfyui_ram_recycled (info) per recycle."
    )
    interval_seconds: int = 300

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_comfyui_ram_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", summary.get("status", "")),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
