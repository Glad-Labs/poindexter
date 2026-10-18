"""integration_db: YouTube report rows → external_metrics → youtube_performance.

Drives the real path end to end: report rows shaped like the CSVs go through
``external_metrics_writer`` under the migration's own tap configs, and
``channel_performance`` reads them back. Pins what a mocked test cannot:

- two split rows for the same video and day both land (the split columns are
  in the natural key) and are summed;
- average view duration and retention are derived from the summed counts;
- thumbnail CTR is weighted by each day's impressions;
- traffic sources are named, and search terms come from source 5.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from poindexter.services import youtube_performance as yp
from poindexter.services.integrations.handlers.tap_external_metrics_writer import (
    external_metrics_writer,
)

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_VID = "itest_ytperf_a"
_DAY = dt.date(2026, 9, 10)


def _taps() -> dict:
    path = next(
        (Path(yp.__file__).parent / "migrations").glob(
            "*_add_youtube_activity_and_traffic_taps_*.py"
        )
    )
    spec = importlib.util.spec_from_file_location("yt_taps_migration_itest", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.TAPS


async def _write(pool, tap: str, record: dict) -> None:
    cfg = _taps()[tap]
    await external_metrics_writer(
        {"stream": cfg["report_type_id"], "record": record},
        site_config=None, row={"name": tap, "config": cfg}, pool=pool,
    )


async def _cleanup(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_metrics WHERE dimensions->>'video_id' = $1", _VID,
        )
        await conn.execute(
            "DELETE FROM media_assets WHERE platform_video_ids->>'youtube' = $1", _VID,
        )


def _split(subscribed: str, **metrics) -> dict:
    return {
        "date": _DAY.isoformat(), "video_id": _VID, "medium": "short", "post_id": None,
        "live_or_on_demand": "onDemand", "subscribed_status": subscribed,
        "country_code": "US", **{k: str(v) for k, v in metrics.items()},
    }


async def test_report_rows_read_back_as_per_video_numbers(test_pool):
    await _cleanup(test_pool)
    try:
        async with test_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO media_assets (type, source, title, duration_ms, platform_video_ids)
                VALUES ('video_short', 'pipeline', 'Itest Short', 30000, $1::jsonb)
                """,
                json.dumps({"youtube": _VID}),
            )
            # Two reach days: 100 impressions at 2%, 300 at 10% → 32 / 400 = 8%.
            for day, imp, ctr in ((_DAY, 100, 0.02), (_DAY + dt.timedelta(days=1), 300, 0.10)):
                for name, value in (
                    ("video_thumbnail_impressions", imp), ("video_thumbnail_impressions_ctr", ctr),
                ):
                    await conn.execute(
                        """
                        INSERT INTO external_metrics (source, metric_name, metric_value, dimensions, date)
                        VALUES ('youtube', $1, $2, $3::jsonb, $4)
                        """,
                        name, value, json.dumps({"video_id": _VID, "medium": "short"}), day,
                    )

        await _write(test_pool, "youtube_activity", _split(
            "subscribed", views=4, engaged_views=4, watch_time_minutes=1, likes=1,
            comments=0, shares=0, subscribers_gained=1, subscribers_lost=0,
        ))
        await _write(test_pool, "youtube_activity", _split(
            "unsubscribed", views=16, engaged_views=15, watch_time_minutes=3, likes=0,
            comments=0, shares=1, subscribers_gained=0, subscribers_lost=0,
        ))
        await _write(test_pool, "youtube_traffic", {
            **_split("unsubscribed", views=12, engaged_views=12, watch_time_minutes=2),
            "traffic_source_type": "24", "traffic_source_detail": "",
        })
        await _write(test_pool, "youtube_traffic", {
            **_split("unsubscribed", views=8, engaged_views=8, watch_time_minutes=1),
            "traffic_source_type": "5", "traffic_source_detail": "itest gpu undervolting",
        })

        perf = await yp.channel_performance(
            test_pool, days=10, end=_DAY + dt.timedelta(days=5),
        )
        [video] = [v for v in perf.videos if v.video_id == _VID]
        assert video.title == "Itest Short"
        assert video.views == 20  # both splits landed and were summed
        assert video.watch_minutes == 4
        assert video.average_view_seconds == pytest.approx(12.0)
        assert video.retention == pytest.approx(0.4)
        assert video.net_subscribers == 1
        assert video.shares == 1
        assert video.impressions == 400
        assert video.click_through_rate == pytest.approx(0.08)
        assert video.sources == {"Shorts feed": 12, "YouTube search": 8}
        assert ("itest gpu undervolting", 8) in perf.search_terms
    finally:
        await _cleanup(test_pool)
