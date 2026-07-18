"""Unit tests — update_utility_rates audit_log breadcrumb visibility.

Silent-excepts OLD-debt burn-down (batch 18). After the job updates a utility
rate (electricity $/kWh or GPU TDP watts) it writes a ``utility_rates_updated``
breadcrumb to ``audit_log``. That insert was swallowed at DEBUG only — invisible
in prod (Loki ships INFO+) — so a persistent failure silently erased the
"when/why did this rate change" trail. The rate change itself still lands in
``app_settings``, so this is a lost audit trail rather than a lost update.

The signal is a ``logger.warning``, NOT an ``emit_finding``: findings are
themselves ``audit_log`` rows (``emit_finding`` -> ``audit_log_bg``), so a
finding about a broken audit_log would vanish in the very outage it reports.
Loki survives a DB outage. (Contrast batch 16, where the failing write was
``cost_logs`` — a different table — so a finding was correct there.)

The other flagged handler in the same method stays ``# silent-ok``: the narrow
``(OSError, FileNotFoundError, TimeoutError)`` guard around GPU detection is the
expected "nvidia-smi absent on a cloud box" path, and the broad ``except``
immediately below it already ``logger.warning``s a genuine detect failure.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.update_utility_rates import UpdateUtilityRatesJob

_LOGGER_NAME = "services.jobs.update_utility_rates"


def _make_pool(gpu_current: Any = "200", audit_raises: bool = False) -> Any:
    conn = AsyncMock()

    async def _fetchval(query: str, *args: Any) -> Any:
        if "gpu_power_watts" in query:
            return gpu_current
        return None

    conn.fetchval = AsyncMock(side_effect=_fetchval)

    async def _execute(*args: Any) -> str:
        query = str(args[0]) if args else ""
        if audit_raises and "audit_log" in query:
            raise RuntimeError("audit_log missing")
        return "INSERT 0 1"

    conn.execute = AsyncMock(side_effect=_execute)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _run_ctx():
    """Patch the EIA call, nvidia-smi, and the TDP map so a GPU change occurs."""
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(
        return_value=(b"NVIDIA GeForce RTX 5090\n", b""),
    )
    resp = MagicMock()
    resp.json = MagicMock(return_value={"response": {"data": []}})
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=resp)

    return (
        patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=client,
        ),
        patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ),
        patch(
            "services.jobs.update_utility_rates._load_gpu_tdp_map",
            return_value={"RTX 5090": 575},
        ),
    )


def _audit_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "audit_log" in r.getMessage()
    ]


@pytest.mark.asyncio
async def test_audit_breadcrumb_failure_emits_warning(caplog):
    """A failed utility_rates_updated write is surfaced at WARNING (Loki)."""
    pool, _conn = _make_pool(gpu_current="200", audit_raises=True)
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    c1, c2, c3 = _run_ctx()
    with c1, c2, c3:
        job = UpdateUtilityRatesJob()
        result = await job.run(pool, {})

    # The rate update itself still succeeded — only the breadcrumb failed.
    assert result.ok is True
    warnings = _audit_warnings(caplog)
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_successful_audit_breadcrumb_emits_no_warning(caplog):
    """The healthy path stays quiet."""
    pool, _conn = _make_pool(gpu_current="200", audit_raises=False)
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    c1, c2, c3 = _run_ctx()
    with c1, c2, c3:
        job = UpdateUtilityRatesJob()
        result = await job.run(pool, {})

    assert result.ok is True
    assert _audit_warnings(caplog) == []
