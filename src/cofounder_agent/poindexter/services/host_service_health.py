"""Liveness for host processes cAdvisor cannot see.

The operator console derives every service's health from cAdvisor's
``container_last_seen``. A HOST process has no container and therefore no
series, so ``serviceHealth()`` marks ``host: true`` rows neutral — deliberately,
because fabricating liveness is worse than admitting ignorance. The cost was
that Ollama, the runtime every LLM call in the pipeline goes through, rendered
permanently dark on the Services page and the System Map: ``host · not
scraped``, forever, whether it was serving or stopped.

**Nothing new is probed here.** The brain daemon has probed Ollama on every
5-minute cycle for as long as ``health_probes.PROBES`` has existed
(``probe_ollama_models`` → ``GET /api/tags``), and mirrors each result into
``brain_knowledge`` as ``probe.<name>`` / ``health_status``. The signal was
already being produced, persisted and alerted on; nothing read it back for the
console. This module is that read side, and it is deliberately a READER — a
second probe issuing its own ``/api/tags`` call on every console poll would
duplicate a working mechanism, add load to the runtime it is measuring, and
give the operator two answers that can disagree.

**Freshness is the entire point of this module.** The probe row is written
``ON CONFLICT DO UPDATE``, so it keeps its last value indefinitely once the
writer stops. Reporting that value as current would mean a dead brain daemon
renders Ollama permanently green — converting the console's honest "I don't
know" into a confident lie, which is strictly worse than the dark node this
replaces. It is also the exact trap this repo has been caught by before: a
retention policy that pruned nothing sat behind a green panel for months
because every signal available was a liveness signal
(``docs/architecture/retention-backlog.md``). So a row older than
``app_settings.host_probe_staleness_seconds`` reports ``stale`` and NEVER its
last known status, and a service whose probe has never run reports ``unknown``.
Neither is ever rendered as healthy.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

# Host services → the brain probe that establishes their liveness, and the
# label the console shows. Keyed by the `name` field of the matching
# `host: true` entry in the console roster (console/js/data.js `services`), so
# the console can overlay by name without a second mapping to keep in sync.
#
# `ollama_models` is the liveness probe on purpose: it is `GET /api/tags`, so
# it answers "is the runtime serving?" without needing the GPU. The deeper
# `ollama_embedding` probe is NOT used for status — it legitimately reports
# `skipped_gpu_busy` while the pipeline holds the GPU lock, and busy is not
# down (see its docstring in brain/health_probes.py).
HOST_SERVICE_PROBES: dict[str, str] = {
    "ollama": "ollama_models",
}

# Three brain cycles (CYCLE_SECONDS = 300). Tolerates a missed cycle or a slow
# one without flapping, while still catching a genuinely stopped daemon within
# ~15 minutes. Tunable per install like every other window in this system.
DEFAULT_STALENESS_SECONDS = 900


def _detail_for(probe_name: str, payload: dict[str, Any]) -> str:
    """One short operator-facing line. Never invents a value it wasn't given."""
    if probe_name == "ollama_models":
        count = payload.get("model_count")
        if isinstance(count, int):
            return f"{count} model{'' if count == 1 else 's'}"
    detail = payload.get("detail")
    return str(detail)[:120] if detail else ""


def classify(
    payload: dict[str, Any] | None,
    age_seconds: float | None,
    staleness_seconds: int,
) -> str:
    """Probe row → console status. Pure, so the freshness rule is testable.

    `unknown` (never probed) and `stale` (writer stopped) are distinct on
    purpose: the first says the mechanism was never wired, the second says it
    was and has gone quiet. Collapsing them would hide a dead brain daemon
    behind the same badge a fresh install shows.
    """
    if payload is None or age_seconds is None:
        return "unknown"
    if age_seconds > staleness_seconds:
        return "stale"
    return "ok" if payload.get("ok") else "err"


async def get_host_service_health(pool, staleness_seconds: int | None = None) -> dict:
    """Current liveness for every host service in ``HOST_SERVICE_PROBES``.

    Returns ``{"services": {<name>: {...}}, "staleness_seconds": N}``. A service
    is always present in the map — a missing probe row is reported as
    ``unknown``, never omitted, so the caller can tell "not wired" apart from
    "not asked about".
    """
    if staleness_seconds is None:
        staleness_seconds = DEFAULT_STALENESS_SECONDS

    entities = [f"probe.{probe}" for probe in HOST_SERVICE_PROBES.values()]
    rows = await pool.fetch(
        """
        SELECT entity, value, updated_at
        FROM brain_knowledge
        WHERE source = 'health_probe'
          AND attribute = 'health_status'
          AND entity = ANY($1::text[])
        """,
        entities,
    )
    by_entity = {r["entity"]: r for r in rows}
    now = datetime.now(timezone.utc)

    services: dict[str, Any] = {}
    for name, probe_name in HOST_SERVICE_PROBES.items():
        row = by_entity.get(f"probe.{probe_name}")
        payload: dict[str, Any] | None = None
        age_seconds: float | None = None
        checked_at: str | None = None

        if row is not None:
            try:
                parsed = json.loads(row["value"])
                payload = parsed if isinstance(parsed, dict) else None
            except (TypeError, ValueError):
                # A malformed row is an absent signal, not a healthy one.
                logger.warning("host_service_health: probe.%s holds unparseable JSON", probe_name)
                payload = None
            updated = row["updated_at"]
            if updated is not None:
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (now - updated).total_seconds())
                checked_at = updated.isoformat()

        services[name] = {
            "status": classify(payload, age_seconds, staleness_seconds),
            "detail": _detail_for(probe_name, payload) if payload else "",
            "probe": probe_name,
            "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
            "checked_at": checked_at,
        }

    return {"services": services, "staleness_seconds": staleness_seconds}
