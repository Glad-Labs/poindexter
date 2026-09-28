"""Probe failure streaks and self-heal times that survive a brain restart.

``health_probes.run_health_probes`` sends a probe's failure notice on its
third failure in a row (``ALERT_AFTER_FAILURES``) and self-heals a failing
probe at most once per ``REMEDIATION_COOLDOWN`` (15 minutes). Both were
module-level dicts, empty in every new process. In the 30 days to 2026-09-28
the brain restarted 91 times and 50 failure streaks crossed a restart and
started again from one. Leaving out ``publish_rate``, whose notices
Prometheus owns, 8 of the 19 failure notices announced a streak a second
time, 4 streaks of three or more failures were never announced, and 3
announced streaks ended with no recovery notice. (Most of that window came
before ``probe_schedule``, when every restart also re-ran every probe.) A
self-heal such as ``docker restart poindexter-worker`` could also run again
sooner than 15 minutes after the last one if the brain restarted in between.

Counts and self-heal times are now written to ``brain_knowledge`` and read
back once per process, on the first cycle, as ``probe_schedule`` does for
last-run times. A count is written when it changes, so a passing probe costs
no write per run; a self-heal time is written when the self-heal starts.

Rows sit beside the probe's ``health_status`` and ``last_run_at`` rows, with
``entity='probe.<name>'`` and ``source='probe_failure_state'``:

- ``attribute='consecutive_failures'``: the count, ``0`` while the probe passes.
- ``attribute='last_remediation_at'``: when the probe's last self-heal
  started, an ISO-8601 UTC timestamp.

The ``probe.`` prefix keeps the rows out of the knowledge topic source (see
``probe_schedule``), and the source keeps them out of ``doctor``, which reads
``source='health_probe'``.

A database failure never stops a probe from running, paging or self-healing.
It only costs the restart protection:

- A failed read is retried next cycle. Until one succeeds, counts start from
  zero and no cooldown is known, which was the old behaviour. A late read
  never replaces a count this process recorded; it writes that count back.
- A failed count write is retried with the probe's next result. A stale count
  would come back after a restart: an ended streak could self-heal a service
  that had recovered, and a new failure would continue the old count past the
  notice instead of starting a new streak.
- A failed self-heal time write only matters if the brain restarts before
  that cooldown ends.
- An unreadable row is ignored. A self-heal time in the future counts as now.

Standalone: stdlib only; callers pass an asyncpg-style pool.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from poindexter.brain.probe_schedule import ENTITY_PREFIX, parse_timestamp

logger = logging.getLogger("brain.probe_failure_state")

SOURCE = "probe_failure_state"
FAILURES = "consecutive_failures"
LAST_REMEDIATION = "last_remediation_at"

_LOAD_SQL = (
    "SELECT entity, attribute, value FROM brain_knowledge "
    "WHERE source = $1 AND attribute = ANY($2::text[])"
)
_UPSERT_SQL = """
    INSERT INTO brain_knowledge (entity, attribute, value, source)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (entity, attribute)
    DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source, updated_at = NOW()
"""


def _parse_count(raw: str) -> int | None:
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


class ProbeFailureState:
    """Consecutive failures and last self-heal per probe, in memory and written through to the DB."""

    def __init__(self) -> None:
        self.failures: dict[str, int] = {}
        self.last_remediation: dict[str, float] = {}
        # The count the table is known to hold for each probe. A probe is
        # missing until a read or a write confirms its row, so its next result
        # is written whatever the count.
        self._stored: dict[str, int] = {}
        self._loaded = False

    async def load(self, pool: Any) -> None:
        """Merge the persisted counts and self-heal times into memory. A no-op after the first successful read."""
        if self._loaded:
            return
        try:
            rows = await pool.fetch(_LOAD_SQL, SOURCE, [FAILURES, LAST_REMEDIATION])
        except Exception as exc:  # noqa: BLE001 — retried next cycle
            logger.warning(
                "[PROBE_FAILURE_STATE] could not read failure counts (%s: %s); "
                "retrying next cycle, and until then every probe counts from zero",
                type(exc).__name__, exc,
            )
            return
        now = time.time()
        counts = heals = 0
        for row in rows:
            entity, attribute, raw = row["entity"], row["attribute"], row["value"]
            name = entity[len(ENTITY_PREFIX):] if entity.startswith(ENTITY_PREFIX) else ""
            if attribute == FAILURES:
                count = _parse_count(raw)
                if not name or count is None:
                    logger.warning(
                        "[PROBE_FAILURE_STATE] ignoring unreadable row %s %s=%r",
                        entity, attribute, raw,
                    )
                    continue
                self._stored[name] = count
                # A result this process has already recorded is newer than the row.
                self.failures.setdefault(name, count)
                counts += 1
            elif attribute == LAST_REMEDIATION:
                started = parse_timestamp(raw)
                if not name or started is None:
                    logger.warning(
                        "[PROBE_FAILURE_STATE] ignoring unreadable row %s %s=%r",
                        entity, attribute, raw,
                    )
                    continue
                if started > now:
                    logger.warning(
                        "[PROBE_FAILURE_STATE] %s last self-healed at %s, which is in "
                        "the future; counting it as now",
                        entity, raw,
                    )
                    started = now
                self.last_remediation[name] = max(
                    self.last_remediation.get(name, 0.0), started,
                )
                heals += 1
        self._loaded = True
        # Write back any count recorded before the table could be read.
        for name, count in list(self.failures.items()):
            await self._store(pool, name, count)
        failing = ", ".join(
            f"{name}={count}" for name, count in sorted(self.failures.items()) if count
        )
        logger.info(
            "[PROBE_FAILURE_STATE] restored failure counts for %d probe(s) (%s) and "
            "self-heal times for %d",
            counts, f"failing: {failing}" if failing else "none failing", heals,
        )

    async def record_failure(self, pool: Any, name: str) -> int:
        """Count one more failure in a row for ``name`` and return the new count."""
        count = self.failures.get(name, 0) + 1
        self.failures[name] = count
        await self._store(pool, name, count)
        return count

    async def record_success(self, pool: Any, name: str) -> None:
        """End ``name``'s failure streak, if it has one."""
        self.failures[name] = 0
        await self._store(pool, name, 0)

    def remediation_due(self, name: str, cooldown_seconds: float) -> bool:
        return time.time() - self.last_remediation.get(name, 0.0) >= cooldown_seconds

    async def mark_remediation(self, pool: Any, name: str) -> None:
        """Record that a self-heal of ``name`` starts now."""
        now = time.time()
        self.last_remediation[name] = now
        try:
            await pool.execute(
                _UPSERT_SQL,
                f"{ENTITY_PREFIX}{name}",
                LAST_REMEDIATION,
                datetime.fromtimestamp(now, UTC).isoformat(),
                SOURCE,
            )
        except Exception as exc:  # noqa: BLE001 — memory already holds the self-heal
            logger.warning(
                "[PROBE_FAILURE_STATE] could not persist %s's self-heal time "
                "(%s: %s); a brain restart before its cooldown ends could let it "
                "self-heal again early",
                name, type(exc).__name__, exc,
            )

    async def _store(self, pool: Any, name: str, count: int) -> None:
        if self._stored.get(name) == count:
            return
        try:
            await pool.execute(
                _UPSERT_SQL, f"{ENTITY_PREFIX}{name}", FAILURES, str(count), SOURCE,
            )
        except Exception as exc:  # noqa: BLE001 — retried with the probe's next result
            self._stored.pop(name, None)
            logger.warning(
                "[PROBE_FAILURE_STATE] could not persist %s's failure count %d "
                "(%s: %s); retrying with its next result",
                name, count, type(exc).__name__, exc,
            )
            return
        self._stored[name] = count


# One state per process. Only health_probes keeps failure counts and self-heals.
state = ProbeFailureState()
