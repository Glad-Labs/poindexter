"""Unit tests for ``services/jobs/ingest_benchmark_results.py``.

Covers the GitHub Actions artifact → benchmark_results pull that gives the
nightly ``benchmarks`` workflow a Grafana surface (its hosted runner can't
reach this DB, so ingestion is pull-side).

Mirrors the pattern in test_sync_cloudflare_analytics_job.py — SiteConfig DI
seam, fake ``httpx`` module via sys.modules, fake asyncpg pool. The parser
fixture uses REAL values from run 32925071563's artifact (the workflow's
first green run, 2026-08-26), trimmed to two benchmarks.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.ingest_benchmark_results import (
    IngestBenchmarkResultsJob,
    parse_benchmark_artifact,
)

# Trimmed from the real benchmark-results-32925071563 artifact.
ARTIFACT_PAYLOAD: dict[str, Any] = {
    "commit_info": {"id": "f7a91003c7c52a521b6660bc451adebe90c32945"},
    "benchmarks": [
        {
            "name": "test_health_endpoint_latency",
            "group": "health",
            "stats": {
                "mean": 0.005095250099999759,
                "min": 0.0049271240000052785,
                "max": 0.0059391340000019,
                "stddev": 0.00023425770079823957,
                "median": 0.005023280000003183,
                "rounds": 20,
            },
        },
        {
            "name": "test_posts_list_latency",
            "group": "posts",
            "stats": {
                "mean": 0.004601786100000046,
                "min": 0.003056447999995271,
                "max": 0.007021369000000277,
                "stddev": 0.0011930339997229895,
                "median": 0.00429041299999966,
                "rounds": 30,
            },
        },
    ],
}

RUN_ID = 32925071563
RUNS_PAYLOAD = {
    "workflow_runs": [
        {
            "id": RUN_ID,
            "run_started_at": "2026-08-26T03:04:26Z",
            "created_at": "2026-08-26T03:04:26Z",
        }
    ]
}
ARTIFACTS_PAYLOAD = {
    "artifacts": [
        {
            "id": 9591228348,
            "name": f"benchmark-results-{RUN_ID}",
            "expired": False,
        }
    ]
}


def _artifact_zip_bytes(payload: dict[str, Any] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "benchmark_results.json",
            json.dumps(payload if payload is not None else ARTIFACT_PAYLOAD),
        )
    return buf.getvalue()


def _sc(repo: str = "Glad-Labs/poindexter") -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "benchmark_ingest_repo": repo,
    }.get(key, default)
    return sc


def _make_pool(fetchval_results: list | None = None):
    """asyncpg-shaped pool: acquire() yields a conn with stubbed methods.

    ``fetchval_results`` pre-seeds conn.fetchval side effects (the job uses
    fetchval for both the already-ingested pre-check and the RETURNING 1
    insert). Default: pre-check misses (None), every insert returns 1.
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="OK")
    if fetchval_results is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_results)
    else:
        conn.fetchval = AsyncMock(side_effect=[None] + [1] * 50)

    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


def _resp(status: int = 200, json_payload: Any = None, content: bytes = b""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "fake-response"
    resp.content = content
    resp.json = MagicMock(return_value=json_payload)
    return resp


def _fake_httpx(get_responses: list) -> tuple[MagicMock, AsyncMock]:
    """Fake ``httpx`` module whose client.get pops responses in order."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=get_responses)

    class _AsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return client

        async def __aexit__(self, *args: Any) -> None:
            return None

    fake = MagicMock()
    fake.AsyncClient = _AsyncClient
    return fake, client


def _patch_token(value: str = "test-token"):
    return patch.object(
        IngestBenchmarkResultsJob,
        "_fetch_gh_token",
        AsyncMock(return_value=value),
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIngestBenchmarkResultsJobMetadata:
    def test_name(self):
        assert IngestBenchmarkResultsJob.name == "ingest_benchmark_results"

    def test_idempotent(self):
        assert IngestBenchmarkResultsJob.idempotent is True

    def test_schedule(self):
        assert IngestBenchmarkResultsJob.schedule == "every 6 hours"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseBenchmarkArtifact:
    def test_parses_real_shape(self):
        sha, rows = parse_benchmark_artifact(ARTIFACT_PAYLOAD)
        assert sha == "f7a91003c7c52a521b6660bc451adebe90c32945"
        assert [r["benchmark_name"] for r in rows] == [
            "test_health_endpoint_latency",
            "test_posts_list_latency",
        ]
        health = rows[0]
        assert health["group_name"] == "health"
        assert health["mean_seconds"] == pytest.approx(0.0050952501)
        assert health["rounds"] == 20

    def test_skips_malformed_benchmarks(self):
        payload = {
            "commit_info": {},
            "benchmarks": [
                {"name": "no_stats"},
                {"stats": {"mean": 0.1}},  # no name
                {"name": "bad_mean", "stats": {"mean": "fast"}},
                {"name": "ok", "group": "g", "stats": {"mean": 0.2}},
            ],
        }
        sha, rows = parse_benchmark_artifact(payload)
        assert sha is None
        assert [r["benchmark_name"] for r in rows] == ["ok"]

    def test_empty_payload(self):
        sha, rows = parse_benchmark_artifact({})
        assert sha is None and rows == []


# ---------------------------------------------------------------------------
# run() — configuration postures
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIngestBenchmarkResultsConfig:
    @pytest.mark.asyncio
    async def test_skips_without_site_config(self):
        pool, _ = _make_pool()
        result = await IngestBenchmarkResultsJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "_site_config" in result.detail

    @pytest.mark.asyncio
    async def test_skips_quietly_when_repo_unset(self):
        """Fresh-install posture: no repo configured = benign no-op."""
        pool, _ = _make_pool()
        result = await IngestBenchmarkResultsJob().run(
            pool, {"_site_config": _sc(repo="")}
        )
        assert result.ok is True
        assert "benchmark_ingest_repo unset" in result.detail

    @pytest.mark.asyncio
    async def test_fails_loud_when_repo_set_but_token_missing(self):
        """Half-configured = degraded, not a quiet green (CF-job posture)."""
        pool, _ = _make_pool()
        with _patch_token(""), patch(
            "services.jobs.ingest_benchmark_results.emit_finding"
        ) as finding:
            result = await IngestBenchmarkResultsJob().run(
                pool, {"_site_config": _sc()}
            )
        assert result.ok is False
        assert "gh_token unset" in result.detail
        assert finding.called
        assert finding.call_args.kwargs["kind"] == "benchmark_ingest_degraded"


# ---------------------------------------------------------------------------
# run() — ingest paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIngestBenchmarkResultsIngest:
    @pytest.mark.asyncio
    async def test_ingests_new_run(self):
        pool, conn = _make_pool()
        fake_httpx, client = _fake_httpx(
            [
                _resp(200, RUNS_PAYLOAD),
                _resp(200, ARTIFACTS_PAYLOAD),
                _resp(200, content=_artifact_zip_bytes()),
            ]
        )
        with _patch_token(), patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await IngestBenchmarkResultsJob().run(
                pool, {"_site_config": _sc()}
            )
        assert result.ok is True
        assert result.changes_made == 2
        assert result.metrics == {"runs_ingested": 1, "rows_inserted": 2}
        # Insert used the ON CONFLICT idempotency key with the run's values.
        insert_sql = conn.fetchval.call_args_list[1].args[0]
        assert "ON CONFLICT (workflow_run_id, benchmark_name)" in insert_sql
        assert conn.fetchval.call_args_list[1].args[1] == RUN_ID

    @pytest.mark.asyncio
    async def test_already_ingested_run_skips_artifact_download(self):
        pool, _ = _make_pool(fetchval_results=[1])  # pre-check hits
        fake_httpx, client = _fake_httpx([_resp(200, RUNS_PAYLOAD)])
        with _patch_token(), patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await IngestBenchmarkResultsJob().run(
                pool, {"_site_config": _sc()}
            )
        assert result.ok is True
        assert result.changes_made == 0
        assert "no new benchmark runs" in result.detail
        # Only the runs-list call — no artifact list/download for known runs.
        assert client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_run_without_artifact_is_benign(self):
        """if-no-files-found:warn runs upload nothing — skip, don't fail."""
        pool, _ = _make_pool()
        fake_httpx, _ = _fake_httpx(
            [
                _resp(200, RUNS_PAYLOAD),
                _resp(200, {"artifacts": []}),
            ]
        )
        with _patch_token(), patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await IngestBenchmarkResultsJob().run(
                pool, {"_site_config": _sc()}
            )
        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_auth_error_fails_loud_with_finding(self):
        pool, _ = _make_pool()
        fake_httpx, _ = _fake_httpx([_resp(403, {})])
        with _patch_token(), patch.dict(
            "sys.modules", {"httpx": fake_httpx}
        ), patch(
            "services.jobs.ingest_benchmark_results.emit_finding"
        ) as finding:
            result = await IngestBenchmarkResultsJob().run(
                pool, {"_site_config": _sc()}
            )
        assert result.ok is False
        assert "403" in result.detail
        assert finding.call_args.kwargs["kind"] == "benchmark_ingest_degraded"
