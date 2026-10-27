"""Long videos are optional per niche (2026-10-27).

A niche whose plan names the Short and not the long form gets Shorts only.
Every other plan, including an empty or unknown one, keeps the long form.
"""

from __future__ import annotations

import pytest

from poindexter.services.media_policy import (
    shot_count_sql,
    wants_long_video,
    wants_long_video_sql,
)


@pytest.mark.parametrize(
    "plan,expected",
    [
        (["podcast", "video", "video_short"], True),
        (["video"], True),
        (["podcast", "video_short"], False),
        (["video_short"], False),
        ([], True),
        (None, True),
        (["podcast"], True),
    ],
)
def test_wants_long_video(plan, expected):
    assert wants_long_video(plan) is expected


def test_sql_rule_reads_the_posts_plan_and_defaults_to_long():
    sql = wants_long_video_sql("pt.task_id")
    assert "p.metadata->>'pipeline_task_id' = pt.task_id" in sql
    assert "'video_short' = ANY(p.media_to_generate)" in sql
    assert "'video' = ANY(p.media_to_generate)" in sql
    assert sql.startswith("COALESCE(") and sql.endswith(", TRUE)")


def test_shot_count_sql_names_the_lane():
    sql = shot_count_sql("pv", "short_shot_list")
    assert "pv.stage_data -> 'task_metadata' -> 'short_shot_list' -> 'shots'" in sql
