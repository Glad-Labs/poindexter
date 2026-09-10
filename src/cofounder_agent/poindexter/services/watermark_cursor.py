"""The high-water-mark rule for pulling from an eventually-visible source.

Both Cloudflare Analytics Engine ingests (``sync_cloudflare_analytics`` →
``page_views``, ``sync_affiliate_clicks`` → ``affiliate_link_clicks``) walk
their source with a cursor:

.. code-block:: sql

    WHERE timestamp > toDateTime('{since}', 'UTC')

**CF AE does not make a data point queryable the instant ``writeDataPoint``
returns.** There is a non-zero, non-uniform visibility delay. That single
fact is the hazard: a row that is *written* but not yet *visible* when a poll
runs is silently absent from the result set, and if that poll then advances
the cursor past the row's own timestamp, the row is below the cursor
**forever** the moment it appears. Nothing errors. Nothing retries. The row
is simply never selected again.

Caught in the wild 2026-08-31 (stack#3523) with a natural control pair — two
identical beacons, both confirmed present in AE:

===========  ================  ==============================================
Written      Preceding poll    Outcome
===========  ================  ==============================================
``19:32:46`` ``19:33:09``      **Lost** — poll ran 23 s after the write, saw
                               nothing, moved the cursor to ``19:33:09``.
``19:45:04`` ``19:43:09``      **Ingested** — written after its preceding
                               poll, so nothing stepped over it.
===========  ================  ==============================================

40 genuine reader page views were lost this way in August alone, 7.3% of the
rows that should have been ingested.

The rule
--------
``next_high_water`` is that fix, and it belongs to BOTH jobs — one rule, one
place, so a fix or a re-tune can never reach only half the ingest surface
(the same reason ``services/net_transient.py`` holds their shared
transient-network posture).

Advancing the cursor to a bare ``now()`` on an empty response is the worst
case, because on a low-traffic site nearly every poll is empty — but the
non-empty path shares the race: visibility is not strictly ordered, so a row
written at ``max_ts - 2s`` can surface after the row at ``max_ts`` that set
the cursor. Hence the cap applies to both.

See :doc:`docs/architecture/analytics-ingestion-lag.md`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# One poll interval for both consumers. Wide enough to cover every AE
# ingestion delay observed here, and it makes the re-pull overlap exactly one
# cycle's worth of rows.
DEFAULT_INGESTION_LAG_SECONDS = 300


def is_future_cursor(cursor: datetime, now: datetime) -> bool:
    """True when a stored cursor sits ahead of wall-clock, i.e. is not real.

    You cannot have already ingested rows that do not exist yet, so a future
    cursor is always corrupt — in practice a hand-edit typo on the recovery
    runbook (``poindexter settings set <key> <iso>``), which is a documented
    operator action, not a hypothetical.

    It matters because :func:`next_high_water` is deliberately monotonic: the
    cursor can never walk backwards on its own, so a future value wedges the
    ingest permanently and SILENTLY — the query ``timestamp > <future>`` just
    returns nothing forever, which is indistinguishable from quiet traffic.
    Callers treat this like an unparseable value and fall back to their
    lookback window.
    """
    return cursor > now


def next_high_water(
    *,
    since: datetime,
    observed_max: datetime | None,
    now: datetime,
    lag_margin_seconds: int,
) -> datetime:
    """Pick the next high-water mark without opening a data-loss window.

    ``observed_max`` is the newest timestamp this batch **saw** — including
    rows the caller then dropped as bots or skipped as already-present. A row
    that was read and deliberately not written has still been processed, and
    passing only the rows actually INSERTED is a bug with teeth: a fully
    deduped batch would report ``None``, the horizon would apply, and the
    cursor would jump past rows the caller's ``LIMIT`` never reached. That is
    the backfill shape exactly (rewind the cursor, re-pull a mostly-ingested
    window), so it is not hypothetical. Pass ``None`` only when the batch was
    genuinely empty or wholly unreadable.

    Three properties, each load-bearing:

    * **Never past the visibility horizon** — capping at
      ``now - lag_margin_seconds`` leaves any row still inside its ingestion
      delay ABOVE the cursor, so the next cycle re-selects it instead of
      stepping over it forever.
    * **Never backwards** — ``max(since, ...)`` means a widened margin, a
      clock skew, a stale row in the batch, or a legacy watermark written by
      the pre-fix code can only STALL the cursor, never rewind it into an
      ever-widening re-scan.
    * **Always progresses** — the source query is ``timestamp > since``
      (strict), so an empty response advances to the horizon and a non-empty
      one advances past everything it read, rather than leaving the cursor
      pinned to re-scan the same batch forever.

    The re-pull overlap this creates is only safe because both callers dedup
    at insert time and recompute (never increment) their rollups. Preserve
    both properties in any new caller.

    ``lag_margin_seconds <= 0`` collapses the horizon onto ``now``, which is
    exactly the pre-2026-08-31 behaviour — an escape hatch should the source
    ever make writes immediately visible, NOT a tuning knob: it re-opens the
    data-loss window.
    """
    horizon = now - timedelta(seconds=max(0, lag_margin_seconds))
    candidate = observed_max if observed_max is not None else now
    return max(since, min(candidate, horizon))
