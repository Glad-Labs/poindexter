"""Unit tests for ``services/jobs/probe_decode_split_coverage.py``.

The probe watches ``cost_logs.decode_duration_ms`` coverage on models that have
previously reported an Ollama timing split, so a fail-open monkey-patch going
dark (LiteLLM upgrade moving ``transform_response``) surfaces as a finding
instead of the throughput dataset silently ceasing to grow.

Pool mocked. ``emit_finding`` patched so routing intent is asserted without
touching audit_log.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from services.jobs.probe_decode_split_coverage import ProbeDecodeSplitCoverageJob
from services.site_config import SiteConfig

_MODULE = "services.jobs.probe_decode_split_coverage"


def _make_pool(rows: list[dict] | None = None) -> Any:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _row(model: str, total: int, covered: int) -> dict:
    return {"model": model, "total": total, "covered": covered}


class TestProbeDecodeSplitCoverageJob:
    async def test_full_coverage_is_quiet(self):
        """The live-prod shape (100% across every model) must not page."""
        pool, _ = _make_pool([_row("qwen2.5:7b", 1165, 1165), _row("phi4:14b", 60, 60)])
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDecodeSplitCoverageJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert result.ok is True
        mock_emit.assert_not_called()
        assert result.metrics["coverage_pct"] == 100.0
        assert result.metrics["models_below_threshold"] == 0

    async def test_emits_finding_when_a_model_regresses(self):
        pool, _ = _make_pool(
            [_row("qwen2.5:7b", 1000, 1000), _row("gemma-4-31B-it-qat:latest", 500, 250)],
        )
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDecodeSplitCoverageJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert result.ok is True
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "llm_decode_split_coverage_low"
        assert kwargs["severity"] == "warn"
        assert kwargs["dedup_key"] == "llm_decode_split_coverage_low"
        # Only the regressed model is named; the healthy one is not an offender.
        assert "gemma-4-31B-it-qat:latest" in kwargs["body"]
        assert [m["model"] for m in kwargs["extra"]["models"]] == [
            "gemma-4-31B-it-qat:latest",
        ]
        assert kwargs["extra"]["models"][0]["coverage_pct"] == 50.0
        assert result.metrics["models_below_threshold"] == 1

    async def test_total_seam_failure_names_every_model(self):
        """A litellm upgrade breaking the wrapper drops every model at once."""
        pool, _ = _make_pool([_row("qwen2.5:7b", 400, 0), _row("phi4:14b", 200, 0)])
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            await ProbeDecodeSplitCoverageJob().run(pool, {"_site_config": SiteConfig()})
        kwargs = mock_emit.call_args.kwargs
        assert {m["model"] for m in kwargs["extra"]["models"]} == {
            "qwen2.5:7b", "phi4:14b",
        }
        assert kwargs["extra"]["overall_coverage_pct"] == 0.0

    async def test_below_sample_floor_gives_no_verdict(self):
        """A quiet window must report, not page at 0/1."""
        pool, _ = _make_pool([_row("qwen2.5:7b", 3, 0)])
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDecodeSplitCoverageJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert result.ok is True
        mock_emit.assert_not_called()
        assert result.metrics["below_sample_floor"] is True

    async def test_no_eligible_rows_is_not_an_alert(self):
        """Empty result set (fresh install, no local calls yet) stays quiet."""
        pool, _ = _make_pool([])
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDecodeSplitCoverageJob().run(
                pool, {"_site_config": SiteConfig()},
            )
        assert result.ok is True
        mock_emit.assert_not_called()

    async def test_thresholds_are_db_driven(self):
        """A stricter threshold from app_settings changes the verdict."""
        rows = [_row("qwen2.5:7b", 1000, 950)]  # 95%
        pool, _ = _make_pool(rows)
        lenient = SiteConfig(initial_config={"llm_decode_split_min_coverage_pct": "90"})
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            await ProbeDecodeSplitCoverageJob().run(pool, {"_site_config": lenient})
        mock_emit.assert_not_called()

        pool, _ = _make_pool(rows)
        strict = SiteConfig(initial_config={"llm_decode_split_min_coverage_pct": "99"})
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            await ProbeDecodeSplitCoverageJob().run(pool, {"_site_config": strict})
        mock_emit.assert_called_once()

    async def test_window_and_learn_days_are_passed_to_the_query(self):
        pool, conn = _make_pool([])
        sc = SiteConfig(
            initial_config={
                "llm_decode_split_window_hours": "6",
                "llm_decode_split_learn_days": "14",
            },
        )
        await ProbeDecodeSplitCoverageJob().run(pool, {"_site_config": sc})
        args = conn.fetch.call_args.args
        assert args[1] == 14  # learn_days
        assert args[2] == 6   # window_hours

    async def test_query_normalizes_the_ollama_prefix(self):
        """One engine logs under several spellings — the GROUP BY must fold them."""
        from services.jobs.probe_decode_split_coverage import _COVERAGE_QUERY

        assert "regexp_replace(model, '^ollama(_chat)?/', '')" in _COVERAGE_QUERY

    async def test_disabled_probe_short_circuits(self):
        pool, conn = _make_pool([])
        sc = SiteConfig(initial_config={"llm_decode_split_probe_enabled": "false"})
        result = await ProbeDecodeSplitCoverageJob().run(pool, {"_site_config": sc})
        assert result.ok is True
        assert result.detail == "probe disabled"
        conn.fetch.assert_not_called()

    async def test_no_pool_is_reported_not_raised(self):
        result = await ProbeDecodeSplitCoverageJob().run(None, {})
        assert result.ok is False
        assert "no pool" in result.detail

    async def test_query_failure_never_crashes_the_cycle(self):
        pool, conn = _make_pool([])
        conn.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
        result = await ProbeDecodeSplitCoverageJob().run(
            pool, {"_site_config": SiteConfig()},
        )
        assert result.ok is False
        assert "query failed" in result.detail

    async def test_failed_and_zero_output_rows_are_excluded(self):
        """A GPU-lock timeout decodes nothing — its NULL is correct, not a miss."""
        from services.jobs.probe_decode_split_coverage import _COVERAGE_QUERY

        assert "c.success = true" in _COVERAGE_QUERY
        assert "c.output_tokens > 0" in _COVERAGE_QUERY
