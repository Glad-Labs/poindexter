"""Finance module-owned scheduled jobs.

``JOBS`` is the presence-based discovery surface: ``plugins.registry``'s
in-tree module scan reads this list and registers each job into the
``jobs`` plugin bucket. Because the list lives inside ``modules/finance/``,
stripping the finance package from the public mirror removes the job with
zero substrate edits (Module v1 Phase 5).
"""

from __future__ import annotations

from .poll_mercury import PollMercuryJob

JOBS = [PollMercuryJob]

__all__ = ["JOBS", "PollMercuryJob"]
