"""Shared primitives for the host-RAM recycle probes.

Extracted from ``comfyui_ram_watch.py`` (poindexter#3360) when the mechanism
was generalised to the queue-less GPU sidecars after the 2026-08-27 freeze.
Both probes answer the same three mechanical questions — *how big is this
container's PID 1*, *restart it*, *record what happened* — and differ only in
how they prove the sidecar is IDLE, which is the part that must stay
per-sidecar.

Nothing here decides whether to recycle. Callers own that; these are the
levers.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger("brain.ram_recycle_common")

DOCKER_RESTART_TIMEOUT_SECONDS = 60
DOCKER_EXEC_TIMEOUT_SECONDS = 30

# CREATE_NO_WINDOW — keeps the Windows .ps1-era fleet from flashing consoles.
_WIN_NO_WINDOW = 0x08000000


def _subprocess_kwargs(timeout: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _WIN_NO_WINDOW
    return kwargs


# --- app_settings reads ------------------------------------------------------


async def read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[RAM_RECYCLE] read %s failed: %s — default %r", key, exc, default
        )
        return default
    return default if val is None else val


def coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def coerce_float(val: Any, default: float) -> float:
    if val is None:
        return default
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return default


def coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


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


def parse_status_rss_swap_gb(status_text: str) -> tuple[float, float] | None:
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


def read_container_main_rss_swap_gb(container: str) -> tuple[float, float] | None:
    """VmRSS + VmSwap of the container's PID 1, in GB.

    **RSS+swap, not RSS** — that is the whole point. The 2026-08-27 freeze was
    driven by sidecars whose RSS looked innocent (chatterbox 1.2 GB) while
    their SWAP was enormous (8.9 GB); a probe watching RSS alone would have
    called every one of them healthy right up to the hard reboot.

    PID 1 is the accumulator for these sidecars: each compose service runs an
    exec-form ``command:``, so the model server is the container's init.
    Returns None when the read fails (container absent, docker CLI missing,
    unparseable output) — callers surface that rather than treating it as 0.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", container, "cat", "/proc/1/status"],
            **_subprocess_kwargs(DOCKER_EXEC_TIMEOUT_SECONDS),
        )
    except FileNotFoundError:
        logger.warning("[RAM_RECYCLE] docker CLI not on PATH — cannot read RSS")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[RAM_RECYCLE] docker exec %s timed out", container)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[RAM_RECYCLE] mem read failed: %s: %s", type(exc).__name__, exc
        )
        return None
    if result.returncode != 0:
        logger.warning(
            "[RAM_RECYCLE] docker exec %s exit %s: %s",
            container, result.returncode, (result.stderr or "").strip()[:200],
        )
        return None
    return parse_status_rss_swap_gb(result.stdout or "")


def read_container_cpu_percent(container: str) -> float | None:
    """Single-shot ``docker stats`` CPU percentage for one container.

    Returns None on any failure — callers MUST treat that as "cannot prove
    idle" (i.e. busy), never as 0.0. A blind probe that reads unknown as idle
    would restart a sidecar mid-inference.
    """
    try:
        result = subprocess.run(
            [
                "docker", "stats", "--no-stream",
                "--format", "{{.CPUPerc}}", container,
            ],
            **_subprocess_kwargs(DOCKER_EXEC_TIMEOUT_SECONDS),
        )
    except FileNotFoundError:
        logger.warning("[RAM_RECYCLE] docker CLI not on PATH — cannot read CPU")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[RAM_RECYCLE] docker stats %s timed out", container)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[RAM_RECYCLE] cpu read failed: %s: %s", type(exc).__name__, exc
        )
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip().rstrip("%").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# --- restart -----------------------------------------------------------------


def restart_container(container: str) -> tuple[bool, str]:
    """``docker restart <container>`` -> ``(ok, message)``. Never raises."""
    try:
        result = subprocess.run(
            ["docker", "restart", container],
            **_subprocess_kwargs(DOCKER_RESTART_TIMEOUT_SECONDS),
        )
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


# --- finding emission --------------------------------------------------------


async def emit_finding(
    pool: Any,
    *,
    source: str,
    kind: str,
    severity: str,
    title: str,
    body: str,
    dedup_key: str,
    extra: dict[str, Any],
) -> None:
    """Write an audit_log ``finding`` row (Findings board + router).

    Shape mirrors ``utils/findings.py::emit_finding`` (the worker-side
    producer) so the Findings dashboard and ``findings_alert_router`` treat
    brain findings identically: ``details={kind,title,body,dedup_key,extra}``
    with severity on the row. ``info`` never routes (the router's severity
    floor) — board-visible only; ``warn`` routes per ``findings.<kind>.*``.
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
            source,
            json.dumps(details),
            severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RAM_RECYCLE] finding insert (%s) failed: %s", kind, exc)
