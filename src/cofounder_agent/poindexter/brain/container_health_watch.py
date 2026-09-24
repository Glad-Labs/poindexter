"""Alert while a container is stuck ``unhealthy``; the firefighter does the restart.

2026-09-24: speaches wedged at 13:19. A VRAM-reclaim call to its model-unload
API deadlocked its Whisper manager (docs/architecture/video-render-vram-gate.md),
and every later transcription blocked. Its ``/health`` kept answering, so Docker
only marked it ``unhealthy`` at 16:14, and it stayed that way until a manual
restart at 18:48. Every render from 13:19 on shipped without burned-in captions,
and nothing restarted it or paged. Docker restart policies act only when a
process EXITS; a process that is alive but wedged stays wedged. The brain
watched particular failures (auto-embed staleness, backups, restart loops) but
not the ``unhealthy`` state itself. A plain ``docker restart`` fixed it in
seconds.

This probe sees what a healthcheck sees and no more: on that day it would have
restarted speaches at about 16:25, not 13:30. The deadlock is fixed at its cause
(no API unloads). The watch is the backstop for the next wedge nobody predicted,
and it is only as quick as the container's own healthcheck.

This probe is the detector half. Every brain cycle it reads each
``poindexter-*`` container's health from the restart-loop probe's single
``docker inspect``. A container that has failed its healthcheck for at least
``container_health_alert_after_minutes`` (the failing streak times the check
interval) opens an episode and gets a firing ``container_unhealthy`` row in
``alert_events``:

- It **re-fires every cycle** while the container is still ``unhealthy``. The
  dispatcher collapses the repeats into one page, and the firefighter's verify
  step reads them: an alert that kept firing after a restart means the restart
  did not work.
- A container in its start period (``starting``, e.g. just restarted) neither
  fires nor ends the episode.
- ``healthy`` again ends the episode with a resolved row, which says whether
  the container was restarted in between (its ``StartedAt`` moved).

The restart is not done here. It belongs to the firefighter
(``poindexter/brain/remediation/``): one ``remediation_rules`` row per container
that is safe to bounce, matched on this alert's fingerprint
(``container_health_watch:<name>``), gets the circuit breaker, the global rate
cap, the never-restart denylist, audit rows and verify-then-page for free.
Rows carry the label ``remediation=rules_only`` so the LLM long-tail never
restarts a container nobody wrote a rule for. This alert covers GPU renderers
mid-job and a busy worker, where a blind bounce kills in-flight work.

Per-container thresholds: ``container_health_alert_after_overrides``
(``name=minutes,...``). image-gen-server runs inference and model loads on its
event loop, so ``/health`` cannot answer while it works; over the 15 days of
Prometheus history before this shipped it read unhealthy 13 times, for 8 to 22
minutes each. Its default override (30) keeps that from paging until its
``/health`` is served off the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from poindexter.brain.container_restart_loop_probe import (
    container_log_tail,
    inspect_stack_containers,
)

logger = logging.getLogger("brain.container_health_watch")

PROBE_NAME = "container_health_watch"
ALERTNAME = "container_unhealthy"
# Firefighter label value that keeps this alert off the LLM long-tail path
# (remediation/engine.py RULES_ONLY). Duplicated as a literal so the probe
# does not import the engine.
REMEDIATION_LABEL = "rules_only"

ENABLED_KEY = "container_health_watch_enabled"
AFTER_MINUTES_KEY = "container_health_alert_after_minutes"
OVERRIDES_KEY = "container_health_alert_after_overrides"

DEFAULT_AFTER_MINUTES = 10
DEFAULT_OVERRIDES = "poindexter-image-gen-server=30"
_DEFAULT_INTERVAL_S = 30.0

# Open episodes: container name -> the StartedAt it had when the episode
# opened. In memory: a brain restart forgets an open episode, and the next
# cycle re-opens it once the container is still past its threshold, with the
# dispatcher's persistent dedup state keeping that from re-paging.
_episodes: dict[str, str] = {}


def _reset_state() -> None:
    _episodes.clear()


async def _read_setting(pool: Any, key: str, default: str) -> str:
    try:
        val = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
    except Exception as exc:  # noqa: BLE001 — a probe must never crash a cycle
        logger.warning("[%s] could not read %s: %s", PROBE_NAME.upper(), key, exc)
        return default
    return str(val).strip() if val is not None and str(val).strip() else default


def parse_overrides(raw: str) -> dict[str, float]:
    """``"a=30, b=45"`` -> ``{"a": 30.0, "b": 45.0}``. A malformed entry is
    logged and skipped rather than failing the whole map."""
    out: dict[str, float] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, minutes = part.partition("=")
        try:
            if not sep or not name.strip():
                raise ValueError("expected name=minutes")
            out[name.strip()] = float(minutes)
        except ValueError as exc:
            logger.warning("[%s] ignoring %s entry %r: %s", PROBE_NAME.upper(), OVERRIDES_KEY, part, exc)
    return out


async def _write_alert(
    pool: Any, *, name: str, status: str, severity: str, title: str, body: str,
) -> None:
    labels = json.dumps({"probe": PROBE_NAME, "container": name, "remediation": REMEDIATION_LABEL})
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
            ALERTNAME, status, severity, labels, annotations, f"{PROBE_NAME}:{name}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] alert_events write failed for %s: %s", PROBE_NAME.upper(), name, exc)


def container_name(container: dict[str, Any]) -> str:
    return str(container.get("Name") or "").lstrip("/")


def health_status(container: dict[str, Any]) -> str:
    """``healthy`` / ``unhealthy`` / ``starting``, or ``""`` without a healthcheck."""
    health = (container.get("State") or {}).get("Health") or {}
    return str(health.get("Status") or "")


def unhealthy_minutes(container: dict[str, Any]) -> float | None:
    """How long this container has been failing its healthcheck, or ``None``
    when it is not ``unhealthy`` (healthy, starting, or no healthcheck)."""
    if health_status(container) != "unhealthy":
        return None
    health = (container.get("State") or {}).get("Health") or {}
    streak = int(health.get("FailingStreak") or 0)
    interval_ns = ((container.get("Config") or {}).get("Healthcheck") or {}).get("Interval") or 0
    interval_s = interval_ns / 1e9 if interval_ns else _DEFAULT_INTERVAL_S
    return streak * interval_s / 60.0


def _started_at(container: dict[str, Any]) -> str:
    return str((container.get("State") or {}).get("StartedAt") or "")


async def run_container_health_watch_probe(
    pool: Any,
    *,
    containers: list[dict[str, Any]] | None = None,
    log_tail_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """One cycle: fire for containers stuck unhealthy, resolve the ones that recovered."""
    enabled = (await _read_setting(pool, ENABLED_KEY, "true")).lower() in ("true", "1", "yes", "on")
    if not enabled:
        return {"ok": True, "detail": "disabled", "unhealthy": [], "firing": []}
    try:
        default_after = float(await _read_setting(pool, AFTER_MINUTES_KEY, str(DEFAULT_AFTER_MINUTES)))
    except ValueError:
        logger.warning("[%s] %s is not a number; using %d", PROBE_NAME.upper(), AFTER_MINUTES_KEY, DEFAULT_AFTER_MINUTES)
        default_after = float(DEFAULT_AFTER_MINUTES)
    overrides = parse_overrides(await _read_setting(pool, OVERRIDES_KEY, DEFAULT_OVERRIDES))
    log_tail_fn = log_tail_fn or container_log_tail

    if containers is None:
        containers = await asyncio.to_thread(inspect_stack_containers)
    if containers is None:
        return {"ok": False, "detail": "docker unreachable — health watch blind this cycle",
                "unhealthy": [], "firing": []}

    unhealthy: list[str] = []
    firing: list[str] = []
    for container in containers:
        name = container_name(container)
        status = health_status(container)
        if status == "healthy":
            if name in _episodes:
                restarted = _started_at(container) != _episodes.pop(name)
                await _write_alert(
                    pool, name=name, status="resolved", severity="info",
                    title=f"{name} is healthy again" + (" after a restart" if restarted else ""),
                    body=(
                        f"{name} passes its healthcheck again. "
                        + ("It was restarted during the episode." if restarted
                           else "It recovered without a restart.")
                    ),
                )
            continue
        minutes = unhealthy_minutes(container)
        if minutes is None:
            continue  # starting (e.g. just restarted) or no healthcheck
        unhealthy.append(name)
        threshold = overrides.get(name, default_after)
        if name not in _episodes and minutes < threshold:
            continue
        _episodes.setdefault(name, _started_at(container))
        tail = await asyncio.to_thread(log_tail_fn, name)
        await _write_alert(
            pool, name=name, status="firing", severity="warning",
            title=f"{name} has failed its healthcheck for {minutes:.0f} min",
            body=(
                f"{name} is unhealthy: {minutes:.0f} min of consecutive failed "
                f"healthchecks (alert after {threshold:g} min). Docker does not "
                f"restart a container that is alive but failing its healthcheck.\n"
                f"Last log lines:\n{tail}"
            ),
        )
        firing.append(name)
    if firing:
        logger.warning("[%s] unhealthy past threshold: %s", PROBE_NAME.upper(), ", ".join(firing))
    detail = (
        f"{len(unhealthy)} unhealthy, {len(firing)} firing"
        if unhealthy else f"all {len(containers)} containers healthy or without a healthcheck"
    )
    return {"ok": True, "detail": detail, "unhealthy": unhealthy, "firing": firing,
            "checked": len(containers)}


__all__ = ["parse_overrides", "run_container_health_watch_probe", "unhealthy_minutes"]
