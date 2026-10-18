"""Upload slots: an approved video goes public at the next set time, not at approval.

Earned 2026-10-18. Videos went public the moment the operator approved them
(recent uploads: Fri 19:50, Tue 12:40, Fri 16:21, Wed 21:01...), and daily
views spiked on upload days and fell to almost nothing between them. A steady
time per type gives the channel a rhythm the audience and the recommender can
learn.

The upload itself still happens right after approval. What changes is
visibility: the video is uploaded **private with ``publishAt``** (YouTube's
own scheduled publishing, which the adapter already supported), and YouTube
makes it public at the slot. That needs nothing of ours to be running at the
slot time, unlike holding the file back.

``youtube_upload_slots`` lists the times per type, operator-local
(``operator_timezone``): ``short=12:00,19:00; long=17:00``. A type with no
times publishes immediately, as before. Each upload takes the earliest slot at
least ``youtube_upload_slot_min_lead_minutes`` away that no other upload of the
same type holds, so several approvals in one evening spread over the next
slots instead of landing together. The chosen time is stamped on the video's
``media_assets.metadata.youtube_publish_at``, which is how later uploads see
the slot as taken and how the YouTube board lists what is queued.

The slot scan is the social scheduler's (``social_drafts.next_prime_slot``),
so the two queues pick slots the same way.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from poindexter.services.clock import resolve_operator_tz
from poindexter.services.logger_config import get_logger
from poindexter.services.social_drafts import next_prime_slot
from poindexter.utils.exception_format import describe_exception

logger = get_logger(__name__)

PUBLISH_AT_KEY = "youtube_publish_at"
_MEDIUM_KEYS = {"short": "video_short", "long": "video"}


def parse_slots(spec: str | None) -> dict[str, list[_dt.time]]:
    """``"short=12:00,19:00; long=17:00"`` → ``{"short": [12:00, 19:00], "long": [17:00]}``.

    Lenient per entry, like the social prime times: an unknown type or a bad
    clock time is logged and dropped, so one typo costs that entry, not the
    whole schedule. Times are sorted and de-duplicated.
    """
    out: dict[str, list[_dt.time]] = {}
    for chunk in (spec or "").split(";"):
        piece = chunk.strip()
        if not piece:
            continue
        key, sep, raw_times = piece.partition("=")
        key = key.strip().lower()
        if not sep or key not in _MEDIUM_KEYS:
            logger.warning(
                "[youtube_upload_slots] skipping %r (expected short=HH:MM,... or long=HH:MM,...)", piece,
            )
            continue
        times: set[_dt.time] = set()
        for raw in raw_times.split(","):
            token = raw.strip()
            if not token:
                continue
            try:
                times.add(_dt.datetime.strptime(token, "%H:%M").time())
            except ValueError:
                logger.warning("[youtube_upload_slots] bad time %r for %s — skipping", token, key)
        if times:
            out[key] = sorted(times)
    return out


def slots_enabled(site_config: Any) -> bool:
    raw = site_config.get("youtube_upload_slots_enabled", "true") if site_config is not None else "true"
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def _lead(site_config: Any) -> _dt.timedelta:
    try:
        minutes = int(site_config.get("youtube_upload_slot_min_lead_minutes", 30))
    except (TypeError, ValueError):
        minutes = 30
    return _dt.timedelta(minutes=max(0, minutes))


_TAKEN_SQL = """
SELECT metadata->>'youtube_publish_at' AS publish_at
  FROM media_assets
 WHERE type = $1
   AND metadata ? 'youtube_publish_at'
"""


async def _taken(pool: Any, asset_type: str, now: _dt.datetime) -> set[_dt.datetime]:
    """Future slots already held by uploads of this type.

    A malformed stamp is skipped rather than failing the query; a query
    failure returns no taken slots (two uploads may then share a slot, which
    is better than not uploading).
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_TAKEN_SQL, asset_type)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("[youtube_upload_slots] taken-slot query failed: %s", describe_exception(exc))
        return set()
    taken: set[_dt.datetime] = set()
    for r in rows:
        try:
            when = _dt.datetime.fromisoformat(str(r["publish_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is not None and when > now:
            taken.add(when)
    return taken


async def next_upload_slot(
    pool: Any, site_config: Any, *, shorts: bool, now: _dt.datetime | None = None,
) -> _dt.datetime | None:
    """The UTC time this upload should go public, or ``None`` to publish now.

    ``None`` when slots are off, the type has no times, or nothing is free in
    the scan window (the social scheduler's 14 days).
    """
    if not slots_enabled(site_config):
        return None
    times = parse_slots(site_config.get("youtube_upload_slots", "")).get("short" if shorts else "long")
    if not times:
        return None
    tz = resolve_operator_tz(site_config.get("operator_timezone", "UTC"))
    now = now or _dt.datetime.now(_dt.timezone.utc)
    taken = {t.astimezone(tz) for t in await _taken(pool, _MEDIUM_KEYS["short" if shorts else "long"], now)}
    slot = next_prime_slot((now + _lead(site_config)).astimezone(tz), times, taken)
    if slot is None:
        logger.warning(
            "[youtube_upload_slots] no free %s slot in the scan window — publishing immediately",
            "short" if shorts else "long",
        )
        return None
    return slot.astimezone(_dt.timezone.utc)


def rfc3339(when: _dt.datetime) -> str:
    """``publishAt`` format: UTC, seconds, ``Z``."""
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def record_slot(pool: Any, asset_id: Any, when: _dt.datetime) -> None:
    """Stamp the slot on the video's row, so later uploads see it as taken."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE media_assets
               SET metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object($2::text, $3::jsonb)
             WHERE id = $1::uuid
            """,
            str(asset_id), PUBLISH_AT_KEY, json.dumps(rfc3339(when)),
        )


__all__ = [
    "PUBLISH_AT_KEY",
    "next_upload_slot",
    "parse_slots",
    "record_slot",
    "rfc3339",
    "slots_enabled",
]
