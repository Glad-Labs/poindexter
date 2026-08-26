"""IngestBenchmarkResultsJob — pull CI benchmark artifacts into benchmark_results.

The nightly ``benchmarks`` GitHub Actions workflow (endpoint latency via
pytest-benchmark against the in-process TestClient) uploads
``benchmark_results.json`` as a run artifact. It runs on a HOSTED runner
with no Tailnet access, so it cannot write results here itself — this job
is the pull side: list recent successful runs via the GitHub API, download
each new run's artifact zip, parse the pytest-benchmark JSON, and land one
row per benchmark per run in ``benchmark_results``. The Observability
board's "CI Endpoint Benchmarks" panel charts the table.

Idempotency is DB-shaped: ``UNIQUE (workflow_run_id, benchmark_name)`` +
``ON CONFLICT DO NOTHING``, plus a cheap already-ingested pre-check per run
so re-polls skip the artifact download entirely.

Config (``plugin.job.ingest_benchmark_results``):
- ``enabled`` (default ``true`` — auto-persisted by PluginScheduler)
- ``config.max_runs_per_cycle`` (default 5) — new runs ingested per fire
- ``config.workflow_file`` (default ``benchmarks.yml``)
- ``config.transient_retries`` (default 3)

Settings (``app_settings``):
- ``benchmark_ingest_repo`` — non-secret, ``owner/repo`` whose Actions runs
  hold the artifacts (the operator sets ``Glad-Labs/glad-labs-stack``).
  Empty = feature unconfigured; the job skips quietly (fresh-install
  posture, same as sync_cloudflare_analytics with no account id).
- ``gh_token`` — existing secret, reused (dev_diary_source reads the same
  key). Required once the repo is set: artifact downloads on a private
  repo 404 unauthenticated, so repo-set + token-empty is a HALF-CONFIGURED
  state and fails loud with a finding.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
_ARTIFACT_MEMBER = "benchmark_results.json"


def _parse_iso(value: str) -> datetime:
    """Parse ISO-8601, tolerating the trailing Z GitHub emits."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def parse_benchmark_artifact(
    payload: dict[str, Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract (commit_sha, rows) from a pytest-benchmark JSON payload.

    Each row carries the columns benchmark_results stores. Benchmarks with
    a malformed/missing ``stats.mean`` are skipped (counted by the caller
    via the returned list length vs ``payload['benchmarks']``).
    """
    commit_sha = (payload.get("commit_info") or {}).get("id")
    rows: list[dict[str, Any]] = []
    for bench in payload.get("benchmarks") or []:
        stats = bench.get("stats") or {}
        name = bench.get("name")
        mean = stats.get("mean")
        if not name or not isinstance(mean, (int, float)):
            continue
        rows.append(
            {
                "benchmark_name": name,
                "group_name": bench.get("group"),
                "mean_seconds": float(mean),
                "min_seconds": stats.get("min"),
                "max_seconds": stats.get("max"),
                "stddev_seconds": stats.get("stddev"),
                "median_seconds": stats.get("median"),
                "rounds": stats.get("rounds"),
            }
        )
    return commit_sha, rows


class IngestBenchmarkResultsJob:
    name = "ingest_benchmark_results"
    description = (
        "Pull benchmark_results.json artifacts from the nightly benchmarks "
        "GitHub Actions workflow and land per-benchmark latency rows in "
        "benchmark_results for the Observability board."
    )
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # DI seam (glad-labs-stack#330)
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(
                ok=True,
                detail="no _site_config in job config — skipping",
                changes_made=0,
            )

        repo = (sc.get("benchmark_ingest_repo", "") or "").strip()
        if not repo:
            return JobResult(
                ok=True,
                detail="benchmark_ingest_repo unset — skipping",
                changes_made=0,
            )

        token = await self._fetch_gh_token(pool)
        if not token:
            # Reached only AFTER benchmark_ingest_repo is confirmed set, so
            # this is a half-configured state, not a fresh install: artifact
            # downloads on a private repo fail unauthenticated, and a quiet
            # skip here would mask a dead ingest as green.
            emit_finding(
                source="ingest_benchmark_results",
                kind="benchmark_ingest_degraded",
                severity="warn",
                title="benchmark ingest degraded — gh_token unset/unreadable",
                body=(
                    f"`benchmark_ingest_repo` is set ({repo}) but the "
                    "`gh_token` secret is empty or unreadable, so benchmark "
                    "artifacts cannot be downloaded and the CI Endpoint "
                    "Benchmarks panel is not receiving data. Set it with: "
                    "`poindexter settings set gh_token <token> --secret` "
                    "(needs repo read + actions read on the ingest repo)."
                ),
                dedup_key="benchmark_ingest:token_unset",
            )
            return JobResult(
                ok=False,
                detail="gh_token unset while benchmark_ingest_repo is set — ingest degraded",
                changes_made=0,
            )

        workflow_file = str(config.get("workflow_file", "benchmarks.yml"))
        max_runs = int(config.get("max_runs_per_cycle", 5))
        transient_retries = int(config.get("transient_retries", 3))

        try:
            import httpx
        except ImportError:
            return JobResult(ok=False, detail="httpx not available", changes_made=0)

        from services.net_transient import (
            is_transient_network_error,
            transient_retry_transport,
        )

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        runs_url = (
            f"{_API_ROOT}/repos/{repo}/actions/workflows/{workflow_file}/runs"
            "?status=success&per_page=20"
        )

        try:
            async with httpx.AsyncClient(
                timeout=60.0,
                # Artifact downloads 302-redirect to blob storage.
                follow_redirects=True,
                transport=transient_retry_transport(transient_retries),
            ) as client:
                resp = await client.get(runs_url, headers=headers)
                if resp.status_code != 200:
                    return self._api_error_result(repo, "runs list", resp)
                runs = (resp.json() or {}).get("workflow_runs") or []

                runs_ingested = 0
                rows_inserted = 0
                for run in runs:
                    if runs_ingested >= max_runs:
                        break
                    run_id = run.get("id")
                    if not run_id:
                        continue
                    async with pool.acquire() as conn:
                        already = await conn.fetchval(
                            "SELECT 1 FROM benchmark_results "
                            "WHERE workflow_run_id = $1 LIMIT 1",
                            run_id,
                        )
                    if already:
                        continue

                    payload = await self._fetch_artifact_payload(
                        client, headers, repo, run_id
                    )
                    if payload is None:
                        # No benchmark artifact on this run (e.g. the suite
                        # skipped and if-no-files-found:warn uploaded nothing).
                        # Benign — later runs still get checked this cycle.
                        continue

                    commit_sha, bench_rows = parse_benchmark_artifact(payload)
                    if not bench_rows:
                        logger.warning(
                            "[BENCH_INGEST] run %s artifact parsed to 0 "
                            "benchmarks — format drift?",
                            run_id,
                        )
                        continue

                    started_raw = (
                        run.get("run_started_at") or run.get("created_at") or ""
                    )
                    try:
                        run_started_at = _parse_iso(started_raw)
                    except Exception:
                        logger.warning(
                            "[BENCH_INGEST] run %s has unparseable "
                            "run_started_at %r — skipping",
                            run_id,
                            started_raw,
                        )
                        continue

                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            for row in bench_rows:
                                inserted = await conn.fetchval(
                                    """
                                    INSERT INTO benchmark_results
                                        (workflow_run_id, run_started_at,
                                         commit_sha, benchmark_name,
                                         group_name, mean_seconds,
                                         min_seconds, max_seconds,
                                         stddev_seconds, median_seconds,
                                         rounds)
                                    VALUES ($1, $2, $3, $4, $5, $6,
                                            $7, $8, $9, $10, $11)
                                    ON CONFLICT (workflow_run_id, benchmark_name)
                                        DO NOTHING
                                    RETURNING 1
                                    """,
                                    run_id,
                                    run_started_at,
                                    commit_sha,
                                    row["benchmark_name"],
                                    row["group_name"],
                                    row["mean_seconds"],
                                    row["min_seconds"],
                                    row["max_seconds"],
                                    row["stddev_seconds"],
                                    row["median_seconds"],
                                    row["rounds"],
                                )
                                if inserted:
                                    rows_inserted += 1
                    runs_ingested += 1

        except Exception as e:
            if is_transient_network_error(e):
                logger.warning(
                    "[BENCH_INGEST] transient network failure after %d "
                    "connect retries — deferring to next cycle: %s",
                    transient_retries,
                    describe_exception(e),
                )
                emit_finding(
                    source="ingest_benchmark_results",
                    kind="network_unreachable",
                    severity="warning",
                    title="GitHub API unreachable (transient network fault)",
                    body=(
                        f"Connect still failing after {transient_retries} "
                        f"retries: {e}. Deferred to the next cycle — ingest "
                        "is DB-idempotent, so nothing is skipped."
                    ),
                    dedup_key="network_unreachable:github",
                )
                return JobResult(
                    ok=True,
                    detail=f"deferred: transient network failure ({describe_exception(e)})",
                    changes_made=0,
                )
            logger.exception("[BENCH_INGEST] ingest pass failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=describe_exception(e), changes_made=0)

        return JobResult(
            ok=True,
            detail=(
                f"ingested {runs_ingested} run(s), {rows_inserted} benchmark row(s)"
                if runs_ingested
                else "no new benchmark runs"
            ),
            changes_made=rows_inserted,
            metrics={
                "runs_ingested": runs_ingested,
                "rows_inserted": rows_inserted,
            },
        )

    async def _fetch_gh_token(self, pool: Any) -> str:
        """Read the existing ``gh_token`` secret (same key dev_diary uses)."""
        if pool is None:
            return ""
        try:
            from plugins.secrets import get_secret

            async with pool.acquire() as conn:
                value = await get_secret(conn, "gh_token")
            return (value or "").strip()
        except Exception as exc:
            logger.warning("[BENCH_INGEST] gh_token read failed: %s", describe_exception(exc))
            return ""

    async def _fetch_artifact_payload(
        self,
        client: Any,
        headers: dict[str, str],
        repo: str,
        run_id: int,
    ) -> dict[str, Any] | None:
        """Download + unzip a run's benchmark artifact. None = no artifact."""
        resp = await client.get(
            f"{_API_ROOT}/repos/{repo}/actions/runs/{run_id}/artifacts",
            headers=headers,
        )
        if resp.status_code != 200:
            logger.warning(
                "[BENCH_INGEST] artifact list for run %s returned %s",
                run_id,
                resp.status_code,
            )
            return None
        artifacts = (resp.json() or {}).get("artifacts") or []
        match = next(
            (
                a
                for a in artifacts
                if (a.get("name") or "").startswith("benchmark-results")
                and not a.get("expired")
            ),
            None,
        )
        if match is None:
            return None

        dl = await client.get(
            f"{_API_ROOT}/repos/{repo}/actions/artifacts/{match['id']}/zip",
            headers=headers,
        )
        if dl.status_code != 200:
            logger.warning(
                "[BENCH_INGEST] artifact download for run %s returned %s",
                run_id,
                dl.status_code,
            )
            return None
        try:
            archive = zipfile.ZipFile(io.BytesIO(dl.content))
            member = next(
                (n for n in archive.namelist() if n.endswith(_ARTIFACT_MEMBER)),
                None,
            )
            if member is None:
                logger.warning(
                    "[BENCH_INGEST] run %s artifact zip has no %s (members: %s)",
                    run_id,
                    _ARTIFACT_MEMBER,
                    archive.namelist()[:5],
                )
                return None
            return json.loads(archive.read(member))
        except Exception as e:
            logger.warning(
                "[BENCH_INGEST] run %s artifact unzip/parse failed: %s",
                run_id,
                describe_exception(e),
            )
            return None

    def _api_error_result(self, repo: str, what: str, resp: Any) -> JobResult:
        """Non-200 from the GitHub API — fail loud, with a finding on auth errors."""
        if resp.status_code in (401, 403, 404):
            emit_finding(
                source="ingest_benchmark_results",
                kind="benchmark_ingest_degraded",
                severity="warn",
                title=f"benchmark ingest degraded — GitHub API {resp.status_code}",
                body=(
                    f"GET {what} for {repo} returned {resp.status_code}. "
                    "The gh_token is likely expired or missing the "
                    "actions:read / repo scope for this repo, so benchmark "
                    "artifacts cannot be ingested and the CI Endpoint "
                    "Benchmarks panel is not receiving data."
                ),
                dedup_key=f"benchmark_ingest:api_{resp.status_code}",
            )
        logger.warning(
            "[BENCH_INGEST] %s returned %s: %s",
            what,
            resp.status_code,
            (resp.text or "")[:300],
        )
        return JobResult(
            ok=False,
            detail=f"github api {what} returned {resp.status_code}",
            changes_made=0,
        )
