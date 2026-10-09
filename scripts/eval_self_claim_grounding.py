"""Measure services.self_claim_grounding before it is wired into qa.self_claim.

Two sets:

- LABELLED: drafts whose first-person claims were fact-checked by hand
  (``--labels``, a JSON file: ``[{"name", "path", "before", "claims":
  [{"match": substring, "expect": "flag" | "pass" | "either"}]}]``).
  ``flag`` = the claim is invented and must come back ``no_evidence`` or
  ``contradicted``; ``pass`` = it is true and must not; ``either`` = not scored.
- PUBLISHED: the newest ``--published`` posts. Their claims are mostly true
  (dev_diary posts are narrated from our own records), so every flag is read
  as a probable false positive and listed for a human to check. The fire rate
  here is the number that killed the last detector (24%).

Evidence is restricted to records written before each draft or post
(``before``), so neither a review session quoting the draft nor the published
post itself can vouch for a claim.

Run where the worker's DB, embeddings endpoint and LLM router are reachable,
e.g. a throwaway container from the worker image with this tree at /app::

    python scripts/eval_self_claim_grounding.py --labels labels.json \\
        --published 20 --model ollama/qwen3-vl:30b-a3b-instruct --out report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "cofounder_agent"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

FLAG_VERDICTS = ("no_evidence", "contradicted")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _expectation(claim_sentence: str, labels: list[dict]) -> str | None:
    for label in labels:
        if label["match"].lower() in claim_sentence.lower():
            return label["expect"]
    return None


def _claim_line(g) -> str:
    top = g.evidence[0] if g.evidence else None
    ev = (
        f"`{g.record or (top.ref if top else '')}`: {' '.join(top.excerpt.split())[:220]}"
        if top else "(no records)"
    )
    missing = f" — missing: {g.missing}" if g.missing else ""
    overruled = f" (judge said {g.judge_verdict}; quote not found)" if g.judge_verdict else ""
    quote = f"\n  - quote: “{g.quote}”" if g.quote else ""
    return f"- **{g.verdict}**{overruled}{missing}\n  - claim: {g.claim.sentence}{quote}\n  - top record: {ev}"


async def _published(pool, limit: int) -> list[dict]:
    rows = await pool.fetch(
        "SELECT p.title, p.content, p.created_at, "
        "COALESCE(pt.niche_slug, '') AS niche FROM posts p "
        "LEFT JOIN pipeline_tasks pt ON pt.task_id = p.metadata->>'pipeline_task_id' "
        "WHERE p.status = 'published' ORDER BY p.published_at DESC NULLS LAST LIMIT $1",
        limit,
    )
    return [
        {"name": f"[{r['niche'] or '?'}] {r['title']}", "content": r["content"], "before": r["created_at"]}
        for r in rows
    ]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--labels", help="labelled drafts JSON")
    ap.add_argument("--published", type=int, default=0, help="newest N published posts")
    ap.add_argument("--model", required=True)
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import asyncpg
    from poindexter.brain.bootstrap import resolve_database_url
    from poindexter.services.bootstrap import build_container
    from poindexter.services.container_registry import set_container
    from poindexter.services.self_claim_grounding import ground_draft

    pool = await asyncpg.create_pool(resolve_database_url(), min_size=1, max_size=4)
    container = await build_container(pool)
    set_container(container)
    sc = container.site_config

    lines: list[str] = ["# self_claim_grounding evaluation", "", f"model `{args.model}`, top_k {args.top_k}", ""]
    scored = Counter()
    summary = Counter()

    if args.labels:
        lines += ["## Labelled drafts", ""]
        for item in json.loads(Path(args.labels).read_text()):
            content = Path(item["path"]).read_text()
            results = await ground_draft(
                pool, content, site_config=sc, model=args.model,
                before=_parse_ts(item["before"]), top_k=args.top_k,
            )
            lines += [f"### {item['name']}", ""]
            for g in results:
                expect = _expectation(g.claim.sentence, item.get("claims", []))
                flagged = g.verdict in FLAG_VERDICTS
                if expect == "flag":
                    scored["flag_ok" if flagged else "flag_missed"] += 1
                elif expect == "pass":
                    scored["pass_ok" if not flagged else "false_flag"] += 1
                mark = {
                    ("flag", True): "✅ caught", ("flag", False): "❌ missed",
                    ("pass", False): "✅ passed", ("pass", True): "❌ false flag",
                }.get((expect, flagged), "· unscored")
                lines.append(f"{mark} {_claim_line(g)}")
            matched = {lbl["match"] for lbl in item.get("claims", [])}
            seen = {lbl["match"] for g in results for lbl in item.get("claims", [])
                    if lbl["match"].lower() in g.claim.sentence.lower()}
            for miss in sorted(matched - seen):
                lines.append(f"❌ not extracted: “{miss}”")
                scored["not_extracted"] += 1
            lines.append("")

    if args.published:
        lines += ["## Published posts (flags = probable false positives)", ""]
        posts = await _published(pool, args.published)
        for post in posts:
            results = await ground_draft(
                pool, post["content"], site_config=sc, model=args.model,
                before=post["before"], top_k=args.top_k,
            )
            summary["posts"] += 1
            summary["claims"] += len(results)
            flags = [g for g in results if g.verdict in FLAG_VERDICTS]
            for g in results:
                summary[g.verdict] += 1
            if flags:
                summary["posts_flagged"] += 1
            lines += [f"### {post['name']} — {len(results)} claim(s), {len(flags)} flagged", ""]
            lines += [_claim_line(g) for g in flags]
            lines.append("")

    head = ["## Summary", ""]
    if scored:
        caught, missed = scored["flag_ok"], scored["flag_missed"]
        head.append(
            f"- labelled: invented claims caught {caught}/{caught + missed}, "
            f"true claims passed {scored['pass_ok']}/{scored['pass_ok'] + scored['false_flag']}, "
            f"labelled claims not extracted {scored['not_extracted']}"
        )
    if summary["posts"]:
        head.append(
            f"- published: {summary['posts']} posts, {summary['claims']} claims, "
            f"{summary['posts_flagged']} post(s) with a flag "
            f"({100 * summary['posts_flagged'] / summary['posts']:.0f}%); verdicts "
            + ", ".join(f"{k} {summary[k]}" for k in ("supported", "vague", "no_evidence", "contradicted", "error"))
        )
    Path(args.out).write_text("\n".join(lines[:4] + head + [""] + lines[4:]) + "\n")
    print("\n".join(head))
    # The settings this run read must be stamped, or they look unused to
    # ProbeZeroReaderSettingsJob (scripts/ci/settings_read_flush_lint.py).
    from poindexter.services.settings_read_telemetry import flush_read_telemetry

    await flush_read_telemetry(pool, sc)
    await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
