"""Handler: ``tap.youtube_reporting`` — YouTube Reporting API reports → ``external_metrics``.

Earned 2026-09-25. The channel had 13 long videos and 10 Shorts and no YouTube
number anywhere: ``external_metrics`` held Search Console and GA4 only, so a
packaging change (a custom thumbnail, a new title style) could not be measured.
The reach report carries thumbnail impressions and click-through rate per video
per day, which is the number a thumbnail exists to move.

## How the Reporting API delivers data

(https://developers.google.com/youtube/reporting/v1/reports)

- A reporting **job** is created once per report type. YouTube then writes one
  CSV per Pacific-time day, the first within 48 hours, plus reports for the 30
  days before the job existed.
- Reports stay downloadable for 60 days (30 for the historical ones).
- A corrected day arrives as a NEW report with a new id. The writer's
  natural-key upsert makes that overwrite the earlier value, not duplicate it.

## Per run

1. **YouTube not set up on this install** (``plugin.publish_adapter.youtube``
   disabled or its OAuth secrets missing): a quiet 0-record run. That is a
   legitimate zero; most installs never publish to YouTube.
2. Find or create the job for ``config.report_type_id``; its id is cached in
   ``state``.
3. List the job's reports, skip the ids already landed, and take up to
   ``config.max_reports_per_run``, oldest first. Download each CSV, map
   ``video_id`` to our post and medium (``long`` / ``short``) through
   ``media_assets.platform_video_ids``, and hand every row to the row's
   ``record_handler`` (``external_metrics_writer``) under
   ``config.metrics_mapping``.
4. Record each report id as it lands, so a run that dies half-way resumes
   where it stopped.

A token without ``yt-analytics.readonly`` (or a Cloud project without the
YouTube Reporting API enabled) RAISES with the exact fix. The tap runner's
``tap_failure`` path records that on the row and alerts after
``tap_failure_alert_after_consecutive`` failures; nothing here pages directly.

## Per-tap config (``external_taps.config``)

- ``report_type_id``: default ``channel_reach_basic_a1`` (date, channel_id,
  video_id, video_thumbnail_impressions, video_thumbnail_impressions_ctr).
  ``channel_reach_combined_a1`` adds traffic source / OS / device dimensions.
- ``job_name``: name given to the job if one has to be created.
- ``max_reports_per_run``: cap per run (default 60).
- ``include_unmapped_videos``: keep rows for videos no ``media_assets`` row
  knows (uploaded outside the pipeline), with ``post_id`` NULL. Default true.
- ``metrics_mapping``: the ``external_metrics_writer`` mapping, keyed by the
  report type id. ``dimension_fields`` should include ``video_id``: the writer's
  natural key has no other per-video column when ``post_id`` is the post field.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from typing import Any

from poindexter.services.integrations import registry
from poindexter.services.integrations.registry import register_handler

logger = logging.getLogger(__name__)

DEFAULT_REPORT_TYPE = "channel_reach_basic_a1"
DEFAULT_MAX_REPORTS_PER_RUN = 60
#: Report ids remembered as landed. YouTube keeps reports 60 days (+30 of
#: history, + re-issued corrections), so this comfortably covers everything
#: still listable without letting the state row grow forever.
_PROCESSED_IDS_CAP = 1000

_RECONSENT = (
    "Grant it once with `poindexter integrations youtube setup --with-analytics` "
    "(it keeps the scopes the token already has)."
)

_MEDIUM_BY_TYPE = {"video": "long", "video_short": "short"}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _iso_date(raw: str) -> str | None:
    """``20260924`` (the report's format) or ``2026-09-24`` → ``2026-09-24``."""
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return None


def classify_google_error(exc: Exception) -> str | None:
    """Name the operator-fixable failure behind a Google API error, or ``None``.

    ``scope``: the token was consented without ``yt-analytics.readonly``.
    ``api_disabled``: the Cloud project has not enabled the YouTube Reporting
    API. Both need a human; anything else is a transient worth retrying.
    """
    text = str(exc).lower()
    if "accessnotconfigured" in text or "service_disabled" in text or (
        "has not been used in project" in text
    ) or "it is disabled" in text:
        return "api_disabled"
    from poindexter.services.publish_adapters.youtube import _is_insufficient_scope

    if _is_insufficient_scope(exc):
        return "scope"
    return None


def _explain(exc: Exception) -> RuntimeError | None:
    kind = classify_google_error(exc)
    if kind == "scope":
        from poindexter.services.publish_adapters.youtube import _ANALYTICS_SCOPE

        return RuntimeError(
            f"YouTube token lacks {_ANALYTICS_SCOPE}, which the Reporting API "
            f"needs. {_RECONSENT} Google said: {str(exc)[:500]}"
        )
    if kind == "api_disabled":
        return RuntimeError(
            "The YouTube Reporting API is not enabled for this OAuth client's "
            "Google Cloud project. Enable it under APIs & Services → Library → "
            f"'YouTube Reporting API', then let the tap retry. Google said: {str(exc)[:500]}"
        )
    return None


def parse_report_csv(text: str) -> list[dict[str, str]]:
    """A report CSV (header row first) → one dict per data row.

    Column order is not assumed; the header names every column
    ("don't assume that views will be the first metric" — Google).
    """
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader if any((v or "").strip() for v in r.values())]


async def load_video_index(pool: Any) -> dict[str, tuple[str | None, str]]:
    """``{youtube_video_id: (post_id, medium)}`` from ``media_assets``."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT platform_video_ids->>'youtube' AS video_id,
                   post_id::text AS post_id, type
              FROM media_assets
             WHERE platform_video_ids ? 'youtube'
            """
        )
    index: dict[str, tuple[str | None, str]] = {}
    for r in rows:
        vid = (r["video_id"] or "").strip()
        if vid:
            index[vid] = (r["post_id"], _MEDIUM_BY_TYPE.get(r["type"], r["type"] or "unknown"))
    return index


def build_records(
    rows: list[dict[str, str]],
    index: dict[str, tuple[str | None, str]],
    *,
    include_unmapped: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Report rows → writer records. Returns ``(records, skipped_unmapped)``."""
    records: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        date = _iso_date(row.get("date", ""))
        vid = (row.get("video_id") or "").strip()
        if not date:
            continue
        post_id, medium = index.get(vid, (None, "unknown"))
        if vid not in index and not include_unmapped:
            skipped += 1
            continue
        rec: dict[str, Any] = dict(row)
        rec.update({"date": date, "video_id": vid, "post_id": post_id, "medium": medium})
        records.append(rec)
    return records, skipped


def _build_service(credentials: Any) -> Any:
    from googleapiclient.discovery import build  # type: ignore[import-not-found]

    return build(
        "youtubereporting", "v1", credentials=credentials,
        cache_discovery=False, static_discovery=True,
    )


def _find_or_create_job_blocking(service: Any, report_type_id: str, job_name: str) -> str:
    page_token = None
    while True:
        kwargs = {"pageToken": page_token} if page_token else {}
        resp = service.jobs().list(**kwargs).execute()
        for job in resp.get("jobs", []) or []:
            if job.get("reportTypeId") == report_type_id:
                return str(job["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    created = service.jobs().create(
        body={"reportTypeId": report_type_id, "name": job_name},
    ).execute()
    logger.info(
        "[tap.youtube_reporting] created reporting job %s for %s — first "
        "reports arrive within 48h, with the prior 30 days of history",
        created.get("id"), report_type_id,
    )
    return str(created["id"])


def _list_reports_blocking(service: Any, job_id: str) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    page_token = None
    while True:
        kwargs: dict[str, Any] = {"jobId": job_id}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.jobs().reports().list(**kwargs).execute()
        reports.extend(resp.get("reports", []) or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return reports


def _download_blocking(service: Any, url: str) -> str:
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-not-found]

    # Google's own sample: a media request with its URI swapped for the
    # report's downloadUrl, streamed in one chunk.
    request = service.media().download(resourceName=" ")
    request.uri = url
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request, chunksize=-1)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8-sig")


async def _save_state(pool: Any, row_id: Any, state: dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE external_taps SET state = $1::jsonb WHERE id = $2",
            json.dumps(state), row_id,
        )


@register_handler("tap", "youtube_reporting")
async def youtube_reporting(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Land new YouTube Reporting API reports in ``external_metrics``."""
    if pool is None:
        raise RuntimeError("tap.youtube_reporting: pool unavailable")
    config = _as_dict(row.get("config"))
    state = _as_dict(row.get("state"))
    report_type_id = str(config.get("report_type_id") or DEFAULT_REPORT_TYPE)
    job_name = str(config.get("job_name") or f"poindexter-{report_type_id}")
    max_reports = max(1, int(config.get("max_reports_per_run") or DEFAULT_MAX_REPORTS_PER_RUN))
    include_unmapped = bool(config.get("include_unmapped_videos", True))
    record_handler = row.get("record_handler") or "external_metrics_writer"
    mapping = (config.get("metrics_mapping") or {}).get(report_type_id)
    if not isinstance(mapping, dict):
        raise ValueError(
            f"tap.youtube_reporting: config.metrics_mapping has no entry for "
            f"{report_type_id!r}, so nothing would be written"
        )

    from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

    adapter = YouTubePublishAdapter(site_config=site_config)
    ready, reason, secrets = await adapter._check_gating()
    if not ready:
        logger.info("[tap.youtube_reporting] %s: skipped — %s", row.get("name"), reason)
        return {"records": 0, "reason": "youtube not set up on this install"}

    credentials = adapter._build_credentials(secrets)
    try:
        service = await asyncio.to_thread(_build_service, credentials)
        if state.get("report_type_id") != report_type_id or not state.get("job_id"):
            state["job_id"] = await asyncio.to_thread(
                _find_or_create_job_blocking, service, report_type_id, job_name,
            )
            state["report_type_id"] = report_type_id
            await _save_state(pool, row.get("id"), state)
        reports = await asyncio.to_thread(_list_reports_blocking, service, state["job_id"])
    except Exception as exc:
        explained = _explain(exc)
        if explained is not None:
            raise explained from exc
        raise

    processed: list[str] = list(state.get("processed_report_ids") or [])
    seen = set(processed)
    pending = sorted(
        (r for r in reports if r.get("id") not in seen and r.get("downloadUrl")),
        key=lambda r: (r.get("createTime") or "", r.get("id") or ""),
    )[:max_reports]
    if not pending:
        return {"records": 0, "reports": 0, "reason": "no new reports"}

    index = await load_video_index(pool)
    written = unmapped = 0
    for report in pending:
        try:
            text = await asyncio.to_thread(_download_blocking, service, report["downloadUrl"])
        except Exception as exc:
            explained = _explain(exc)
            if explained is not None:
                raise explained from exc
            raise
        records, skipped = build_records(
            parse_report_csv(text), index, include_unmapped=include_unmapped,
        )
        unmapped += skipped
        for record in records:
            result = await registry.dispatch(
                "tap", record_handler, {"stream": report_type_id, "record": record},
                site_config=site_config, row=row, pool=pool,
            )
            if isinstance(result, dict):
                written += int(result.get("inserted", 0) or 0)
        processed.append(str(report["id"]))
        state["processed_report_ids"] = processed[-_PROCESSED_IDS_CAP:]
        state["last_create_time"] = report.get("createTime")
        state["reports_processed_total"] = int(state.get("reports_processed_total") or 0) + 1
        await _save_state(pool, row.get("id"), state)

    logger.info(
        "[tap.youtube_reporting] %s: %d report(s), %d metric row(s)%s",
        row.get("name"), len(pending), written,
        f", {unmapped} row(s) for videos no media_asset knows skipped" if unmapped else "",
    )
    return {"records": written, "reports": len(pending), "unmapped_skipped": unmapped}
