"""Sample Job implementation — a no-op that logs + returns JobResult(ok=True).

Serves as a reference for third-party Job authors. Actual idle_worker
migrations happen in Phase C.
"""

from __future__ import annotations

from typing import Any

from plugins.job import JobResult


class NoopJob:
    """Does nothing useful. Proves the scheduling + config chain works."""

    name = "noop"
    description = "Sample Job — does nothing, returns ok=True"
    schedule = "every 1 hour"
    idempotent = True

    async def run(self, _pool: Any, _config: dict[str, Any]) -> JobResult:
        return JobResult(
            ok=True,
            detail="noop job completed",
            changes_made=0,
        )
