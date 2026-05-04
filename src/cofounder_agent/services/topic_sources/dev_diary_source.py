"""DevDiarySource — daily "what we shipped today" context bundle.

Pulls a structured snapshot of the last 24h of Glad Labs activity:

- Merged PRs        (via ``gh pr list --state merged``)
- Notable commits   (via ``git log`` filtered by feat:/fix:/refactor:/perf: prefixes)
- Brain decisions   (high-confidence rows from the ``brain_decisions`` table)
- Resolved audit    (warning/error events that have a corresponding
                     "resolved" / "fixed" / "completed" follow-up)
- Recent posts      (posts published in the last 24h with title + url)
- Cost summary      (per-model spend + inference count from ``cost_logs``)

The ``gather_context`` coroutine returns the rich context dict that
``services/jobs/run_dev_diary_post.py`` hands to the writer. The
``extract`` coroutine conforms to the standard ``TopicSource``
Protocol so the niche topic-discovery sweep also picks up dev-diary
candidates if/when an operator wires it in (low priority — the
scheduled job is the primary driver).

Subprocess calls (``gh``, ``git``) are wrapped in ``asyncio.to_thread``
so they don't block the event loop. Failures are non-fatal — an empty
list is returned and the corresponding section of the context bundle
is just empty. The "skip if quiet day" decision lives in the job, not
here, so the source's contract stays small + testable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess  # nosec B404 — invoking trusted local tools (git, gh) by absolute name
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from plugins.topic_source import DiscoveredTopic

logger = logging.getLogger(__name__)


# Default lookback window. Override per call via ``hours_lookback`` kwarg
# or via the ``config`` dict when invoked through the TopicSource runner.
_DEFAULT_LOOKBACK_HOURS = 24

# Conventional-commit prefixes we treat as "notable". Anything else
# (chore:, docs:, ci:, test:, style:) is filtered out — those are
# bookkeeping commits that don't make for good build-in-public material.
_NOTABLE_COMMIT_PREFIXES = ("feat", "fix", "refactor", "perf", "security")

# Confidence floor for brain_decisions inclusion. Lower = noisier;
# higher = misses lower-confidence-but-still-interesting calls.
_DEFAULT_BRAIN_CONFIDENCE_FLOOR = 0.7


# ---------------------------------------------------------------------------
# Public dataclass: the context bundle
# ---------------------------------------------------------------------------


@dataclass
class DevDiaryContext:
    """Rich daily-activity bundle. Serialised to dict for the writer."""

    date: str  # YYYY-MM-DD (UTC) — the day the diary covers
    merged_prs: list[dict[str, Any]]
    notable_commits: list[dict[str, Any]]
    brain_decisions: list[dict[str, Any]]
    audit_resolved: list[dict[str, Any]]
    recent_posts: list[dict[str, Any]]
    cost_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "merged_prs": self.merged_prs,
            "notable_commits": self.notable_commits,
            "brain_decisions": self.brain_decisions,
            "audit_resolved": self.audit_resolved,
            "recent_posts": self.recent_posts,
            "cost_summary": self.cost_summary,
        }

    def is_empty(self) -> bool:
        """True when the day has no Glad Labs activity worth posting about.

        brain_decisions is INTENTIONALLY excluded — the brain emits a
        high-confidence "Cycle complete" decision every 5 minutes as a
        heartbeat, so its presence is not signal that real work happened.
        Real signal comes from git activity (PRs, notable commits),
        published posts, or audit events that actually got resolved.
        """
        return (
            not self.merged_prs
            and not self.notable_commits
            and not self.recent_posts
            and not self.audit_resolved
        )

    def headline(self) -> str:
        """Pick a short headline from the most prominent activity.

        Used as the topic title when the structured bundle is handed to
        the writer. Falls back to a generic dated title if nothing
        notable is found.

        Truncation is at WORD BOUNDARIES — a previous bug
        (Glad-Labs/poindexter#352) sliced at character 60 mid-string,
        which turned ``...auto-load POINDEXTER_SECRET_KEY`` into
        ``...auto-load POINDEXTER_SE``. The writer then hallucinated an
        explanation of ``POINDEXTER_SE`` as if it were a real env var.
        ``textwrap.shorten`` breaks at whitespace and adds an ellipsis,
        so the writer sees an obviously-truncated title and the
        identifier never appears partial.
        """
        if self.merged_prs:
            top = self.merged_prs[0].get("title", "").strip()
            if top:
                return f"Daily dev diary — {self.date}: {_short(top, 80)}"
        if self.notable_commits:
            top = self.notable_commits[0].get("subject", "").strip()
            if top:
                return f"Daily dev diary — {self.date}: {_short(top, 80)}"
        return f"Daily dev diary — {self.date}"


def _short(text: str, width: int) -> str:
    """Word-boundary-aware truncation with ellipsis.

    Wraps ``textwrap.shorten`` for the normal case (multi-word strings
    that cleanly truncate at whitespace). Two edge cases need handling
    because ``shorten``'s behaviour isn't ideal for our context:

    - Single word longer than ``width`` → ``shorten`` returns the bare
      placeholder. That would produce a title of just "…" which is
      useless. We fall back to a hard-cut at ``width-1`` plus the
      placeholder so the writer at least sees the start of the
      identifier and an explicit truncation marker.
    - Already-fits text → return as-is rather than running it through
      ``shorten``'s whitespace-collapse pass.

    The point isn't perfect cosmetics — it's preventing the
    Glad-Labs/poindexter#352 failure mode where ``POINDEXTER_SECRET_KEY``
    became ``POINDEXTER_SE`` and the writer hallucinated an explanation
    of the truncated identifier.
    """
    import textwrap
    text = " ".join(text.split())  # collapse internal whitespace
    if len(text) <= width:
        return text
    short = textwrap.shorten(text, width=width, placeholder="…")
    if short == "…":
        # Single-word-longer-than-width pathological case — surface a
        # prefix + the placeholder rather than just the placeholder.
        return text[: max(width - 1, 1)] + "…"
    return short


# ---------------------------------------------------------------------------
# Subprocess collectors (gh + git) — sync helpers wrapped in to_thread
# ---------------------------------------------------------------------------


def _run_subprocess(cmd: list[str], cwd: str | None = None, timeout: int = 30) -> str:
    """Run a subprocess, returning stdout on success, '' on any failure.

    Failures are logged at debug level — this source must not crash the
    job if ``gh`` isn't installed or ``git`` is in a weird state.
    """
    try:
        result = subprocess.run(  # nosec B603 — fixed argv list, not shell-interpolated
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            logger.debug(
                "DevDiarySource: %s exited %s — stderr: %s",
                cmd[0], result.returncode, (result.stderr or "")[:300],
            )
            return ""
        return result.stdout or ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("DevDiarySource: subprocess %r failed: %s", cmd[0], exc)
        return ""


def _collect_merged_prs(hours: int, repo_root: str | None) -> list[dict[str, Any]]:
    """Use ``gh pr list`` to collect PRs merged in the last ``hours``.

    Returns a list of ``{number, title, url, merged_at, author}`` dicts.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # gh's --search supports "merged:>=YYYY-MM-DDTHH:MM:SSZ"
    cmd = [
        "gh", "pr", "list",
        "--state", "merged",
        "--search", f"merged:>={since}",
        "--limit", "30",
        "--json", "number,title,url,mergedAt,author",
    ]
    raw = _run_subprocess(cmd, cwd=repo_root, timeout=30)
    if not raw.strip():
        return []
    try:
        import json
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.debug("DevDiarySource: gh pr list returned non-JSON: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for pr in data:
        if not isinstance(pr, dict):
            continue
        author = pr.get("author") or {}
        out.append({
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "url": pr.get("url", ""),
            "merged_at": pr.get("mergedAt", ""),
            "author": author.get("login", "") if isinstance(author, dict) else "",
        })
    return out


# Conventional-commit subject parser. Captures the prefix (feat/fix/etc)
# so the job can group commits by type if it wants to.
_CC_RE = re.compile(r"^([a-z]+)(?:\([^)]+\))?!?:\s*(.+)$")


def _collect_notable_commits(hours: int, repo_root: str | None) -> list[dict[str, Any]]:
    """Use ``git log`` to collect commits in the last ``hours``, filtered
    to ``feat:/fix:/refactor:/perf:/security:`` prefixes.

    Returns ``{sha, subject, prefix, author, date}`` dicts.
    """
    since = f"{int(hours)} hours ago"
    cmd = [
        "git", "log",
        f"--since={since}",
        "--pretty=format:%H%x09%s%x09%an%x09%aI",
        "--no-merges",
        "-n", "100",
    ]
    raw = _run_subprocess(cmd, cwd=repo_root, timeout=20)
    if not raw.strip():
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        sha, subject, author, date = parts[0], parts[1], parts[2], parts[3]
        m = _CC_RE.match(subject.strip())
        if not m:
            continue
        prefix = m.group(1).lower()
        if prefix not in _NOTABLE_COMMIT_PREFIXES:
            continue
        out.append({
            "sha": sha[:8],
            "subject": subject.strip(),
            "prefix": prefix,
            "author": author,
            "date": date,
        })
    return out


# ---------------------------------------------------------------------------
# DB collectors (asyncpg)
# ---------------------------------------------------------------------------


async def _collect_brain_decisions(
    pool: Any, hours: int, confidence_floor: float,
) -> list[dict[str, Any]]:
    """Pull high-confidence brain_decisions rows from the last ``hours``.

    Skips silently if the ``brain_decisions`` table doesn't exist
    (early-boot or unit-test environments without the brain schema).
    """
    if pool is None:
        return []
    try:
        # Filter out the brain's heartbeat decisions — every monitor
        # cycle emits a high-confidence "Cycle complete: 0 issues..."
        # row that's pure noise for the writer. Same for the cycle
        # narratives that start with "Monitored N internal".
        rows = await pool.fetch(
            """
            SELECT id, decision, reasoning, confidence, created_at
            FROM brain_decisions
            WHERE created_at > NOW() - ($1::int || ' hours')::interval
              AND confidence >= $2
              AND decision NOT LIKE 'Cycle complete:%%'
              AND decision NOT LIKE 'Monitored %% internal%%'
              AND COALESCE(reasoning, '') NOT LIKE 'Monitored %% internal%%'
            ORDER BY confidence DESC, created_at DESC
            LIMIT 20
            """,
            hours, confidence_floor,
        )
    except Exception as exc:
        logger.debug("DevDiarySource: brain_decisions fetch failed: %s", exc)
        return []
    return [
        {
            "id": r["id"],
            "decision": r["decision"],
            "reasoning": r["reasoning"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else 0.0,
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
        }
        for r in rows
    ]


async def _collect_audit_resolved(pool: Any, hours: int) -> list[dict[str, Any]]:
    """Pull warning/error events that have a corresponding ``resolved``
    follow-up event in the same window.

    Heuristic: for each (event_type, task_id) pair where severity is
    'warning' or 'error', we look for any later event in the same window
    whose ``event_type`` ends in ``_resolved`` or ``_fixed`` or
    ``_completed`` (or whose details JSONB has ``resolved=true``). The
    join is cheap because the audit_log indexes ``event_type`` + ``task_id``.
    """
    if pool is None:
        return []
    try:
        rows = await pool.fetch(
            """
            SELECT a.id, a.event_type, a.source, a.task_id, a.severity,
                   a.timestamp, a.details
            FROM audit_log a
            WHERE a.timestamp > NOW() - ($1::int || ' hours')::interval
              AND a.severity IN ('warning', 'error')
              AND EXISTS (
                  SELECT 1 FROM audit_log b
                  WHERE b.timestamp > a.timestamp
                    AND b.timestamp <= NOW()
                    AND (
                        b.event_type LIKE '%_resolved'
                        OR b.event_type LIKE '%_fixed'
                        OR b.event_type LIKE '%_completed'
                        OR (b.details ? 'resolved' AND (b.details->>'resolved')::boolean IS TRUE)
                    )
                    AND (
                        b.task_id IS NOT DISTINCT FROM a.task_id
                        OR b.source = a.source
                    )
              )
            ORDER BY a.timestamp DESC
            LIMIT 20
            """,
            hours,
        )
    except Exception as exc:
        logger.debug("DevDiarySource: audit_log fetch failed: %s", exc)
        return []
    return [
        {
            "id": r["id"],
            "event_type": r["event_type"],
            "source": r["source"],
            "task_id": r["task_id"],
            "severity": r["severity"],
            "timestamp": r["timestamp"].isoformat() if r["timestamp"] else "",
        }
        for r in rows
    ]


async def _collect_recent_posts(pool: Any, hours: int) -> list[dict[str, Any]]:
    """Pull posts published in the last ``hours``.

    Source-of-truth column for "published-at" varies a little across
    schema generations; we try ``published_at`` first and fall back to
    ``updated_at WHERE status = 'published'``. Either way, an empty
    result is fine — that branch just reports "no new posts today".
    """
    if pool is None:
        return []
    try:
        rows = await pool.fetch(
            """
            SELECT id, title, slug, published_at
            FROM posts
            WHERE published_at IS NOT NULL
              AND published_at > NOW() - ($1::int || ' hours')::interval
            ORDER BY published_at DESC
            LIMIT 10
            """,
            hours,
        )
    except Exception as exc:
        logger.debug("DevDiarySource: posts fetch failed: %s", exc)
        return []
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "slug": r["slug"],
            "published_at": r["published_at"].isoformat() if r["published_at"] else "",
        }
        for r in rows
    ]


async def _collect_cost_summary(pool: Any, hours: int) -> dict[str, Any]:
    """Aggregate cost_logs for the last ``hours`` — total + per-model breakdown."""
    if pool is None:
        return {"total_usd": 0.0, "total_inferences": 0, "by_model": []}
    try:
        rows = await pool.fetch(
            """
            SELECT model,
                   COUNT(*) AS inferences,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd,
                   COALESCE(SUM(total_tokens), 0) AS tokens
            FROM cost_logs
            WHERE created_at > NOW() - ($1::int || ' hours')::interval
            GROUP BY model
            ORDER BY cost_usd DESC
            """,
            hours,
        )
    except Exception as exc:
        logger.debug("DevDiarySource: cost_logs fetch failed: %s", exc)
        return {"total_usd": 0.0, "total_inferences": 0, "by_model": []}

    by_model = [
        {
            "model": r["model"],
            "inferences": int(r["inferences"]),
            "cost_usd": float(r["cost_usd"]),
            "tokens": int(r["tokens"]),
        }
        for r in rows
    ]
    return {
        "total_usd": round(sum(m["cost_usd"] for m in by_model), 4),
        "total_inferences": sum(m["inferences"] for m in by_model),
        "by_model": by_model,
    }


# ---------------------------------------------------------------------------
# DevDiarySource — public entry point
# ---------------------------------------------------------------------------


def _resolve_repo_root() -> str | None:
    """Find the repo root by walking up until we hit a ``.git`` dir.

    Returns the absolute path as a string, or None when the source is
    invoked from a directory that isn't inside a git repo (CI sandbox,
    test fixture).
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return str(parent)
    return None


class DevDiarySource:
    """Daily dev-diary context bundler.

    Conforms to the TopicSource Protocol via ``extract``, but the
    primary use case is via ``gather_context`` from
    ``services/jobs/run_dev_diary_post.py``.
    """

    name = "dev_diary"

    async def gather_context(
        self,
        pool: Any,
        *,
        hours_lookback: int = _DEFAULT_LOOKBACK_HOURS,
        confidence_floor: float = _DEFAULT_BRAIN_CONFIDENCE_FLOOR,
        repo_root: str | None = None,
    ) -> DevDiaryContext:
        """Pull all six context sections concurrently and return a bundle.

        Subprocess sections (gh + git) run via ``asyncio.to_thread``;
        DB sections run as native asyncpg coroutines. Failures in any
        single section produce an empty list / dict for that section,
        never an exception.
        """
        repo_root = repo_root or _resolve_repo_root()

        prs_task = asyncio.to_thread(_collect_merged_prs, hours_lookback, repo_root)
        commits_task = asyncio.to_thread(_collect_notable_commits, hours_lookback, repo_root)
        decisions_task = _collect_brain_decisions(pool, hours_lookback, confidence_floor)
        audit_task = _collect_audit_resolved(pool, hours_lookback)
        posts_task = _collect_recent_posts(pool, hours_lookback)
        cost_task = _collect_cost_summary(pool, hours_lookback)

        prs, commits, decisions, audit, posts, cost = await asyncio.gather(
            prs_task, commits_task, decisions_task, audit_task, posts_task, cost_task,
        )

        # Day label — UTC, as that's what all the source timestamps are in.
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        ctx = DevDiaryContext(
            date=day,
            merged_prs=prs,
            notable_commits=commits,
            brain_decisions=decisions,
            audit_resolved=audit,
            recent_posts=posts,
            cost_summary=cost,
        )
        logger.info(
            "DevDiarySource: gathered context (date=%s prs=%d commits=%d "
            "decisions=%d audit=%d posts=%d cost=$%.4f)",
            ctx.date, len(prs), len(commits), len(decisions), len(audit),
            len(posts), cost.get("total_usd", 0.0),
        )
        return ctx

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        """TopicSource Protocol entry point.

        Returns a single DiscoveredTopic representing today's dev diary,
        OR an empty list if the day was quiet (no PRs, no commits, no
        high-confidence decisions). The caller (topic-discovery sweep)
        treats an empty list as "no candidates from this source today",
        which is the correct outcome for a quiet day.
        """
        hours = int(config.get("hours_lookback", _DEFAULT_LOOKBACK_HOURS) or _DEFAULT_LOOKBACK_HOURS)
        confidence = float(
            config.get("confidence_floor", _DEFAULT_BRAIN_CONFIDENCE_FLOOR)
            or _DEFAULT_BRAIN_CONFIDENCE_FLOOR
        )
        repo_root = config.get("repo_root") or os.environ.get("DEV_DIARY_REPO_ROOT")

        ctx = await self.gather_context(
            pool,
            hours_lookback=hours,
            confidence_floor=confidence,
            repo_root=repo_root,
        )
        if ctx.is_empty():
            return []

        return [
            DiscoveredTopic(
                title=ctx.headline(),
                category="dev_diary",
                source=self.name,
                source_url="",
                relevance_score=0.85,  # high — internally-curated
                description=(
                    f"{len(ctx.merged_prs)} PRs merged, "
                    f"{len(ctx.notable_commits)} notable commits, "
                    f"{len(ctx.brain_decisions)} brain decisions"
                ),
                keywords=["build-in-public", "dev-diary"],
            )
        ]
