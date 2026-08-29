#!/usr/bin/env python3
"""Run the end-to-end retrieval eval against the live corpus.

What it answers
---------------
"Given the whole production corpus, does retrieval surface the chunk that
actually contains the answer — and does the consumer receive that text?"

Neither existing evaluator answers this. ``ragas evaluate`` scores generated
content given whatever retrieval returned; the model_eval reranker scorer ranks
a fixed candidate list. Both are blind to a document the retriever never found.

Headline metric
---------------
``deep_head_recall_gap`` — recall@5 on questions whose answer sits in the first
500 chars, minus recall@5 on questions whose answer sits past it. Near zero
means deep content is as reachable as the opening; a positive gap means
something on the path still favours the head.

Usage
-----
Run it in the worker container — the DSN, the ``services.*`` tree and a
reachable Ollama all live there. ``/app`` is ``src/cofounder_agent``, NOT the
repo root, so copy the file in (same constraint as the #1033 backfill):

    docker cp scripts/eval_retrieval.py poindexter-worker:/tmp/eval_retrieval.py
    docker exec poindexter-worker python /tmp/eval_retrieval.py --build --run
    docker exec poindexter-worker python /tmp/eval_retrieval.py --run --compare
    docker exec poindexter-worker python /tmp/eval_retrieval.py --run --persist

``--build`` regenerates the golden set (one small LLM call per case) and caches
it to ``--cache``. ``--run`` scores it. ``--compare`` additionally scores
vector-only and hybrid-without-rerank against the identical cases, which is how
you attribute a change to a stage rather than guess.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cofounder_agent"))

# Derived from TMPDIR rather than a hardcoded /tmp literal: a predictable
# path in a world-writable directory is a symlink-swap surface on a shared
# box, and honouring the environment is the right default anyway.
_CACHE = os.path.join(tempfile.gettempdir(), "retrieval_golden_set.json")


def _fmt(label: str, s: dict) -> str:
    if not s or not s.get("n"):
        return f"  {label:<10} (no cases)"
    return (
        f"  {label:<10} n={s['n']:<4} "
        f"R@1={s['recall@1']:.3f} R@5={s['recall@5']:.3f} R@10={s['recall@10']:.3f} "
        f"MRR={s['mrr']:.3f}  payload={s['payload_contains_span']:.3f} "
        f"legacy={s['legacy_payload_contains_span']:.3f}"
    )


def _report(result) -> None:
    d = result.detail
    print(f"\n=== variant: {d['variant']}  (hybrid={d['hybrid']} rerank={d['rerank']}) ===")
    print(f"golden set {d['golden_name']} v{d['golden_version']}  "
          f"cases={result.n_cases}  errors={d['errors']}  {result.latency_ms/1000:.1f}s")
    print(_fmt("OVERALL", d["overall"]))
    print("  -- by region --")
    for region in ("head", "deep"):
        if region in d["by_region"]:
            print(_fmt(region, d["by_region"][region]))
    if "deep_head_recall_gap" in d:
        gap = d["deep_head_recall_gap"]
        verdict = "deep as reachable as head" if abs(gap) < 0.05 else (
            "DEEP CONTENT STILL HARDER TO REACH" if gap > 0 else "deep easier than head")
        print(f"  -> deep_head_recall_gap = {gap:+.3f}   ({verdict})")
    print("  -- by source_table --")
    for src, s in sorted(d["by_source_table"].items()):
        print(_fmt(src, s))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", action="store_true", help="regenerate the golden set (LLM calls)")
    ap.add_argument("--run", action="store_true", help="score the golden set")
    ap.add_argument("--compare", action="store_true", help="also score vector-only and hybrid-no-rerank")
    ap.add_argument("--persist", action="store_true", help="write results to audit_log")
    ap.add_argument("--cache", default=_CACHE)
    ap.add_argument("--graph", action="store_true",
                    help="also score prod + knowledge-graph expansion")
    ap.add_argument(
        "--ollama-url",
        default="",
        help="override local_llm_api_url for this run. Required when running "
             "from the HOST: the DB value is host.docker.internal, which only "
             "resolves inside a container. Use http://localhost:11434 on host.",
    )
    args = ap.parse_args()
    if not (args.build or args.run):
        ap.error("nothing to do — pass --build and/or --run")

    import asyncpg
    from services.model_eval.golden_sets.retrieval import build_retrieval_golden_set
    from services.model_eval.types import GoldenCase, GoldenSet
    from services.retrieval_eval import persist_result, score_retrieval
    from services.site_config import SiteConfig

    dsn = os.getenv("DATABASE_URL") or os.getenv("LOCAL_DATABASE_URL")
    if not dsn:
        print("FATAL: no DSN", file=sys.stderr)
        return 2
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    assert pool is not None

    site_config = SiteConfig()
    await site_config.load(pool)
    if args.ollama_url:
        # In-memory only — never written back to app_settings.
        site_config._config["local_llm_api_url"] = args.ollama_url
        print(f"ollama endpoint overridden -> {args.ollama_url}")

    if args.build:
        print("building golden set (one small LLM call per case) ...")
        gs = await build_retrieval_golden_set(pool=pool, site_config=site_config)
        with open(args.cache, "w") as fh:
            json.dump(
                {"name": gs.name, "version": gs.version,
                 "cases": [{"query": c.query, "payload": c.payload} for c in gs.cases]},
                fh,
            )
        deep = sum(1 for c in gs.cases if c.payload["region"] == "deep")
        print(f"  {len(gs.cases)} cases  ({deep} deep / {len(gs.cases)-deep} head)  "
              f"v{gs.version} -> {args.cache}")

    if args.run:
        with open(args.cache) as fh:
            raw = json.load(fh)
        gs = GoldenSet(
            name=raw["name"], version=raw["version"],
            cases=[GoldenCase(query=c["query"], candidates=[], payload=c["payload"])
                   for c in raw["cases"]],
        )
        variants = [("prod", None, None, None)]
        if args.compare:
            variants += [("vector_only", False, False, False),
                         ("hybrid_no_rerank", True, False, False)]
        if args.graph:
            # Matched pair — same cases, same stages, graph expansion the
            # ONLY difference. Rerank is pinned OFF in both arms so the
            # cross-encoder cannot confound the graph contribution (and so
            # the pair is runnable on a host that lacks the rerank extra).
            # Rerank ON in both arms. Graph neighbours enter below every
            # matched result, so the cross-encoder is the only mechanism
            # that can promote one — a rerank-off pair is structurally
            # incapable of showing an effect (learned the hard way).
            variants += [("rerank_graph_off", True, True, False),
                         ("rerank_graph_ON", True, True, True)]

        for name, hybrid, rerank, graph in variants:
            res = await score_retrieval(
                pool=pool, site_config=site_config, golden_set=gs,
                hybrid=hybrid, rerank=rerank, graph_expand=graph, variant=name,
                embed_base_url=args.ollama_url or None,
            )
            _report(res)
            if args.persist:
                await persist_result(pool, res)
                print("  persisted to audit_log (event_type='retrieval_eval')")

    await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
