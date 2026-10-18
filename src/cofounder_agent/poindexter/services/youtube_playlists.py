"""Topic playlists: each YouTube upload joins the playlists its topic matches.

Earned 2026-10-18. The channel had no playlists, while "related videos" was
already its third-biggest view source (162 of ~770 since June). A playlist
keeps a viewer on the next video of the same topic instead of leaving them to
YouTube's picks, and it gives the channel page shelves to browse.

Post categories cannot do the grouping (27 of the 29 uploaded videos are
"Technology", and every one is in the same niche), so the playlists are
declared in the ``youtube_playlists`` setting: a JSON list of
``{"title", "description", "keywords"}``. A video joins every playlist one of
whose keywords appears in its title or SEO keywords, in list order, up to
``youtube_playlist_max_per_video``. A keyword of three characters or fewer
must match a whole word ("ci" never matches "circuit"); a longer one matches
the start of a word ("gpu" matches "gpus").

On YouTube a playlist is found by its exact title among the channel's own and
created on first use (``youtube_playlist_privacy``). A video already in a
playlist is never added again, so every path here is safe to re-run.

Two callers, like captions: :func:`playlists_after_upload` from
``media_distribute`` (best-effort, never raises, ``youtube_playlist_failed`` on
a failure) and :func:`backfill_playlists` for videos already up
(``poindexter integrations youtube playlists``, dry run by default).
The outcome is stamped on ``media_assets.metadata.youtube_playlists``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

from poindexter.services.logger_config import get_logger
from poindexter.services.publish_adapters.youtube import is_quota_exhausted
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = get_logger(__name__)

PLAYLISTS_KEY = "youtube_playlists"
QUOTA_NOT_TRIED = (
    "not tried — the day's YouTube API quota is used up "
    "(resets at midnight Pacific); re-run then"
)
_VIDEO_TYPES = ("video", "video_short")


@dataclass(frozen=True)
class PlaylistRule:
    title: str
    description: str
    keywords: tuple[str, ...]


@dataclass
class PlaylistOutcome:
    video_id: str
    medium: str
    title: str
    playlists: list[str] = field(default_factory=list)
    action: str = ""
    failed: bool = False


class PlaylistConfigError(ValueError):
    """The ``youtube_playlists`` setting is not a list of playlist objects."""


def parse_rules(raw: Any) -> list[PlaylistRule]:
    """``youtube_playlists`` → rules. Raises :class:`PlaylistConfigError` on a bad shape.

    An empty value means no playlists. A rule without a title or without any
    keyword is refused rather than skipped, so a typo in the setting is loud.
    """
    if raw in (None, ""):
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise PlaylistConfigError(f"youtube_playlists is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise PlaylistConfigError("youtube_playlists must be a JSON list of playlist objects")
    rules: list[PlaylistRule] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise PlaylistConfigError(f"youtube_playlists[{i}] is not an object")
        title = str(item.get("title") or "").strip()
        keywords = tuple(
            str(k).strip().lower() for k in (item.get("keywords") or []) if str(k).strip()
        )
        if not title or not keywords:
            raise PlaylistConfigError(f"youtube_playlists[{i}] needs a title and at least one keyword")
        rules.append(PlaylistRule(title, str(item.get("description") or "").strip(), keywords))
    return rules


def _normalise(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "


def _keyword_hits(keyword: str, text: str) -> bool:
    kw = _normalise(keyword).strip()
    if not kw:
        return False
    if len(kw) <= 3:
        return f" {kw} " in text
    return f" {kw}" in text


def match_playlists(text: str, rules: list[PlaylistRule], *, limit: int) -> list[PlaylistRule]:
    """The rules ``text`` matches, in rule order, at most ``limit``."""
    norm = _normalise(text)
    hits = [r for r in rules if any(_keyword_hits(k, norm) for k in r.keywords)]
    return hits[: max(0, limit)]


def _rules_and_limit(site_config: Any) -> tuple[list[PlaylistRule], int, str]:
    rules = parse_rules(site_config.get("youtube_playlists", "") if site_config is not None else "")
    try:
        limit = int(site_config.get("youtube_playlist_max_per_video", 2))
    except (TypeError, ValueError):
        limit = 2
    privacy = str(site_config.get("youtube_playlist_privacy", "public") or "public").strip().lower()
    return rules, limit, privacy


def playlists_enabled(site_config: Any) -> bool:
    raw = site_config.get("youtube_playlists_enabled", "true") if site_config is not None else "true"
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def video_text(title: str, seo_keywords: str = "", extra: str = "") -> str:
    """The text a video's playlists are matched against."""
    return " ".join(t for t in (title, seo_keywords, extra) if t)


async def _stamp(pool: Any, asset_id: Any, status: str, outcome: dict[str, str]) -> None:
    payload = {
        "status": status,
        "playlists": outcome,
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE media_assets
               SET metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object($2::text, $3::jsonb)
             WHERE id = $1::uuid
            """,
            str(asset_id), PLAYLISTS_KEY, json.dumps(payload),
        )


async def playlists_after_upload(
    pool: Any,
    site_config: Any,
    *,
    asset_id: Any,
    video_id: str,
    text: str,
    subject: str = "",
    shorts: bool = False,
) -> str:
    """Add a just-uploaded video to its topic playlists. Never raises.

    Returns the stamped status. A failure (including a broken
    ``youtube_playlists`` setting) is raised as ``youtube_playlist_failed``;
    ``poindexter integrations youtube playlists --apply`` retries it.
    """
    if not playlists_enabled(site_config):
        return "skipped: youtube_playlists_enabled is off"
    outcome: dict[str, str] = {}
    try:
        rules, limit, privacy = _rules_and_limit(site_config)
        matched = match_playlists(text, rules, limit=limit)
        if not matched:
            status = "no playlist matched"
        else:
            from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

            ok, detail, outcome = await YouTubePublishAdapter(site_config=site_config).add_to_playlists(
                video_id=video_id, playlists=[(r.title, r.description) for r in matched], privacy=privacy,
            )
            status = "ok" if ok else f"failed: {detail}"
            if ok and "added" in outcome.values():
                # A first video may have just created a playlist; give it its
                # shelf (a no-op for playlists already shown). Best-effort.
                shelf_ok, shelf_detail, _ = await sync_shelves(site_config)
                if not shelf_ok:
                    logger.warning("[youtube_playlists] shelves not updated: %s", shelf_detail)
    except Exception as exc:  # noqa: BLE001 — playlists never fail an upload
        status = f"failed: {describe_exception(exc)}"
    try:
        if asset_id:
            await _stamp(pool, asset_id, status, outcome)
    except Exception as exc:  # noqa: BLE001 — a lost stamp only costs a re-check
        logger.warning("[youtube_playlists] stamp failed for %s: %s", video_id, describe_exception(exc))
    if status.startswith("failed"):
        emit_finding(
            source="youtube_playlists",
            kind="youtube_playlist_failed",
            title=f"YouTube {'Short' if shorts else 'video'} was not added to its playlists",
            body=(
                f"Video {video_id or '?'} uploaded, but adding it to its playlists failed: "
                f"{status[8:300]}. Retry with `poindexter integrations youtube playlists --apply`."
            ),
            severity="warn",
            dedup_key=f"youtube_playlist_failed:{subject or video_id}:{'short' if shorts else 'long'}",
            extra={"video_id": video_id, "subject": subject, "status": status[:300]},
        )
    return status


def shelves_enabled(site_config: Any) -> bool:
    raw = site_config.get("youtube_playlist_shelves_enabled", "true") if site_config is not None else "true"
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


async def sync_shelves(site_config: Any, *, apply: bool = True) -> tuple[bool, str, dict[str, str]]:
    """Show every configured playlist as a shelf on the channel home page.

    Since 2026-10-18. Shelves come in the order of the ``youtube_playlists``
    setting, appended after any section already on the page; a playlist not
    created yet (no video has joined it) is skipped until one does. Safe to
    re-run: a playlist that already has a shelf is left alone.
    """
    if not shelves_enabled(site_config):
        return True, "youtube_playlist_shelves_enabled is off", {}
    rules, _limit, _privacy = _rules_and_limit(site_config)
    from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

    return await YouTubePublishAdapter(site_config=site_config).ensure_playlist_shelves(
        titles=[r.title for r in rules], apply=apply, force=True,
    )


_BACKFILL_SQL = """
SELECT ma.id, ma.type,
       ma.platform_video_ids->>'youtube' AS video_id,
       ma.metadata->'youtube_playlists'->>'status' AS playlist_status,
       coalesce(nullif(ma.metadata->'youtube_package'->>'title', ''), p.title, ma.title, '') AS title,
       coalesce(p.title, '') AS post_title,
       coalesce(p.seo_keywords, '') AS seo_keywords
  FROM media_assets ma
  LEFT JOIN posts p ON p.id = ma.post_id
 WHERE ma.type = ANY($1::text[])
   AND ma.platform_video_ids ? 'youtube'
   AND ($2::text IS NULL
        OR ma.platform_video_ids->>'youtube' = $2
        OR ma.post_id::text = $2
        OR ma.task_id::text = $2)
 ORDER BY ma.created_at
"""


async def backfill_playlists(
    pool: Any,
    site_config: Any,
    *,
    selector: str | None = None,
    apply: bool = False,
    limit: int | None = None,
) -> list[PlaylistOutcome]:
    """Topic playlists for videos already on YouTube.

    Dry run: list each video's matched playlists; nothing is sent. ``apply``:
    add them (creating any missing playlist). Oldest first, so each playlist
    reads in publishing order. A video already stamped ``ok`` is skipped
    unless named by ``selector``. Raises :class:`PlaylistConfigError` when
    the setting is malformed, before touching any video.
    """
    rules, per_video, privacy = _rules_and_limit(site_config)
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(_BACKFILL_SQL, list(_VIDEO_TYPES), selector)]
    outcomes: list[PlaylistOutcome] = []
    quota_spent = False
    for row in rows:
        if limit is not None and len(outcomes) >= limit:
            break
        if quota_spent:
            outcomes.append(PlaylistOutcome(
                video_id=row["video_id"], medium="short" if row["type"] == "video_short" else "long",
                title=row["title"] or "", action=QUOTA_NOT_TRIED,
            ))
            continue
        matched = match_playlists(
            video_text(row["post_title"], row["seo_keywords"], row["title"]), rules, limit=per_video,
        )
        out = PlaylistOutcome(
            video_id=row["video_id"], medium="short" if row["type"] == "video_short" else "long",
            title=row["title"] or "", playlists=[r.title for r in matched],
        )
        outcomes.append(out)
        if row.get("playlist_status") == "ok" and selector is None:
            out.action = "skipped — already done"
            continue
        if not matched:
            out.action = "no playlist matched"
            continue
        if not apply:
            out.action = "would add — review, then --apply"
            continue
        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        ok, detail, outcome = await YouTubePublishAdapter(site_config=site_config).add_to_playlists(
            video_id=out.video_id, playlists=[(r.title, r.description) for r in matched],
            privacy=privacy, force=True,
        )
        status = "ok" if ok else f"failed: {detail}"
        await _stamp(pool, row["id"], status, outcome)
        out.action = ", ".join(f"{t}: {s}" for t, s in outcome.items()) if ok else status
        out.failed = not ok
        # Every later call would fail the same way and the day's uploads need
        # the quota too, so stop here (2026-10-18).
        quota_spent = not ok and is_quota_exhausted(detail)
    return outcomes


def estimate_quota_units(outcomes: list[PlaylistOutcome]) -> int:
    """Rough --apply cost of these dry-run outcomes: per video one playlist
    listing, and per playlist one membership check + one insert. A playlist
    created on the way adds 50 more each."""
    from poindexter.services.publish_adapters.youtube import QUOTA_UNITS

    per_playlist = QUOTA_UNITS["playlistItems.list"] + QUOTA_UNITS["playlistItems.insert"]
    return sum(
        QUOTA_UNITS["playlists.list"] + per_playlist * len(o.playlists)
        for o in outcomes if o.action.startswith("would add")
    )


__all__ = [
    "PLAYLISTS_KEY",
    "PlaylistConfigError",
    "PlaylistOutcome",
    "PlaylistRule",
    "backfill_playlists",
    "estimate_quota_units",
    "match_playlists",
    "parse_rules",
    "playlists_after_upload",
    "playlists_enabled",
    "shelves_enabled",
    "sync_shelves",
    "video_text",
]
