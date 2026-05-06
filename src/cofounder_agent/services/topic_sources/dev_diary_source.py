"""DevDiarySource — daily "what we shipped today" context bundle.

Pulls a structured snapshot of the last 24h of Glad Labs activity:

- Merged PRs        (via the GitHub REST API ``GET /repos/{repo}/pulls``)
- Notable commits   (via ``GET /repos/{repo}/commits`` filtered by
                     feat:/fix:/refactor:/perf:/security: prefixes)
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

GitHub data is fetched via direct REST API calls using ``httpx`` —
no subprocess dependency on ``gh`` or ``git`` binaries, no requirement
that ``.git`` be bind-mounted into the worker container. Failures are
non-fatal but LOUD: 4xx/5xx responses, network timeouts, and JSON
decode errors all log at ``warning`` level so Loki picks them up,
then return an empty list. The "skip if quiet day" decision lives in
the job, not here, so the source's contract stays small + testable.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

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

# Default GitHub repo when no app_setting / SiteConfig override is provided.
_DEFAULT_GH_REPO = "Glad-Labs/glad-labs-stack"

# GitHub REST API base URL.
_GITHUB_API_BASE = "https://api.github.com"

# Per-PR body cap upstream of the prompt formatter. The formatter
# applies its own cap; the API fetch itself is unbounded so we always
# have the full text available for fallback.
_PR_BODY_CAP_CHARS = 2000


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
    # Operator-supplied 1-2 sentence emotional through-line for the
    # day. Per feedback_dev_diary_voice_is_founder_not_journalist:
    # bundle facts are dry by design; the operator note is the
    # authentic personality the post draws from. Empty string when
    # the operator didn't submit a note today.
    operator_notes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "merged_prs": self.merged_prs,
            "notable_commits": self.notable_commits,
            "brain_decisions": self.brain_decisions,
            "audit_resolved": self.audit_resolved,
            "recent_posts": self.recent_posts,
            "cost_summary": self.cost_summary,
            "operator_notes": self.operator_notes,
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
# GitHub REST API collectors
# ---------------------------------------------------------------------------


def _build_gh_headers(gh_token: str | None) -> dict[str, str]:
    """Build request headers for the GitHub REST API.

    Always sets ``Accept`` and ``X-GitHub-Api-Version``. Includes
    ``Authorization: Bearer <token>`` when a non-empty token is
    available; otherwise emits a debug log and lets the request fly
    unauthenticated (works for public repos at the lower rate limit).
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "poindexter-dev-diary",
    }
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    else:
        logger.debug(
            "DevDiarySource: no gh_token configured — calling GitHub API "
            "unauthenticated (works for public repos at the lower rate limit)"
        )
    return headers


async def _gh_get_json(
    client: httpx.AsyncClient, url: str, headers: dict[str, str],
) -> Any | None:
    """Issue a single ``GET`` against the GitHub REST API and parse JSON.

    Returns the parsed payload on success, ``None`` on any failure mode
    (4xx/5xx, network error, timeout, JSON decode error). All failure
    modes are logged at ``warning`` so Loki picks them up — the silent-
    debug logging the subprocess version used was the root cause of
    Glad-Labs/poindexter#405 (worker reported "quiet day" instead of
    surfacing that the API call was failing).
    """
    try:
        resp = await client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
        logger.warning(
            "DevDiarySource: GitHub API request to %s failed: %s",
            url, exc,
        )
        return None

    if resp.status_code >= 400:
        body_preview = (resp.text or "")[:300]
        logger.warning(
            "DevDiarySource: GitHub API %s returned %s — body: %s",
            url, resp.status_code, body_preview,
        )
        return None

    try:
        return resp.json()
    except (ValueError, TypeError) as exc:
        logger.warning(
            "DevDiarySource: GitHub API %s returned non-JSON: %s",
            url, exc,
        )
        return None


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 GitHub timestamp into a UTC-aware datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        # GitHub uses ``...Z`` suffix; ``fromisoformat`` accepts both
        # ``Z`` (Python 3.11+) and explicit offsets. Normalise to be
        # safe for older interpreters.
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _collect_merged_prs(
    hours: int,
    repo: str,
    gh_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Use the GitHub REST API to collect PRs merged in the last ``hours``.

    Returns a list of ``{number, title, url, merged_at, author, body}``
    dicts. The ``body`` field is critical for technical accuracy: with
    title-only data the writer guesses meaning from keywords (a PR
    titled ``fix(validator): kill IGNORECASE bypass`` was described as
    *adding* IGNORECASE — the opposite direction). With the body the
    writer has the actual change description to ground against.

    Body is capped to ~2000 chars per PR upstream of the prompt
    formatter, which applies its own cap; the GitHub fetch itself is
    unbounded so we always have the full text available for fallback.

    ``gh_token``: when truthy, sent as ``Authorization: Bearer <token>``
    for authenticated rate limits + private-repo access. Sourced from
    ``app_settings('gh_token')`` (is_secret=true) and threaded through
    ``gather_context``. Empty / None falls back to unauthenticated mode
    (works on public repos, returns nothing on private ones).

    ``client``: optional pre-built ``httpx.AsyncClient``. Used by tests
    to inject a ``MockTransport``. When ``None``, a default client is
    constructed for the duration of the call.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    url = (
        f"{_GITHUB_API_BASE}/repos/{repo}/pulls"
        "?state=closed&sort=updated&direction=desc&per_page=30"
    )
    headers = _build_gh_headers(gh_token)

    if client is None:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0)
        ) as owned_client:
            data = await _gh_get_json(owned_client, url, headers)
    else:
        data = await _gh_get_json(client, url, headers)

    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for pr in data:
        if not isinstance(pr, dict):
            continue
        merged_at_raw = pr.get("merged_at")
        merged_at = _parse_iso_utc(merged_at_raw)
        if merged_at is None:
            # Closed-but-not-merged PRs have ``merged_at: null``.
            continue
        if merged_at < since:
            continue
        author = pr.get("user") or {}
        out.append({
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "url": pr.get("html_url", ""),
            "merged_at": merged_at_raw,
            "author": author.get("login", "") if isinstance(author, dict) else "",
            "body": (pr.get("body") or "")[:_PR_BODY_CAP_CHARS],
        })
    return out


# Conventional-commit subject parser. Captures the prefix (feat/fix/etc)
# so the job can group commits by type if it wants to.
_CC_RE = re.compile(r"^([a-z]+)(?:\([^)]+\))?!?:\s*(.+)$")


async def _collect_notable_commits(
    hours: int,
    repo: str,
    gh_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Use the GitHub REST API to collect commits in the last ``hours``,
    filtered to ``feat:/fix:/refactor:/perf:/security:`` prefixes.

    Returns ``{sha, subject, prefix, author, date}`` dicts. Subject is
    parsed from the first line of the commit message (GitHub's
    ``commit.message`` field includes the full message body too).
    """
    since_iso = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"{_GITHUB_API_BASE}/repos/{repo}/commits"
        f"?since={since_iso}&per_page=100"
    )
    headers = _build_gh_headers(gh_token)

    if client is None:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0)
        ) as owned_client:
            data = await _gh_get_json(owned_client, url, headers)
    else:
        data = await _gh_get_json(client, url, headers)

    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        sha = entry.get("sha", "") or ""
        commit = entry.get("commit") or {}
        if not isinstance(commit, dict):
            continue
        full_message = commit.get("message", "") or ""
        subject = full_message.split("\n", 1)[0].strip()
        if not subject:
            continue
        m = _CC_RE.match(subject)
        if not m:
            continue
        prefix = m.group(1).lower()
        if prefix not in _NOTABLE_COMMIT_PREFIXES:
            continue

        author_block = commit.get("author") or {}
        author_name = (
            author_block.get("name", "") if isinstance(author_block, dict) else ""
        )
        date_str = (
            author_block.get("date", "") if isinstance(author_block, dict) else ""
        )

        # Skip merge commits (they have multiple parents); the GitHub
        # commits endpoint returns them by default. Conventional-commit
        # parsing already filters most of these out (merge subjects
        # rarely match), but this is a belt-and-suspenders guard.
        parents = entry.get("parents") or []
        if isinstance(parents, list) and len(parents) > 1:
            continue

        out.append({
            "sha": sha[:8] if sha else "",
            "subject": subject,
            "prefix": prefix,
            "author": author_name,
            "date": date_str,
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


async def _collect_operator_notes(
    pool: Any, niche_slug: str,
) -> list[dict[str, Any]]:
    """Pull today's operator notes for the niche (UTC date), oldest first.

    Per ``feedback_dev_diary_voice_is_founder_not_journalist``: bundle
    facts are dry by design. The operator's note is the authentic
    emotional through-line the post draws personality from. Multiple
    notes may exist per day; the prompt threads them in the order
    they were submitted.

    Best-effort: returns ``[]`` on table-missing / fetch failure.
    """
    if pool is None:
        return []
    try:
        rows = await pool.fetch(
            """
            SELECT note, mood, created_at, created_by
              FROM operator_notes
             WHERE niche_slug = $1
               AND note_date = CURRENT_DATE
             ORDER BY created_at ASC
            """,
            niche_slug,
        )
    except Exception as exc:
        logger.debug("DevDiarySource: operator_notes fetch failed: %s", exc)
        return []
    return [
        {
            "note": r["note"],
            "mood": r["mood"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "created_by": r["created_by"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# DevDiarySource — public entry point
# ---------------------------------------------------------------------------


async def _fetch_gh_token(pool: Any) -> str:
    """Read the ``gh_token`` secret from app_settings, decrypted.

    Uses ``plugins.secrets.get_secret`` so encrypted (``enc:v1:...``)
    and legacy plaintext rows are both handled transparently. Returns
    an empty string when the row is missing, empty, or the fetch
    fails (e.g. during early-boot / unit tests without a real pool).
    Empty token is fine — the GitHub API call just runs unauthenticated.
    """
    if pool is None:
        return ""
    try:
        from plugins.secrets import get_secret
        async with pool.acquire() as conn:
            value = await get_secret(conn, "gh_token")
        return value or ""
    except Exception as exc:
        logger.debug("DevDiarySource: gh_token fetch failed: %s", exc)
        return ""


async def _fetch_gh_repo(pool: Any) -> str:
    """Read the ``gh_repo`` setting from app_settings.

    Non-secret, plain string. Empty / missing / fetch error all fall
    back to the default. Returns ``""`` only if the operator deliberately
    blanked the row, which the caller treats as "use the constructor /
    default value".
    """
    if pool is None:
        return ""
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'gh_repo'"
            )
        return value or ""
    except Exception as exc:
        logger.debug("DevDiarySource: gh_repo fetch failed: %s", exc)
        return ""


class DevDiarySource:
    """Daily dev-diary context bundler.

    Conforms to the TopicSource Protocol via ``extract``, but the
    primary use case is via ``gather_context`` from
    ``services/jobs/run_dev_diary_post.py``.
    """

    name = "dev_diary"

    def __init__(self, *, gh_repo: str | None = None) -> None:
        """Build a DevDiarySource.

        ``gh_repo``: optional ``owner/name`` override. Resolution order
        in ``gather_context``: explicit ``gh_repo`` kwarg → SiteConfig
        ``gh_repo`` setting → ``app_settings.gh_repo`` row → constructor
        ``gh_repo`` arg → ``_DEFAULT_GH_REPO`` constant.
        """
        self._ctor_gh_repo = (gh_repo or "").strip()

    async def gather_context(
        self,
        pool: Any,
        *,
        hours_lookback: int = _DEFAULT_LOOKBACK_HOURS,
        confidence_floor: float = _DEFAULT_BRAIN_CONFIDENCE_FLOOR,
        gh_repo: str | None = None,
        gh_token: str | None = None,
        site_config: Any = None,
    ) -> DevDiaryContext:
        """Pull all context sections concurrently and return a bundle.

        DB sections run as native asyncpg coroutines; GitHub sections
        run as ``httpx`` coroutines. Failures in any single section
        produce an empty list / dict for that section, never an
        exception.

        ``gh_repo``: explicit ``owner/name`` override. When ``None``
        (the typical path), the source resolves the repo via
        ``site_config.get('gh_repo')`` if a SiteConfig is supplied,
        then falls back to the ``app_settings.gh_repo`` row, then the
        constructor arg, then ``_DEFAULT_GH_REPO``.

        ``gh_token``: explicit override for the GitHub auth token.
        When ``None`` (the typical path), the token is loaded from
        ``app_settings('gh_token')`` via ``plugins.secrets.get_secret``
        — see ``_fetch_gh_token``. Passing an empty string explicitly
        forces unauthenticated mode without touching the DB.

        ``site_config``: optional ``SiteConfig`` DI seam. When provided,
        ``gh_repo`` is read from it before any DB lookup (matches the
        pattern used by other topic sources).
        """
        repo = (gh_repo or "").strip()
        if not repo and site_config is not None:
            try:
                repo = (site_config.get("gh_repo", "") or "").strip()
            except Exception as exc:
                logger.debug(
                    "DevDiarySource: site_config.get('gh_repo') failed: %s", exc,
                )
                repo = ""
        if not repo:
            repo = (await _fetch_gh_repo(pool)).strip()
        if not repo:
            repo = self._ctor_gh_repo
        if not repo:
            repo = _DEFAULT_GH_REPO

        if gh_token is None:
            gh_token = await _fetch_gh_token(pool)

        prs_task = _collect_merged_prs(hours_lookback, repo, gh_token)
        commits_task = _collect_notable_commits(hours_lookback, repo, gh_token)
        decisions_task = _collect_brain_decisions(pool, hours_lookback, confidence_floor)
        audit_task = _collect_audit_resolved(pool, hours_lookback)
        posts_task = _collect_recent_posts(pool, hours_lookback)
        cost_task = _collect_cost_summary(pool, hours_lookback)
        notes_task = _collect_operator_notes(pool, "dev_diary")

        import asyncio
        prs, commits, decisions, audit, posts, cost, notes = await asyncio.gather(
            prs_task, commits_task, decisions_task, audit_task, posts_task,
            cost_task, notes_task,
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
            operator_notes=notes,
        )
        logger.info(
            "DevDiarySource: gathered context (date=%s repo=%s prs=%d commits=%d "
            "decisions=%d audit=%d posts=%d cost=$%.4f notes=%d)",
            ctx.date, repo, len(prs), len(commits), len(decisions), len(audit),
            len(posts), cost.get("total_usd", 0.0), len(notes),
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
        gh_repo = config.get("gh_repo") or os.environ.get("DEV_DIARY_GH_REPO") or None
        site_config = config.get("_site_config")

        ctx = await self.gather_context(
            pool,
            hours_lookback=hours,
            confidence_floor=confidence,
            gh_repo=gh_repo,
            site_config=site_config,
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
