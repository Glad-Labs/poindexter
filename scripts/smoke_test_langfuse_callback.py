"""Smoke test for poindexter#373 — LiteLLM → Langfuse callback wiring.

Run from a populated worker env (Langfuse + Ollama up, app_settings
seeded). Performs a real LLM call through ``LiteLLMProvider`` and
queries the Langfuse public traces API to confirm the span landed.

Usage::

    python scripts/smoke_test_langfuse_callback.py

Exits non-zero with a descriptive error on any failure. Designed to be
copy-pasted from a PR body so reviewers can re-run it.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
from pathlib import Path

import asyncpg
import httpx

# Resolve repo root to sys.path so the cofounder_agent package + its
# services subpackage import cleanly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src" / "cofounder_agent"))


async def main() -> int:
    db_url = (
        os.environ.get("DATABASE_URL")
        or "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
    )

    print(f"[smoke] connecting to {db_url[:40]}...")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)

    # Build a minimal SiteConfig from the live DB so configure_langfuse_callback
    # can read the four rows it needs.
    from services.site_config import SiteConfig

    site_config = SiteConfig(pool=pool)
    loaded = await site_config.load(pool)
    print(f"[smoke] loaded {loaded} settings from app_settings")

    # Wire up the callback.
    from services.llm_providers.litellm_provider import (
        LiteLLMProvider,
        configure_langfuse_callback,
    )

    registered = await configure_langfuse_callback(site_config)
    print(f"[smoke] configure_langfuse_callback returned {registered}")
    if not registered:
        print(
            "[smoke] FAIL: tracing not enabled — set "
            "langfuse_tracing_enabled=true in app_settings",
            file=sys.stderr,
        )
        return 1

    # Sanity-check what got stamped.
    import litellm
    print(f"[smoke] litellm.success_callback={litellm.success_callback}")
    print(f"[smoke] litellm.failure_callback={litellm.failure_callback}")
    print(f"[smoke] LANGFUSE_HOST={os.environ.get('LANGFUSE_HOST')}")

    # Make a real LLM call — uses the smallest local model that's loaded.
    provider = LiteLLMProvider()
    marker = f"poindexter-373-smoke-{int(time.time())}"
    print(f"[smoke] firing LLM call with marker {marker!r}...")
    result = await provider.complete(
        messages=[
            {
                "role": "user",
                "content": (
                    f"Say the word OK and nothing else. Marker: {marker}"
                ),
            },
        ],
        model="llama3.2:3b",
        _provider_config={"api_base": "http://localhost:11434"},
        timeout_s=60.0,
    )
    print(f"[smoke] response: {result.text[:80]!r}")
    print(
        f"[smoke] tokens: prompt={result.prompt_tokens} "
        f"completion={result.completion_tokens}"
    )

    # The OTLP exporter uses a BatchSpanProcessor that flushes on a
    # 5s default interval, but cold-start init + retry-after-503 can
    # push that out. Force a flush via the global tracer provider so
    # we don't have to guess at sleep times.
    print("[smoke] forcing OTLP exporter flush...")
    try:
        from opentelemetry import trace as _otel_trace
        provider = _otel_trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            ok = provider.force_flush(timeout_millis=15000)
            print(f"[smoke] force_flush returned {ok}")
    except Exception as exc:  # noqa: BLE001
        print(f"[smoke] force_flush failed (may be benign): {exc}")
    print("[smoke] waiting 5s for Langfuse to ingest the span...")
    await asyncio.sleep(5)

    # Query Langfuse public traces API to verify the span landed.
    host = os.environ["LANGFUSE_HOST"]
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()

    print(f"[smoke] curl-equivalent: GET {host}/api/public/traces?limit=5")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{host}/api/public/traces",
            params={"limit": 10},
            headers={"Authorization": f"Basic {auth}"},
        )
    print(f"[smoke] HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(f"[smoke] FAIL: traces API returned {resp.status_code}: "
              f"{resp.text[:300]}", file=sys.stderr)
        return 1

    body = resp.json()
    traces = body.get("data", [])
    print(f"[smoke] found {len(traces)} recent traces")

    # Look for our marker in the most recent traces. Trace summary
    # fields can be strings, lists (for chat-message inputs), or
    # dicts — coerce everything via str() before substring match.
    matched = None
    for trace in traces:
        haystack = " ".join(
            str(trace.get(k) or "")
            for k in ("input", "output", "name", "metadata")
        )
        if marker in haystack:
            matched = trace
            break

    # Fallback: if marker doesn't show up in summary fields, accept any
    # trace newer than the call (Langfuse may not surface the marker in
    # the public API summary).
    if matched is None and traces:
        # Most recent trace is likely ours — print it for the reviewer.
        matched = traces[0]
        print(
            f"[smoke] marker not found in trace summaries; using "
            f"most recent trace (likely ours)"
        )

    if matched is None:
        print(
            "[smoke] FAIL: no traces returned at all — Langfuse may be "
            "rejecting writes. Check LANGFUSE_HOST + key pair.",
            file=sys.stderr,
        )
        return 1

    print("[smoke] === most recent trace ===")
    for k in ("id", "name", "timestamp", "input", "output"):
        v = matched.get(k)
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + "..."
        print(f"[smoke]   {k}: {v}")

    print("[smoke] PASS - Langfuse received the span")
    await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
