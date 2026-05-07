#!/usr/bin/env python3
"""CI lint: validate every Grafana panel query against its datasource.

PR Glad-Labs/poindexter#308 deleted four panels whose SQL referenced
renamed/dropped columns or tables (``prompt_templates``, ``pipeline_stages``,
``newsletter_subscribers.status``, ``internal_topic_candidates.title``).
Each had been silently broken since the schema change — only visible if
an operator opened the dashboard. This script is the CI fix that catches
that class of bug at PR time.

WHAT IT DOES

For every JSON dashboard under ``infrastructure/grafana/dashboards/``:

  1. Walk panels (recursing into ``panels[].panels[]`` rows) — see
     ``lib_grafana_panels.walk_panels``.
  2. For each ``target`` extract ``rawSql`` (postgres) / ``expr`` (prometheus
     or loki) based on the target's or panel's ``datasource.type``.
  3. Substitute Grafana macros (``$__timeFilter``, ``$__interval`` etc.)
     using the table in ``lib_grafana_panels.MACRO_DOCS``.
  4. Validate the query:
       * postgres   — ``EXPLAIN <sql>`` against ``DATABASE_URL``. Identifier
         resolution happens during planning, so the dropped-column case
         surfaces here without us actually running the query.
       * prometheus — hit ``${PROMETHEUS_URL}/api/v1/query?query=<expr>``;
         a ``parse error`` 4xx / ``status: error`` is a failure.
       * loki       — same pattern at ``${LOKI_URL}/loki/api/v1/query``.
         If ``LOKI_URL`` is unset, loki targets are skipped with a warning.
  5. Aggregate, print a per-panel table, exit non-zero on any failures.

CAUGHT POSTGRES ERROR CODES

  * ``42703`` undefined_column   (renamed/dropped column)  → FAIL
  * ``42P01`` undefined_table    (renamed/dropped table)   → FAIL
  * ``22P02`` invalid_text_repr  (macro arg type mismatch) → WARN
  * other PromQL ``parse error``                           → FAIL
  * everything else                                        → WARN

WHEN THIS LINT CATCHES SOMETHING

When this lint catches something on a PR going forward, the next PR
should fix the panel — NOT silence the lint. Dropping a panel is a
last-resort cleanup, not the default response.

LOCAL USAGE

Against the running stack on Matt's box:

    DATABASE_URL=postgresql://postgres:postgres@localhost:15432/poindexter \\
        PROMETHEUS_URL=http://localhost:9090 \\
        LOKI_URL=http://localhost:3100 \\
        python scripts/ci/grafana_panels_lint.py infrastructure/grafana/dashboards/

If a directory is omitted the script defaults to that path.

SEE ALSO

  * scripts/ci/lib_grafana_panels.py — pure helpers (unit-tested).
  * scripts/ci/migrations_smoke.py   — sister CI script for migrations.
  * Glad-Labs/poindexter#308         — motivating PR (4 caught issues).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx

# Make ``lib_grafana_panels`` importable when the script is invoked from
# the repo root (mirrors how ``migrations_smoke.py`` reaches into ``services``).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_grafana_panels import (  # noqa: E402
    classify,
    classify_pg_error,
    extract_query,
    iter_root_panels,
    substitute,
    walk_panels,
)


@dataclass
class Finding:
    dashboard: str
    panel_id: Any
    panel_title: str
    refid: str
    kind: str  # "postgres" | "prometheus" | "loki"
    query: str
    severity: str  # "FAIL" | "WARN" | "OK" | "SKIP"
    message: str


# ---------------------------------------------------------------------------
# Validators (live datasource calls)
# ---------------------------------------------------------------------------


async def _check_postgres(pool: asyncpg.Pool, sql: str) -> tuple[str, str]:
    try:
        substituted = substitute(sql)
    except ValueError as exc:
        return "FAIL", str(exc)
    async with pool.acquire() as conn:
        try:
            await conn.execute(f"EXPLAIN {substituted}")
        except asyncpg.PostgresError as exc:
            code = getattr(exc, "sqlstate", "") or ""
            return classify_pg_error(code), f"{code} {type(exc).__name__}: {exc}"
        return "OK", ""


async def _check_prom_like(
    client: httpx.AsyncClient,
    base: str,
    expr: str,
    *,
    path: str,
    extra_params: dict[str, str] | None = None,
) -> tuple[str, str]:
    try:
        substituted = substitute(expr)
    except ValueError as exc:
        return "FAIL", str(exc)
    url = f"{base.rstrip('/')}{path}"
    params = {"query": substituted}
    if extra_params:
        params.update(extra_params)
    try:
        resp = await client.get(url, params=params, timeout=10.0)
    except httpx.HTTPError as exc:
        return "WARN", f"network: {exc}"
    if resp.status_code == 400:
        # 400 from prometheus/loki is the parse-error class — exactly
        # what we want to fail the build on.
        body_text = resp.text[:200]
        # Loki's "log queries are not supported as an instant query type"
        # is a query-type mismatch, not a syntax failure. The dashboard
        # itself sets queryType=range; downgrade to WARN.
        if "instant query" in body_text.lower():
            return "WARN", f"loki query-type mismatch (set queryType=range): {body_text}"
        return "FAIL", f"parse: {body_text}"
    if resp.status_code >= 500:
        return "WARN", f"{resp.status_code}: {resp.text[:200]}"
    if resp.headers.get("content-type", "").startswith("application/json"):
        body = resp.json()
        if body.get("status") == "error":
            etype = body.get("errorType", "")
            msg = body.get("error", "")
            sev = "FAIL" if etype in {"bad_data", "parse"} or "parse" in msg.lower() else "WARN"
            return sev, f"{etype}: {msg}"
    return "OK", ""


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def lint(dashboards_dir: Path) -> int:
    files = sorted(dashboards_dir.glob("*.json"))
    if not files:
        print(f"ERROR: no dashboards under {dashboards_dir}", file=sys.stderr)
        return 2

    database_url = os.environ.get("DATABASE_URL")
    prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
    loki_url = os.environ.get("LOKI_URL")  # optional in CI

    pool: asyncpg.Pool | None = None
    if database_url:
        try:
            pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=4)
        except (OSError, asyncpg.PostgresError) as exc:
            print(f"WARN: could not connect to DATABASE_URL ({exc}) — postgres skipped", file=sys.stderr)
    else:
        print("WARN: DATABASE_URL unset — postgres targets will be skipped", file=sys.stderr)

    findings: list[Finding] = []
    async with httpx.AsyncClient() as client:
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            for panel in iter_root_panels(data):
                for renderable in walk_panels(panel):
                    for target in renderable.get("targets", []) or []:
                        kind = classify(target, renderable)
                        query = extract_query(target, kind) if kind else None
                        if not kind or not query:
                            continue
                        sev, msg = await _validate(
                            kind, query, pool, client, prometheus_url, loki_url
                        )
                        findings.append(
                            Finding(
                                path.name,
                                renderable.get("id", ""),
                                renderable.get("title", ""),
                                target.get("refId", "?"),
                                kind,
                                query,
                                sev,
                                msg,
                            )
                        )

    if pool is not None:
        await pool.close()

    return _report(findings)


async def _validate(
    kind: str,
    query: str,
    pool: asyncpg.Pool | None,
    client: httpx.AsyncClient,
    prometheus_url: str,
    loki_url: str | None,
) -> tuple[str, str]:
    if kind == "postgres":
        if pool is None:
            return "SKIP", "no DATABASE_URL"
        return await _check_postgres(pool, query)
    if kind == "prometheus":
        return await _check_prom_like(client, prometheus_url, query, path="/api/v1/query")
    if kind == "loki":
        if not loki_url:
            return "SKIP", "no LOKI_URL"
        # Use query_range so log-stream queries (panels with
        # queryType=range, e.g. {container=~"x"}) parse correctly. The
        # instant /query endpoint rejects them as "not supported".
        # Use a 60s window with a 60s step to keep the response small —
        # we only care about parse-level correctness.
        end = int(time.time())
        start = end - 60
        return await _check_prom_like(
            client,
            loki_url,
            query,
            path="/loki/api/v1/query_range",
            extra_params={
                "start": f"{start}",
                "end": f"{end}",
                "step": "60s",
                "limit": "1",
            },
        )
    return "SKIP", f"unknown kind {kind}"


def _report(findings: list[Finding]) -> int:
    fails = [f for f in findings if f.severity == "FAIL"]
    warns = [f for f in findings if f.severity == "WARN"]
    skips = [f for f in findings if f.severity == "SKIP"]
    oks = [f for f in findings if f.severity == "OK"]

    print(
        f"[grafana-lint] checked {len(findings)} target(s) — "
        f"{len(oks)} ok / {len(warns)} warn / {len(skips)} skip / {len(fails)} fail"
    )
    for f in warns + fails:
        print(
            f"  [{f.severity}] {f.dashboard} panel={f.panel_id} "
            f"({f.panel_title!r}) target={f.refid} {f.kind}: {f.message}"
        )
        snippet = f.query.replace("\n", " ")[:200]
        print(f"        query: {snippet}")

    if fails:
        print(
            f"FAIL: {len(fails)} panel target(s) reference broken schema/queries",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    args = sys.argv[1:]
    target = (
        Path(args[0])
        if args
        else Path(__file__).resolve().parents[2] / "infrastructure" / "grafana" / "dashboards"
    )
    if not target.is_dir():
        print(f"ERROR: not a directory: {target}", file=sys.stderr)
        return 2
    return asyncio.run(lint(target))


if __name__ == "__main__":
    sys.exit(main())
