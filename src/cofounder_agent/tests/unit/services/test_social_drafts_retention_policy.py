"""Guard: the social_post_drafts retention policy can only prune tombstones.

``social_post_drafts`` only grows — one row per platform per post — so it has a
``retention.ttl_prune`` policy seeded in ``0000_baseline.seeds.sql``. That
policy is deliberately scoped to ``status = 'rejected'`` and must stay that way.

The hazard is subtle enough to be worth a test rather than a comment.
``create_draft``'s dedup guard and ``existing_draft_keys`` both treat
``social_drafts._KEY_HELD_STATUSES`` — pending/scheduled/failed/**posted** —
as *live* keys; that is what stops a finalize re-run (preview_gate regen,
checkpoint restore, task retry) from regenerating a promo that already went
out (poindexter#833, where the same promo posted three times). Prune a
``posted`` row and its key looks fresh again, so the promo can be regenerated
and **re-posted to a live audience**. ``approve_draft``'s publish-gate is no
help: the post is genuinely published, so the duplicate sails through.

The prune-safety test reads ``_KEY_HELD_STATUSES`` directly rather than
grepping for a SQL literal, so a status added to the live set is covered here
the moment it is added — which is how ``scheduled`` got covered.

``rejected`` carries no such coupling — it is already excluded from both
guards, so pruning it is provably inert. Verified empirically against prod
2026-07-27: deleting all 41 rejected rows left the live-key set unchanged at
36 (0 keys freed).

These tests read the seed file and the service together, so widening the
filter, or changing which statuses the guard considers live, fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SEEDS = (
    Path(__file__).resolve().parents[3]
    / "services" / "migrations" / "0000_baseline.seeds.sql"
)
_SERVICE = (
    Path(__file__).resolve().parents[3] / "services" / "social_drafts.py"
)

# Statuses that are NEVER safe to prune, because a surviving row of this status
# is what suppresses regeneration of its (task, platform, subreddit) key. Read
# from the service so the two can't drift: a status added to the live-key set
# is automatically covered by the prune-safety test below.
from services.social_drafts import _KEY_HELD_STATUSES as _KEY_HOLDING_STATUSES


def _policy_line() -> str:
    for line in _SEEDS.read_text(encoding="utf-8").splitlines():
        if "retention_policies" in line and "social_post_drafts" in line:
            return line
    raise AssertionError(
        "no retention_policies seed row for social_post_drafts — the table only "
        "grows and nothing else prunes it (poindexter#928)"
    )


def _filter_sql(line: str) -> str:
    """The policy's filter_sql column, un-escaping SQL's doubled quotes."""
    # VALUES (id, name, handler, table_name, filter_sql, ...) — filter_sql is
    # the 5th value and the only one containing a status comparison.
    match = re.search(r"'((?:[^']|'')*status(?:[^']|'')*)'", line)
    assert match, f"could not locate filter_sql in seed row: {line[:200]}"
    return match.group(1).replace("''", "'")


def test_policy_exists_and_prunes_by_age_on_created_at():
    line = _policy_line()
    assert "'ttl_prune'" in line
    assert "'created_at'" in line, (
        "age_column must be created_at — social_post_drafts has no rejected_at"
    )


def test_policy_filters_to_rejected_only():
    assert _filter_sql(_policy_line()) == "status = 'rejected'"


@pytest.mark.parametrize("status", _KEY_HOLDING_STATUSES)
def test_policy_never_prunes_a_key_holding_status(status):
    """Widening the filter to a live status would let an old promo re-post."""
    assert status not in _filter_sql(_policy_line()), (
        f"retention filter must not match {status!r} rows — create_draft's dedup "
        f"guard treats them as live keys, so pruning one lets a finalize re-run "
        f"regenerate and re-post the promo (poindexter#833)"
    )


def test_policy_is_enabled():
    """A seeded-but-disabled policy silently lets the table grow forever."""
    assert ", true, " in _policy_line(), "policy should ship enabled"


def test_dedup_guard_still_treats_posted_as_live():
    """The other half of the contract.

    If the guard ever stops counting ``posted`` as a live key, this test's
    premise changes and the retention filter should be revisited — better to
    fail here than to silently drift apart.
    """
    assert "posted" in _KEY_HOLDING_STATUSES, (
        "posted left the live-key set — pruning a posted row would now free "
        "its key, so re-check the retention filter"
    )


def test_both_guards_read_the_shared_live_key_set():
    """create_draft and existing_draft_keys must not re-inline the statuses.

    They previously carried the literal ``'pending', 'failed', 'posted'``
    twice; a status added to one and not the other is precisely the drift
    that re-opens poindexter#833.
    """
    source = _SERVICE.read_text(encoding="utf-8")
    assert source.count("_KEY_HELD_STATUSES") >= 3, (
        "create_draft / existing_draft_keys should both bind "
        "_KEY_HELD_STATUSES rather than inlining a status list"
    )


def test_rejected_is_excluded_from_the_dedup_guard():
    """Why pruning rejected is inert: the guard never looks at those rows."""
    assert "rejected" not in _KEY_HOLDING_STATUSES, (
        "rejected joined the live-key set — pruning it would now free keys"
    )


def test_scheduled_is_a_live_key():
    """A queued promo holds its key just as firmly as a pending one.

    Without this, a finalize re-run sees the key as free, inserts a second
    draft, and both eventually post — poindexter#833 with extra steps.
    """
    assert "scheduled" in _KEY_HOLDING_STATUSES, (
        "scheduled must hold its (task, platform, subreddit) key — a draft "
        "with a fire time on it is a live commitment"
    )
