"""Integration tests for plugins.scheduler.PluginScheduler.

Uses the real-services harness so PluginConfig.load/save actually write
to a test DB. Verifies scheduling behavior without waiting for jobs to
fire (apscheduler provides introspection).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest

from plugins import JobResult, PluginConfig, PluginScheduler
from tests.integration.conftest import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


@dataclass
class _DemoJob:
    """Minimal Job-shaped test double. Counts invocations for assertions."""

    name: str = "demo_job"
    description: str = "demo"
    schedule: str = "every 60 seconds"
    idempotent: bool = True
    run_count: int = 0

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        self.run_count += 1
        return JobResult(ok=True, detail="ran", changes_made=0)


async def test_register_accepts_enabled_job(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """A Job whose PluginConfig has enabled=True gets scheduled."""
    async with clean_test_tables.acquire() as conn:
        await PluginConfig(
            plugin_type="job",
            name="demo_job",
            enabled=True,
        ).save(conn)

    scheduler = PluginScheduler(clean_test_tables)
    job = _DemoJob()
    added = await scheduler.register_job(job)

    assert added is True
    assert scheduler.jobs() == ["demo_job"]


async def test_register_skips_disabled_job(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """Disabled jobs are skipped without error."""
    async with clean_test_tables.acquire() as conn:
        await PluginConfig(
            plugin_type="job",
            name="demo_job",
            enabled=False,
        ).save(conn)

    scheduler = PluginScheduler(clean_test_tables)
    added = await scheduler.register_job(_DemoJob())

    assert added is False
    assert scheduler.jobs() == []


async def test_register_respects_missing_config_default_enabled(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """When no PluginConfig row exists, enabled defaults to True."""
    # No setup — the DB is clean. Default PluginConfig will be enabled=True.
    scheduler = PluginScheduler(clean_test_tables)
    added = await scheduler.register_job(_DemoJob())

    assert added is True


async def test_register_skips_bad_schedule(
    migrations_applied, clean_test_tables: asyncpg.Pool, caplog
) -> None:
    """A Job with an unrecognized schedule is logged + skipped."""
    scheduler = PluginScheduler(clean_test_tables)
    bad_job = _DemoJob(name="bad_job", schedule="sometimes")

    with caplog.at_level("ERROR"):
        added = await scheduler.register_job(bad_job)

    assert added is False
    assert scheduler.jobs() == []
    assert any("unrecognized schedule" in rec.message for rec in caplog.records)


async def test_double_register_same_job_is_noop(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """Registering the same Job twice returns False on second attempt."""
    scheduler = PluginScheduler(clean_test_tables)
    job = _DemoJob()

    first = await scheduler.register_job(job)
    second = await scheduler.register_job(job)

    assert first is True
    assert second is False
    assert scheduler.jobs() == ["demo_job"]


async def test_register_all_filters_correctly(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """register_all returns only the names that were actually scheduled."""
    async with clean_test_tables.acquire() as conn:
        await PluginConfig(
            plugin_type="job", name="enabled_job", enabled=True,
        ).save(conn)
        await PluginConfig(
            plugin_type="job", name="disabled_job", enabled=False,
        ).save(conn)

    scheduler = PluginScheduler(clean_test_tables)
    jobs = [
        _DemoJob(name="enabled_job"),
        _DemoJob(name="disabled_job"),
        _DemoJob(name="bad_schedule", schedule="sometime maybe"),
    ]
    accepted = await scheduler.register_all(jobs)

    assert accepted == ["enabled_job"]


async def test_various_schedule_formats_parse(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """Interval and cron formats both parse without error."""
    scheduler = PluginScheduler(clean_test_tables)

    formats = [
        "every 30 seconds",
        "every 5 minutes",
        "every 1 hour",
        "every 7 days",
        "0 */6 * * *",   # cron every 6 hours
        "0 9 * * 1-5",   # cron weekdays at 9am
    ]

    for i, fmt in enumerate(formats):
        job = _DemoJob(name=f"demo_{i}", schedule=fmt)
        added = await scheduler.register_job(job)
        assert added is True, f"schedule format {fmt!r} should have parsed"

    assert len(scheduler.jobs()) == len(formats)


async def test_start_and_shutdown_clean(
    migrations_applied, clean_test_tables: asyncpg.Pool
) -> None:
    """Starting and shutting down the scheduler doesn't leak or error."""
    scheduler = PluginScheduler(clean_test_tables)
    await scheduler.register_job(_DemoJob())
    scheduler.start()

    # Let the event loop spin briefly so apscheduler settles.
    await asyncio.sleep(0.05)

    await scheduler.shutdown(wait=False)
