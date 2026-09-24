"""Backlog expressions for downsample + embeddings_orphan_prune (poindexter#1067).

``probe_retention_backlog`` reported five enabled policies ``unmonitored``
(``gpu_metrics`` / ``sensor_samples`` via ``downsample``, and the three
``embeddings.orphan_prune.*`` rows): nothing verified they keep up, and
``last_error`` NULL + a current ``last_run_at`` cannot tell an idle policy from
a misconfigured one. Measured on prod after this change: all five ``measured``,
all at 0.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import poindexter.services.integrations.handlers.retention_downsample  # noqa: F401
import poindexter.services.integrations.handlers.retention_embeddings_orphan_prune as orphan
from poindexter.services.integrations.retention_backlog import (
    BacklogQuery,
    build_backlog_query,
    handlers_with_backlog,
)

pytestmark = pytest.mark.unit

_RULE = {"keep_raw_days": 30, "rollup_table": "gpu_metrics_hourly",
         "rollup_interval": "1 hour", "aggregations": [{"col": "u", "fn": "avg"}]}


def test_both_handlers_declare_backlog():
    assert {"downsample", "embeddings_orphan_prune"} <= handlers_with_backlog()


def test_downsample_counts_raw_rows_past_the_window_anchored_at_the_last_run():
    ran = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)
    q = build_backlog_query(
        "downsample",
        {"table_name": "gpu_metrics", "age_column": "timestamp",
         "downsample_rule": _RULE, "last_run_at": ran},
    )
    assert isinstance(q, BacklogQuery)
    assert "FROM gpu_metrics" in q.sql
    assert "timestamp < $2::timestamptz - make_interval(days => $1)" in q.sql
    assert q.params == (30, ran)


def test_downsample_without_a_run_falls_back_to_now():
    q = build_backlog_query(
        "downsample",
        {"table_name": "sensor_samples", "age_column": "sampled_at", "downsample_rule": _RULE},
    )
    assert "sampled_at < now() - make_interval(days => $1)" in q.sql
    assert q.params == (30,)


def test_downsample_rejects_a_bad_identifier():
    with pytest.raises(ValueError, match="table_name"):
        build_backlog_query(
            "downsample",
            {"table_name": "t; DROP TABLE posts", "downsample_rule": _RULE},
        )


@pytest.mark.parametrize(
    "row",
    [
        {"table_name": "t", "downsample_rule": _RULE, "config": {"dry_run": True}},
        {"table_name": "t", "downsample_rule": {}},
    ],
)
def test_downsample_dry_run_or_no_rule_declares_none(row):
    assert build_backlog_query("downsample", row) is None


@pytest.mark.parametrize("source", ["posts", "audit", "brain"])
def test_orphan_backlog_is_the_delete_join_without_the_limit(source):
    q = build_backlog_query("embeddings_orphan_prune", {"config": {"source_table": source}})
    assert "COUNT(*)" in q.sql
    assert f"e.source_table = '{source}'" in q.sql
    assert "LIMIT" not in q.sql
    assert q.params == ()


def test_orphan_backlog_covers_exactly_the_sources_the_handler_prunes():
    assert set(orphan._ORPHAN_COUNT_SQL) == set(orphan._SOURCE_HANDLERS)


def test_orphan_backlog_refuses_an_unknown_source():
    """The handler raises on it; the probe must report an error, not a zero."""
    with pytest.raises(ValueError, match="source_table"):
        build_backlog_query("embeddings_orphan_prune", {"config": {"source_table": "nope"}})
