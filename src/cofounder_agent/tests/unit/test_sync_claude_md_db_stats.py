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
# decoy numbers that MUST survive untouched. The heading is reproduced with its
# real parenthetical: the freshness marker lives there, so a fixture without it
# would let a header-stamping regression pass green.
HEADER = (
    "### Key Numbers (repo-derived stats auto-synced daily by CI; DB-derived "
    "counts last refreshed 2026-06-10)\n"
)
SAMPLE = (
    HEADER
    + "- 78 live posts on gladlabs.io (222 posts total; 1,626 pipeline_tasks "
    "across all generation runs)\n"
    # post-#2820 wording: a qualifier sits between "keys" and the paren, and the
    # paren carries trailing prose. Both used to break the anchor outright.
    "- 801 app_settings keys live on prod (60 secret; drifts as keys are added). "
    "Seeded from 4 sources wired 2026-05-09\n"
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
    new, changes = dbsync.apply_to_claude_md(FRESH, text=SAMPLE, today="2026-07-26")

    assert "80 live posts on gladlabs.io" in new
    assert "(230 posts total;" in new
    assert "1,700 pipeline_tasks across" in new  # thousands separator applied
    assert "805 app_settings keys live on prod (61 secret;" in new
    assert "41,000 embeddings across" in new
    # prose on both sides of the two app_settings numbers survives untouched
    assert "drifts as keys are added). Seeded from 4 sources" in new
    # one change entry per rewritten claim (5 distinct lines) + the header stamp
    assert len(changes) == 6
    assert not any(c.startswith("WARNING") for c in changes)


def test_decoy_numbers_untouched():
    new, _ = dbsync.apply_to_claude_md(FRESH, text=SAMPLE)
    # numbers that look syncable but aren't anchored must survive
    assert "Seeded from 4 sources" in new
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


# --- Key Numbers freshness marker -----------------------------------------
#
# The counts under the header refresh nightly; before this, the date *in* the
# header only ever moved when a human retyped it. That made the most
# authoritative-looking staleness signal in CLAUDE.md wrong by construction —
# it read "last refreshed 2026-06-10" over numbers pulled from prod that
# morning. These pin the stamp to the same trigger as the counts.


def test_count_change_restamps_header():
    new, changes = dbsync.apply_to_claude_md(FRESH, text=SAMPLE, today="2026-07-26")

    assert "DB-derived counts last refreshed 2026-07-26)" in new
    assert "2026-06-10" not in new  # the stale date is gone, not merely joined
    assert "header last-refreshed ->2026-07-26" in changes


def test_header_untouched_when_no_count_changed():
    # An unconditional restamp would make --check report drift every day on
    # nothing but the calendar, and the nightly session would open an empty PR
    # each morning. The stamp rides on a real count change or nothing.
    same = OrderedDict([
        ("live_posts", 78),
        ("total_posts", 222),
        ("pipeline_tasks", 1626),
        ("app_settings", 801),
        ("app_settings_secret", 60),
        ("embeddings", 40497),
    ])
    new, changes = dbsync.apply_to_claude_md(same, text=SAMPLE, today="2026-07-26")

    assert new == SAMPLE
    assert changes == []


def test_restamp_is_idempotent_within_a_day():
    once, _ = dbsync.apply_to_claude_md(FRESH, text=SAMPLE, today="2026-07-26")
    twice, changes = dbsync.apply_to_claude_md(FRESH, text=once, today="2026-07-26")

    assert twice == once
    assert changes == []


def test_stamp_only_rewrites_the_header_line():
    new, _ = dbsync.apply_to_claude_md(FRESH, text=SAMPLE, today="2026-07-26")
    differing = [
        (a, b)
        for a, b in zip(SAMPLE.splitlines(), new.splitlines(), strict=True)
        if a != b
    ]
    # the header plus the three count-bearing bullets; the decoy line survives
    assert len(differing) == 4
    assert differing[0][1].startswith("### Key Numbers (")
    # the repo-derived half of the header is not collateral damage
    assert "repo-derived stats auto-synced daily by CI;" in new


def test_legacy_header_wording_converges():
    # The pre-2026-07-26 phrasing carried a hedge that outlived its cause. The
    # stamp rewrites the whole DB-derived clause, so drifted hand-edits
    # converge rather than needing a pattern per historical wording.
    legacy = (
        "### Key Numbers (code-derived stats as of 2026-06-21; DB-derived counts "
        "— posts, embeddings, app_settings totals — last refreshed 2026-06-10, "
        "pending a prod-DB probe)\n"
    ) + SAMPLE.split("\n", 1)[1]

    new, changes = dbsync.apply_to_claude_md(FRESH, text=legacy, today="2026-07-26")

    assert "DB-derived counts last refreshed 2026-07-26)" in new
    assert "pending a prod-DB probe" not in new
    assert "header last-refreshed ->2026-07-26" in changes


def test_missing_header_anchor_reports_loudly():
    # Fail loud, not silent: the counts are still worth landing, but the
    # operator has to learn the freshness date stopped being maintained.
    anchorless = SAMPLE.split("\n", 1)[1]

    new, changes = dbsync.apply_to_claude_md(FRESH, text=anchorless, today="2026-07-26")

    assert "80 live posts on gladlabs.io" in new  # counts still applied
    warnings = [c for c in changes if c.startswith("WARNING")]
    assert len(warnings) == 1
    assert "Key Numbers header not restamped" in warnings[0]


def test_default_stamp_is_todays_utc_date():
    from datetime import datetime, timezone

    expected = datetime.now(timezone.utc).date().isoformat()
    new, changes = dbsync.apply_to_claude_md(FRESH, text=SAMPLE)  # no today= pin

    assert f"DB-derived counts last refreshed {expected})" in new
    assert f"header last-refreshed ->{expected}" in changes


# --- anchor breakage -------------------------------------------------------
#
# Every rewrite here rides on CLAUDE.md prose. When that prose is reworded the
# match drops to zero and the claim stops syncing while still *looking*
# current — which is how `app_settings` went stale the day #2820 rewrote its
# bullet from "N app_settings keys (S secret)" to "N app_settings keys live on
# prod (S secret; drifts as keys are added)". A miss now reports itself.


def test_app_settings_anchor_survives_both_wordings():
    legacy_line = "- 801 app_settings keys (60 secret) plus 4 cost_tier mappings\n"
    text = HEADER + legacy_line

    new, changes = dbsync.apply_to_claude_md(FRESH, text=text, today="2026-07-26")

    assert "805 app_settings keys (61 secret) plus 4 cost_tier mappings" in new
    assert "app_settings ->805 (61 secret)" in changes


def test_reworded_anchor_reports_loudly():
    # "app_settings keys" renamed out from under the pattern entirely.
    broken = SAMPLE.replace(
        "801 app_settings keys live on prod (60 secret;",
        "801 settings rows on prod (60 private;",
    )

    new, changes = dbsync.apply_to_claude_md(FRESH, text=broken, today="2026-07-26")

    warnings = [c for c in changes if c.startswith("WARNING")]
    assert len(warnings) == 1
    assert "app_settings" in warnings[0]
    assert "no longer synced" in warnings[0]
    # the unmatched claim keeps its stale number rather than being silently lost
    assert "801 settings rows on prod (60 private;" in new
    # the claims that DID match still sync
    assert "80 live posts on gladlabs.io" in new


def test_fixture_matches_every_anchor():
    # Guards the fixture itself: if SAMPLE drifts from the real CLAUDE.md
    # wording, every downstream assertion in this file starts testing fiction.
    _, changes = dbsync.apply_to_claude_md(FRESH, text=SAMPLE, today="2026-07-26")
    assert [c for c in changes if c.startswith("WARNING")] == []


# --- README.md -------------------------------------------------------------
#
# The same prod probe answers the root README's marketing stats, which had
# rotted exactly the way CLAUDE.md's did before #2832: the intro claimed 166
# live posts against a real 198, hand-typed and watched by nothing. README is
# the higher-stakes target — it ships verbatim to the public mirror — so it
# gets the same anchored rewrites, with one difference: every claim is floored
# to a round "N+" so it understates rather than overstates, and moves only on
# a threshold crossing rather than nightly.

# Verbatim README shapes plus decoys that MUST survive: the "Python 3.13+" and
# "8 GB+ VRAM" prereqs, and a second "posts" sentence with no stat in it.
README_SAMPLE = (
    "This is not a demo repo. Poindexter is the production system behind "
    "[gladlabs.io](https://www.gladlabs.io) — 166 live posts and counting, "
    "every one generated by this pipeline.\n"
    "**Prereqs:** Python 3.13+ · NVIDIA GPU with 8 GB+ VRAM\n"
    "| **DB-as-config**  | 1,300+ settings in PostgreSQL. Change with SQL. |\n"
    "| **Multi-site**    | One daemon manages N sites.                     |\n"
    "- 166 live posts on [gladlabs.io](https://www.gladlabs.io) "
    "(340 posts total, 2,000+ pipeline runs).\n"
    "- Push-only static export; posts are decoupled from the frontend.\n"
)


def test_readme_rewrites_every_db_claim_floored():
    new, changes = dbsync.apply_to_readme(FRESH, text=README_SAMPLE)

    assert "80+ live posts and counting" in new
    assert "80+ live posts on [gladlabs.io]" in new
    assert "(230+ posts total," in new
    assert "1,000+ pipeline runs" in new
    assert "800+ settings in PostgreSQL" in new
    assert len(changes) == 5
    assert [c for c in changes if c.startswith("WARNING")] == []


def test_readme_claims_are_floored_never_exact():
    """A floored claim stays TRUE if the sync ever stops running; an exact one
    is wrong the next day and churns a docs PR every night in between."""
    new, _ = dbsync.apply_to_readme(FRESH, text=README_SAMPLE)
    for exact in ("80 live posts", "230 posts total", "1,700 pipeline", "805 settings"):
        assert exact not in new


def test_readme_leaves_unrelated_numbers_alone():
    new, _ = dbsync.apply_to_readme(FRESH, text=README_SAMPLE)
    assert "Python 3.13+ · NVIDIA GPU with 8 GB+ VRAM" in new
    assert "One daemon manages N sites." in new
    assert "posts are decoupled from the frontend" in new


def test_readme_is_idempotent_on_already_fresh_text():
    """The anchors have to re-match their own floored output, or the nightly
    would report drift forever on a file it already updated."""
    once, _ = dbsync.apply_to_readme(FRESH, text=README_SAMPLE)
    twice, changes = dbsync.apply_to_readme(FRESH, text=once)
    assert twice == once
    assert changes == []


def test_readme_reworded_anchor_reports_loudly():
    broken = README_SAMPLE.replace(
        "166 live posts and counting", "a growing archive and counting"
    )
    new, changes = dbsync.apply_to_readme(FRESH, text=broken)

    warnings = [c for c in changes if c.startswith("WARNING")]
    assert len(warnings) == 1
    assert "live_posts_intro" in warnings[0]
    # the claims that DID match still sync
    assert "80+ live posts on [gladlabs.io]" in new


def test_readme_fixture_matches_every_anchor():
    """Guards the fixture: if README_SAMPLE drifts from the real README
    wording, every assertion above starts testing fiction."""
    _, changes = dbsync.apply_to_readme(FRESH, text=README_SAMPLE)
    assert [c for c in changes if c.startswith("WARNING")] == []


def test_every_anchor_matches_the_real_checked_in_readme():
    """Acceptance: the patterns work against the file they ship for."""
    _, changes = dbsync.apply_to_readme(FRESH)
    assert [c for c in changes if c.startswith("WARNING")] == []


def test_readme_and_claude_md_anchor_sets_are_disjoint_in_ownership():
    """The test count is repo-derived and belongs to the sibling script; this
    one must not claim it, or two nightlies would fight over one line."""
    assert "test_functions" not in dbsync.README_ANCHORS
    assert set(dbsync.README_ANCHORS) == {
        "live_posts_intro",
        "live_posts_status",
        "total_posts_status",
        "pipeline_tasks_status",
        "app_settings_feature_row",
    }
