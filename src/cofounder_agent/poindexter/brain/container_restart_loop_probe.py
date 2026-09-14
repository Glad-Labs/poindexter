"""Page when a stack container is restart-looping — with the crash reason attached.

2026-09-13: the chatterbox TTS sidecar restarted 507 times in eight hours on a
``ModuleNotFoundError`` and nothing said "a container is crash-looping". The
only page came from a downstream probe ("narration lane failing … TTS probe
unreachable"), an hour later, naming the symptom and not the cause. A restart
loop is the most legible failure a container can have: ``RestartCount`` climbs,
``State.Restarting`` is true, and the last log lines carry the traceback.

Every brain cycle this probe inspects all ``poindexter-*`` containers in one
``docker inspect`` call and compares each ``RestartCount`` with the value it
persisted last cycle (``brain_knowledge``, so a brain restart does not reset the
baseline). A container is *looping* when the count grew by at least
``container_restart_loop_threshold`` (default 3) since the previous cycle, or it
is ``restarting`` with a count already at or above the threshold. Looping pages
**critical** once per episode with the container's last log lines, escalates
with a reminder every ``container_restart_loop_reminder_hours`` (default 1)
while the loop continues, and writes a recovery note when the container has
been running without new restarts for two consecutive cycles.

Deliberately-stopped containers (``exited`` with a stable count — parked voice,
one-shot runners) are not loops. A container that restarted once because the
deploy recreated it is not a loop either — the threshold is per cycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

PROBE_NAME = "container_restart_loop_probe"
ENTITY = "container_restart_loop"
ALERTNAME = "container_restart_loop"
THRESHOLD_KEY = "container_restart_loop_threshold"
REMINDER_HOURS_KEY = "container_restart_loop_reminder_hours"
ENABLED_KEY = "container_restart_loop_probe_enabled"
DEFAULT_THRESHOLD = 3
DEFAULT_REMINDER_HOURS = 1
DOCKER_TIMEOUT_SECONDS = 60
LOG_TAIL_LINES = 15
STATE_TTL_DAYS = 14
NAME_FILTER = "^poindexter-"

# In-memory episode state, keyed by container name. Persisted counts live in
# brain_knowledge (attribute ``restarts:<name>``); the alert bookkeeping is
# rebuilt from the same table on the first cycle after a brain restart.
_baseline: dict[str, int] = {}
_baseline_loaded = False
_alerted_at: dict[str, float] = {}     # container -> monotonic-ish epoch of the last page
_stable_cycles: dict[str, int] = {}    # container -> consecutive calm cycles while in an episode


def _reset_state() -> None:
    """Test seam."""
    global _baseline_loaded
    _baseline.clear()
    _alerted_at.clear()
    _stable_cycles.clear()
    _baseline_loaded = False


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------

def _run(argv: list[str], *, timeout: int = DOCKER_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str] | None:
    try:
        kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout}
        if os.name == "nt":  # pragma: no cover
            kwargs["creationflags"] = 0x08000000
        return subprocess.run(argv, **kwargs)  # noqa: S603 — fixed argv, no shell
    except FileNotFoundError:
        logger.warning("[%s] docker CLI not on PATH", PROBE_NAME.upper())
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[%s] %s timed out after %ds", PROBE_NAME.upper(), " ".join(argv[:3]), timeout)
        return None


def inspect_stack_containers() -> list[dict[str, Any]] | None:
    """One ``docker ps`` + one ``docker inspect`` for every poindexter-* container.

    Returns ``None`` when docker is unreachable (the caller reports, never
    treats it as "no containers").
    """
    ps = _run(["docker", "ps", "-aq", "--filter", f"name={NAME_FILTER}"])
    if ps is None or ps.returncode != 0:
        return None
    ids = [line.strip() for line in (ps.stdout or "").splitlines() if line.strip()]
    if not ids:
        return []
    ins = _run(["docker", "inspect", *ids])
    if ins is None or ins.returncode != 0:
        return None
    try:
        body = json.loads(ins.stdout or "[]")
    except json.JSONDecodeError:
        return None
    out: list[dict[str, Any]] = []
    for c in body if isinstance(body, list) else []:
        if not isinstance(c, dict):
            continue
        state = c.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        out.append({
            "name": str(c.get("Name") or "").lstrip("/"),
            "status": str(state.get("Status") or ""),
            "restarting": bool(state.get("Restarting")),
            "restart_count": int(c.get("RestartCount") or 0),
            "health": health,
            "started_at": state.get("StartedAt"),
            "exit_code": state.get("ExitCode"),
            "image": str((c.get("Config") or {}).get("Image") or ""),
        })
    return out


def container_log_tail(name: str, lines: int = LOG_TAIL_LINES) -> str:
    res = _run(["docker", "logs", "--tail", str(lines), name], timeout=30)
    if res is None:
        return "(logs unavailable)"
    text = (res.stdout or "") + (res.stderr or "")
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    return text[-2500:] if text else "(no log output)"


# ---------------------------------------------------------------------------
# settings + persisted state
# ---------------------------------------------------------------------------

async def _read_setting(pool: Any, key: str, default: str) -> str:
    try:
        val = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
    except Exception as exc:  # noqa: BLE001 — a probe must never crash a cycle
        logger.warning("[%s] could not read %s: %s", PROBE_NAME.upper(), key, exc)
        return default
    return str(val).strip() if val is not None and str(val).strip() else default


async def _read_int(pool: Any, key: str, default: int) -> int:
    raw = await _read_setting(pool, key, str(default))
    try:
        return int(raw)
    except ValueError:
        logger.warning("[%s] %s is not an integer (%r); using %d", PROBE_NAME.upper(), key, raw, default)
        return default


async def _load_baseline(pool: Any) -> None:
    global _baseline_loaded
    if _baseline_loaded:
        return
    try:
        rows = await pool.fetch(
            "SELECT attribute, value FROM brain_knowledge WHERE entity = $1 AND attribute LIKE 'restarts:%'",
            ENTITY,
        )
        for row in rows or []:
            attr = row["attribute"] if not isinstance(row, dict) else row.get("attribute")
            val = row["value"] if not isinstance(row, dict) else row.get("value")
            if isinstance(attr, str) and attr.startswith("restarts:"):
                try:
                    _baseline[attr.split(":", 1)[1]] = int(str(val))
                except (TypeError, ValueError):
                    continue
    except Exception as exc:  # noqa: BLE001 — a cold baseline is the first-cycle behaviour, not a failure
        logger.warning("[%s] could not restore restart baseline: %s", PROBE_NAME.upper(), exc)
    _baseline_loaded = True


async def _persist_count(pool: Any, name: str, count: int) -> None:
    try:
        await pool.execute(
            "INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, expires_at) "
            "VALUES ($1, $2, $3, 1.0, $4, NOW() + make_interval(days => $5)) "
            "ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, "
            "expires_at = EXCLUDED.expires_at, updated_at = NOW()",
            ENTITY, f"restarts:{name}", str(count), PROBE_NAME, STATE_TTL_DAYS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] could not persist restart count for %s: %s", PROBE_NAME.upper(), name, exc)


async def _write_alert(pool: Any, *, name: str, status: str, severity: str, title: str, body: str, suffix: str) -> None:
    labels = json.dumps({"probe": PROBE_NAME, "container": name})
    annotations = json.dumps({"summary": title, "description": body})
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, status, severity, category,
                labels, annotations, fingerprint
            ) VALUES (
                $1, $2, $3, 'infrastructure',
                $4::jsonb, $5::jsonb, $6
            )
            """,
            ALERTNAME, status, severity, labels, annotations,
            f"{PROBE_NAME}:{name}:{suffix}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] alert_events write failed for %s: %s", PROBE_NAME.upper(), name, exc)


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------

def classify(container: dict[str, Any], previous: int | None, threshold: int) -> str:
    """``looping`` | ``calm`` | ``unknown`` for one container against its last-cycle count.

    ``unknown`` = no baseline yet (first sight of this container): a count
    is recorded, nothing is judged — a fresh brain must not page for history.
    """
    count = int(container.get("restart_count") or 0)
    if previous is None:
        return "unknown"
    grew_by = count - previous
    if grew_by >= threshold:
        return "looping"
    if container.get("restarting") and count >= threshold:
        return "looping"
    return "calm"


async def run_container_restart_loop_probe(pool: Any, *, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    enabled = (await _read_setting(pool, ENABLED_KEY, "true")).lower() in ("1", "true", "yes", "on")
    if not enabled:
        return {"ok": True, "detail": "disabled", "looping": [], "checked": 0}
    threshold = max(1, await _read_int(pool, THRESHOLD_KEY, DEFAULT_THRESHOLD))
    reminder_hours = max(0, await _read_int(pool, REMINDER_HOURS_KEY, DEFAULT_REMINDER_HOURS))
    await _load_baseline(pool)

    containers = await asyncio.to_thread(inspect_stack_containers)
    if containers is None:
        return {"ok": False, "detail": "docker unreachable — restart-loop watch blind this cycle", "looping": [], "checked": 0}

    looping: list[str] = []
    paged: list[str] = []
    recovered: list[str] = []
    for c in containers:
        name = c["name"]
        if not name:
            continue
        previous = _baseline.get(name)
        verdict = classify(c, previous, threshold)
        count = int(c["restart_count"])
        if previous != count:
            await _persist_count(pool, name, count)
        _baseline[name] = count

        if verdict == "looping":
            looping.append(name)
            _stable_cycles[name] = 0
            last = _alerted_at.get(name)
            first_page = last is None
            due_reminder = (not first_page) and reminder_hours > 0 and (now - last) >= reminder_hours * 3600
            if first_page or due_reminder:
                tail = await asyncio.to_thread(container_log_tail, name)
                grew = count - (previous or 0)
                title = (
                    f"{name} is restart-looping: RestartCount {count} (+{grew} this cycle), "
                    f"state={c['status']}, exit={c.get('exit_code')}"
                )
                body = (
                    f"Container `{name}` (image `{c['image']}`) keeps dying and being restarted by Docker. "
                    f"A restart loop never self-heals — the image, the config, or a dependency is broken. "
                    f"If the deploy sync just rebuilt this image, the last merged change to its build inputs is the suspect.\n\n"
                    f"Last {LOG_TAIL_LINES} log lines:\n```\n{tail}\n```"
                )
                await _write_alert(
                    pool, name=name, status="firing", severity="critical", title=title, body=body,
                    suffix="looping" if first_page else f"reminder-{int(now // 3600)}",
                )
                _alerted_at[name] = now
                paged.append(name)
        elif name in _alerted_at:
            # In an episode; count the calm cycles before calling it recovered.
            calm_now = c["status"] == "running" and not c["restarting"] and previous == count
            _stable_cycles[name] = _stable_cycles.get(name, 0) + 1 if calm_now else 0
            if _stable_cycles[name] >= 2:
                await _write_alert(
                    pool, name=name, status="resolved", severity="info",
                    title=f"{name} stopped restart-looping (RestartCount steady at {count}, {c.get('health') or c['status']})",
                    body=f"`{name}` has run for two probe cycles without a new restart.",
                    suffix="recovered",
                )
                recovered.append(name)
                _alerted_at.pop(name, None)
                _stable_cycles.pop(name, None)

    detail = (
        f"{len(containers)} containers; looping: {', '.join(looping) if looping else 'none'}"
        + (f"; paged: {', '.join(paged)}" if paged else "")
        + (f"; recovered: {', '.join(recovered)}" if recovered else "")
    )
    return {"ok": not looping, "detail": detail, "looping": looping, "paged": paged,
            "recovered": recovered, "checked": len(containers)}
