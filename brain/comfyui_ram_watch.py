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
import json
import logging
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

try:  # Flat import when brain/ is on sys.path (container runtime).
    from docker_utils import resolve_url
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.docker_utils import resolve_url

logger = logging.getLogger("brain.comfyui_ram_watch")

ENABLED_KEY = "comfyui_ram_recycle_enabled"
WATERMARK_GB_KEY = "comfyui_ram_recycle_watermark_gb"
COOLDOWN_MINUTES_KEY = "comfyui_ram_recycle_cooldown_minutes"
# Shared with the worker's comfyui video provider + reclaim rung, so the
# probe watches the exact server the renders use.
SERVER_URL_KEY = "video_comfyui_server_url"

DEFAULT_ENABLED = True
# Generous by design: a fresh post-render working set (weights staged for
# the fp8 14B pair) sits well below this, so only genuine cross-render
# accumulation crosses it. The 2026-08-26 incident was at 28.6 GB.
DEFAULT_WATERMARK_GB = 20.0
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_SERVER_URL = "http://comfyui:8188"

_CONTAINER = "poindexter-comfyui"
_SOURCE = "brain.comfyui_ram_watch"
_RECYCLED_KIND = "comfyui_ram_recycled"
_FAILED_KIND = "comfyui_ram_recycle_failed"
_DOCKER_RESTART_TIMEOUT_SECONDS = 60
_DOCKER_EXEC_TIMEOUT_SECONDS = 30
_API_TIMEOUT_SECONDS = 10

# Module-level cooldown stamp — persists across cycles so a low-set
# watermark can't bounce the sidecar every 5-minute brain cycle.
_last_recycle_monotonic: float | None = None


def _reset_recycle_state() -> None:
    """Test helper — wipe the cross-cycle cooldown stamp."""
    global _last_recycle_monotonic
    _last_recycle_monotonic = None


# --- app_settings reads (same pattern as postiz_queue_watch.py) -------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[COMFYUI_RAM] read %s failed: %s — default %r", key, exc, default
        )
        return default
    return default if val is None else val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_float(val: Any, default: float) -> float:
    if val is None:
        return default
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return default


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


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


# --- container memory read ---------------------------------------------------


def _kb_field(line: str) -> int | None:
    """``VmRSS:   14234232 kB`` -> 14234232."""
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except (TypeError, ValueError):
        return None


def _parse_status_rss_swap_gb(status_text: str) -> tuple[float, float] | None:
    """Parse ``VmRSS:``/``VmSwap:`` kB lines from a /proc/<pid>/status dump.

    ``VmSwap`` is absent on swapless kernels — that reads as 0, not a
    failure. A dump with no ``VmRSS`` is unusable and returns None.
    """
    rss_kb: int | None = None
    swap_kb: int | None = None
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            rss_kb = _kb_field(line)
        elif line.startswith("VmSwap:"):
            swap_kb = _kb_field(line)
    if rss_kb is None:
        return None
    return (rss_kb / 1024 / 1024, (swap_kb or 0) / 1024 / 1024)


def _read_container_main_rss_swap_gb(container: str) -> tuple[float, float] | None:
    """VmRSS + VmSwap of the container's PID 1, in GB.

    PID 1 IS the accumulator: the compose service runs an exec-form
    ``command: ["python", "main.py", ...]``, so the main ComfyUI python is
    the container's init, and ``/proc/1/status`` inside its namespace is
    exactly the process the 2026-08-26 incident measured. Returns None
    when the read fails (container absent, docker CLI missing,
    unparseable output) — the caller surfaces that as ``stats_failed``.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": _DOCKER_EXEC_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(
            ["docker", "exec", container, "cat", "/proc/1/status"], **kwargs
        )
    except FileNotFoundError:
        logger.warning("[COMFYUI_RAM] docker CLI not on PATH — cannot read RSS")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[COMFYUI_RAM] docker exec %s timed out", container)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[COMFYUI_RAM] mem read failed: %s: %s", type(exc).__name__, exc
        )
        return None
    if result.returncode != 0:
        logger.warning(
            "[COMFYUI_RAM] docker exec %s exit %s: %s",
            container, result.returncode, (result.stderr or "").strip()[:200],
        )
        return None
    return _parse_status_rss_swap_gb(result.stdout or "")


# --- restart + finding emission ---------------------------------------------


def _restart_comfyui_container(container: str) -> tuple[bool, str]:
    """``docker restart <container>`` (shape mirrors postiz_queue_watch)."""
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": _DOCKER_RESTART_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(["docker", "restart", container], **kwargs)
        if result.returncode == 0:
            return True, f"Restarted {container}"
        return False, (
            f"docker restart {container} exit {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"docker restart {container} timed out"
    except Exception as exc:  # noqa: BLE001
        return False, f"docker restart error: {type(exc).__name__}: {str(exc)[:160]}"


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
    """Write an audit_log ``finding`` row (Findings board + router).

    Shape mirrors ``utils/findings.py::emit_finding`` (the worker-side
    producer) so the Findings dashboard and ``findings_alert_router``
    treat brain findings identically: ``details={kind,title,body,
    dedup_key,extra}`` with severity on the row. ``info`` never routes
    (the router's severity floor) — board-visible only; ``warn`` routes
    per the ``findings.<kind>.*`` policy quad.
    """
    details = {
        "kind": kind,
        "title": title,
        "body": body,
        "dedup_key": dedup_key,
        "extra": extra,
    }
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ('finding', $1, $2::jsonb, $3)",
            _SOURCE,
            json.dumps(details),
            severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COMFYUI_RAM] finding insert (%s) failed: %s", kind, exc)


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
