"""End-to-end tests for the Singer subprocess tap handler + the
external_metrics_writer record consumer.

We use a tiny Python script as the "tap binary" so the whole pipeline
runs without a real third-party Singer tap installed. The test taps
emit valid Singer messages on stdout exactly the way tap-csv /
tap-google-search-console / etc. do.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from typing import Any

import pytest

from services.integrations import registry as registry_module
from services.integrations.handlers import (
    tap_external_metrics_writer,
    tap_singer_subprocess,
)


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        return "INSERT 0 1"

    async def fetchval(self, query, *args):
        return self._pool.next_fetchval


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self.next_fetchval: Any = None

    def acquire(self):
        return _FakeConn(self)


def _row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000033",
        "name": "test_singer",
        "handler_name": "singer_subprocess",
        "tap_type": "singer",
        "target_table": "external_metrics",
        "record_handler": "external_metrics_writer",
        "config": {},
        "state": {},
        "enabled": True,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolation():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    # Real handlers from this module under test.
    registry_module._REGISTRY["tap.singer_subprocess"] = (
        tap_singer_subprocess.singer_subprocess
    )
    registry_module._REGISTRY["tap.external_metrics_writer"] = (
        tap_external_metrics_writer.external_metrics_writer
    )
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Helper: write a tiny Python script that pretends to be a Singer tap.
# ---------------------------------------------------------------------------


def _make_tap_script(tmp_path, *, lines: list[str], exit_code: int = 0) -> str:
    """Create a fake Singer tap as a Python file; return invocation string."""
    script_body = textwrap.dedent(f"""
        import sys
        for line in {lines!r}:
            sys.stdout.write(line + "\\n")
            sys.stdout.flush()
        sys.exit({exit_code})
    """)
    path = tmp_path / "fake_tap.py"
    path.write_text(script_body, encoding="utf-8")
    # Cross-platform invocation: use the same Python interpreter.
    return f'"{sys.executable}" "{path}"'


# ---------------------------------------------------------------------------
# external_metrics_writer unit-only tests (no subprocess)
# ---------------------------------------------------------------------------


class TestExternalMetricsWriter:
    @pytest.mark.asyncio
    async def test_emits_one_row_per_metric_field(self):
        pool = _FakePool()
        row = _row(
            config={
                "metrics_mapping": {
                    "page_metrics": {
                        "source": "google_search_console",
                        "date_field": "date",
                        "metric_fields": ["impressions", "clicks", "ctr"],
                        "dimension_fields": ["country"],
                    }
                }
            }
        )
        result = await tap_external_metrics_writer.external_metrics_writer(
            {
                "stream": "page_metrics",
                "record": {
                    "date": "2026-04-24",
                    "impressions": 1000,
                    "clicks": 25,
                    "ctr": 0.025,
                    "country": "US",
                },
                "schema": {},
            },
            site_config=None,
            row=row,
            pool=pool,
        )
        assert result["inserted"] == 3
        # Every INSERT writes the same source/date/dimensions, varying metric.
        metric_args = [args for q, args in pool.executes if "INSERT INTO external_metrics" in q]
        assert len(metric_args) == 3
        assert {a[1] for a in metric_args} == {"impressions", "clicks", "ctr"}
        # Country dimension stored as JSON
        for a in metric_args:
            assert "US" in a[3]

    @pytest.mark.asyncio
    async def test_skips_unmapped_stream(self):
        pool = _FakePool()
        row = _row(config={"metrics_mapping": {"other_stream": {"source": "x"}}})
        result = await tap_external_metrics_writer.external_metrics_writer(
            {"stream": "page_metrics", "record": {}, "schema": {}},
            site_config=None,
            row=row,
            pool=pool,
        )
        assert result["inserted"] == 0
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_skips_record_with_bad_date(self):
        pool = _FakePool()
        row = _row(
            config={
                "metrics_mapping": {
                    "page_metrics": {
                        "date_field": "date",
                        "metric_fields": ["impressions"],
                    }
                }
            }
        )
        result = await tap_external_metrics_writer.external_metrics_writer(
            {"stream": "page_metrics", "record": {"date": "not-a-date", "impressions": 5}, "schema": {}},
            site_config=None,
            row=row,
            pool=pool,
        )
        assert result["inserted"] == 0
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_links_post_by_slug(self):
        pool = _FakePool()
        pool.next_fetchval = "post-uuid-here"
        row = _row(
            config={
                "metrics_mapping": {
                    "page_metrics": {
                        "date_field": "date",
                        "post_field": "slug",
                        "metric_fields": ["impressions"],
                    }
                }
            }
        )
        await tap_external_metrics_writer.external_metrics_writer(
            {
                "stream": "page_metrics",
                "record": {"date": "2026-04-24", "slug": "my-post", "impressions": 100},
                "schema": {},
            },
            site_config=None,
            row=row,
            pool=pool,
        )
        # First execute: SELECT id FROM posts WHERE slug = $1 — but our
        # FakeConn routes that through fetchval. The INSERT should
        # carry post_id=post-uuid-here and slug=my-post.
        ins = [args for q, args in pool.executes if "INSERT INTO external_metrics" in q]
        assert ins
        assert ins[0][4] == "post-uuid-here"
        assert ins[0][5] == "my-post"

    @pytest.mark.asyncio
    async def test_skips_metric_with_unparseable_value(self):
        pool = _FakePool()
        row = _row(
            config={
                "metrics_mapping": {
                    "page_metrics": {
                        "date_field": "date",
                        "metric_fields": ["impressions", "clicks"],
                    }
                }
            }
        )
        result = await tap_external_metrics_writer.external_metrics_writer(
            {
                "stream": "page_metrics",
                "record": {"date": "2026-04-24", "impressions": "garbage", "clicks": 10},
                "schema": {},
            },
            site_config=None,
            row=row,
            pool=pool,
        )
        # Only 'clicks' inserted; 'impressions' skipped due to unparseable value.
        assert result["inserted"] == 1
        assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# singer_subprocess end-to-end tests with a real subprocess
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.name == "nt" and not os.getenv("PYTEST_RUN_SUBPROCESS_TESTS"),
    reason="Subprocess tests can be flaky on Windows shells; set PYTEST_RUN_SUBPROCESS_TESTS=1 to opt in.",
)
class TestSingerSubprocessEndToEnd:
    @pytest.mark.asyncio
    async def test_dispatches_records_through_handler(self, tmp_path):
        pool = _FakePool()
        lines = [
            json.dumps({"type": "SCHEMA", "stream": "page_metrics", "schema": {"properties": {}}}),
            json.dumps({"type": "RECORD", "stream": "page_metrics", "record": {
                "date": "2026-04-24", "impressions": 100, "clicks": 5,
            }}),
            json.dumps({"type": "STATE", "value": {"bookmarks": {"page_metrics": "2026-04-24"}}}),
        ]
        command = _make_tap_script(tmp_path, lines=lines)

        row = _row(
            config={
                "command": command,
                "metrics_mapping": {
                    "page_metrics": {
                        "source": "test_source",
                        "date_field": "date",
                        "metric_fields": ["impressions", "clicks"],
                    }
                },
                "max_records": 10,
                "timeout_seconds": 30,
            },
        )
        result = await tap_singer_subprocess.singer_subprocess(
            None, site_config=None, row=row, pool=pool,
        )

        assert result["records"] == 1
        assert "page_metrics" in result["schemas"]
        # external_metrics_writer fired and emitted 2 rows (one per metric).
        ins = [args for q, args in pool.executes if "INSERT INTO external_metrics" in q]
        assert len(ins) == 2
        # State persisted on the row.
        state_updates = [args for q, args in pool.executes if "UPDATE external_taps SET state" in q]
        assert state_updates
        state = json.loads(state_updates[-1][1])
        assert state == {"bookmarks": {"page_metrics": "2026-04-24"}}

    @pytest.mark.asyncio
    async def test_streams_filter_drops_records(self, tmp_path):
        pool = _FakePool()
        lines = [
            json.dumps({"type": "SCHEMA", "stream": "wanted", "schema": {}}),
            json.dumps({"type": "SCHEMA", "stream": "ignored", "schema": {}}),
            json.dumps({"type": "RECORD", "stream": "wanted", "record": {"date": "2026-04-24", "impressions": 1}}),
            json.dumps({"type": "RECORD", "stream": "ignored", "record": {"date": "2026-04-24", "impressions": 99}}),
        ]
        command = _make_tap_script(tmp_path, lines=lines)
        row = _row(
            config={
                "command": command,
                "streams": ["wanted"],
                "metrics_mapping": {
                    "wanted": {
                        "date_field": "date",
                        "metric_fields": ["impressions"],
                    }
                },
            },
        )
        result = await tap_singer_subprocess.singer_subprocess(
            None, site_config=None, row=row, pool=pool,
        )
        assert result["records"] == 1
        assert result["filtered"] == 1

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises(self, tmp_path):
        pool = _FakePool()
        # Tap crashes with exit 1 after emitting nothing.
        command = _make_tap_script(tmp_path, lines=[], exit_code=1)
        row = _row(
            config={
                "command": command,
                "metrics_mapping": {},
            },
        )
        with pytest.raises(RuntimeError, match="exited 1"):
            await tap_singer_subprocess.singer_subprocess(
                None, site_config=None, row=row, pool=pool,
            )

    @pytest.mark.asyncio
    async def test_missing_command_raises(self):
        pool = _FakePool()
        row = _row(config={"metrics_mapping": {}})
        with pytest.raises(ValueError, match="command"):
            await tap_singer_subprocess.singer_subprocess(
                None, site_config=None, row=row, pool=pool,
            )

    @pytest.mark.asyncio
    async def test_missing_record_handler_raises(self, tmp_path):
        pool = _FakePool()
        command = _make_tap_script(tmp_path, lines=[])
        row = _row(record_handler=None, config={"command": command})
        with pytest.raises(ValueError, match="record_handler"):
            await tap_singer_subprocess.singer_subprocess(
                None, site_config=None, row=row, pool=pool,
            )
