"""Operator-triggered container restart — brain's side of the intent queue
(poindexter#909).

The console writes a ``service_restart_requests`` row (worker route:
``routes/service_restart_routes.py``); this module claims it on brain's own
poll loop and restarts the container via the SAME
``brain_daemon.docker_restart_container`` helper the self-healing
firefighter's ``restart_container`` remediation action already uses
(fire-drill verified, see ``brain/remediation/registry.py``). Brain-image
isolation: resolves ``brain_daemon`` lazily exactly like
``alert_dispatcher._resolve_brain_daemon_module`` and imports nothing from
``services/`` — raw SQL only.

**The footprint guard (2026-09-17).** Rows the gpu_scheduler's reclaim ladder
queues (``requested_by='gpu_vram_reclaim'``) are not operator clicks: they mean
"this sidecar declined to unload while the render GPU was short, so I assume
it is squatting". That inference is blind — the worker has no per-process
view of the card. Measured 2026-09-17: 35 restarts in three hours, every one
of an IDLE sidecar holding ~0.5 GB, because the card was legitimately full of
someone else's work (the director LLM cold-loading 18.5 GB, ComfyUI mid-S2V
at 27 GB). Restarting image-gen mid-still-phase four times is how the daily
post's illustrations became stock substitutes. Brain is the one process that
can see both docker and the gpu-exporter's per-pid metric, so it resolves the
container's host pids and sums their VRAM before bouncing: below
``vram_reclaim_min_freed_gb`` (the ladder's own squat floor) the request is
finalized ``done`` with a ``skipped —`` detail and a
``service_restart_skipped`` audit row instead. Unknown footprint (exporter
down, pids unresolvable) also skips — never bounce blind. Console/MCP requests
are unconditional, exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("brain.service_restart")

# How many pending requests to claim per poll — a burst of operator clicks
# (e.g. restarting several sidecars) shouldn't need multiple poll cycles.
_CLAIM_BATCH_SIZE = 5

# A row is claimed in one transaction and finalized after the restart returns.
# If this process dies in between — brain itself restarted, OOM, host reboot —
# the row strands in `claimed` forever, because the claim query only selects
# `status='pending'`. The console then reports its honest-but-permanent "still
# in progress". Nothing else reclaims these, so sweep them to a terminal
# `failed` on the next poll (`feedback_no_silent_defaults`: an operator action
# that silently never completes is exactly the failure mode to close).
#
# Terminal `failed`, NOT back to `pending`: a restart is side-effecting and
# non-idempotent, and we cannot know whether the docker restart landed before
# we died. Silently retrying could bounce a container repeatedly. Report it
# and let the operator decide.
_CLAIM_STALE_AFTER_MINUTES = 10


# Requests the gpu_scheduler's reclaim ladder writes — the only kind the
# footprint guard applies to. Operator clicks (console / MCP / CLI) are
# deliberate and go straight through.
_RECLAIM_REQUESTER = "gpu_vram_reclaim"
_GUARD_ENABLED_KEY = "vram_reclaim_restart_footprint_guard_enabled"
_SQUAT_FLOOR_KEY = "vram_reclaim_min_freed_gb"
_EXPORTER_URL_KEY = "gpu_exporter_metrics_url"
_DEFAULT_SQUAT_FLOOR_GB = 1.0

_PROCESS_METRIC_RE = re.compile(
    r'^nvidia_gpu_process_memory_mib\{([^}]*)\}\s+([0-9.eE+-]+)\s*$'
)
_PID_LABEL_RE = re.compile(r'pid="(\d+)"')


async def _setting_text(pool: Any, key: str) -> str | None:
    """Raw ``app_settings.value`` for ``key`` — None when absent or unreadable."""
    try:
        val = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
    except Exception:  # noqa: BLE001  # silent-ok: a settings read failing must degrade to the documented default, not break the poll
        return None
    return None if val is None else str(val)


async def _setting_bool(pool: Any, key: str, default: bool) -> bool:
    raw = await _setting_text(pool, key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def _setting_float(pool: Any, key: str, default: float) -> float:
    raw = await _setting_text(pool, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _default_exporter_url() -> str:
    in_docker = os.path.exists("/.dockerenv")
    host = "host.docker.internal" if in_docker else "localhost"
    return f"http://{host}:9835/metrics"


def parse_process_memory_mib(text: str) -> dict[int, float]:
    """``nvidia_gpu_process_memory_mib`` rows of the exporter body, keyed by pid.

    Pure so it is unit-testable; a pid that appears on several cards is summed.
    """
    out: dict[int, float] = {}
    for line in text.splitlines():
        m = _PROCESS_METRIC_RE.match(line.strip())
        if not m:
            continue
        pid_m = _PID_LABEL_RE.search(m.group(1))
        if not pid_m:
            continue
        try:
            out[int(pid_m.group(1))] = out.get(int(pid_m.group(1)), 0.0) + float(m.group(2))
        except ValueError:
            continue
    return out


async def _fetch_exporter_text(url: str) -> str | None:
    """The gpu-exporter's metrics body, or None when unreachable."""
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        logger.warning("[service_restart] %s=%r is not an http(s) URL", _EXPORTER_URL_KEY, url)
        return None

    def _read() -> str:
        with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310 - scheme restricted to http(s) above; the URL is operator config (app_settings), never request input
            return resp.read().decode("utf-8", "replace")

    try:
        return await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[service_restart] gpu-exporter unreachable at %s (%s: %s)",
            url, type(exc).__name__, exc,
        )
        return None


async def _container_pids(container: str) -> list[int] | None:
    """Host pids of every process in ``container`` (``docker top``), or None."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "top", container, "-eo", "pid"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[service_restart] docker top %s failed: %s", container, exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "[service_restart] docker top %s rc=%s: %s",
            container, result.returncode, (result.stderr or "")[:120],
        )
        return None
    pids: list[int] = []
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


async def container_gpu_footprint_gb(container: str, pool: Any) -> float | None:
    """GB of VRAM ``container``'s processes hold right now, or None (unknown).

    Host pids come from docker (brain has the socket; the worker does not), the
    per-pid bytes from the gpu-exporter's ``nvidia_gpu_process_memory_mib``
    (it runs ``pid: host``, so its pids ARE host pids). This is the driver's
    view — CUDA context included — which is exactly what a sidecar's own
    ``torch.cuda.memory_reserved()`` cannot see and why the ladder guessed.
    """
    pids = await _container_pids(container)
    if pids is None:
        return None
    url = (await _setting_text(pool, _EXPORTER_URL_KEY) or "").strip() or _default_exporter_url()
    body = await _fetch_exporter_text(url)
    if body is None:
        return None
    by_pid = parse_process_memory_mib(body)
    return sum(by_pid.get(pid, 0.0) for pid in pids) / 1024.0


async def _reclaim_request_verdict(
    container: str, requested_by: str | None, pool: Any,
) -> str | None:
    """None = restart; else the ``skipped —`` detail explaining why not."""
    if (requested_by or "") != _RECLAIM_REQUESTER:
        return None
    if not await _setting_bool(pool, _GUARD_ENABLED_KEY, True):
        return None
    floor_gb = await _setting_float(pool, _SQUAT_FLOOR_KEY, _DEFAULT_SQUAT_FLOOR_GB)
    footprint = await container_gpu_footprint_gb(container, pool)
    if footprint is None:
        return (
            f"skipped — {container}'s GPU footprint is unknown (docker top or the "
            "gpu-exporter did not answer); a restart on a guess kills renders, so "
            "not bouncing blind"
        )
    if footprint < floor_gb:
        return (
            f"skipped — {container} holds {footprint:.2f} GB on the GPU, below the "
            f"{floor_gb:.1f} GB squat floor ({_SQUAT_FLOOR_KEY}); the card is short "
            "because of someone else's work, not this sidecar"
        )
    logger.info(
        "[service_restart] %s holds %.2f GB (>= %.1f GB floor) — squat confirmed, restarting",
        container, footprint, floor_gb,
    )
    return None


def _resolve_brain_daemon_module() -> Any | None:
    """Identical resolution to alert_dispatcher._resolve_brain_daemon_module —
    duplicated rather than imported to keep this module standalone-testable
    without pulling in the full brain_daemon import graph."""
    mod = sys.modules.get("poindexter.brain.brain_daemon")
    if mod is not None:
        return mod
    try:
        from poindexter.brain import brain_daemon as mod  # type: ignore

        return mod
    except ImportError:
        return None


async def _write_audit(pool: Any, *, event_type: str, details: dict[str, Any], severity: str) -> None:
    """Matches remediation/engine.py's _write_audit shape — same audit_log
    columns, so this shows up in the console's Audit tab / event stream
    alongside firefighter-driven restarts."""
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, task_id, details, severity) "
            "VALUES ($1, $2, $3, $4::jsonb, $5)",
            event_type, "brain:service_restart", None, json.dumps(details, default=str), severity,
        )
    except Exception:  # noqa: BLE001  # silent-ok: audit is a courtesy write, never worth breaking the restart over
        logger.debug("[service_restart] audit_log write failed", exc_info=True)


async def _sweep_stale_claims(pool: Any) -> None:
    """Fail out rows stuck in ``claimed`` past ``_CLAIM_STALE_AFTER_MINUTES``.

    Best-effort and self-contained: a sweep failure is logged and the poll
    continues to the claim step, exactly like the per-row error posture.
    """
    detail = (
        f"orphaned: brain did not finalize this restart within "
        f"{_CLAIM_STALE_AFTER_MINUTES}m (brain likely restarted mid-flight). "
        f"The docker restart may or may not have run — check container uptime."
    )
    try:
        # Fully parameterized — the staleness window and the detail text are
        # bind params, not interpolated SQL, so there is no injection surface
        # to reason about (and no bandit B608 to annotate away).
        rows = await pool.fetch(
            """
            UPDATE service_restart_requests
               SET status = 'failed',
                   detail = $2,
                   completed_at = now()
             WHERE status = 'claimed'
               AND claimed_at < now() - ($1::int * interval '1 minute')
         RETURNING id, container
            """,
            _CLAIM_STALE_AFTER_MINUTES,
            detail,
        )
    except Exception:  # noqa: BLE001 — sweep is maintenance; never block the poll
        logger.warning("[service_restart] stale-claim sweep failed", exc_info=True)
        return

    for row in rows or []:
        logger.warning(
            "[service_restart] orphaned claim swept to failed: %s (id=%s)",
            row["container"], row["id"],
        )
        await _write_audit(
            pool,
            event_type="service_restart_orphaned",
            severity="warning",
            details={"request_id": str(row["id"]), "container": row["container"]},
        )


async def poll_and_execute_restart_requests(pool: Any) -> None:
    """Claim + execute pending operator-triggered container restarts.

    Best-effort like ``alert_dispatcher.poll_and_dispatch``: a claim or
    execution failure for one row is logged and the loop moves on next
    cycle — it never raises into the caller (``service_restart_loop``'s
    watchdog exists for wholesale task death, not per-row errors).
    """
    await _sweep_stale_claims(pool)

    mod = _resolve_brain_daemon_module()
    if mod is None or not hasattr(mod, "docker_restart_container"):
        logger.warning(
            "[service_restart] brain_daemon.docker_restart_container unavailable "
            "— restart requests will accumulate unclaimed"
        )
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, container, requested_by FROM service_restart_requests
                WHERE status = 'pending'
                ORDER BY requested_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                _CLAIM_BATCH_SIZE,
            )
            if not rows:
                return
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE service_restart_requests SET status = 'claimed', claimed_at = now() "
                "WHERE id = ANY($1::uuid[])",
                ids,
            )

    for row in rows:
        request_id, container = row["id"], row["container"]
        requested_by = row.get("requested_by") if hasattr(row, "get") else None
        try:
            skip_detail = await _reclaim_request_verdict(container, requested_by, pool)
        except Exception as e:  # noqa: BLE001 — the guard must never turn into a bounce OR a stuck row
            skip_detail = f"skipped — footprint guard raised ({e})"[:400]
        if skip_detail is not None:
            # Terminal `done`, not `failed`: the request was handled — the
            # remedy was judged unnecessary. The status CHECK has no 'skipped'.
            await pool.execute(
                "UPDATE service_restart_requests "
                "SET status = $1, detail = $2, completed_at = now() WHERE id = $3",
                "done", skip_detail[:400], request_id,
            )
            await _write_audit(
                pool,
                event_type="service_restart_skipped",
                severity="info",
                details={
                    "request_id": str(request_id), "container": container,
                    "requested_by": requested_by, "detail": skip_detail,
                },
            )
            logger.info("[service_restart] %s -> %s", container, skip_detail)
            continue
        try:
            ok, detail = await mod.docker_restart_container(container, pool=pool)
        except Exception as e:  # noqa: BLE001 — one bad row must not kill the batch
            ok, detail = False, f"docker_restart_container raised: {e}"[:400]
        final_status = "done" if ok else "failed"
        await pool.execute(
            "UPDATE service_restart_requests "
            "SET status = $1, detail = $2, completed_at = now() WHERE id = $3",
            final_status, detail, request_id,
        )
        await _write_audit(
            pool,
            event_type="service_restart_completed",
            severity="info" if ok else "warning",
            details={"request_id": str(request_id), "container": container, "ok": ok, "detail": detail},
        )
        logger.info(
            "[service_restart] %s -> %s (%s)", container, final_status, detail,
        )
