"""Job — the scheduled-maintenance Protocol.

A Job is a periodic housekeeping task: ``fix_categories``, ``fix_seo``,
``sync_page_views``, ``memory_stale_check``, ``embedding_refresh``, etc.
After Phase C lands, every existing method in ``services/idle_worker.py``
becomes a Job registered via entry_points, and apscheduler is the runner.

Register a Job via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.jobs"]
    fix_seo = "poindexter.jobs.fix_seo:FixSeoJob"

The Job's ``schedule`` attribute is handed to apscheduler at register
time; the Job itself does not implement its own scheduling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class JobResult:
    """Structured return type for Job.run().

    ``changes_made`` is the primary operational signal — the number of
    rows a "fix" job actually touched (e.g. "3 posts had SEO filled in"),
    not just "I ran." Metrics that expose this as a Prometheus gauge
    give you a feel for whether background work is doing anything
    useful over time.
    """

    ok: bool
    detail: str
    changes_made: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Job(Protocol):
    """A scheduled housekeeping task.

    Attributes:
        name: Unique job name (matches the entry_point key). Also used
            as the apscheduler job id, so it must be stable.
        description: Human-readable explanation for the operator UI.
        schedule: Either a cron expression (``"0 */6 * * *"``) or an
            interval string (``"every 30 minutes"``). Handed verbatim
            to the apscheduler adapter at register time.
        idempotent: Whether two overlapping runs would be safe. If
            ``False``, apscheduler will not start a new instance while
            the previous one is still running.
    """

    name: str
    description: str
    schedule: str
    idempotent: bool

    async def run(
        self,
        pool: Any,  # asyncpg.Pool
        config: dict[str, Any],
    ) -> JobResult:
        """Execute one invocation of the job.

        Must not raise on routine failures — return
        ``JobResult(ok=False, detail=...)`` instead so apscheduler
        doesn't count it as a crash and back off aggressively.
        """
        ...
