"""PSU power-source selection + watchdog transitions for the brain's
electricity-cost calc.

Extracted from ``brain_daemon.log_electricity_cost`` so the priority chain
(Shelly outlet meter → always-on iCUE CSV tap → software estimate → static
floor) and the graduated, flap-suppressed alert transitions are unit-testable
without importing the whole daemon. The daemon owns the I/O (exporter scrape,
Telegram/Discord, the durable degraded-streak counter); this module owns the
decision logic plus the one DB read for the iCUE fallback.

Background: true wall power is now metered by a **Shelly smart plug** on the
outlet (emitted as ``psu_total_power_watts`` by the host exporter). It replaced
HWiNFO's HX1500i USB read, which was retired 2026-07-10 because it contended
with iCUE over the Corsair USB-HID. iCUE's own CSV export (``tap.corsair_csv``
→ ``sensor_samples``) is the always-on fallback before we resort to a software
estimate.

Debounce (2026-07-12): the host exporter collects ``/metrics`` on a background
cadence and serves a cached snapshot, but a genuinely brief outage can still
drop a single cycle. So a degraded source must persist for
``degraded_cycles_before_page`` consecutive brain cycles before it pages — a
one-cycle scrape miss self-heals silently instead of paging Telegram. (Before
this, per-request exporter slowness blowing the brain's 3s scrape timeout paged
~15×/day on transient blips.)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Static floor when no real or estimated wall power is available. A bare
# local PC idles around here.
STATIC_DEFAULT_WATTS = 150.0

# Wall-power source labels, best → worst, with a quality tier each. The tier
# drives graduated alerting: dropping from the primary Shelly meter to the
# always-on iCUE tap is a Discord heads-up (cost stays accurate); losing BOTH
# and falling to the software estimate / static floor is a Telegram page (cost
# is now guesswork) — but only after the debounce window (see
# ``psu_watchdog_transition``).
SOURCE_QUALITY = {
    "shelly": "primary",     # Shelly outlet meter (psu_total_power_watts) — true wall power
    "hx1500i": "primary",    # legacy alias: pre-2026-07 HWiNFO HX1500i read of the same metric
    "icue": "fallback",      # iCUE CSV tap (always-on backup-of-record)
    "estimate": "degraded",  # CPU+GPU+overhead software estimate
    "default": "degraded",   # static floor, no signal at all
}


def select_power_source(psu_watts, icue_watts, estimate_watts):
    """Pick wall-power watts + a source label by priority.

    Shelly outlet meter (primary) → iCUE tap PSU (fallback) → software
    estimate → static floor. A ``0``/``None`` reading is treated as absent so a
    dropped sensor never pins cost at zero.

    ``psu_watts`` is whatever the exporter published as ``psu_total_power_watts``
    (the Shelly outlet meter today; historically HWiNFO's HX1500i read).

    Returns ``(watts: float, source: str)``.
    """
    if psu_watts:
        return float(psu_watts), "shelly"
    if icue_watts:
        return float(icue_watts), "icue"
    if estimate_watts:
        return float(estimate_watts), "estimate"
    return STATIC_DEFAULT_WATTS, "default"


def psu_watchdog_transition(
    prev_source,
    new_source,
    watts,
    *,
    degraded_streak: int = 0,
    degraded_cycles_before_page: int = 3,
):
    """Notifications to emit on a PSU power-source change, with flap-suppression.

    Returns ``(notes, new_degraded_streak)`` where ``notes`` is a list of
    ``{"severity": "critical"|"info", "message": str}`` and
    ``new_degraded_streak`` is the updated consecutive-degraded-cycle counter
    the caller must persist and feed back next cycle.

    ``critical`` → page (Telegram + Discord); ``info`` → Discord-only heads-up.

    Debounce: a *degraded* source (software estimate / static floor) pages
    exactly once — on the cycle its consecutive streak reaches
    ``degraded_cycles_before_page`` — never before (a transient scrape miss in
    the grace window) and never again after (no per-cycle spam). A degraded
    blip that never crosses the threshold recovers silently, so the recovery
    note is suppressed too. Never fires on the first observation
    (``prev_source is None``) so a fresh daemon doesn't false-alarm.
    """
    new_q = SOURCE_QUALITY.get(new_source, "degraded")
    new_streak = degraded_streak + 1 if new_q == "degraded" else 0

    w = f"{watts:.0f}W"

    # No alarm on the very first observation, but start the degraded counter so
    # a box that boots already-degraded still pages after the debounce window
    # rather than never.
    if prev_source is None:
        return [], new_streak

    prev_q = SOURCE_QUALITY.get(prev_source)

    if new_q == "degraded":
        # Page once, exactly when the streak crosses the threshold. Below it
        # we're in the grace window (a momentary scrape miss that self-heals);
        # at/above it after the crossing we've already paged and stay silent.
        if new_streak == degraded_cycles_before_page:
            if new_source == "estimate":
                fallback_desc = "the CPU+GPU software estimate"
            else:
                fallback_desc = f"the static {STATIC_DEFAULT_WATTS:.0f}W floor"
            return [{
                "severity": "critical",
                "message": (
                    f"🚨 No metered PSU power for {new_streak} straight cycles — "
                    f"the Shelly outlet meter and the iCUE tap are both "
                    f"unavailable, so electricity cost has fallen back to "
                    f"{fallback_desc} ({w}). Check the Shelly plug (powered + on "
                    f"the network) and that iCUE CSV logging is running."
                ),
            }], new_streak
        return [], new_streak

    if new_q == "primary":
        # Meter is (back as) primary. Only announce a recovery if we had
        # actually paged — a degraded blip that never crossed the threshold
        # heals silently, so there's no recovery spam to match a page we never
        # sent.
        if degraded_streak >= degraded_cycles_before_page:
            return [{
                "severity": "info",
                "message": (
                    f"✅ PSU wall-power recovered — the Shelly outlet meter is "
                    f"reporting again ({w} wall)."
                ),
            }], new_streak
        return [], new_streak

    # new_q == "fallback" (iCUE tap covering)
    if degraded_streak >= degraded_cycles_before_page:
        # Recovering from a sustained outage, but only as far as the fallback.
        return [{
            "severity": "info",
            "message": (
                f"↗️ PSU partial recovery — the iCUE tap is covering ({w} wall); "
                f"the Shelly meter is still down."
            ),
        }], new_streak
    if prev_q == "primary":
        # Meter just dropped to the always-on iCUE tap. Cost stays accurate, so
        # this is a Discord heads-up, never a Telegram page.
        return [{
            "severity": "info",
            "message": (
                f"⚠️ Shelly meter dropped — covering with the always-on iCUE tap "
                f"({w} wall). Electricity cost stays accurate."
            ),
        }], new_streak
    return [], new_streak


async def fetch_icue_psu_watts(pool, max_age_minutes: int = 90):
    """Latest Corsair PSU input (wall) power from the always-on iCUE CSV
    tap, or ``None`` if no fresh sample within ``max_age_minutes``.

    The iCUE LINK tap (handler ``tap.corsair_csv`` → ``sensor_samples``,
    source ``corsair_csv``) keeps logging while HWiNFO fights iCUE over the
    bus, so it is the reliable fallback. ``psu_power_in`` is the AC wall
    draw — the same quantity the Shelly outlet meter exposes as
    ``psu_total_power_watts``.

    The window default is 90 min, NOT a few minutes: the tap is ingested by
    the worker's ``run_taps`` job, which fires hourly, so ``sensor_samples``
    is refreshed once an hour and a reading is up to ~60 min old between
    ingests. 90 min spans a full cycle plus margin (a delayed/missed fire).
    A stale-but-real wall-power reading still beats the CPU+GPU+overhead
    software estimate for an idle-ish cost metric. Still freshness-gated so a
    genuinely dead tap (iCUE down for hours) falls through to the estimate
    rather than billing on hours-old watts.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT metric_value
            FROM sensor_samples
            WHERE source = 'corsair_csv'
              AND metric_name = 'psu_power_in'
              AND sampled_at > NOW() - make_interval(mins => $1::int)
            ORDER BY sampled_at DESC
            LIMIT 1
            """,
            int(max_age_minutes),
        )
        if row and row["metric_value"] is not None:
            return float(row["metric_value"])
        return None
    except Exception as exc:  # noqa: BLE001 — fallback must never crash the cycle
        # silent-ok: this is the DB fallback for the PSU reading; on error it
        # returns None exactly like a genuine no-data row, so the Grafana power
        # gap is the visible signal. Fires every 5-min cycle — a WARNING would
        # spam (see the PSU alert-storm history). Brain can't emit_finding.
        logger.debug(
            "[BRAIN] iCUE PSU fallback query failed (%s: %s)",
            type(exc).__name__, exc,
        )
        return None
