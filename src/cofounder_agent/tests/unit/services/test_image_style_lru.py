"""Least-recently-used featured-style rotation (#image-zimage-and-variety).

The old selection excluded a fixed cross-post "recent" window and then
``random.choice``-d the remainder, which clustered styles unevenly — some
never surfaced, others repeated. ``_select_style_lru`` instead always picks the
least-recently-used style (never-used counts as oldest), so the whole pool
cycles before any style repeats, keeping style decoupled from content.
"""

from __future__ import annotations

import random

from modules.content.stages.source_featured_image import _select_style_lru

STYLES = [("flat_vector", "t1"), ("pixel_art", "t2"), ("silhouette", "t3")]


def test_never_used_style_wins():
    # pixel_art & silhouette never used; flat_vector used recently.
    last_used = {"flat_vector": "2026-07-10T00:00:00"}
    chosen, _ = _select_style_lru(STYLES, last_used, set(), rng=random.Random(0))
    assert chosen in ("pixel_art", "silhouette")


def test_oldest_used_wins_when_all_used():
    last_used = {
        "flat_vector": "2026-07-10T00:00:00",
        "pixel_art": "2026-07-01T00:00:00",   # oldest
        "silhouette": "2026-07-05T00:00:00",
    }
    chosen, _ = _select_style_lru(STYLES, last_used, set(), rng=random.Random(0))
    assert chosen == "pixel_art"


def test_in_memory_recent_excluded():
    last_used: dict[str, str] = {}  # all never-used
    chosen, _ = _select_style_lru(
        STYLES, last_used, {"pixel_art", "silhouette"}, rng=random.Random(0),
    )
    assert chosen == "flat_vector"


def test_full_cycle_before_repeat():
    """Greedily selecting + recording last_used cycles the whole pool before repeating."""
    last_used: dict[str, str] = {}
    picks = []
    for i in range(len(STYLES)):
        chosen, _ = _select_style_lru(STYLES, last_used, set(), rng=random.Random(i))
        picks.append(chosen)
        last_used[chosen] = f"2026-07-10T00:00:0{i}"
    assert sorted(picks) == sorted(s[0] for s in STYLES)  # each used once
