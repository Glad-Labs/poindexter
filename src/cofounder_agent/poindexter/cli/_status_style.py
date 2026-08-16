"""Colorblind-safe status colors for the ``poindexter`` CLI.

Several commands render a status column whose **colour is the signal** —
``posts list``, ``tasks list``, ``doctor``, ``costs budget``. Each grew its
own ad-hoc ``{status: colour}`` dict, and each independently reached for the
traffic-light palette (green = good, yellow = warn, red = bad).

That palette is unreadable for red-green colour vision deficiency, which
affects ~8% of men — including this project's operator. Under deuteranomaly
green and red converge toward a muddy yellow-brown, so a health report whose
entire meaning is carried by green-vs-red conveys nothing. ``tasks list`` was
the worst case: ``approved`` (green) and ``published`` (bright_green) were
near-identical even with normal colour vision, and both sat opposite
``failed`` (red).

The fix is to drop green from semantic maps entirely. Red-green deficiency
leaves the **blue-yellow axis** intact, so cyan-vs-yellow-vs-red separates
cleanly on hue for every common CVD type, and the roles below add brightness
as a second channel so the distinctions survive greyscale too.

Scope
-----

This module governs colour that **encodes a category**. Decorative one-off
success messages elsewhere in the CLI (``click.secho("Published: …",
fg="green")``) are deliberately left alone: a lone green line distinguishes
nothing, so it carries no accessibility cost, and rewriting ~85 call sites
would be churn without a functional gain.

Adding a status? Map it to a role below rather than picking a colour — that
is what keeps the confusion pairs from creeping back in. If per-operator
tuning is ever wanted (a protanope may prefer a different pairing), this
module is the single seam to parameterise; it is intentionally *not* a
DB-backed setting today, since the CLI renders colour on paths that run
before — and without — a database connection.
"""

from __future__ import annotations

# Semantic roles, expressed as click colour names. Callers map a status to a
# role; they should not reach for a raw colour.
#
# Deliberately no green: it is the half of the red-green pair that carries
# meaning in every map below, so removing it is what makes the rest legible.
SUCCESS = "cyan"           # Reached a good terminal state
LIVE = "bright_cyan"       # Good AND publicly visible — the brightest state
ACTIVE = "yellow"          # In flight / queued / transient
ATTENTION = "bright_magenta"  # Blocked on an operator decision
FAILURE = "red"            # Errored or rejected
NEUTRAL = "white"          # Inert, no judgement implied
INACTIVE = "bright_black"  # Dimmed: archived, cancelled, suppressed


# ``posts`` lifecycle. ``published`` is LIVE (it is on the public site);
# ``scheduled`` is ACTIVE rather than a second blue, which is what made it
# indistinguishable from ``published`` before.
POST_STATUS: dict[str, str] = {
    "published": LIVE,
    "scheduled": ACTIVE,
    "draft": NEUTRAL,
    "archived": INACTIVE,
}


# ``pipeline_tasks`` lifecycle. ``awaiting_approval`` is the only state that
# blocks on a human, so it gets ATTENTION — previously it was cyan, which
# read as just another in-flight state.
TASK_STATUS: dict[str, str] = {
    "pending": NEUTRAL,
    "in_progress": ACTIVE,
    "awaiting_approval": ATTENTION,
    "approved": SUCCESS,
    "published": LIVE,
    "rejected": FAILURE,
    "rejected_retry": ACTIVE,
    "rejected_final": FAILURE,
    "failed": FAILURE,
    "cancelled": INACTIVE,
    "expired": INACTIVE,
    "dismissed": INACTIVE,
}


# ``doctor`` probe outcomes.
HEALTH_STATUS: dict[str, str] = {
    "ok": SUCCESS,
    "warn": ACTIVE,
    "fail": FAILURE,
    "suppressed": NEUTRAL,
    "stale": ATTENTION,
}


# ``costs budget`` spend status.
BUDGET_STATUS: dict[str, str] = {
    "healthy": SUCCESS,
    "warning": ACTIVE,
    "critical": FAILURE,
}


def color_for(mapping: dict[str, str], status: str | None) -> str:
    """Return the colour for ``status``, falling back to :data:`NEUTRAL`.

    An unknown status is rendered inert rather than guessed at — colouring it
    as success or failure would assert something the CLI does not know.
    """
    if not status:
        return NEUTRAL
    return mapping.get(status, NEUTRAL)
