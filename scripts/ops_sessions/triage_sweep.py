"""Weekly triage: run sweep script + keyword area-labels + Discord digest."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _common as c
import httpx

AREA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": ("fastapi", "asyncpg", "worker", "service layer", "endpoint"),
    "frontend": ("next.js", "nextjs", "react", "public-site", "vercel"),
    "testing": ("pytest", "unit test", "coverage", "flaky test"),
    "infra": ("docker", "compose", "container", "deploy"),
    "monitoring": ("grafana", "prometheus", "dashboard", "panel", "loki", "alert"),
    "pipeline": ("canonical_blog", "graph_def", "atom", "qa rail", "template_runner"),
    "monetization": ("adsense", "affiliate", "revenue", "stripe", "lemon squeezy"),
}


def pick_area_label(body: str) -> str | None:
    low = body.lower()
    hits = [area for area, kws in AREA_KEYWORDS.items() if any(k in low for k in kws)]
    return hits[0] if len(hits) == 1 else None


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("triage-sweep")
    root = _repo_root()
    sweep = c.run(
        # sys.executable = the launching main-env python (the worktree/shared
        # checkout has the deps run_weekly_sweep needs).
        [sys.executable, str(root / "scripts" / "triage" / "run_weekly_sweep.py")],
        cwd=str(root / "src" / "cofounder_agent"),
    )
    report = json.loads(sweep.stdout or "{}") if sweep.stdout else {}
    proposals: list[str] = []
    for repo, gaps in report.get("gaps", {}).items():
        for gap in gaps:
            if "area" in gap.get("missing", []):
                area = pick_area_label(gap.get("body", ""))
                if area:
                    c.gh("issue", "edit", "--repo", repo, str(gap["number"]), "--add-label", area)
            proposals.append(f"{repo}#{gap['number']}: {gap.get('proposal', '')}")
    webhook = c.bootstrap_value("discord_ops_webhook_url")
    if webhook and proposals:
        body = f"**Weekly triage: {len(proposals)} proposals**\n" + "\n".join(proposals[:25])
        try:
            httpx.post(webhook, json={"content": body[:1900]}, timeout=15)
        except httpx.HTTPError as exc:
            log.warning("discord post failed: %s", exc)
    log.info("proposals=%d", len(proposals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
