"""In-memory style-pick dedup tracker (#181).

Prevents concurrent / sequential content tasks in the same worker
process from picking the same featured-image style. Entries auto-expire
after ``STYLE_HISTORY_TTL`` seconds so the style pool doesn't starve
when the rotation is small.

Phase G1 converted this from a module-level singleton to a class.
Worker lifespan creates a single ``ImageStyleTracker`` instance and
exposes it via FastAPI ``app.state``; stages read it from the pipeline
context (keyed as ``image_style_tracker``). Tests construct fresh
instances per test.
"""

from __future__ import annotations

import time
from collections import deque

STYLE_HISTORY_SIZE = 10
STYLE_HISTORY_TTL = 3600  # 1 hour


class ImageStyleTracker:
    """Bounded, TTL-expiring deque of recent style picks.

    ``history_size`` caps memory usage; ``ttl_seconds`` caps how long a
    pick blocks its own reuse. Both default to the module-level
    constants but are overridable for tests.
    """

    def __init__(
        self,
        history_size: int = STYLE_HISTORY_SIZE,
        ttl_seconds: int = STYLE_HISTORY_TTL,
    ):
        self._picks: deque[tuple[str, float]] = deque(maxlen=history_size)
        self._ttl = ttl_seconds

    def record(self, style_name: str) -> None:
        """Record that *style_name* was just chosen."""
        self._picks.append((style_name, time.monotonic()))

    def recent(self) -> list[str]:
        """Return style names picked within the TTL window."""
        cutoff = time.monotonic() - self._ttl
        return [name for name, ts in self._picks if ts >= cutoff]

    def reset(self) -> None:
        """Wipe history — useful for tests."""
        self._picks.clear()
