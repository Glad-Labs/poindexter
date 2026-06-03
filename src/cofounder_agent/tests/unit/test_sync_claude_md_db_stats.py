"""Unit tests for ``scripts/sync_claude_md_db_stats.py``.

Covers the pure-logic core — the prose-anchored rewrites in
``apply_to_claude_md`` — without touching a database. The count queries
themselves are validated by running the script against prod; here we only
guard the regex surface (right numbers rewritten, nothing else touched,
idempotent on already-fresh text).

Imports the repo-root ``scripts`` module via the namespace path that the
cofounder pytest config puts on ``pythonpath`` (``../..`` = repo root).
"""

from __future__ import annotations

from collections import OrderedDict

import pytest

import scripts.sync_claude_md_db_stats as dbsync  # type: ignore[import-not-found]  # repo-root namespace pkg via pytest pythonpath


# A minimal CLAUDE.md fragment carrying every claim the script syncs, plus
# decoy numbers that MUST survive untouched.
SAMPLE = (
    "## Key Numbers\n"
    "- 78 live posts on gladlabs.io (222 posts total; 1,626 pipeline_tasks "
    "across all generation runs)\n"
    "- 801 app_settings keys (60 secret) plus 4 cost_tier mappings wired 2026-05-09\n"
    "- 40,497 embeddings across posts / issues / audit / memory / brain / claude_sessions\n"
    "- PluginScheduler boots 39 jobs — see registry\n"
)

FRESH = OrderedDict([
    ("live_posts", 80),
    ("total_posts", 230),
    ("pipeline_tasks", 1700),
    ("app_settings", 805),
    ("app_settings_secret", 61),
    ("embeddings", 41000),
])


def test_rewrites_every_db_claim():
    new, changes = dbsync.apply_to_claude_md(FRESH, text=SAMPLE)

    assert "80 live posts on gladlabs.io" in new
    assert "(230 posts total;" in new
    assert "1,700 pipeline_tasks across" in new  # thousands separator applied
    assert "805 app_settings keys (61 secret)" in new
    assert "41,000 embeddings across" in new
    # one change entry per rewritten claim (5 distinct lines)
    assert len(changes) == 5


def test_decoy_numbers_untouched():
    new, _ = dbsync.apply_to_claude_md(FRESH, text=SAMPLE)
    # numbers that look syncable but aren't anchored must survive
    assert "4 cost_tier mappings" in new
    assert "boots 39 jobs" in new
    assert "wired 2026-05-09" in new


def test_idempotent_on_fresh_text():
    once, _ = dbsync.apply_to_claude_md(FRESH, text=SAMPLE)
    twice, changes = dbsync.apply_to_claude_md(FRESH, text=once)
    assert twice == once
    assert changes == []  # nothing to do the second time


def test_no_change_when_values_already_match():
    same = OrderedDict([
        ("live_posts", 78),
        ("total_posts", 222),
        ("pipeline_tasks", 1626),
        ("app_settings", 801),
        ("app_settings_secret", 60),
        ("embeddings", 40497),
    ])
    new, changes = dbsync.apply_to_claude_md(same, text=SAMPLE)
    assert new == SAMPLE
    assert changes == []


def test_query_keys_cover_every_anchor():
    # Every key formatted in apply_to_claude_md must have a query, else a
    # live run KeyErrors. Exercised implicitly above, asserted explicitly here.
    assert set(FRESH) == set(dbsync.COUNT_QUERIES)


@pytest.mark.parametrize("n,expected", [(78, "78"), (1626, "1,626"), (40497, "40,497")])
def test_thousands_formatting(n, expected):
    stats = OrderedDict((k, n) for k in dbsync.COUNT_QUERIES)
    new, _ = dbsync.apply_to_claude_md(stats, text=SAMPLE)
    assert f"{expected} live posts" in new
