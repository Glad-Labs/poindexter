"""Probe last-run times that survive a brain restart.

``health_probes``, ``business_probes`` and ``post_performance_probe`` run a
probe only when its interval has elapsed since it last ran. Each kept that
last-run time in a module-level dict, which is empty in every new process, so
every brain restart made every probe due at once. The brain restarted 111
times in the 30 days to 2026-09-25; 111 of ``post_performance``'s 120 pages and
all 31 of ``webhook_freshness``'s alerts landed within 15 minutes of one. Their
24-hour intervals never elapsed; the process forgot it had run them.

Each run is now also written to ``brain_knowledge``, and a new process reads
those rows back once, on its first cycle. After that ``is_due`` is a dict
lookup: the table is read once per process and written once per probe run.

A row is ``entity='probe.<name>'``, ``attribute='last_run_at'``,
``source='probe_schedule'``, value an ISO-8601 UTC timestamp: beside the
probe's ``health_status`` row, told apart from it by attribute and source.
Keep the ``probe.`` prefix: ``services/topic_sources/knowledge.py`` mines
``brain_knowledge`` for blog topics and skips entities containing
``probe.``, and an entity such as ``probe_schedule.content_gen`` would match
its ``%content%`` filter without being skipped.

Every failure falls back to running the probe: an extra run is recoverable, a
probe that never runs is silent.

- A failed read is retried next cycle. Until one succeeds, probes run as if
  they had never run, which was the old behaviour.
- A failed write leaves this process's schedule intact and only loses the
  restart protection for that one run.
- A value that does not parse is ignored. One dated in the future (a clock
  stepped back, a hand edit) counts as now, so it delays a probe by at most
  one interval instead of disabling it.

Standalone: stdlib only; callers pass an asyncpg-style pool.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("brain.probe_schedule")

ENTITY_PREFIX = "probe."
ATTRIBUTE = "last_run_at"
SOURCE = "probe_schedule"

_LOAD_SQL = (
    "SELECT entity, value FROM brain_knowledge WHERE attribute = $1 AND source = $2"
)
_UPSERT_SQL = """
    INSERT INTO brain_knowledge (entity, attribute, value, source)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (entity, attribute)
    DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source, updated_at = NOW()
"""


def parse_timestamp(raw: str) -> float | None:
    """Epoch seconds for an ISO-8601 value, read as UTC when it has no zone; None if unreadable."""
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class ProbeSchedule:
    """Last-run time per probe name, held in memory and written through to the DB."""

    def __init__(self) -> None:
        self.last_run: dict[str, float] = {}
        self._loaded = False

    async def load(self, pool: Any) -> None:
        """Merge the persisted last-run times into memory. A no-op after the first successful read."""
        if self._loaded:
            return
        try:
            rows = await pool.fetch(_LOAD_SQL, ATTRIBUTE, SOURCE)
        except Exception as exc:  # noqa: BLE001 — retried next cycle
            logger.warning(
                "[PROBE_SCHEDULE] could not read last-run times (%s: %s); retrying "
                "next cycle, and until then every probe runs as if it never ran",
                type(exc).__name__, exc,
            )
            return
        now = time.time()
        restored = 0
        for row in rows:
            entity, raw = row["entity"], row["value"]
            ran_at = parse_timestamp(raw)
            if not entity.startswith(ENTITY_PREFIX) or ran_at is None:
                logger.warning(
                    "[PROBE_SCHEDULE] ignoring unreadable row %s=%r", entity, raw
                )
                continue
            if ran_at > now:
                logger.warning(
                    "[PROBE_SCHEDULE] %s last ran at %s, which is in the future; "
                    "counting it as now",
                    entity, raw,
                )
                ran_at = now
            name = entity[len(ENTITY_PREFIX):]
            self.last_run[name] = max(self.last_run.get(name, 0.0), ran_at)
            restored += 1
        self._loaded = True
        logger.info(
            "[PROBE_SCHEDULE] restored last-run times for %d probe(s)", restored
        )

    def is_due(self, name: str, interval_seconds: float) -> bool:
        return time.time() - self.last_run.get(name, 0.0) >= interval_seconds

    async def mark_run(self, pool: Any, name: str) -> None:
        now = time.time()
        self.last_run[name] = now
        try:
            await pool.execute(
                _UPSERT_SQL,
                f"{ENTITY_PREFIX}{name}",
                ATTRIBUTE,
                datetime.fromtimestamp(now, UTC).isoformat(),
                SOURCE,
            )
        except Exception as exc:  # noqa: BLE001 — memory already holds the run
            logger.warning(
                "[PROBE_SCHEDULE] could not persist %s's last run (%s: %s); a "
                "brain restart before its next run will run it early",
                name, type(exc).__name__, exc,
            )


# One schedule per process. The three probe modules share the ``probe.<name>``
# namespace in the table, so they share it in memory too.
schedule = ProbeSchedule()
