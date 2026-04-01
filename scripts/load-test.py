"""
Load-test script for Glad Labs public endpoints before HN launch.

Targets:
  - Railway API  (cofounder-production.up.railway.app)
  - Vercel frontend (www.gladlabs.io)

Usage:
  python scripts/load-test.py              # default: 50 concurrent, 30s per endpoint
  python scripts/load-test.py --concurrency 10 --duration 10   # lighter run

Requires: httpx (already in project deps)
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = "https://cofounder-production.up.railway.app"
FRONTEND_BASE = "https://www.gladlabs.io"

DEFAULT_CONCURRENCY = 50
DEFAULT_DURATION = 30  # seconds per endpoint


# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------

@dataclass
class EndpointResult:
    name: str
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    total: int = 0
    status_codes: dict[int, int] = field(default_factory=dict)

    def record(self, latency_ms: float, status: int) -> None:
        self.total += 1
        self.status_codes[status] = self.status_codes.get(status, 0) + 1
        if 200 <= status < 400:
            self.latencies_ms.append(latency_ms)
        else:
            self.errors += 1

    def record_error(self) -> None:
        self.total += 1
        self.errors += 1

    # --- aggregation helpers ------------------------------------------------

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        k = (p / 100) * (len(sorted_lat) - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_lat[int(k)]
        return sorted_lat[f] * (c - k) + sorted_lat[c] * (k - f)

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    @property
    def error_rate(self) -> float:
        return (self.errors / self.total * 100) if self.total else 0.0

    @property
    def throughput(self) -> float:
        """Requests per second (approximate, based on duration constant)."""
        return self.total  # divided by duration in the report


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

async def _hammer(
    client: httpx.AsyncClient,
    result: EndpointResult,
    make_request: Callable[[httpx.AsyncClient], Coroutine[Any, Any, httpx.Response]],
    duration: float,
) -> None:
    """Send requests in a tight loop for *duration* seconds."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        try:
            resp = await make_request(client)
            latency = (time.monotonic() - t0) * 1000
            result.record(latency, resp.status_code)
        except (httpx.HTTPError, Exception):
            result.record_error()
        # Tiny yield so the event loop stays responsive
        await asyncio.sleep(0)


async def run_endpoint_test(
    name: str,
    make_request: Callable[[httpx.AsyncClient], Coroutine[Any, Any, httpx.Response]],
    concurrency: int,
    duration: int,
) -> EndpointResult:
    result = EndpointResult(name=name)
    limits = httpx.Limits(
        max_connections=concurrency + 10,
        max_keepalive_connections=concurrency,
    )
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=10.0),
        limits=limits,
        follow_redirects=True,
        http2=True,
    ) as client:
        tasks = [
            asyncio.create_task(_hammer(client, result, make_request, duration))
            for _ in range(concurrency)
        ]
        await asyncio.gather(*tasks)
    return result


# ---------------------------------------------------------------------------
# Endpoint definitions
# ---------------------------------------------------------------------------

def api_get(path: str):
    """Return a factory that performs a GET against the Railway API."""
    url = f"{API_BASE}{path}"
    async def _req(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(url)
    return _req


def frontend_get(path: str):
    url = f"{FRONTEND_BASE}{path}"
    async def _req(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(url)
    return _req


def api_post_json(path: str, body: dict):
    url = f"{API_BASE}{path}"
    async def _req(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(url, json=body)
    return _req


async def discover_slug() -> str:
    """Fetch the posts listing and return the slug of the first post."""
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
        resp = await c.get(f"{API_BASE}/api/posts?limit=5")
        resp.raise_for_status()
        data = resp.json()
        # handle both {"posts": [...]} and top-level list
        posts = data if isinstance(data, list) else data.get("posts", data.get("data", []))
        if posts:
            return posts[0].get("slug", posts[0].get("id", "unknown"))
    return "unknown"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_table(results: list[EndpointResult], duration: int) -> None:
    header = f"{'Endpoint':<42} {'Reqs':>6} {'Err%':>6} {'p50ms':>7} {'p95ms':>7} {'p99ms':>7} {'RPS':>7}"
    sep = "-" * len(header)
    print("\n" + sep)
    print(header)
    print(sep)
    for r in results:
        rps = r.total / duration if duration else 0
        print(
            f"{r.name:<42} {r.total:>6} {r.error_rate:>5.1f}% "
            f"{r.p50:>7.0f} {r.p95:>7.0f} {r.p99:>7.0f} {rps:>7.1f}"
        )
    print(sep)

    # Status code breakdown
    print("\nStatus code breakdown:")
    for r in results:
        codes = ", ".join(f"{code}: {cnt}" for code, cnt in sorted(r.status_codes.items()))
        print(f"  {r.name:<42} {codes}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(concurrency: int, duration: int) -> None:
    print(f"Glad Labs load test  |  concurrency={concurrency}  duration={duration}s/endpoint")
    print(f"API:      {API_BASE}")
    print(f"Frontend: {FRONTEND_BASE}")
    print()

    # Discover a real slug first
    print("[*] Discovering a real post slug ...")
    slug = await discover_slug()
    print(f"    Using slug: {slug}")
    print()

    endpoints: list[tuple[str, Callable]] = [
        ("API  GET /api/posts?limit=20",        api_get("/api/posts?limit=20")),
        (f"API  GET /api/posts/{slug}",          api_get(f"/api/posts/{slug}")),
        ("API  GET /api/categories",             api_get("/api/categories")),
        ("API  POST /api/track/view",            api_post_json("/api/track/view", {
            "path": f"/posts/{slug}",
            "slug": slug,
            "referrer": "https://news.ycombinator.com/",
        })),
        ("FE   GET / (homepage)",                frontend_get("/")),
        (f"FE   GET /posts/{slug}",              frontend_get(f"/posts/{slug}")),
    ]

    results: list[EndpointResult] = []
    for name, factory in endpoints:
        print(f"[*] Testing: {name}  ({duration}s) ...")
        r = await run_endpoint_test(name, factory, concurrency, duration)
        results.append(r)
        # Brief summary per endpoint so you can watch progress
        rps = r.total / duration if duration else 0
        print(f"    -> {r.total} reqs, {r.error_rate:.1f}% errors, "
              f"p50={r.p50:.0f}ms p95={r.p95:.0f}ms, {rps:.1f} rps")
        print()

    print_table(results, duration)

    # Exit code: fail if any endpoint has >5% error rate
    if any(r.error_rate > 5 for r in results):
        print("WARN: One or more endpoints exceeded 5% error rate.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Glad Labs HN launch load test")
    parser.add_argument("--concurrency", "-c", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"Number of concurrent workers per endpoint (default {DEFAULT_CONCURRENCY})")
    parser.add_argument("--duration", "-d", type=int, default=DEFAULT_DURATION,
                        help=f"Seconds to hammer each endpoint (default {DEFAULT_DURATION})")
    args = parser.parse_args()

    asyncio.run(main(args.concurrency, args.duration))
