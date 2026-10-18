"""Caption tracks for YouTube uploads: transcribe the video, upload the SRT.

Earned 2026-10-18. Every render already burns captions into its pixels from a
narration transcript, but the SRT went to a temp file and was thrown away, so
no upload ever carried a caption track. YouTube indexes caption text for
search, and YouTube search is one of the channel's two biggest view sources
(267 of ~770 views since June), so the words spoken in a video were invisible
to the search that finds it.

The transcript is made from the **uploaded file's own audio**, not from the
script, so the cues match what is actually said: one ASR pass through the same
caption provider the render uses (``video_caption_engine``, Speaches by
default). The SRT is kept next to the video as ``<video>.srt`` and reused.

Two callers:

- :func:`caption_after_upload` — ``media_distribute`` calls it right after a
  successful YouTube upload. Best-effort: a caption failure never fails the
  upload; it is stamped on the asset row and raised as a
  ``youtube_caption_failed`` finding.
- :func:`backfill_captions` — ``poindexter integrations youtube captions``
  for videos already on the channel. Dry run by default: it transcribes and
  stores the SRT for review and sends nothing; ``apply=True`` uploads exactly
  the stored file.

The outcome lives on the video's ``media_assets.metadata.youtube_captions``
(``status`` ``set`` / ``already there`` / ``failed: …``, ``at``, ``language``).
A video already ``set`` is skipped; the adapter also checks YouTube for an
existing track of the same language and name, so a re-run never stacks a
duplicate.

Settings: ``youtube_captions_enabled``, ``youtube_caption_language``,
``youtube_caption_track_name``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any

from poindexter.services.logger_config import get_logger
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = get_logger(__name__)

CAPTIONS_KEY = "youtube_captions"
_VIDEO_TYPES = ("video", "video_short")


@dataclass
class CaptionOutcome:
    video_id: str
    medium: str
    title: str
    srt_path: str = ""
    action: str = ""
    cues: int = 0
    failed: bool = False


def _setting(site_config: Any, key: str, default: str) -> str:
    value = site_config.get(key, default) if site_config is not None else default
    return str(value if value is not None else default).strip()


def captions_enabled(site_config: Any) -> bool:
    return _setting(site_config, "youtube_captions_enabled", "true").lower() in {"true", "1", "yes", "on"}


def caption_language(site_config: Any) -> str:
    return _setting(site_config, "youtube_caption_language", "en") or "en"


def caption_track_name(site_config: Any) -> str:
    return _setting(site_config, "youtube_caption_track_name", "English") or "English"


def sidecar_srt_path(video_path: str) -> str:
    """``/videos/abc_short.mp4`` → ``/videos/abc_short.srt``."""
    root, _ = os.path.splitext(video_path)
    return root + ".srt"


def count_cues(srt_text: str) -> int:
    """Cue count of an SRT document (blocks with a ``-->`` timing line)."""
    return sum(1 for line in (srt_text or "").splitlines() if "-->" in line)


async def _extract_audio(video_path: str, wav_path: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000", wav_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read the audio: {err.decode(errors='replace')[-300:]}")


async def _download(url: str, dest: str) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)


async def ensure_srt(
    video_path: str, site_config: Any, *, task_id: str = "", srt_path: str = "",
) -> tuple[str, str]:
    """The video's SRT path, transcribing it once. ``(path, "")`` or ``("", why)``.

    ``srt_path`` defaults to ``<video>.srt``; a non-empty file there is reused
    as-is. The backfill passes the canonical sidecar path while transcribing a
    downloaded copy, so the SRT lands beside the video row's own path.
    """
    srt_path = srt_path or sidecar_srt_path(video_path)
    if os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
        return srt_path, ""
    if not os.path.exists(video_path):
        return "", f"video file missing ({video_path})"
    from poindexter.services.caption_providers import get_caption_provider

    with tempfile.TemporaryDirectory(prefix="yt_captions_") as tmp:
        wav = os.path.join(tmp, "audio.wav")
        try:
            await _extract_audio(video_path, wav)
            result = await get_caption_provider(site_config).transcribe(
                audio_path=wav, task_id=task_id, granularity="segment",
            )
        except Exception as exc:  # noqa: BLE001 — reported per video by the caller
            return "", describe_exception(exc)
    if not getattr(result, "success", False) or not getattr(result, "srt_text", ""):
        return "", f"transcription failed: {getattr(result, 'error', '') or 'empty transcript'}"
    os.makedirs(os.path.dirname(srt_path) or ".", exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write(result.srt_text)
    return srt_path, ""


async def _stamp(pool: Any, asset_id: Any, status: str, language: str) -> None:
    payload = {
        "status": status,
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "language": language,
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE media_assets
               SET metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object($2::text, $3::jsonb)
             WHERE id = $1::uuid
            """,
            str(asset_id), CAPTIONS_KEY, json.dumps(payload),
        )


async def caption_after_upload(
    pool: Any,
    site_config: Any,
    *,
    asset_id: Any,
    video_path: str,
    video_id: str,
    subject: str = "",
    task_id: str = "",
    shorts: bool = False,
) -> str:
    """Transcribe a just-uploaded video and attach its caption track.

    Never raises. Returns the status stamped on the asset row (or why it was
    skipped). A failure is raised as a ``youtube_caption_failed`` finding;
    ``poindexter integrations youtube captions --apply`` retries it.
    """
    if not captions_enabled(site_config):
        return "skipped: youtube_captions_enabled is off"
    language = caption_language(site_config)
    try:
        srt_path, why = await ensure_srt(video_path, site_config, task_id=task_id)
        if srt_path:
            from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

            ok, detail = await YouTubePublishAdapter(site_config=site_config).set_captions(
                video_id=video_id, srt_path=srt_path, language=language,
                name=caption_track_name(site_config),
            )
            status = detail if ok else f"failed: {detail}"
        else:
            status = f"failed: {why}"
    except Exception as exc:  # noqa: BLE001 — captions never fail an upload
        status = f"failed: {describe_exception(exc)}"
    try:
        if asset_id:
            await _stamp(pool, asset_id, status, language)
    except Exception as exc:  # noqa: BLE001 — a lost stamp only costs a re-check
        logger.warning("[youtube_captions] stamp failed for %s: %s", video_id, describe_exception(exc))
    if status.startswith("failed"):
        emit_finding(
            source="youtube_captions",
            kind="youtube_caption_failed",
            title=f"YouTube {'Short' if shorts else 'video'} uploaded without its caption track",
            body=(
                f"Video {video_id or '?'} uploaded, but its captions did not: {status[8:300]}. "
                "Retry with `poindexter integrations youtube captions --apply`."
            ),
            severity="warn",
            dedup_key=f"youtube_caption_failed:{subject or video_id}:{'short' if shorts else 'long'}",
            extra={"video_id": video_id, "subject": subject, "status": status[:300]},
        )
    return status


_BACKFILL_SQL = """
SELECT ma.id, ma.type, ma.storage_path, ma.url, ma.task_id,
       ma.platform_video_ids->>'youtube' AS video_id,
       ma.metadata->'youtube_captions'->>'status' AS caption_status,
       coalesce(nullif(ma.metadata->'youtube_package'->>'title', ''), p.title, ma.title, '') AS title
  FROM media_assets ma
  LEFT JOIN posts p ON p.id = ma.post_id
 WHERE ma.type = ANY($1::text[])
   AND ma.platform_video_ids ? 'youtube'
   AND ($2::text IS NULL
        OR ma.platform_video_ids->>'youtube' = $2
        OR ma.post_id::text = $2
        OR ma.task_id::text = $2)
 ORDER BY ma.created_at DESC
"""


async def backfill_captions(
    pool: Any,
    site_config: Any,
    *,
    selector: str | None = None,
    apply: bool = False,
    limit: int | None = None,
) -> list[CaptionOutcome]:
    """Caption tracks for videos already on YouTube.

    Dry run: transcribe each uncaptioned video and store ``<video>.srt`` for
    review; nothing is sent. ``apply``: upload exactly the stored SRT. A video
    whose file is gone is fetched from its stored URL when it has one.
    A video already stamped ``set`` is skipped unless named by ``selector``.
    """
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(_BACKFILL_SQL, list(_VIDEO_TYPES), selector)]
    language = caption_language(site_config)
    name = caption_track_name(site_config)
    outcomes: list[CaptionOutcome] = []
    for row in rows:
        if limit is not None and len(outcomes) >= limit:
            break
        out = CaptionOutcome(
            video_id=row["video_id"], medium="short" if row["type"] == "video_short" else "long",
            title=row["title"] or "",
        )
        outcomes.append(out)
        if row.get("caption_status") in {"set", "already there"} and selector is None:
            out.action = "skipped — already captioned"
            continue
        video_path = row.get("storage_path") or ""
        srt_path = sidecar_srt_path(video_path) if video_path else ""
        source, downloaded = video_path, ""
        have_srt = bool(srt_path) and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0
        if not have_srt and not os.path.exists(video_path or "") and str(row.get("url") or "").startswith("http"):
            downloaded = os.path.join(tempfile.mkdtemp(prefix="yt_captions_dl_"), "video.mp4")
            try:
                await _download(str(row["url"]), downloaded)
            except Exception as exc:  # noqa: BLE001 — reported on this row
                out.action, out.failed = f"failed: download {describe_exception(exc)}", True
                continue
            source = downloaded
        try:
            srt_path, why = await ensure_srt(
                source, site_config, task_id=str(row.get("task_id") or ""), srt_path=srt_path,
            )
        finally:
            if downloaded:
                shutil.rmtree(os.path.dirname(downloaded), ignore_errors=True)
        if not srt_path:
            out.action, out.failed = f"failed: {why}", True
            continue
        out.srt_path = srt_path
        with open(srt_path, encoding="utf-8") as fh:
            out.cues = count_cues(fh.read())
        if not apply:
            out.action = "transcribed — review, then --apply"
            continue
        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        ok, detail = await YouTubePublishAdapter(site_config=site_config).set_captions(
            video_id=out.video_id, srt_path=srt_path, language=language, name=name, force=True,
        )
        status = detail if ok else f"failed: {detail}"
        await _stamp(pool, row["id"], status, language)
        out.action, out.failed = status, not ok
    return outcomes


__all__ = [
    "CAPTIONS_KEY",
    "CaptionOutcome",
    "backfill_captions",
    "caption_after_upload",
    "caption_language",
    "caption_track_name",
    "captions_enabled",
    "count_cues",
    "ensure_srt",
    "sidecar_srt_path",
]
