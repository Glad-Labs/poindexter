"""Unit tests for ``services/integrations/handlers/tap_youtube_reporting.py``.

The YouTube Reporting API tap (reach report → ``external_metrics``). Pins:

1. Report parsing — header-driven columns, ``YYYYMMDD`` dates.
2. Video mapping — ``video_id`` → our post + medium; unmapped videos kept or
   dropped per config.
3. The quiet zero — an install without YouTube publishing does nothing.
4. The loud failures — a missing analytics scope or a disabled Reporting API
   raise with the fix, for the tap runner's ``tap_failure`` path.
5. Progress — only unseen reports, oldest first, capped, recorded per report.
6. The row the migration seeds writes the metric rows it promises through the
   REAL ``external_metrics_writer``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.integrations.handlers import tap_youtube_reporting as tap

_CSV = (
    "video_thumbnail_impressions_ctr,date,video_id,channel_id,video_thumbnail_impressions\n"
    "0.051,20260920,vidLong,UC1,1200\n"
    "0.02,20260920,vidShort,UC1,300\n"
    "0.1,20260920,vidElsewhere,UC1,10\n"
    "\n"
)

_MAPPING = {
    "channel_reach_basic_a1": {
        "source": "youtube",
        "date_field": "date",
        "post_field": "post_id",
        "metric_fields": ["video_thumbnail_impressions", "video_thumbnail_impressions_ctr"],
        "dimension_fields": ["video_id", "medium"],
    },
}


def _row(**config: Any) -> dict[str, Any]:
    return {
        "id": "row-1",
        "name": "youtube_reach",
        "record_handler": "external_metrics_writer",
        "config": json.dumps({"metrics_mapping": _MAPPING, **config}),
        "state": "{}",
    }


def _pool(index_rows: list[dict[str, Any]] | None = None) -> tuple[MagicMock, AsyncMock]:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=index_rows if index_rows is not None else [
        {"video_id": "vidLong", "post_id": "11111111-1111-1111-1111-111111111111", "type": "video"},
        {"video_id": "vidShort", "post_id": "11111111-1111-1111-1111-111111111111", "type": "video_short"},
    ])
    conn.execute = AsyncMock(return_value="OK")
    conn.fetchval = AsyncMock(return_value=None)
    pool = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _ready_adapter(ready: bool = True):
    gating = AsyncMock(return_value=(ready, None if ready else "adapter disabled", {"refresh_token": "r"}))
    return patch.multiple(
        "poindexter.services.publish_adapters.youtube.YouTubePublishAdapter",
        _check_gating=gating,
        _build_credentials=MagicMock(return_value=object()),
    )


def _reports(*ids_and_times: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"id": rid, "createTime": ct, "downloadUrl": f"https://example.test/{rid}"}
        for rid, ct in ids_and_times
    ]


def _saved_states(conn: AsyncMock) -> list[dict[str, Any]]:
    return [
        json.loads(c.args[1]) for c in conn.execute.call_args_list
        if "UPDATE external_taps SET state" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# 1-2. parsing + mapping
# ---------------------------------------------------------------------------


def test_iso_date_accepts_the_report_format():
    assert tap._iso_date("20260924") == "2026-09-24"
    assert tap._iso_date("2026-09-24") == "2026-09-24"
    assert tap._iso_date("Sept 24") is None


def test_report_csv_is_read_by_header_not_position():
    rows = tap.parse_report_csv(_CSV)
    assert len(rows) == 3  # the blank line is not a row
    assert rows[0]["video_thumbnail_impressions"] == "1200"
    assert rows[0]["video_thumbnail_impressions_ctr"] == "0.051"


def test_records_carry_post_and_medium_and_unmapped_is_configurable():
    index = {"vidLong": ("p1", "long"), "vidShort": ("p1", "short")}
    rows = tap.parse_report_csv(_CSV)
    kept, skipped = tap.build_records(rows, index, include_unmapped=True)
    assert [(r["video_id"], r["medium"], r["post_id"]) for r in kept] == [
        ("vidLong", "long", "p1"), ("vidShort", "short", "p1"), ("vidElsewhere", "unknown", None),
    ]
    assert skipped == 0 and kept[0]["date"] == "2026-09-20"
    dropped, skipped = tap.build_records(rows, index, include_unmapped=False)
    assert [r["video_id"] for r in dropped] == ["vidLong", "vidShort"] and skipped == 1


# ---------------------------------------------------------------------------
# 3-4. quiet zero, loud failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_install_without_youtube_is_a_quiet_zero():
    pool, conn = _pool()
    with _ready_adapter(ready=False), patch.object(tap, "_build_service") as build:
        out = await tap.youtube_reporting(None, site_config=None, row=_row(), pool=pool)
    assert out["records"] == 0 and "not set up" in out["reason"]
    build.assert_not_called()


@pytest.mark.asyncio
async def test_a_mapping_for_another_report_type_is_a_config_error():
    pool, _ = _pool()
    with pytest.raises(ValueError, match="metrics_mapping"):
        await tap.youtube_reporting(
            None, site_config=None, row=_row(report_type_id="channel_reach_combined_a1"), pool=pool,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "fix"), [
    ("<HttpError 403 'Request had insufficient authentication scopes.'>", "--with-analytics"),
    ("<HttpError 403 'YouTube Reporting API has not been used in project 42 before or it is "
     "disabled. Enable it by visiting https://console.developers.google.com/...' "
     "reason: accessNotConfigured>", "APIs & Services"),
])
async def test_operator_fixable_google_errors_raise_with_the_fix(message, fix):
    pool, _ = _pool()
    with _ready_adapter(), patch.object(tap, "_build_service", return_value=object()), \
         patch.object(tap, "_find_or_create_job_blocking", side_effect=Exception(message)):
        with pytest.raises(RuntimeError) as exc:
            await tap.youtube_reporting(None, site_config=None, row=_row(), pool=pool)
    assert fix in str(exc.value)


def test_a_plain_server_error_is_not_mislabelled():
    assert tap.classify_google_error(Exception("<HttpError 503 'backend error'>")) is None


# ---------------------------------------------------------------------------
# 5. progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_unseen_reports_oldest_first_capped_and_recorded_one_by_one():
    pool, conn = _pool()
    row = _row(max_reports_per_run=2)
    row["state"] = json.dumps({
        "job_id": "job-1", "report_type_id": "channel_reach_basic_a1",
        "processed_report_ids": ["r-old"],
    })
    listed = _reports(
        ("r-new-2", "2026-09-22T10:00:00Z"), ("r-old", "2026-09-20T10:00:00Z"),
        ("r-new-1", "2026-09-21T10:00:00Z"), ("r-new-3", "2026-09-23T10:00:00Z"),
    )
    downloaded: list[str] = []

    def _download(_svc, url):
        downloaded.append(url.rsplit("/", 1)[-1])
        return _CSV

    dispatch = AsyncMock(return_value={"inserted": 2})
    with _ready_adapter(), patch.object(tap, "_build_service", return_value=object()), \
         patch.object(tap, "_find_or_create_job_blocking") as find_job, \
         patch.object(tap, "_list_reports_blocking", return_value=listed), \
         patch.object(tap, "_download_blocking", side_effect=_download), \
         patch.object(tap.registry, "dispatch", dispatch):
        out = await tap.youtube_reporting(None, site_config=None, row=row, pool=pool)

    find_job.assert_not_called()  # cached job id reused
    assert downloaded == ["r-new-1", "r-new-2"]  # oldest unseen first, cap 2
    assert out == {"records": 12, "reports": 2, "unmapped_skipped": 0}  # 3 rows x 2 reports x 2
    states = _saved_states(conn)
    assert states[0]["processed_report_ids"] == ["r-old", "r-new-1"]
    assert states[-1]["processed_report_ids"] == ["r-old", "r-new-1", "r-new-2"]
    assert states[-1]["last_create_time"] == "2026-09-22T10:00:00Z"
    stream, record = dispatch.call_args.args[2]["stream"], dispatch.call_args.args[2]["record"]
    assert stream == "channel_reach_basic_a1" and record["date"] == "2026-09-20"


@pytest.mark.asyncio
async def test_first_run_resolves_the_job_and_caches_it():
    pool, conn = _pool()
    with _ready_adapter(), patch.object(tap, "_build_service", return_value=object()), \
         patch.object(tap, "_find_or_create_job_blocking", return_value="job-9") as find_job, \
         patch.object(tap, "_list_reports_blocking", return_value=[]):
        out = await tap.youtube_reporting(None, site_config=None, row=_row(), pool=pool)
    assert find_job.call_args.args[1:] == ("channel_reach_basic_a1", "poindexter-channel_reach_basic_a1")
    assert _saved_states(conn)[0]["job_id"] == "job-9"
    assert out["records"] == 0 and out["reason"] == "no new reports"


# ---------------------------------------------------------------------------
# 6. the seeded row, through the real writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_seeded_row_lands_one_metric_row_per_metric_through_the_real_writer():
    import importlib

    from poindexter.services.integrations.handlers import load_all

    load_all()
    mig = importlib.import_module(
        "poindexter.services.migrations."
        "20260925_141628_add_the_youtube_reach_tap_so_thumbnail_impressions_and_ctr_land_in_external_metrics"
    )
    pool, conn = _pool()
    row = {"id": "row-1", "name": "youtube_reach", "record_handler": "external_metrics_writer",
           "config": json.dumps(mig._CONFIG), "state": "{}"}
    with _ready_adapter(), patch.object(tap, "_build_service", return_value=object()), \
         patch.object(tap, "_find_or_create_job_blocking", return_value="job-1"), \
         patch.object(tap, "_list_reports_blocking", return_value=_reports(("r1", "2026-09-21T00:00:00Z"))), \
         patch.object(tap, "_download_blocking", return_value=_CSV):
        out = await tap.youtube_reporting(None, site_config=None, row=row, pool=pool)

    inserts = [c.args for c in conn.execute.call_args_list if "INSERT INTO external_metrics" in c.args[0]]
    assert out["records"] == len(inserts) == 6  # 3 videos x 2 metrics
    source, metric, value, dims, post_id, slug, date = inserts[0][1:8]
    assert (source, metric, value) == ("youtube", "video_thumbnail_impressions", 1200.0)
    assert json.loads(dims) == {"video_id": "vidLong", "medium": "long"}
    assert post_id == "11111111-1111-1111-1111-111111111111" and slug is None
    assert str(date) == "2026-09-20"
    unmapped = [a for a in inserts if json.loads(a[4])["video_id"] == "vidElsewhere"]
    assert unmapped and all(a[5] is None for a in unmapped)  # kept, no post


def test_load_all_registers_the_handler():
    from poindexter.services.integrations import registry
    from poindexter.services.integrations.handlers import load_all

    load_all()
    assert "tap.youtube_reporting" in registry.registered_names("tap")
