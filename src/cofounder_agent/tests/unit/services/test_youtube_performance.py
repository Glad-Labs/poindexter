"""youtube_performance: derived numbers, and the taps that feed it.

The activity and traffic taps store one row per split of a video's day
(subscribed status, country, live or on demand). These tests pin the two rules
that make the reader honest about that: counts are summed, averages are
derived from them (never summed), and a video with no known length has no
retention rather than a guessed one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from poindexter.services import youtube_performance as yp
from tests.unit._nonempty import nonempty

_MIGRATION = next(
    (Path(yp.__file__).parent / "migrations").glob(
        "*_add_youtube_activity_and_traffic_taps_*.py"
    )
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("yt_taps_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(**kw):
    base = {"video_id": "v1", "medium": "short", "title": "T", "duration_ms": 40_000}
    base.update(kw)
    return base


class TestDerivedNumbers:
    def test_average_view_and_retention_come_from_watch_time(self):
        [v] = yp.build_videos([_row(views=20, watch_minutes=4)], [])
        assert v.average_view_seconds == pytest.approx(12.0)
        assert v.retention == pytest.approx(0.3)

    def test_no_length_means_no_retention(self):
        """A hand-uploaded video has no media_assets length; never guess one."""
        [v] = yp.build_videos([_row(duration_ms=None, views=10, watch_minutes=1)], [])
        assert v.average_view_seconds == pytest.approx(6.0)
        assert v.retention is None

    def test_no_views_means_no_average(self):
        [v] = yp.build_videos([_row(impressions=50, impression_clicks=2)], [])
        assert v.average_view_seconds is None
        assert v.retention is None
        assert v.click_through_rate == pytest.approx(0.04)

    def test_net_subscribers(self):
        [v] = yp.build_videos([_row(subscribers_gained=3, subscribers_lost=1)], [])
        assert v.net_subscribers == 2

    def test_most_viewed_first_then_most_impressions(self):
        videos = yp.build_videos(
            [
                _row(video_id="a", views=5),
                _row(video_id="b", views=0, impressions=90),
                _row(video_id="c", views=9),
                _row(video_id="d", views=0, impressions=10),
            ],
            [],
        )
        assert [v.video_id for v in videos] == ["c", "a", "b", "d"]

    def test_sources_are_named_and_summed_per_video(self):
        [v] = yp.build_videos(
            [_row(views=10)],
            [
                {"video_id": "v1", "source_type": "24", "views": 6},
                {"video_id": "v1", "source_type": "5", "views": 3},
                {"video_id": "v1", "source_type": "5", "views": 1},
                {"video_id": "other", "source_type": "5", "views": 7},
            ],
        )
        assert v.sources == {"Shorts feed": 6, "YouTube search": 4}

    @pytest.mark.parametrize(
        "code, name",
        [("5", "YouTube search"), ("24", "Shorts feed"), ("99", "source 99"), (None, "unknown")],
    )
    def test_source_names(self, code, name):
        assert yp.source_name(code) == name


class TestTapRows:
    """The migration's two tap rows, read the way the handler reads them."""

    def test_each_tap_writes_its_own_source(self):
        """Both reports have a `views` column. A shared source would let
        anything summing `views` count every view twice, and the reach tap
        already owns `youtube`."""
        taps = _load_migration().TAPS
        sources = [
            cfg["metrics_mapping"][cfg["report_type_id"]]["source"] for cfg in taps.values()
        ]
        assert sorted(sources) == ["youtube_activity", "youtube_traffic"]

    def test_the_reader_reads_the_sources_the_taps_write(self):
        taps = _load_migration().TAPS
        for cfg in nonempty(taps.values(), "TAPS"):
            source = cfg["metrics_mapping"][cfg["report_type_id"]]["source"]
            assert f"source = '{source}'" in (yp._VIDEOS_SQL + yp._SOURCES_SQL)

    def test_every_split_dimension_is_kept(self):
        """The reports split a video's day; without each split column in the
        natural key, one split row would overwrite the next."""
        taps = _load_migration().TAPS
        for name, cfg in nonempty(taps.items(), "TAPS"):
            dims = cfg["metrics_mapping"][cfg["report_type_id"]]["dimension_fields"]
            for split in ("video_id", "live_or_on_demand", "subscribed_status", "country_code"):
                assert split in dims, (name, split)
        traffic = taps["youtube_traffic"]["metrics_mapping"]["channel_traffic_source_a3"]
        assert {"traffic_source_type", "traffic_source_detail"} <= set(traffic["dimension_fields"])

    def test_no_average_column_is_stored(self):
        """An average of one split row is meaningless once summed."""
        for cfg in nonempty(_load_migration().TAPS.values(), "TAPS"):
            metrics = cfg["metrics_mapping"][cfg["report_type_id"]]["metric_fields"]
            assert not [m for m in metrics if "average" in m or m.endswith("_rate")]

    def test_report_ids_match_their_mapping_keys(self):
        for cfg in nonempty(_load_migration().TAPS.values(), "TAPS"):
            assert list(cfg["metrics_mapping"]) == [cfg["report_type_id"]]
            assert cfg["job_name"] == f"poindexter-{cfg['report_type_id']}"


def test_channel_performance_rejects_an_empty_window():
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(yp.channel_performance(object(), days=0))
