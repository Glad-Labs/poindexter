"""Chapters for long videos: labelled sections from the video's own transcript.

Earned 2026-10-18. A list of ``M:SS Title`` lines in a YouTube description,
starting at 0:00, becomes chapters: labelled stretches on the progress bar and
"key moments" in Google and YouTube search. Search is one of the channel's two
biggest view sources, and a long video with chapters gets one search result
per section instead of one for the whole video. No upload had any.

The shot list's ``intent`` fields describe pictures, not topics, so chapters
come from the timed transcript the captions step makes
(``youtube_captions.ensure_srt``, kept as ``<video>.srt`` and reused for the
caption track). The director model proposes the split; code holds it to what
YouTube accepts and to the transcript:

- the first chapter starts at 0:00 (YouTube ignores a list that does not);
- every other start is snapped to the start of a real transcript cue, so a
  chapter never begins mid-sentence at a time the model made up;
- starts sit at least ``youtube_chapter_min_seconds`` apart (YouTube's floor
  is 10 s); a chapter that lands too close to the one before is dropped, and
  the list stands if three remain;
- 3 to ``youtube_chapter_max`` chapters, each title at most
  ``youtube_chapter_title_max_chars``, with no number the transcript lacks.

One corrective retry carries the rejection reason (``checked_llm_line``).
After that, no chapters: a wrong chapter list in public is worse than none.

Long videos only; a Short has no chapters. The result is stored on the video's
``media_assets.metadata.youtube_chapters`` and composed into the description
by ``youtube_payload._build_youtube_description`` at upload and on every
``sync-metadata``, so a later re-sync keeps them.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from typing import Any

from poindexter.services.logger_config import get_logger
from poindexter.utils.exception_format import describe_exception

logger = get_logger(__name__)

CHAPTERS_KEY = "youtube_chapters"

_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->")
_CHAPTER_LINE = re.compile(r"^\s*[-*•]?\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*[-–—:|]?\s*(.+?)\s*$")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class ChapterOutcome:
    video_id: str
    title: str
    chapters: str = ""
    action: str = ""
    failed: bool = False


def parse_srt(srt_text: str) -> list[tuple[float, str]]:
    """SRT → ``[(start_seconds, text)]`` in order."""
    cues: list[tuple[float, str]] = []
    for block in re.split(r"\n\s*\n", (srt_text or "").strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if m:
                h, mnt, sec, ms = m.groups()
                start = int(h) * 3600 + int(mnt) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000
                text = " ".join(lines[i + 1:]).strip()
                if text:
                    cues.append((start, text))
                break
    return cues


def transcript_for_prompt(cues: list[tuple[float, str]]) -> str:
    return "\n".join(f"[{int(start)}] {text}" for start, text in cues)


def format_stamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def check_chapters(
    raw: str,
    cues: list[tuple[float, str]],
    *,
    max_chapters: int,
    min_seconds: int,
    max_title_chars: int,
) -> tuple[str, str]:
    """Reduce a model reply to a publishable chapter list, or name why not.

    Returns ``(text, "")`` with ``text`` as ``M:SS Title`` lines, else
    ``("", reason)``.
    """
    if not cues:
        return "", "no transcript"
    starts = [c[0] for c in cues]
    transcript_numbers = set(_NUMBER.findall(" ".join(c[1] for c in cues).replace(",", "")))
    parsed: list[tuple[float, str]] = []
    for line in (raw or "").splitlines():
        m = _CHAPTER_LINE.match(line)
        if not m:
            continue
        h, mnt, sec, title = m.groups()
        when = int(h or 0) * 3600 + int(mnt) * 60 + int(sec)
        title = title.strip().strip('"').rstrip(".").strip()
        if title:
            parsed.append((float(when), title))
    if len(parsed) < 3:
        return "", f"fewer than 3 chapter lines ({len(parsed)})"
    if len(parsed) > max_chapters:
        return "", f"more than {max_chapters} chapters ({len(parsed)})"
    out: list[tuple[float, str]] = []
    for i, (start, title) in enumerate(parsed):
        if len(title) > max_title_chars:
            return "", f"title over {max_title_chars} characters: {title!r}"
        if re.match(r"^(chapter|part|section)\s*\d+\b", title, re.IGNORECASE):
            return "", f"title is a label, not a topic: {title!r}"
        invented = [n for n in _NUMBER.findall(title.replace(",", "")) if n not in transcript_numbers]
        if invented:
            return "", f"number not in the transcript: {invented[0]} in {title!r}"
        if i == 0:
            snapped = 0.0
        else:
            # The latest cue that starts at or before the model's time.
            earlier = [s for s in starts if s <= start + 0.5]
            snapped = earlier[-1] if earlier else starts[0]
        out.append((snapped, title))
    # A chapter that lands too soon after the one before is dropped rather
    # than sinking the whole list: on 2026-10-18 four of nine videos lost every
    # chapter to one crowded final line near the end. Dropping it keeps the
    # rest, provided three still stand.
    kept: list[tuple[float, str]] = [out[0]]
    for start, title in out[1:]:
        if start - kept[-1][0] >= min_seconds:
            kept.append((start, title))
    if len(kept) < 3:
        crowded = next((t for (a, _), (b, t) in zip(out, out[1:], strict=False) if b - a < min_seconds), "")
        return "", f"chapters under {min_seconds}s apart, fewer than 3 left ({crowded!r})"
    out = kept
    return "\n".join(f"{format_stamp(when)} {title}" for when, title in out), ""


def _sc(site_config: Any, key: str, default: Any) -> Any:
    try:
        value = site_config.get(key, default)
    except Exception:  # noqa: BLE001 — a settings read failure uses the default
        return default
    return default if value in (None, "") else value


def _int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(_sc(site_config, key, default))
    except (TypeError, ValueError):
        return default


def chapters_enabled(site_config: Any) -> bool:
    return str(_sc(site_config, "youtube_chapters_enabled", "true")).strip().lower() in {"true", "1", "yes", "on"}


def _model(site_config: Any) -> str:
    return str(
        _sc(site_config, "youtube_chapters_model", "")
        or _sc(site_config, "video_director_model", "")
    ).strip()


async def generate_chapters(
    *, site_config: Any, pool: Any, srt_text: str, title: str, task_id: str | None = None,
    key_terms: str = "",
) -> tuple[str, str]:
    """Chapter text for a transcript, or ``("", why)``. Never raises.

    ``key_terms`` (the post's SEO keywords) gives the model the correct
    spellings of names the speech recognizer misheard: on 2026-10-18 a
    transcript's "Tielang" for TileLang went straight into a chapter title.
    """
    if not chapters_enabled(site_config):
        return "", "youtube_chapters_enabled is off"
    model = _model(site_config)
    if not model:
        return "", "no youtube_chapters_model / video_director_model set"
    cues = parse_srt(srt_text)
    min_seconds = max(10, _int(site_config, "youtube_chapter_min_seconds", 20))
    if not cues or cues[-1][0] < 3 * min_seconds:
        return "", "video too short for chapters"
    max_chapters = max(3, _int(site_config, "youtube_chapter_max", 8))
    max_title = max(20, _int(site_config, "youtube_chapter_title_max_chars", 60))

    from poindexter.services.checked_llm_line import generate_checked_line
    from poindexter.services.prompt_manager import get_prompt_manager

    prompts = get_prompt_manager()
    limits = {"max_chapters": max_chapters, "min_seconds": min_seconds, "max_title_chars": max_title}
    return await generate_checked_line(
        pool=pool,
        model=model,
        prompt=prompts.get_prompt(
            "video.chapters", title=title or "(untitled)", transcript=transcript_for_prompt(cues),
            key_terms=key_terms or "(none)", **limits,
        ),
        check=lambda raw: check_chapters(
            raw, cues, max_chapters=max_chapters, min_seconds=min_seconds, max_title_chars=max_title,
        ),
        fix_prompt=lambda reason: prompts.get_prompt("video.chapters_fix", reason=reason, **limits),
        phase="youtube_chapters",
        label="chapters",
        temperature=float(_sc(site_config, "youtube_chapters_temperature", 0.3)),
        max_tokens=max(64, _int(site_config, "youtube_chapters_max_tokens", 400)),
        timeout_s=max(5.0, float(_sc(site_config, "youtube_chapters_timeout_seconds", 120))),
        disable_thinking=str(_sc(site_config, "video_director_disable_thinking", "true")).lower() == "true",
        task_id=task_id,
    )


async def store_chapters(pool: Any, asset_id: Any, text: str, note: str) -> None:
    payload = {
        "text": text,
        "note": note,
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE media_assets
               SET metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object($2::text, $3::jsonb)
             WHERE id = $1::uuid
            """,
            str(asset_id), CHAPTERS_KEY, json.dumps(payload),
        )


async def load_chapters(pool: Any, asset_id: Any) -> str:
    """The stored chapter text for a video asset, or ``""``."""
    if not asset_id:
        return ""
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT metadata->'youtube_chapters'->>'text' FROM media_assets WHERE id = $1::uuid",
            str(asset_id),
        )
    return str(raw or "")


async def chapters_for_upload(
    pool: Any, site_config: Any, *, asset_id: Any, video_path: str, title: str, task_id: str = "",
    key_terms: str = "",
) -> str:
    """Chapters for a long video about to upload: stored ones, else made now.

    Transcribes through ``youtube_captions.ensure_srt`` (so the caption track
    reuses the same SRT), generates, checks and stores. Never raises; returns
    ``""`` when there are none.
    """
    try:
        stored = await load_chapters(pool, asset_id)
        if stored or not chapters_enabled(site_config):
            return stored
        from poindexter.services.youtube_captions import ensure_srt

        srt_path, why = await ensure_srt(video_path, site_config, task_id=task_id)
        if not srt_path:
            logger.info("[youtube_chapters] no transcript for %s (%s) — no chapters", asset_id, why)
            return ""
        with open(srt_path, encoding="utf-8") as fh:
            srt_text = fh.read()
        text, note = await generate_chapters(
            site_config=site_config, pool=pool, srt_text=srt_text, title=title, task_id=task_id or None,
            key_terms=key_terms,
        )
        if asset_id:
            await store_chapters(pool, asset_id, text, note)
        if not text:
            logger.info("[youtube_chapters] no chapters for %s: %s", asset_id, note)
        return text
    except Exception as exc:  # noqa: BLE001 — chapters never block an upload
        logger.warning("[youtube_chapters] failed for %s: %s", asset_id, describe_exception(exc))
        return ""


_BACKFILL_SQL = """
SELECT ma.id, ma.storage_path, ma.task_id,
       ma.platform_video_ids->>'youtube' AS video_id,
       ma.metadata->'youtube_chapters'->>'text' AS chapters,
       coalesce(p.title, ma.title, '') AS title,
       coalesce(p.seo_keywords, '') AS seo_keywords
  FROM media_assets ma
  LEFT JOIN posts p ON p.id = ma.post_id
 WHERE ma.type = 'video'
   AND ma.platform_video_ids ? 'youtube'
   AND ($1::text IS NULL OR ma.platform_video_ids->>'youtube' = $1 OR ma.post_id::text = $1)
 ORDER BY ma.created_at DESC
"""


async def backfill_chapters(
    pool: Any, site_config: Any, *, selector: str | None = None, regenerate: bool = False,
    limit: int | None = None,
) -> list[ChapterOutcome]:
    """Make and store chapters for long videos already on YouTube.

    A LOCAL write: nothing is sent. Pushing them is
    ``sync-metadata --apply``, which composes stored chapters into each
    description. Videos with stored chapters are kept unless ``regenerate``.
    The transcript is the caption SRT beside the video; a video with neither
    the SRT nor its file is reported and skipped.
    """
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(_BACKFILL_SQL, selector)]
    outcomes: list[ChapterOutcome] = []
    from poindexter.services.youtube_captions import ensure_srt

    for row in rows:
        if limit is not None and len(outcomes) >= limit:
            break
        out = ChapterOutcome(video_id=row["video_id"], title=row["title"] or "")
        outcomes.append(out)
        if row.get("chapters") and not regenerate:
            out.chapters, out.action = row["chapters"], "kept — already stored"
            continue
        srt_path, why = await ensure_srt(row.get("storage_path") or "", site_config, task_id=str(row.get("task_id") or ""))
        if not srt_path:
            out.action, out.failed = f"failed: {why}", True
            continue
        with open(srt_path, encoding="utf-8") as fh:
            srt_text = fh.read()
        text, note = await generate_chapters(
            site_config=site_config, pool=pool, srt_text=srt_text, title=out.title,
            task_id=str(row.get("task_id") or "") or None, key_terms=str(row.get("seo_keywords") or ""),
        )
        await store_chapters(pool, row["id"], text, note)
        if text:
            out.chapters = text
            out.action = "stored — push with sync-metadata --apply" + (" (after a retry)" if note == "retry" else "")
        else:
            out.action, out.failed = f"none: {note}", note not in {"video too short for chapters"}
    return outcomes


__all__ = [
    "CHAPTERS_KEY",
    "ChapterOutcome",
    "backfill_chapters",
    "chapters_enabled",
    "chapters_for_upload",
    "check_chapters",
    "format_stamp",
    "generate_chapters",
    "load_chapters",
    "parse_srt",
    "store_chapters",
    "transcript_for_prompt",
]
