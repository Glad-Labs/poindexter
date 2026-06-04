"""PSU power-source selection + watchdog transitions for the brain's
electricity-cost calc.

Extracted from ``brain_daemon.log_electricity_cost`` so the priority chain
(HWiNFO HX1500i → always-on iCUE CSV tap → software estimate → static
floor) and the graduated alert transitions are unit-testable without
importing the whole daemon. The daemon owns the I/O (exporter scrape,
Telegram/Discord); this module owns the decision logic plus the one DB
read for the iCUE fallback.

Background: HWiNFO, AIDA64, and iCUE all poll overlapping hardware and
contend over the SMBus/USB-HID, so HWiNFO intermittently loses the
Corsair PSU read. iCUE is always on and keeps logging through the fight
(its CSV is ingested by ``tap.corsair_csv`` into ``sensor_samples``), so
it is the reliable fallback before we resort to a software estimate.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Static floor when no real or estimated wall power is available. A bare
# local PC idles around here.
STATIC_DEFAULT_WATTS = 150.0

# Wall-power source labels, best → worst, with a quality tier each. The
# tier drives graduated alerting: dropping from the primary HWiNFO read to
# the always-on iCUE tap is a Discord heads-up (cost stays accurate);
# losing BOTH and falling to the software estimate is a Telegram page
# (cost is now guesswork).
SOURCE_QUALITY = {
    "hx1500i": "primary",    # HWiNFO reading the HX1500i directly
    "icue": "fallback",      # iCUE CSV tap (always-on backup-of-record)
    "estimate": "degraded",  # CPU+GPU+overhead software estimate
    "default": "degraded",   # static floor, no signal at all
}


def select_power_source(psu_watts, icue_watts, estimate_watts):
    """Pick wall-power watts + a source label by priority.

    HWiNFO PSU (primary) → iCUE tap PSU (fallback) → software estimate →
    static floor. A ``0``/``None`` reading is treated as absent so a
    dropped sensor never pins cost at zero.

    Returns ``(watts: float, source: str)``.
    """
    if psu_watts:
        return float(psu_watts), "hx1500i"
    if icue_watts:
        return float(icue_watts), "icue"
    if estimate_watts:
        return float(estimate_watts), "estimate"
    return STATIC_DEFAULT_WATTS, "default"


def psu_watchdog_transition(prev_source, new_source, watts):
    """Notifications to emit on a PSU power-source change.

    Returns a list of ``{"severity": "critical"|"info", "message": str}``.
    ``critical`` → page (Telegram + Discord); ``info`` → Discord-only
    heads-up. Fires only on quality-tier changes (so ``estimate``↔
    ``default`` is silent), and never on the first observation
    (``prev_source is None``) so a fresh daemon doesn't false-alarm.
    """
    if prev_source is None or prev_source == new_source:
        return []

    prev_q = SOURCE_QUALITY.get(prev_source)
    new_q = SOURCE_QUALITY.get(new_source, "degraded")
    if prev_q == new_q:
        return []

    w = f"{watts:.0f}W"
    if new_q == "primary":
        return [{
            "severity": "info",
            "message": (
                f"✅ PSU primary recovered — HWiNFO is reading the HX1500i "
                f"directly again ({w} wall)."
            ),
        }]
    if new_q == "fallback":
        if prev_q == "primary":
            return [{
                "severity": "info",
                "message": (
                    f"⚠️ HWiNFO lost the HX1500i read — covering with the "
                    f"always-on iCUE tap ({w} wall). Electricity cost stays "
                    f"accurate; the sensor monitors are likely fighting over "
                    f"the SMBus/USB-HID."
                ),
            }]
        return [{
            "severity": "info",
            "message": (
                f"↗️ PSU partial recovery — iCUE tap covering ({w} wall); "
                f"HWiNFO still down."
            ),
        }]
    # new_q == "degraded"
    return [{
        "severity": "critical",
        "message": (
            f"🚨 No real PSU data — HWiNFO and the iCUE tap are BOTH "
            f"unavailable. Electricity cost is on the {new_source} estimate "
            f"({w}). Check that iCUE and HWiNFO are running."
        ),
    }]


async def fetch_icue_psu_watts(pool, max_age_minutes: int = 90):
    """Latest Corsair PSU input (wall) power from the always-on iCUE CSV
    tap, or ``None`` if no fresh sample within ``max_age_minutes``.

    The iCUE LINK tap (handler ``tap.corsair_csv`` → ``sensor_samples``,
    source ``corsair_csv``) keeps logging while HWiNFO fights iCUE over the
    bus, so it is the reliable fallback. ``psu_power_in`` is the AC wall
    draw — the same quantity HWiNFO exposes as ``psu_total_power_watts``
    (PSU Power In).

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
        logger.debug(
            "[BRAIN] iCUE PSU fallback query failed (%s: %s)",
            type(exc).__name__, exc,
        )
        return None
