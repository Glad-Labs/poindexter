r"""Shared helpers for syncing the marketing stats in the root ``README.md``.

README.md is the public front door — it ships verbatim to the
Glad-Labs/poindexter mirror, so a stale number there is the first thing a
prospective user reads. Every stat in it was hand-typed and every one of
them rotted: on 2026-08-31 the intro still claimed "166 live posts"
against a real 198, and the shields.io badge read ``tests-11,400+``
against ~17,200 test functions. Both were true the day they were typed,
and nothing was watching.

The two CLAUDE.md sync scripts already probe exactly these numbers every
night — ``sync-claude-md-stats.py`` from the checked-in repo,
``sync_claude_md_db_stats.py`` from prod Postgres — so putting README on
the same prose-anchored-regex mechanism costs one pattern per claim.
Each script keeps ownership of the claims it can answer; this module
holds only what the two share.

Two things differ from the CLAUDE.md sync, and both live here:

* **Floored, not exact.** CLAUDE.md is internal ground truth and wants the
  precise count. README makes a claim to strangers, so every number is
  rounded DOWN to a round step and suffixed ``+``: 198 live posts renders
  as "190+". Flooring understates on purpose — the claim can never become
  an overstatement between syncs, which is what lets it age gracefully if
  the sync ever stops — and it collapses the churn. An exact README would
  open a docs PR every single night; a floored one moves only when a real
  threshold is crossed.
* **A miss is reported, never assumed correct.** Same rule the DB sync
  learned in #2832: zero regex matches means the surrounding prose was
  reworded and the claim silently stopped syncing, freezing a number that
  still reads as current. ``substitute_anchored`` emits a ``WARNING:``
  line for that case instead of a quiet no-op.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README_MD = ROOT / "README.md"

# How coarsely each claim is floored. Sized so a claim moves at most every
# few weeks at current rates (~1 post/day, ~40 settings/month): fine enough
# that "190+" stays a fair description of 198, coarse enough that the nightly
# sync is a no-op almost every night.
FLOOR_STEPS: Mapping[str, int] = {
    "live_posts": 10,
    "total_posts": 10,
    "pipeline_tasks": 1_000,
    "app_settings": 100,
    "test_functions": 1_000,
}


def floored(value: int, step: int) -> str:
    """Render ``value`` as a conservative ``N+`` claim, rounded DOWN to ``step``.

    ``floored(198, 10) == "190+"``; ``floored(17_238, 1000) == "17,000+"``.
    Thousands separators match README's existing style.
    """
    if step <= 0:
        raise ValueError(f"floor step must be positive, got {step}")
    return f"{(value // step) * step:,}+"


def shield_escape(claim: str) -> str:
    """URL-encode a claim for a shields.io badge path segment.

    shields.io reads ``-`` as its own field separator and needs the comma
    and plus percent-encoded, so ``17,000+`` travels as ``17%2C000%2B``.
    """
    return claim.replace("%", "%25").replace(",", "%2C").replace("+", "%2B")


def substitute_anchored(
    text: str,
    specs: Iterable[tuple[str, str, str]],
) -> tuple[str, list[str]]:
    """Apply prose-anchored rewrites. Returns ``(new_text, changes)``.

    ``specs`` is an iterable of ``(name, pattern, replacement)``. Each
    pattern is substituted at most once, and the replacement is applied
    literally — a lambda, not a template string, so a ``\\`` or ``\\1`` in a
    value can never be read as a backreference.

    ``changes`` carries one human-readable line per rewrite, for the PR
    body. A pattern that matches nothing yields a ``WARNING:`` line naming
    the dead anchor rather than passing silently as "already correct" —
    the #2832 failure mode, where a reworded sentence froze a count that
    kept reading as current.
    """
    changes: list[str] = []
    for name, pattern, replacement in specs:
        new, n = re.subn(pattern, lambda _m, r=replacement: r, text, count=1)
        if not n:
            changes.append(
                f"WARNING: anchor not found for {name} — its README.md wording "
                f"changed, so that claim is no longer synced. Update the "
                f"pattern in the owning sync script.",
            )
            continue
        if new != text:
            changes.append(f"{name} ->{replacement}")
            text = new
    return text, changes


def is_warning(change: str) -> bool:
    """True for a ``changes`` entry that reports a dead anchor, not a rewrite."""
    return change.startswith("WARNING:")
