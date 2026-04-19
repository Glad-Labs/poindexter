"""In-memory style-pick dedup tracker (#181).

Prevents concurrent / sequential content tasks in the same worker process
from picking the same featured-image style. Entries auto-expire after
``STYLE_HISTORY_TTL`` seconds so the style pool doesn't starve when the
rotation is small.

Lifted from ``services/content_router_service.py`` during Phase E2.
``source_featured_image`` stage reads via :func:`get_in_memory_recent_styles`
and writes via :func:`record_style_pick`. DB-recent styles come from a
separate query inside that stage.
"""

from __future__ import annotations

import time
from collections import deque


STYLE_HISTORY_SIZE = 10
STYLE_HISTORY_TTL = 3600  # 1 hour

# Deque of (style_name, monotonic_timestamp). Module-level so every task
# in the worker process sees the same pick history.
_recent_style_picks: deque[tuple[str, float]] = deque(maxlen=STYLE_HISTORY_SIZE)


def record_style_pick(style_name: str) -> None:
    """Record that *style_name* was just chosen."""
    _recent_style_picks.append((style_name, time.monotonic()))


def get_in_memory_recent_styles() -> list[str]:
    """Return style names picked within the TTL window."""
    cutoff = time.monotonic() - STYLE_HISTORY_TTL
    return [name for name, ts in _recent_style_picks if ts >= cutoff]


def reset_history() -> None:
    """Test helper — wipe the deque."""
    _recent_style_picks.clear()
