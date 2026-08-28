"""Recycle an ollama llama-server runner whose host memory has leaked past a watermark.

WHY THIS IS NOT PART OF ``sidecar_ram_watch``
---------------------------------------------
That probe recycles **docker containers** — it measures with ``docker exec`` and
acts with ``docker restart``. Ollama runs as a **host systemd unit** here
(``ollama-vision.service`` / ``ollama-primary.service``), so neither half
reaches it: cadvisor's ``container_memory_*`` never covers a host unit, and this
daemon runs in its own cgroup and pid namespaces, so it cannot read the unit's
cgroup files or ``/proc/<pid>/status`` either.

So the two halves come from elsewhere:

* **Measure** — ``scripts/nvidia-smi-exporter.py`` runs with ``pid: host`` and
  publishes ``ollama_runner_anon_bytes{unit=...}``. The brain already scrapes
  that exporter for wall power, so this adds no new dependency.
* **Act** — ollama's own HTTP API. ``keep_alive: 0`` terminates the runner
  process, which is what actually returns the memory; a follow-up load with
  ``keep_alive: -1`` restores the pin. **Restarting the systemd unit is neither
  possible from here nor necessary.**

WHAT IT IS FIXING (measured 2026-08-28, poindexter#3434)
--------------------------------------------------------
The runner leaks ~6.9 MiB per request, dead linear, no plateau. A fresh runner
holds 0.30 GiB; one that had served ~5.5h held **9.35 GiB**, all of it swapped
out and never touched again. That single process was the largest holder of the
host's swap and is what saturated the zram fast tier.

The metric is ``RssAnon + VmSwap``, never RSS alone: the kernel evicts an
untouched leak within minutes, so ``VmRSS`` falls to ~70 MiB and the process
reads as innocent while holding 9 GiB.

THE COST OF A RECYCLE IS REAL
-----------------------------
Reloading a 30B model takes ~85s, during which requests to that endpoint fail.
That is why this is watermark-gated and idle-gated rather than a periodic timer,
and why the reload is issued eagerly instead of waiting for the next caller to
pay the latency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

try:  # Flat import when brain/ is on sys.path (container runtime).
    from ram_recycle_common import (
        coerce_bool,
        coerce_float,
        coerce_int,
        emit_finding,
        read_setting,
    )
except ImportError:  # pragma: no cover - package-style import
    from brain.ram_recycle_common import (
        coerce_bool,
        coerce_float,
        coerce_int,
        emit_finding,
        read_setting,
    )

ENABLED_KEY = "ollama_runner_ram_recycle_enabled"
TARGETS_KEY = "ollama_runner_ram_recycle_targets"
COOLDOWN_MINUTES_KEY = "ollama_runner_ram_recycle_cooldown_minutes"
CPU_IDLE_PERCENT_KEY = "ollama_runner_ram_recycle_cpu_idle_percent"
REQUIRE_GPU_LOCK_FREE_KEY = "ollama_runner_ram_recycle_require_gpu_lock_free"
EXPORTER_URL_KEY = "ollama_runner_ram_recycle_exporter_url"

# OFF by default. Every other install's ollama layout differs (one instance, a
# container, a remote host), and a probe that restarts someone's LLM endpoint
# uninvited is a bad default. Matt's operator stack opts in.
DEFAULT_ENABLED = False

# `unit|watermark_gb|endpoint|model`, entries comma-separated. Fields are
# PIPE-delimited, not colon-delimited like sidecar_ram_recycle_targets, because
# two of the four fields carry their own colons: the endpoint is a URL
# (`http://host:11435`) and the model carries a tag (`qwen3-vl:30b`). Colons
# make the split genuinely ambiguous — a right-split reads the model as "30b"
# — and no amount of clever splitting fixes it, so the delimiter changes.
# 4 GB sits well above a fresh runner (0.30 GiB) and well below the 9.35 GiB
# incident, so it catches the leak long before the fast swap tier notices.
DEFAULT_TARGETS = (
    "ollama-vision.service|4|http://host.docker.internal:11435|qwen3-vl:30b"
)

DEFAULT_COOLDOWN_MINUTES = 120
# A loaded-but-idle runner sits at 0.0%; one mid-generation pegs a core.
DEFAULT_CPU_IDLE_PERCENT = 5.0
DEFAULT_REQUIRE_GPU_LOCK_FREE = True
DEFAULT_EXPORTER_URL = ""  # empty = derive from the runtime (docker vs host)

GPU_ADVISORY_LOCK_KEY = 7_777_777_777
_HTTP_TIMEOUT_SECONDS = 15
_RELOAD_TIMEOUT_SECONDS = 420  # a 30B reload is ~85s; leave slack for a cold page cache

_SOURCE = "brain.ollama_runner_ram_watch"
_RECYCLED_KIND = "ollama_runner_ram_recycled"
_FAILED_KIND = "ollama_runner_ram_recycle_failed"

_last_recycle_monotonic: dict[str, float] = {}

_ANON_RE = re.compile(r'^ollama_runner_anon_bytes\{unit="([^"]+)"\}\s+([0-9.eE+-]+)\s*$')
_CPU_RE = re.compile(r'^ollama_runner_cpu_percent\{unit="([^"]+)"\}\s+([0-9.eE+-]+)\s*$')


def _reset_recycle_state() -> None:
    """Test seam — clears the per-unit cooldown stamps."""
    _last_recycle_monotonic.clear()


def _require_http_url(url: str, what: str) -> str:
    """Reject anything but http/https before it reaches ``urlopen``.

    Both URLs here come from ``app_settings``, so this is operator input, not
    attacker input — but ``urlopen`` honours ``file://`` and other schemes, and
    a typo that silently reads a local file (then parses it as Prometheus text,
    or POSTs a recycle at it) is a confusing failure rather than a loud one.
    Constraining the scheme makes the failure explicit and satisfies bandit
    B310 in substance rather than by annotation.
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"{what} must be an http(s) URL, got {url!r}")
    return url


def default_exporter_url() -> str:
    """The gpu-exporter, reached the way brain_daemon already reaches it."""
    in_docker = os.path.exists("/.dockerenv")
    host = "host.docker.internal" if in_docker else "localhost"
    return f"http://{host}:9835/metrics"


def parse_targets(raw: Any) -> list[tuple[str, float, str, str]]:
    """Parse ``unit|watermark_gb|endpoint|model`` CSV entries.

    Pipe-delimited on purpose. Two fields carry their own colons — the endpoint
    is a URL and the model carries a ``:tag`` — so a colon split is ambiguous
    no matter which end you split from: ``a:4:http://h:11435:qwen3-vl:30b``
    right-splits to model ``30b``, endpoint ``http://h:11435:qwen3-vl``. Both
    look plausible and neither is right.
    """
    if raw is None:
        return []
    out: list[tuple[str, float, str, str]] = []
    for entry in str(raw).split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [f.strip() for f in entry.split("|")]
        if len(parts) != 4:
            logger.warning(
                "[OLLAMA_RAM] target %r is not unit|watermark_gb|endpoint|model "
                "— skipping",
                entry,
            )
            continue
        unit, watermark_raw, endpoint, model = parts
        try:
            watermark = float(watermark_raw)
        except ValueError:
            logger.warning(
                "[OLLAMA_RAM] target %r watermark unparseable — skipping", entry
            )
            continue
        if not unit or not endpoint or not model or watermark <= 0:
            logger.warning("[OLLAMA_RAM] target %r is incomplete — skipping", entry)
            continue
        out.append((unit, watermark, endpoint, model))
    return out


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": coerce_bool(
            await read_setting(pool, ENABLED_KEY, "false"), DEFAULT_ENABLED
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
        "exporter_url": str(
            await read_setting(pool, EXPORTER_URL_KEY, DEFAULT_EXPORTER_URL) or ""
        ).strip()
        or default_exporter_url(),
    }


# --- measurement -------------------------------------------------------------


def parse_runner_stats(exposition: str) -> dict[str, dict[str, float]]:
    """``{unit: {"anon_gb": .., "cpu_percent": ..}}`` from the exporter's text.

    ``cpu_percent`` is absent on the exporter's very first scrape after start —
    it is a rate and needs two samples. Absent is left absent rather than
    defaulted to 0.0, because 0.0 reads as "idle, safe to recycle" and would
    let the probe recycle a busy runner during the exporter's first interval.
    """
    out: dict[str, dict[str, float]] = {}
    for line in exposition.splitlines():
        line = line.strip()
        for pattern, field, scale in (
            (_ANON_RE, "anon_gb", 1024**3),
            (_CPU_RE, "cpu_percent", 1),
        ):
            match = pattern.match(line)
            if not match:
                continue
            try:
                out.setdefault(match.group(1), {})[field] = float(match.group(2)) / scale
            except ValueError:
                logger.warning("[OLLAMA_RAM] unparseable metric line: %r", line)
            break
    return out


def read_runner_stats(exporter_url: str) -> dict[str, dict[str, float]] | None:
    """Scrape the gpu-exporter. ``None`` = unreachable (NOT 'nothing running')."""
    try:
        _require_http_url(exporter_url, "exporter_url")
        with urllib.request.urlopen(  # nosec B310 - scheme constrained to http(s) above
            exporter_url, timeout=_HTTP_TIMEOUT_SECONDS
        ) as resp:
            return parse_runner_stats(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("[OLLAMA_RAM] exporter %s unreadable: %s", exporter_url, exc)
        return None


# --- idle gate ---------------------------------------------------------------


async def gpu_lock_held(pool: Any) -> bool | None:
    """True = a GPU session is in flight, False = free, None = unknown.

    Same reassembly as sidecar_ram_watch: ``pg_advisory_lock(bigint)`` splits
    the key across classid/objid, so shift rather than hardcoding halves.
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
        logger.warning("[OLLAMA_RAM] GPU lock query failed: %s", exc)
        return None
    return bool(held)


async def _prove_idle(
    pool: Any,
    unit: str,
    stats: dict[str, float],
    *,
    cpu_idle_percent: float,
    require_gpu_lock_free: bool,
    gpu_lock_fn: Callable[[], Awaitable[bool | None]],
) -> tuple[bool, str]:
    """``(is_idle, why_not)``. Unprovable counts as NOT idle, by design (#3094).

    Two gates, same shape as sidecar_ram_watch. The GPU advisory lock is the
    load-bearing one: every QA rail that calls this endpoint holds it, so a
    free lock means no rail is mid-call. It is global and blunt — measured free
    only ~7% of the time during a busy render window (2026-08-27) — so this
    probe can defer for hours under load. That is the intended trade: a recycle
    costs an ~85s reload, but a recycle DURING a QA pass costs that rail its
    answer.

    The runner's own CPU is the second gate, and it is not redundant: ollama
    serves requests that never take the GPU scheduler lock (a warm-up ping, a
    direct curl), and those peg the runner without touching the lock.

    ``/api/ps`` is deliberately NOT used here. It reports which model is
    LOADED, not whether it is generating, so it answers a different question
    and would read "idle" mid-inference.
    """
    if require_gpu_lock_free:
        held = await gpu_lock_fn()
        if held is None:
            return False, "GPU lock state unknown"
        if held:
            return False, "a GPU session holds the scheduler lock"
    cpu = stats.get("cpu_percent")
    if cpu is None:
        return False, f"{unit} runner CPU unknown (exporter needs two scrapes)"
    if cpu >= cpu_idle_percent:
        return False, f"{unit} runner CPU {cpu:.1f}% >= {cpu_idle_percent:g}%"
    return True, ""


# --- the recycle -------------------------------------------------------------


def recycle_runner(endpoint: str, model: str) -> tuple[bool, str]:
    """Unload then re-pin. Unloading is what frees the memory.

    ``keep_alive: 0`` makes ollama terminate the runner process; the leaked
    anonymous pages die with it. The reload is issued eagerly so the ~85s cost
    lands here rather than on whichever QA rail calls next, and it re-pins with
    ``keep_alive: -1`` so the model stays resident as the placement doctrine
    intends.
    """

    def _post(body: dict[str, Any], timeout: int) -> None:
        req = urllib.request.Request(
            f"{endpoint}/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(  # nosec B310 - scheme constrained to http(s) above
            req, timeout=timeout
        ) as resp:
            resp.read()

    try:
        _require_http_url(endpoint, "target endpoint")
        _post({"model": model, "keep_alive": 0}, _HTTP_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"unload failed: {exc}"
    try:
        _post(
            {"model": model, "prompt": "", "stream": False, "keep_alive": -1},
            _RELOAD_TIMEOUT_SECONDS,
        )
    except (urllib.error.URLError, OSError) as exc:
        # The memory IS freed at this point — the unload succeeded. Report the
        # partial outcome loudly rather than as a failure that implies nothing
        # happened; the next caller will load the model on demand.
        return True, f"unloaded, but re-pin failed ({exc}) — will load on demand"
    return True, "unloaded and re-pinned"


# --- the probe ---------------------------------------------------------------


async def run_ollama_runner_ram_watch_probe(
    pool: Any,
    *,
    gpu_lock_fn: Callable[[], Awaitable[bool | None]] | None = None,
    mem_fn: Callable[[str], dict[str, dict[str, float]] | None] | None = None,
    recycle_fn: Callable[[str, str], tuple[bool, str]] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Single cycle of the ollama-runner host-RAM recycle watch."""
    gpu_lock_fn = gpu_lock_fn or (lambda: gpu_lock_held(pool))
    mem_fn = mem_fn or read_runner_stats
    recycle_fn = recycle_fn or recycle_runner
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

    measured = await asyncio.to_thread(mem_fn, config["exporter_url"])
    if measured is None:
        return {
            "ok": False,
            "status": "exporter_unreachable",
            "detail": (
                f"gpu-exporter at {config['exporter_url']} unreadable — cannot "
                f"tell a healthy runner from a leaking one, so nothing is recycled"
            ),
        }

    cooldown_minutes = int(config["cooldown_minutes"])
    over: list[tuple[float, str, float, str, str, dict[str, float]]] = []
    skipped: list[str] = []

    for unit, watermark_gb, endpoint, model in targets:
        stamp = _last_recycle_monotonic.get(unit)
        if stamp is not None and cooldown_minutes > 0:
            since_s = now_fn() - stamp
            if since_s < cooldown_minutes * 60.0:
                skipped.append(
                    f"{unit}: cooldown ({since_s / 60.0:.0f}m < {cooldown_minutes}m)"
                )
                continue
        stats = measured.get(unit)
        anon_gb = (stats or {}).get("anon_gb")
        if anon_gb is None:
            skipped.append(f"{unit}: no runner (not loaded)")
            continue
        if anon_gb < watermark_gb:
            skipped.append(f"{unit}: {anon_gb:.1f} GB < {watermark_gb:g} GB")
            continue
        over.append((anon_gb, unit, watermark_gb, endpoint, model, stats or {}))

    if not over:
        return {
            "ok": True,
            "status": "under_watermark",
            "detail": "; ".join(skipped) or "nothing over watermark",
        }

    # One recycle per cycle, fattest first — bounds the blast radius the same
    # way sidecar_ram_watch does.
    anon_gb, unit, watermark_gb, endpoint, model, stats = max(
        over, key=lambda row: row[0]
    )

    idle, why_not = await _prove_idle(
        pool,
        unit,
        stats,
        cpu_idle_percent=float(config["cpu_idle_percent"]),
        require_gpu_lock_free=bool(config["require_gpu_lock_free"]),
        gpu_lock_fn=gpu_lock_fn,
    )
    if not idle:
        return {
            "ok": True,
            "status": "deferred",
            "detail": f"{unit} at {anon_gb:.1f} GB but not idle: {why_not}",
            "unit": unit,
            "anon_gb": round(anon_gb, 2),
        }

    ok, detail = await asyncio.to_thread(recycle_fn, endpoint, model)
    if not ok:
        await emit_finding(
            pool,
            source=_SOURCE,
            kind=_FAILED_KIND,
            severity="warning",
            title=f"Ollama runner recycle failed: {unit} ({anon_gb:.1f} GB)",
            body=(
                f"{unit} crossed its {watermark_gb:g} GB watermark while idle, "
                f"but the recycle against {endpoint} failed: {detail}. The "
                f"memory is still held; it will be retried after the "
                f"{COOLDOWN_MINUTES_KEY} cooldown."
            ),
            dedup_key=f"{_FAILED_KIND}:{unit}",
            extra={"unit": unit, "anon_gb": round(anon_gb, 2), "error": detail},
        )
        return {"ok": False, "status": "recycle_failed", "detail": detail, "unit": unit}

    _last_recycle_monotonic[unit] = now_fn()
    await emit_finding(
        pool,
        source=_SOURCE,
        kind=_RECYCLED_KIND,
        severity="info",
        title=f"Ollama runner recycled: {unit} {anon_gb:.1f} GB reclaimed",
        body=(
            f"{unit}'s llama-server runner had leaked to {anon_gb:.1f} GB of host "
            f"anonymous memory (RssAnon+VmSwap), past its {watermark_gb:g} GB "
            f"watermark, while provably idle (GPU scheduler lock free AND runner CPU "
            f"under {config['cpu_idle_percent']:g}%, re-checked immediately before). The brain unloaded and re-pinned {model} on "
            f"{endpoint}, which terminates the runner process and returns the "
            f"memory to the host: {detail}. The runner leaks ~6.9 MiB per request "
            f"(measured 2026-08-28), so this recurs by design — it is a recycle, "
            f"not a fix. Tune via app_settings.{TARGETS_KEY} / "
            f"{COOLDOWN_MINUTES_KEY}; disable via {ENABLED_KEY}."
        ),
        dedup_key=f"{_RECYCLED_KIND}:{unit}",
        extra={
            "unit": unit,
            "anon_gb": round(anon_gb, 2),
            "watermark_gb": watermark_gb,
            "endpoint": endpoint,
            "model": model,
        },
    )
    return {
        "ok": True,
        "status": "recycled",
        "detail": f"{unit}: {anon_gb:.1f} GB reclaimed — {detail}",
        "unit": unit,
        "anon_gb": round(anon_gb, 2),
        "watermark_gb": watermark_gb,
    }


class OllamaRunnerRamWatchProbe:
    """Probe-Protocol wrapper (mirrors SidecarRamWatchProbe)."""

    name: str = "ollama_runner_ram_watch"
    description: str = (
        "Watches ollama llama-server runners' host anonymous memory "
        "(RssAnon+VmSwap, via the gpu-exporter, since host systemd units are "
        "invisible to cadvisor) and unload/re-pins the fattest over-watermark "
        "one through ollama's API — only when the GPU scheduler lock is free. "
        "Emits ollama_runner_ram_recycled (info)."
    )
    interval_seconds: int = 600

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_ollama_runner_ram_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", summary.get("status", "")),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
