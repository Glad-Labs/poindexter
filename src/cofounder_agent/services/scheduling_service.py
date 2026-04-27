"""Scheduling service — bulk + single-post queue management for
``scheduled_publisher`` (Glad-Labs/poindexter#147).

The existing ``services/scheduled_publisher.py`` background loop watches
``posts.status = 'scheduled' AND posts.published_at <= NOW()`` and flips
those rows to ``status = 'published'``. This module is the operator-side
layer that *populates* that queue: assigning slots, listing them,
shifting them, clearing them.

Design notes
------------

The original spec (issue #147) referred to a ``posts.publish_after``
column. That column does not exist in the current schema — the live
publisher loop already uses ``posts.published_at`` as the "publish at
this future time" field, gated by ``status = 'scheduled'``. Rather
than introduce a parallel column and a schema migration just to match
the spec's wording, this module writes to the existing
(``status='scheduled'``, ``published_at=<future ts>``) pair so the
in-flight publisher behaviour is unchanged. The public function
signatures use ``when`` / ``start`` / ``shift_by`` parameter names
rather than ``publish_after`` to keep the API agnostic of the
underlying column.

DI contract
-----------

Every public function takes ``pool`` (asyncpg.Pool) and ``site_config``
(SiteConfig) explicitly. There are NO module-level singletons. Tests
construct their own SiteConfig with ``SiteConfig(initial_config=...)``.

Settings the service reads (all DB-first, app_settings):

* ``publish_quiet_hours`` (str, default ``""``) — global default
  quiet-hours window in ``HH:MM-HH:MM`` form. Per-call ``quiet_hours``
  argument overrides this. Empty string = no restriction.
* ``scheduled_publisher_lookahead_minutes`` (int, default 5) — owned by
  the publisher loop, mentioned here so the service does not collide.

Audit
-----

Every public mutation (``assign_slot``, ``assign_batch``, ``shift``,
``clear``) writes a ``pipeline_events`` row with ``event_type``
``schedule.<verb>`` so the dashboard / event bus picks it up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable, Sequence

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result envelopes
# ---------------------------------------------------------------------------


@dataclass
class ScheduleResult:
    """Uniform return shape for every public function in this module."""

    ok: bool
    detail: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0


# ---------------------------------------------------------------------------
# Duration & time parsers
# ---------------------------------------------------------------------------


# Matches one or more <number><unit> components in a duration string,
# e.g. "30m", "1h30m", "90m", "2h", "1d", "45s". Whitespace allowed.
_DURATION_RE = re.compile(r"\s*(\d+)\s*([smhdSMHD])\s*")
_DURATION_UNITS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_duration(value: str | timedelta | int | float) -> timedelta:
    """Parse a duration string into a ``timedelta``.

    Accepts:
    * ``timedelta`` instances (returned as-is)
    * ``int`` / ``float`` (interpreted as seconds)
    * Strings of the form ``30m``, ``2h``, ``1d``, ``45s``, ``1h30m``,
      ``90m`` (compound units, lowercase or upper-case).

    Raises ``ValueError`` on unrecognised input. Empty strings raise
    rather than silently returning a zero duration — silent zero is the
    bug class Matt asked us to avoid.
    """
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    if not isinstance(value, str):
        raise ValueError(f"Unsupported duration type: {type(value).__name__}")

    raw = value.strip()
    if not raw:
        raise ValueError("Empty duration string")

    matches = list(_DURATION_RE.finditer(raw))
    consumed = "".join(m.group(0) for m in matches)
    if not matches or consumed.replace(" ", "") != raw.replace(" ", ""):
        raise ValueError(
            f"Could not parse duration {value!r} — expected forms like "
            f"'30m', '2h', '1d', '45s', '1h30m'."
        )

    total = 0.0
    for m in matches:
        n = int(m.group(1))
        unit = m.group(2).lower()
        total += n * _DURATION_UNITS[unit]
    return timedelta(seconds=total)


_REL_NOW = re.compile(r"^\s*now\s*$", re.IGNORECASE)
_REL_TOMORROW = re.compile(
    r"^\s*tomorrow(?:\s+(.+))?\s*$",
    re.IGNORECASE,
)
_REL_NEXT_WEEKDAY = re.compile(
    r"^\s*next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+(.+))?\s*$",
    re.IGNORECASE,
)
_TIME_OF_DAY = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
    re.IGNORECASE,
)
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _parse_time_of_day(token: str | None) -> time:
    """Parse a clock-time fragment like ``9am``, ``14:00``, ``9:30pm``.

    Defaults to midnight when ``token`` is None or empty (so
    ``tomorrow`` alone means tomorrow at 00:00).
    """
    if not token:
        return time(0, 0)
    m = _TIME_OF_DAY.match(token)
    if not m:
        raise ValueError(f"Could not parse clock time: {token!r}")
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hh < 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"Out-of-range clock time: {token!r}")
    return time(hh, mm)


def parse_when(
    value: str | datetime,
    *,
    now: datetime | None = None,
) -> datetime:
    """Parse an absolute or relative time spec into a tz-aware datetime.

    Accepts:
    * ``datetime`` instances (made tz-aware as UTC if naive)
    * ``"now"``
    * ISO 8601 (``2026-04-28T09:00`` / ``2026-04-28 09:00``, with or
      without timezone offset)
    * ``"tomorrow"``, ``"tomorrow 9am"``, ``"tomorrow 14:00"``
    * ``"next monday"``, ``"next monday 14:00"``

    The optional ``now`` argument lets tests inject a deterministic
    reference. Always returns a UTC-aware datetime.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Empty/invalid time spec: {value!r}")

    raw = value.strip()
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    # "now"
    if _REL_NOW.match(raw):
        return ref

    # "tomorrow [time]"
    m = _REL_TOMORROW.match(raw)
    if m:
        clock = _parse_time_of_day(m.group(1))
        target_date = (ref + timedelta(days=1)).date()
        return datetime.combine(target_date, clock, tzinfo=timezone.utc)

    # "next <weekday> [time]"
    m = _REL_NEXT_WEEKDAY.match(raw)
    if m:
        target_weekday = _WEEKDAYS[m.group(1).lower()]
        clock = _parse_time_of_day(m.group(2))
        # "next monday" means the next occurrence STRICTLY after today.
        days_ahead = (target_weekday - ref.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = (ref + timedelta(days=days_ahead)).date()
        return datetime.combine(target_date, clock, tzinfo=timezone.utc)

    # ISO 8601 with optional space separator.
    iso_candidate = raw.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(iso_candidate)
    except ValueError as e:
        raise ValueError(
            f"Could not parse time {value!r} — try ISO 8601 "
            f"('2026-04-28 09:00'), 'now', 'tomorrow 9am', "
            f"or 'next monday 14:00'."
        ) from e
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


_QUIET_RE = re.compile(r"^\s*(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})\s*$")


def parse_quiet_hours(spec: str) -> tuple[time, time] | None:
    """Parse ``HH:MM-HH:MM`` into a ``(start, end)`` tuple of ``time``.

    Returns None when ``spec`` is empty (= no restriction). Raises
    ``ValueError`` on malformed input rather than silently disabling
    the window — silent fallback would leak posts past the operator's
    intended quiet hours.
    """
    if not spec or not spec.strip():
        return None
    m = _QUIET_RE.match(spec)
    if not m:
        raise ValueError(
            f"Invalid quiet_hours spec {spec!r} — expected HH:MM-HH:MM"
        )
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    for hh, mm in ((h1, m1), (h2, m2)):
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError(f"Out-of-range time in quiet_hours: {spec!r}")
    return time(h1, m1), time(h2, m2)


def _is_in_quiet_window(
    moment: datetime,
    window: tuple[time, time] | None,
) -> bool:
    """True iff ``moment``'s clock-time falls inside the quiet window.

    Handles overnight windows (e.g. 22:00-07:00) by checking either
    "after start" or "before end" when start > end.
    """
    if window is None:
        return False
    start, end = window
    t = moment.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= t < end
    # Overnight — wraps midnight.
    return t >= start or t < end


def _next_allowed(
    moment: datetime,
    window: tuple[time, time] | None,
) -> datetime:
    """Advance ``moment`` past the quiet-hours window, if any.

    If the moment is outside the window, returns it unchanged. Otherwise
    advances to the window's end on the same (or next) calendar day.
    """
    if window is None or not _is_in_quiet_window(moment, window):
        return moment
    start, end = window
    same_day_end = moment.replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0
    )
    if start <= end:
        # Daytime window — exit is later today.
        return same_day_end
    # Overnight window. If we're after the start time, the exit is
    # tomorrow at `end`. If we're before `end` already today, exit is
    # today at `end`.
    t = moment.timetz().replace(tzinfo=None)
    if t >= start:
        return same_day_end + timedelta(days=1)
    return same_day_end


# ---------------------------------------------------------------------------
# Slot generator
# ---------------------------------------------------------------------------


def generate_slots(
    *,
    start: datetime,
    interval: timedelta,
    count: int,
    quiet_hours: tuple[time, time] | None = None,
) -> list[datetime]:
    """Compute ``count`` publish slots starting at ``start`` stepping
    ``interval`` apart, skipping any slot that lands inside
    ``quiet_hours``. Displaced slots move to the next allowed time, and
    subsequent slots step from there.

    Pure function — no I/O. Useful as a reusable primitive and as the
    unit of measure for the batch tests.
    """
    if count <= 0:
        return []
    if interval.total_seconds() <= 0:
        raise ValueError(
            f"Interval must be positive, got {interval} "
            f"({interval.total_seconds()}s)"
        )

    slots: list[datetime] = []
    cursor = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    for _ in range(count):
        cursor = _next_allowed(cursor, quiet_hours)
        slots.append(cursor)
        cursor = cursor + interval
    return slots


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _emit_event(
    pool: Any,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Insert a ``pipeline_events`` row. Silent failure here is logged
    but does not abort the calling action — the schedule write itself
    is the source of truth, the event row is for observability."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pipeline_events (event_type, payload)
                VALUES ($1, $2::jsonb)
                """,
                event_type,
                json.dumps(payload, default=str),
            )
    except Exception as e:  # pragma: no cover — best-effort log
        logger.warning(
            "[scheduling] Failed to write pipeline_events row %s: %s",
            event_type, e,
        )


def _normalise_post_id(post_id: Any) -> str:
    """Stringify a post id so callers can pass UUIDs, ints, or strings.

    Returning a string keeps the SQL parameter type uniform — asyncpg
    will cast on insert.
    """
    return str(post_id)


# ---------------------------------------------------------------------------
# Public API — assign_slot
# ---------------------------------------------------------------------------


async def assign_slot(
    post_id: Any,
    when: datetime | str,
    *,
    pool: Any,
    site_config: Any,
    force: bool = False,
) -> ScheduleResult:
    """Assign a single post to a specific publish time.

    Sets ``posts.status = 'scheduled'`` and ``posts.published_at = when``
    so the existing ``scheduled_publisher`` background loop will pick
    the post up at the right time. Refuses to overwrite an existing
    schedule unless ``force=True``.

    Returns a ScheduleResult; ``ok=False`` when the post does not exist
    or already has a schedule and ``force`` is not set.
    """
    pid = _normalise_post_id(post_id)
    target = parse_when(when) if isinstance(when, str) else when
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, slug, title, status, published_at FROM posts WHERE id::text = $1",
            pid,
        )
        if row is None:
            return ScheduleResult(
                ok=False,
                detail=f"Post {pid} not found",
            )
        already_scheduled = (
            row["status"] == "scheduled"
            and row["published_at"] is not None
            and row["published_at"] > datetime.now(timezone.utc)
        )
        if already_scheduled and not force:
            return ScheduleResult(
                ok=False,
                detail=(
                    f"Post {pid} already scheduled for "
                    f"{row['published_at'].isoformat()}; pass force=True "
                    f"to overwrite."
                ),
                rows=[dict(row)],
            )

        await conn.execute(
            """
            UPDATE posts
               SET status = 'scheduled',
                   published_at = $2,
                   updated_at = NOW()
             WHERE id::text = $1
            """,
            pid,
            target,
        )

    await _emit_event(
        pool,
        "schedule.assigned",
        {
            "post_id": pid,
            "slug": row["slug"],
            "title": row["title"],
            "when": target.isoformat(),
            "force": force,
            "previous_status": row["status"],
        },
    )

    return ScheduleResult(
        ok=True,
        detail=f"Scheduled {pid} for {target.isoformat()}",
        rows=[
            {
                "post_id": pid,
                "slug": row["slug"],
                "title": row["title"],
                "published_at": target,
                "status": "scheduled",
            }
        ],
        count=1,
    )


# ---------------------------------------------------------------------------
# Public API — assign_batch
# ---------------------------------------------------------------------------


_VALID_ORDER_COLS = {
    # "Approved at" is recorded on the source content_tasks row; the
    # ``posts`` table doesn't carry that column, so we approximate it
    # with the post's creation time. ``content_tasks`` and ``posts`` are
    # joined via metadata in production but for queue ordering the
    # creation timestamp on the ``posts`` row is the same monotonic
    # signal.
    "approved_at": "created_at",
    "created_at": "created_at",
    "id": "id",
    "title": "title",
}


async def assign_batch(
    *,
    count: int,
    interval: timedelta | str,
    start: datetime | str,
    quiet_hours: str | None = None,
    ordered_by: str = "approved_at",
    pool: Any,
    site_config: Any,
    force: bool = False,
) -> ScheduleResult:
    """Assign sequential publish slots to the next ``count`` approved
    posts.

    A post is eligible when:
      * ``posts.status = 'approved'`` (or ``'awaiting_approval'``;
        operators commonly use either name in the existing pipeline,
        but the canonical post status flow is ``draft → approved →
        scheduled → published``), AND
      * ``posts.published_at IS NULL`` (no slot yet) OR ``force=True``.

    Eligible posts are read in ``ordered_by`` order (default
    ``approved_at`` → ``created_at``). For each post the next slot is
    computed from ``start`` stepping by ``interval``, skipping any time
    that falls inside the quiet-hours window.

    When ``count`` is larger than the number of eligible posts, only
    the available posts are scheduled and ``detail`` reports the gap.
    When zero posts are eligible the result is ``ok=False`` with an
    informative ``detail`` (Matt's "fail loud" rule).
    """
    if count <= 0:
        return ScheduleResult(ok=False, detail=f"count must be > 0 (got {count})")

    interval_td = parse_duration(interval) if isinstance(interval, str) else interval
    start_dt = parse_when(start) if isinstance(start, str) else start
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    quiet_spec = quiet_hours
    if quiet_spec is None:
        quiet_spec = site_config.get("publish_quiet_hours", "")
    quiet_window = parse_quiet_hours(quiet_spec) if quiet_spec else None

    order_col = _VALID_ORDER_COLS.get(ordered_by)
    if order_col is None:
        return ScheduleResult(
            ok=False,
            detail=(
                f"Unsupported ordered_by={ordered_by!r}. "
                f"Allowed: {sorted(_VALID_ORDER_COLS.keys())}"
            ),
        )

    # Pull eligible posts.
    if force:
        where = "status IN ('approved', 'awaiting_approval')"
    else:
        where = (
            "status IN ('approved', 'awaiting_approval') "
            "AND published_at IS NULL"
        )
    sql = (
        f"SELECT id, slug, title, status, published_at, created_at "
        f"  FROM posts WHERE {where} "
        f"ORDER BY {order_col} ASC LIMIT $1"
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, count)

    if not rows:
        return ScheduleResult(
            ok=False,
            detail=(
                "No eligible posts to schedule "
                "(status='approved' AND published_at IS NULL). "
                "Approve posts first, or pass force=True to overwrite "
                "existing schedules."
            ),
        )

    slots = generate_slots(
        start=start_dt,
        interval=interval_td,
        count=len(rows),
        quiet_hours=quiet_window,
    )

    scheduled: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for row, slot in zip(rows, slots):
                await conn.execute(
                    """
                    UPDATE posts
                       SET status = 'scheduled',
                           published_at = $2,
                           updated_at = NOW()
                     WHERE id = $1
                    """,
                    row["id"],
                    slot,
                )
                scheduled.append({
                    "post_id": str(row["id"]),
                    "slug": row["slug"],
                    "title": row["title"],
                    "published_at": slot,
                    "status": "scheduled",
                })

    await _emit_event(
        pool,
        "schedule.batch_assigned",
        {
            "count_requested": count,
            "count_scheduled": len(scheduled),
            "interval_seconds": int(interval_td.total_seconds()),
            "start": start_dt.isoformat(),
            "quiet_hours": quiet_spec or "",
            "ordered_by": ordered_by,
            "force": force,
            "post_ids": [s["post_id"] for s in scheduled],
        },
    )

    detail = (
        f"Scheduled {len(scheduled)} post(s) every "
        f"{int(interval_td.total_seconds())}s starting "
        f"{start_dt.isoformat()}"
    )
    if len(scheduled) < count:
        detail += (
            f" (requested {count}, only {len(scheduled)} eligible — "
            f"approve more posts to fill the queue)"
        )
    return ScheduleResult(
        ok=True,
        detail=detail,
        rows=scheduled,
        count=len(scheduled),
    )


# ---------------------------------------------------------------------------
# Public API — list_scheduled / show_scheduled
# ---------------------------------------------------------------------------


async def list_scheduled(
    *,
    pool: Any,
    upcoming_only: bool = True,
) -> ScheduleResult:
    """Return rows for every post with a populated schedule.

    By default only future / pending slots are returned (the operator
    case "what's coming up?"). Pass ``upcoming_only=False`` to include
    historical schedules — useful for audit / debugging.
    """
    if upcoming_only:
        sql = (
            "SELECT id::text AS post_id, slug, title, published_at, status "
            "  FROM posts "
            " WHERE status = 'scheduled' "
            "   AND published_at IS NOT NULL "
            "   AND published_at >= NOW() "
            " ORDER BY published_at ASC"
        )
    else:
        sql = (
            "SELECT id::text AS post_id, slug, title, published_at, status "
            "  FROM posts "
            " WHERE published_at IS NOT NULL "
            " ORDER BY published_at ASC"
        )

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)

    out = [dict(r) for r in rows]
    return ScheduleResult(
        ok=True,
        detail=f"{len(out)} scheduled post(s)",
        rows=out,
        count=len(out),
    )


async def show_scheduled(
    post_id: Any,
    *,
    pool: Any,
) -> ScheduleResult:
    """Return the schedule detail for a single post."""
    pid = _normalise_post_id(post_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text AS post_id, slug, title, published_at, status,
                   created_at, updated_at
              FROM posts
             WHERE id::text = $1
            """,
            pid,
        )
    if row is None:
        return ScheduleResult(ok=False, detail=f"Post {pid} not found")
    out = dict(row)
    return ScheduleResult(
        ok=True,
        detail=(
            f"{out['status']} — "
            f"{out['published_at'].isoformat() if out['published_at'] else 'unscheduled'}"
        ),
        rows=[out],
        count=1,
    )


# ---------------------------------------------------------------------------
# Public API — shift / clear
# ---------------------------------------------------------------------------


def _normalise_id_list(post_ids: Iterable[Any] | None) -> list[str] | None:
    if post_ids is None:
        return None
    return [_normalise_post_id(p) for p in post_ids]


async def shift(
    *,
    by_delta: timedelta | str,
    post_ids: Sequence[Any] | None = None,
    pool: Any,
    site_config: Any,
) -> ScheduleResult:
    """Shift the schedule of one or more posts by ``by_delta``.

    When ``post_ids=None`` every still-future scheduled post is shifted
    (i.e. anything with ``status='scheduled' AND published_at >= NOW()``).
    Past slots are intentionally left alone — shifting an already-published
    post would not un-publish it.
    """
    delta = parse_duration(by_delta) if isinstance(by_delta, str) else by_delta
    ids = _normalise_id_list(post_ids)

    async with pool.acquire() as conn:
        if ids is None:
            rows = await conn.fetch(
                """
                UPDATE posts
                   SET published_at = published_at + $1::interval,
                       updated_at = NOW()
                 WHERE status = 'scheduled'
                   AND published_at IS NOT NULL
                   AND published_at >= NOW()
                 RETURNING id::text AS post_id, slug, title, published_at, status
                """,
                delta,
            )
        else:
            rows = await conn.fetch(
                """
                UPDATE posts
                   SET published_at = published_at + $2::interval,
                       updated_at = NOW()
                 WHERE id::text = ANY($1::text[])
                   AND published_at IS NOT NULL
                 RETURNING id::text AS post_id, slug, title, published_at, status
                """,
                ids,
                delta,
            )

    out = [dict(r) for r in rows]
    if not out:
        return ScheduleResult(
            ok=False,
            detail=(
                "No scheduled posts matched the shift. "
                "(Hint: --all only shifts posts that are still in the future.)"
            ),
        )

    await _emit_event(
        pool,
        "schedule.shifted",
        {
            "count": len(out),
            "delta_seconds": int(delta.total_seconds()),
            "post_ids": [r["post_id"] for r in out],
            "scope": "selection" if ids else "all_future",
        },
    )

    return ScheduleResult(
        ok=True,
        detail=(
            f"Shifted {len(out)} post(s) by "
            f"{int(delta.total_seconds())}s"
        ),
        rows=out,
        count=len(out),
    )


async def clear(
    *,
    post_ids: Sequence[Any] | None = None,
    pool: Any,
    site_config: Any,
) -> ScheduleResult:
    """Clear the schedule on one or more posts.

    "Clearing" sets ``published_at = NULL`` and rolls the status back
    to ``'approved'`` so the post returns to the queue and can be
    re-scheduled. With ``post_ids=None`` clears every future-dated
    scheduled post.
    """
    ids = _normalise_id_list(post_ids)

    async with pool.acquire() as conn:
        if ids is None:
            rows = await conn.fetch(
                """
                UPDATE posts
                   SET status = 'approved',
                       published_at = NULL,
                       updated_at = NOW()
                 WHERE status = 'scheduled'
                   AND published_at IS NOT NULL
                   AND published_at >= NOW()
                 RETURNING id::text AS post_id, slug, title, status
                """,
            )
        else:
            rows = await conn.fetch(
                """
                UPDATE posts
                   SET status = 'approved',
                       published_at = NULL,
                       updated_at = NOW()
                 WHERE id::text = ANY($1::text[])
                   AND status = 'scheduled'
                 RETURNING id::text AS post_id, slug, title, status
                """,
                ids,
            )

    out = [dict(r) for r in rows]
    if not out:
        return ScheduleResult(
            ok=False,
            detail="No scheduled posts matched the clear.",
        )

    await _emit_event(
        pool,
        "schedule.cleared",
        {
            "count": len(out),
            "post_ids": [r["post_id"] for r in out],
            "scope": "selection" if ids else "all_future",
        },
    )

    return ScheduleResult(
        ok=True,
        detail=f"Cleared schedule on {len(out)} post(s)",
        rows=out,
        count=len(out),
    )
