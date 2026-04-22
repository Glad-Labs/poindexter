"""Unit tests for ``services/jobs/update_utility_rates.py``.

Covers the GPU-TDP-map loader, the EIA-rate refresh + drift
threshold, the GPU-TDP refresh, and the overall Job.run happy +
skip paths.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.jobs.update_utility_rates import (
    DEFAULT_GPU_TDP_MAP,
    UpdateUtilityRatesJob,
    _load_gpu_tdp_map,
    _refresh_electricity_rate,
    _refresh_gpu_tdp,
)


def _mock_sc() -> MagicMock:
    """Return a MagicMock shaped like SiteConfig for job.run() calls.

    Post-Phase-H (GH#95) jobs receive site_config via the Job Protocol
    kwarg instead of reaching into a module singleton.
    """
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": d
    sc.get_bool.side_effect = lambda k, d=False: d
    sc.get_int.side_effect = lambda k, d=0: d
    return sc


def _make_pool(
    electricity_current: Any = None,
    gpu_current: Any = None,
    execute_raises: BaseException | None = None,
) -> Any:
    """Pool whose fetchval returns (electricity_current | gpu_current)
    based on which key is queried."""
    conn = AsyncMock()

    async def _fetchval(query: str, *args: Any) -> Any:
        if "electricity_rate_kwh" in query:
            return electricity_current
        if "gpu_power_watts" in query:
            return gpu_current
        return None

    conn.fetchval = AsyncMock(side_effect=_fetchval)
    if execute_raises is not None:
        conn.execute = AsyncMock(side_effect=execute_raises)
    else:
        conn.execute = AsyncMock(return_value="INSERT 0 1")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _patched_httpx(json_body: dict | None = None, raise_for_status=False, raises=None):
    resp = MagicMock()
    resp.json = MagicMock(return_value=json_body or {})
    if raise_for_status:
        resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(),
        ))
    else:
        resp.raise_for_status = MagicMock()

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if raises is not None:
        client.get = AsyncMock(side_effect=raises)
    else:
        client.get = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# _load_gpu_tdp_map
# ---------------------------------------------------------------------------


class TestLoadGpuTdpMap:
    def test_empty_config_returns_defaults(self):
        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": "" if k == "gpu_tdp_map" else d
        assert _load_gpu_tdp_map(sc) is DEFAULT_GPU_TDP_MAP

    def test_valid_json_override_parsed(self):
        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": '{"RTX 9999": 999}' if k == "gpu_tdp_map" else d
        result = _load_gpu_tdp_map(sc)
        assert result == {"RTX 9999": 999}

    def test_invalid_json_falls_back_to_defaults(self):
        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": "{not-json" if k == "gpu_tdp_map" else d
        assert _load_gpu_tdp_map(sc) is DEFAULT_GPU_TDP_MAP

    def test_non_dict_override_ignored(self):
        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": '["not", "a", "dict"]' if k == "gpu_tdp_map" else d
        assert _load_gpu_tdp_map(sc) is DEFAULT_GPU_TDP_MAP


# ---------------------------------------------------------------------------
# _refresh_electricity_rate
# ---------------------------------------------------------------------------


class TestRefreshElectricityRate:
    @pytest.mark.asyncio
    async def test_no_records_returns_none(self):
        pool, _ = _make_pool()
        client = _patched_httpx(json_body={"response": {"data": []}})
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=client,
        ):
            result = await _refresh_electricity_rate(
                pool, api_key="k", drift_threshold=0.1,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_first_ever_fetch_writes_setting(self):
        """When no current value, drift is treated as >threshold → always write."""
        pool, conn = _make_pool(electricity_current=None)
        body = {"response": {"data": [{"price": 16.11, "period": "2025-12"}]}}
        client = _patched_httpx(json_body=body)
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=client,
        ):
            change = await _refresh_electricity_rate(
                pool, api_key="k", drift_threshold=0.10,
            )
        assert change is not None
        assert change["new"] == 0.1611
        assert change["period"] == "2025-12"
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_small_drift_no_update(self):
        """Drift under threshold → no UPDATE."""
        pool, conn = _make_pool(electricity_current="0.1600")
        # 16.11 cents = $0.1611 — 0.69% drift from $0.1600, under 10%.
        body = {"response": {"data": [{"price": 16.11, "period": "2025-12"}]}}
        client = _patched_httpx(json_body=body)
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=client,
        ):
            change = await _refresh_electricity_rate(
                pool, api_key="k", drift_threshold=0.10,
            )
        assert change is None
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_large_drift_triggers_update(self):
        """Drift over threshold → UPDATE."""
        pool, conn = _make_pool(electricity_current="0.1000")
        # $0.1611 is ~60% up from $0.1000.
        body = {"response": {"data": [{"price": 16.11, "period": "2025-12"}]}}
        client = _patched_httpx(json_body=body)
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=client,
        ):
            change = await _refresh_electricity_rate(
                pool, api_key="k", drift_threshold=0.10,
            )
        assert change is not None
        assert change["old"] == 0.1
        assert change["new"] == 0.1611
        conn.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# _refresh_gpu_tdp
# ---------------------------------------------------------------------------


class TestRefreshGpuTdp:
    @pytest.mark.asyncio
    async def test_known_gpu_triggers_update(self):
        pool, conn = _make_pool(gpu_current="200")
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(
            return_value=(b"NVIDIA GeForce RTX 5090\n", b""),
        )
        with patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            change = await _refresh_gpu_tdp(pool, {"RTX 5090": 575})
        assert change == {"old": 200, "new": 575, "gpu": "NVIDIA GeForce RTX 5090"}
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_gpu_returns_none(self):
        pool, _ = _make_pool()
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(return_value=(b"Mystery GPU\n", b""))
        with patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            change = await _refresh_gpu_tdp(pool, {"RTX 5090": 575})
        assert change is None

    @pytest.mark.asyncio
    async def test_unchanged_tdp_returns_none(self):
        pool, conn = _make_pool(gpu_current="575")
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(
            return_value=(b"NVIDIA GeForce RTX 5090\n", b""),
        )
        with patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            change = await _refresh_gpu_tdp(pool, {"RTX 5090": 575})
        assert change is None
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_nvidia_smi_output_returns_none(self):
        pool, _ = _make_pool()
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(return_value=(b"\n", b""))
        with patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ):
            assert await _refresh_gpu_tdp(pool, DEFAULT_GPU_TDP_MAP) is None


# ---------------------------------------------------------------------------
# Job.run
# ---------------------------------------------------------------------------


class TestContract:
    def test_has_required_attrs(self):
        job = UpdateUtilityRatesJob()
        assert job.name == "update_utility_rates"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_skip_both_returns_zero_changes(self):
        pool, _ = _make_pool()
        job = UpdateUtilityRatesJob()
        result = await job.run(
            pool,
            {"skip_electricity": True, "skip_gpu": True},
            site_config=_mock_sc(),
        )
        assert result.ok is True
        assert result.changes_made == 0
        assert "current" in result.detail

    @pytest.mark.asyncio
    async def test_eia_failure_does_not_abort_gpu_path(self):
        """A failed EIA call must not prevent the GPU branch from running."""
        pool, _ = _make_pool(gpu_current="200")
        # EIA raises network error
        eia_client = _patched_httpx(raises=httpx.ConnectError("dns fail"))
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(
            return_value=(b"NVIDIA GeForce RTX 5090\n", b""),
        )
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=eia_client,
        ), patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ), patch(
            "services.jobs.update_utility_rates._load_gpu_tdp_map",
            return_value={"RTX 5090": 575},
        ):
            job = UpdateUtilityRatesJob()
            result = await job.run(pool, {}, site_config=_mock_sc())
        # 1 change (GPU) despite EIA failing.
        assert result.ok is True
        assert result.changes_made == 1
        assert "gpu_power_watts" in result.detail

    @pytest.mark.asyncio
    async def test_nvidia_smi_missing_does_not_abort(self):
        """On cloud boxes without nvidia-smi the subprocess call raises
        FileNotFoundError — the job should still complete ok."""
        pool, _ = _make_pool()
        eia_body = {"response": {"data": []}}  # no records → no change
        eia_client = _patched_httpx(json_body=eia_body)
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=eia_client,
        ), patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("nvidia-smi not found")),
        ):
            job = UpdateUtilityRatesJob()
            result = await job.run(pool, {}, site_config=_mock_sc())
        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_config_api_key_wins_over_site_config(self):
        """``config.eia_api_key`` overrides site_config."""
        pool, _ = _make_pool()
        client = _patched_httpx(json_body={"response": {"data": []}})
        sc = _mock_sc()
        sc.get.side_effect = lambda k, d="": "SITE_KEY"
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=client,
        ) as mock_cls, patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError),
        ):
            job = UpdateUtilityRatesJob()
            await job.run(pool, {"eia_api_key": "OVERRIDE"}, site_config=sc)
        # Verify the URL built with OVERRIDE.
        mock_cls.assert_called_once()
        # The URL is the first positional arg to client.get
        get_url = client.get.call_args.args[0]
        assert "api_key=OVERRIDE" in get_url
        assert "SITE_KEY" not in get_url

    @pytest.mark.asyncio
    async def test_changes_trigger_audit_log_insert(self):
        """When a change occurs, audit_log INSERT should fire."""
        pool, conn = _make_pool(gpu_current="200")
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(
            return_value=(b"NVIDIA GeForce RTX 5090\n", b""),
        )
        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=_patched_httpx(json_body={"response": {"data": []}}),
        ), patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ), patch(
            "services.jobs.update_utility_rates._load_gpu_tdp_map",
            return_value={"RTX 5090": 575},
        ):
            job = UpdateUtilityRatesJob()
            await job.run(pool, {}, site_config=_mock_sc())

        # Two execute calls: 1) UPSERT gpu_power_watts, 2) audit_log INSERT
        assert conn.execute.await_count >= 2
        audit_call = conn.execute.await_args_list[-1]
        assert "audit_log" in audit_call.args[0]
        assert audit_call.args[1] == "utility_rates_updated"
        # details is JSON string; confirm our change made it in.
        details = json.loads(audit_call.args[3])
        assert "gpu_power_watts" in details

    @pytest.mark.asyncio
    async def test_audit_log_failure_does_not_abort(self):
        pool, _ = _make_pool(
            gpu_current="200",
            execute_raises=RuntimeError("audit_log missing"),
        )
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(
            return_value=(b"NVIDIA GeForce RTX 5090\n", b""),
        )
        # Override execute to succeed on UPSERTs but fail on audit_log
        call_count = {"n": 0}

        async def _execute_side_effect(*args: Any) -> str:
            call_count["n"] += 1
            # Roughly: 1st = gpu_power_watts upsert, 2nd = audit_log
            query = args[0] if args else ""
            if "audit_log" in str(query):
                raise RuntimeError("audit_log missing")
            return "INSERT 0 1"

        pool.acquire.return_value.__aenter__.return_value.execute = AsyncMock(
            side_effect=_execute_side_effect,
        )

        with patch(
            "services.jobs.update_utility_rates.httpx.AsyncClient",
            return_value=_patched_httpx(json_body={"response": {"data": []}}),
        ), patch(
            "services.jobs.update_utility_rates.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ), patch(
            "services.jobs.update_utility_rates._load_gpu_tdp_map",
            return_value={"RTX 5090": 575},
        ):
            job = UpdateUtilityRatesJob()
            result = await job.run(pool, {}, site_config=_mock_sc())
        # The UPSERT succeeded; audit_log failure didn't reset changes_made.
        assert result.ok is True
        assert result.changes_made == 1
