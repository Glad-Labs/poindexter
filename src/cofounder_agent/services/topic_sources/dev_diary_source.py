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
then return an empty list. The source scores how substantial a day was
(``SubstancePolicy`` / ``DevDiaryContext.substance_score``) but does not
act on it — the skip decision itself lives in the job, so the source's
contract stays small + testable.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from plugins.topic_source import DiscoveredTopic
from utils.crawler_ua import build_crawler_ua
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


def _emit_context_source_failed(source_name: str, exc: Exception) -> None:
    """Record a best-effort dev-diary context-source failure as a
    non-paging finding.

    Each ``_collect_*`` / ``_fetch_*`` helper degrades gracefully — it
    returns an empty fallback so the daily post still ships. But a
    swallowed DB/secret error was previously logged at ``debug`` (below
    the prod log level) and so vanished, contradicting this module's own
    "non-fatal but LOUD" contract that the GitHub-fetch path already
    honours at ``warning``. Emitting an ``info`` finding makes "a source
    came up empty because it *failed*" distinguishable from "a source was
    legitimately quiet" — visible on the Findings dashboard, deduped per
    source, never routed/paged (``info`` is below the router's severity
    floor), and never blocking. Convention: ``utils/findings.py``.
    """
    emit_finding(
        source="dev_diary_source",
        kind="dev_diary_context_source_failed",
        title=f"Dev-diary context source '{source_name}' failed",
        body=f"{source_name} fetch failed: {describe_exception(exc)}",
        dedup_key=f"dev_diary_context_source_failed:{source_name}",
    )


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
_DEFAULT_GH_REPO = "Glad-Labs/poindexter"

# GitHub REST API base URL.
_GITHUB_API_BASE = "https://api.github.com"

# Per-PR body cap upstream of the prompt formatter. The formatter
# applies its own cap; the API fetch itself is unbounded so we always
# have the full text available for fallback.
_PR_BODY_CAP_CHARS = 2000

# Conventional-commit subject parser. Captures the prefix (feat/fix/etc)
# so the job can group commits by type if it wants to.
_CC_RE = re.compile(r"^([a-z]+)(?:\([^)]+\))?!?:\s*(.+)$")


# ---------------------------------------------------------------------------
# Substance policy — "is this day worth a diary at all?"
# ---------------------------------------------------------------------------
#
# Measured on Glad-Labs/poindexter across 2026-07-08..07-31 (24 days):
# four days (07-21, 07-22, 07-25, 07-27) had merged PRs but ZERO substantive
# work — every PR was release-please, dependabot, or a docs/ci sweep. Each
# still produced a dev diary, because the old gate only asked whether ANY
# activity existed. This policy is what raises that bar.
#
# Two rules, both read off that sample rather than invented:
#
#   1. Bot-authored PRs are never substance. All four thin days were
#      dominated by `app/glad-labs-release-bot` and `app/dependabot`.
#   2. Bookkeeping conventional-commit types are never substance.
#
# Note this is a DENYLIST, unlike the allowlist `_NOTABLE_COMMIT_PREFIXES`
# applies to commits. An allowlist looked tempting until the same sample
# showed real work routinely ships under an UNTYPED title — "Dev.to
# selective syndication", "Community draft assistant (WS2 PR1: Reddit)",
# "Electric-cost console tracking". Allowlisting prefixes would have thrown
# those away and skipped genuinely busy days. So: untyped ⇒ substantive.
_DEFAULT_BOOKKEEPING_TYPES = (
    "chore", "docs", "ci", "test", "style", "build", "deps", "revert",
)

# fnmatch globs (matched lowercased) for automation accounts. GitHub App
# actors arrive as `app/<slug>`; classic bots carry the `[bot]` suffix.
_DEFAULT_BOT_AUTHOR_PATTERNS = ("app/*", "*[bot]", "dependabot*", "*-bot")

# Squash-merged commits inherit the PR title and carry a trailing `(#N)`,
# so one shipped PR appears in BOTH `merged_prs` and `notable_commits`.
# Scoring the streams naively would double-count every PR; this pulls the
# number back out so a commit can be deduped against the PR it came from.
_PR_REF_RE = re.compile(r"\(#(\d+)\)")


def _conventional_type(subject: str) -> str:
    """Lowercase conventional-commit type, or ``""`` when untyped."""
    m = _CC_RE.match((subject or "").strip())
    return m.group(1).lower() if m else ""


def _is_bot_author(login: str, patterns: tuple[str, ...]) -> bool:
    """True when ``login`` matches any automation-account glob."""
    name = (login or "").strip().lower()
    if not name:
        return False
    return any(
        fnmatch.fnmatch(name, pat.strip().lower())
        for pat in patterns
        if pat and pat.strip()
    )


def _csv_tuple(raw: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a CSV setting into a tuple, falling back when blank.

    ``app_settings.value`` is NOT NULL with ``''`` as the unset sentinel
    (feedback_app_settings_value_not_null), so a blank string means
    "operator hasn't set this", NOT "match nothing" — the difference
    between inheriting the defaults and silently disabling the filter.
    """
    if raw is None:
        return fallback
    parts = tuple(p.strip() for p in str(raw).split(",") if p.strip())
    return parts or fallback


def _as_float(raw: Any, fallback: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class SubstancePolicy:
    """How much real work a day needs before it earns a dev diary.

    Weights are per-item; ``min_score`` is the bar. A day scoring below it
    is skipped as "thin". Defaults are grounded in the sample above:
    ``min_score=1.0`` with ``weight_pr=1.0`` means "at least one
    substantive, human-authored PR", which skipped exactly the four dead
    days and wrote on all twenty real ones.

    ``weight_post`` / ``weight_audit`` default to 0. The content pipeline
    publishing a post is the machine doing its routine job, not the
    operator shipping — and two of the four thin days DID publish one, so
    counting posts would have left half the problem in place. Posts and
    audit events stay in the bundle as writer context either way; this
    only governs whether they justify writing at all.
    """

    min_score: float = 1.0
    weight_pr: float = 1.0
    weight_commit: float = 1.0
    weight_post: float = 0.0
    weight_audit: float = 0.0
    bookkeeping_types: tuple[str, ...] = _DEFAULT_BOOKKEEPING_TYPES
    bot_author_patterns: tuple[str, ...] = _DEFAULT_BOT_AUTHOR_PATTERNS

    @classmethod
    def from_settings(cls, get: Any) -> SubstancePolicy:
        """Build from a ``get(key, default)`` accessor (SiteConfig or dict).

        Every field is DB-tunable per feedback_db_first_config — the bar is
        an editorial judgement that will drift, and shipping code to move it
        would be the wrong seam.
        """
        if get is None:
            return cls()
        d = cls()
        return cls(
            min_score=_as_float(
                get("dev_diary_min_substance_score", d.min_score), d.min_score,
            ),
            weight_pr=_as_float(
                get("dev_diary_substance_weight_pr", d.weight_pr), d.weight_pr,
            ),
            weight_commit=_as_float(
                get("dev_diary_substance_weight_commit", d.weight_commit),
                d.weight_commit,
            ),
            weight_post=_as_float(
                get("dev_diary_substance_weight_post", d.weight_post),
                d.weight_post,
            ),
            weight_audit=_as_float(
                get("dev_diary_substance_weight_audit", d.weight_audit),
                d.weight_audit,
            ),
            bookkeeping_types=_csv_tuple(
                get("dev_diary_bookkeeping_types", None), d.bookkeeping_types,
            ),
            bot_author_patterns=_csv_tuple(
                get("dev_diary_bot_author_patterns", None), d.bot_author_patterns,
            ),
        )


# ---------------------------------------------------------------------------
# Public dataclass: the context bundle
# ---------------------------------------------------------------------------


@dataclass
class DevDiaryContext:
    """Rich activity bundle over ``lookback_hours``. Serialised for the writer."""

    date: str  # YYYY-MM-DD (UTC) — the day the window ENDS on
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
    # Width of the window this bundle was gathered over. Drives the topic's
    # period word ("Daily"/"Weekly"/"N-day") so the headline can't claim a
    # cadence the data doesn't cover — the job went weekly 2026-08-09 and a
    # hardcoded "Daily" would have mislabelled every post from then on.
    # Defaults to 24 so existing callers that don't pass it are unchanged.
    lookback_hours: int = 24

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
        """True when the day has literally no Glad Labs activity.

        brain_decisions is INTENTIONALLY excluded — the brain emits a
        high-confidence "Cycle complete" decision every 5 minutes as a
        heartbeat, so its presence is not signal that real work happened.
        Real signal comes from git activity (PRs, notable commits),
        published posts, or audit events that actually got resolved.

        NOTE: this is the *literal* emptiness check and is deliberately
        NOT the skip gate any more. It only catches days where nothing at
        all happened; a day of pure release-bot and docs churn is not
        empty by this definition yet is not worth a diary either. The job
        gates on :meth:`substance_score` against
        ``dev_diary_min_substance_score``. Kept as-is because callers
        outside the job (the ``poindexter dev-diary`` preview) still ask
        the literal question.
        """
        return (
            not self.merged_prs
            and not self.notable_commits
            and not self.recent_posts
            and not self.audit_resolved
        )

    def substantive_prs(
        self, policy: SubstancePolicy | None = None,
    ) -> list[dict[str, Any]]:
        """Merged PRs that represent actual shipped work.

        Drops bot-authored PRs (release-please, dependabot) and
        bookkeeping conventional-commit types. Untyped titles are KEPT —
        see the denylist rationale on :data:`_DEFAULT_BOOKKEEPING_TYPES`.
        """
        pol = policy or SubstancePolicy()
        out: list[dict[str, Any]] = []
        for pr in self.merged_prs:
            if _is_bot_author(pr.get("author", ""), pol.bot_author_patterns):
                continue
            if _conventional_type(pr.get("title", "")) in pol.bookkeeping_types:
                continue
            out.append(pr)
        return out

    def substantive_commits(
        self, policy: SubstancePolicy | None = None,
    ) -> list[dict[str, Any]]:
        """Notable commits that aren't already counted as a merged PR.

        The commits collector already allowlists feat/fix/refactor/perf/
        security prefixes, so bookkeeping is filtered upstream. What's
        left to do here is DEDUPE: this repo squash-merges, so a shipped
        PR lands on main as one commit carrying `(#N)`, and would
        otherwise be scored twice — once as a PR, once as its own commit.
        """
        pol = policy or SubstancePolicy()
        counted = {
            str(pr.get("number"))
            for pr in self.substantive_prs(pol)
            if pr.get("number") is not None
        }
        out: list[dict[str, Any]] = []
        for commit in self.notable_commits:
            refs = set(_PR_REF_RE.findall(commit.get("subject", "") or ""))
            if refs & counted:
                continue
            out.append(commit)
        return out

    def substance_score(self, policy: SubstancePolicy | None = None) -> float:
        """Weighted "how much real work happened today" score.

        Compared against ``policy.min_score`` by the job to decide whether
        the day earns a diary. See :class:`SubstancePolicy` for why posts
        and audit events carry zero weight by default.
        """
        pol = policy or SubstancePolicy()
        return (
            pol.weight_pr * len(self.substantive_prs(pol))
            + pol.weight_commit * len(self.substantive_commits(pol))
            + pol.weight_post * len(self.recent_posts)
            + pol.weight_audit * len(self.audit_resolved)
        )

    def substance_breakdown(
        self, policy: SubstancePolicy | None = None,
    ) -> dict[str, Any]:
        """Per-signal detail behind :meth:`substance_score`.

        Exists so a skip is explainable — the job puts this in its
        JobResult metrics and the operator notification, so "why did
        today skip?" is answerable without re-running the collectors.
        """
        pol = policy or SubstancePolicy()
        prs = self.substantive_prs(pol)
        commits = self.substantive_commits(pol)
        return {
            "score": self.substance_score(pol),
            "min_score": pol.min_score,
            "substantive_prs": len(prs),
            "substantive_commits": len(commits),
            "merged_prs_total": len(self.merged_prs),
            "notable_commits_total": len(self.notable_commits),
            "recent_posts": len(self.recent_posts),
            "audit_resolved": len(self.audit_resolved),
            "filtered_pr_titles": [
                pr.get("title", "") for pr in self.merged_prs if pr not in prs
            ][:10],
        }

    def headline(self) -> str:
        """Build a generic, count-based topic for the day.

        The topic is intentionally NOT derived from any individual PR or
        commit subject — embedding a single title invites two failure
        modes the writer has hit in production
        (Glad-Labs/poindexter#352, #353):

        1. Mid-identifier truncation. A long title like
           ``fix(cli): rank-batch sys#N markers + auto-load
           POINDEXTER_SECRET_KEY`` got sliced to
           ``...auto-load POINDEXTER_SE`` somewhere in the
           ``topic`` → ``pipeline_tasks`` → writer chain, and the
           writer hallucinated an explanation of ``POINDEXTER_SE`` as if
           it were a real env var.
        2. Topic-anchored fabrication. Even with a clean truncation, the
           writer tends to riff on the topic string semantically rather
           than reading the structured ``task_metadata.context_bundle``
           that has the actual PR titles, URLs, and authors. Removing
           the PR title from the topic forces the writer onto the bundle.

        The full PR / commit data is preserved in
        ``task_metadata.context_bundle`` (via :meth:`to_dict`); only the
        topic *summary* line changes.
        """
        date = self.date
        parts: list[str] = []
        if self.merged_prs:
            n = len(self.merged_prs)
            parts.append(f"{n} PR{'s' if n != 1 else ''}")
        if self.notable_commits:
            n = len(self.notable_commits)
            parts.append(f"{n} commit{'s' if n != 1 else ''}")
        if not parts and self.recent_posts:
            n = len(self.recent_posts)
            parts.append(f"{n} post{'s' if n != 1 else ''}")
        period = self.period_label()
        if parts:
            return f"{period} dev diary — {date} ({', '.join(parts)})"
        return f"{period} dev diary — {date}"

    def period_label(self) -> str:
        """Human period word for :meth:`headline`, from ``lookback_hours``.

        The window is the only honest source for this: the job's cron and its
        lookback are configured separately, so labelling off the cron would
        claim a coverage the bundle may not have. Thresholds are generous
        because an operator may nudge the window (a 26h daily run is still
        "Daily"); anything that isn't close to a day or a week says how many
        days it actually covers rather than rounding to a lie.
        """
        hours = int(self.lookback_hours or 24)
        if hours <= 36:
            return "Daily"
        if 144 <= hours <= 192:  # 6-8 days
            return "Weekly"
        days = max(1, round(hours / 24))
        return f"{days}-day"


# ---------------------------------------------------------------------------
# GitHub REST API collectors
# ---------------------------------------------------------------------------


def _build_gh_headers(
    gh_token: str | None, site_config: Any = None,
) -> dict[str, str]:
    """Build request headers for the GitHub REST API.

    Always sets ``Accept`` and ``X-GitHub-Api-Version``. Includes
    ``Authorization: Bearer <token>`` when a non-empty token is
    available; otherwise emits a debug log and lets the request fly
    unauthenticated (works for public repos at the lower rate limit).

    ``site_config``: optional ``SiteConfig`` threaded from
    ``gather_context`` so the ``User-Agent`` carries ``crawler_contact_url``
    when an operator sets it. The UA is built by the shared
    ``utils.crawler_ua.build_crawler_ua`` helper (single source of truth;
    contact-less by default — the OSS leak guard). GitHub requires a UA and
    accepts the ``Mozilla/5.0 (compatible; …)`` form.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": build_crawler_ua(site_config, product="PoindexterDevDiary"),
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
    site_config: Any = None,
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
    headers = _build_gh_headers(gh_token, site_config)

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


async def _collect_notable_commits(
    hours: int,
    repo: str,
    gh_token: str | None = None,
    client: httpx.AsyncClient | None = None,
    site_config: Any = None,
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
    headers = _build_gh_headers(gh_token, site_config)

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
        _emit_context_source_failed("brain_decisions", exc)
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
        _emit_context_source_failed("audit_log", exc)
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
        _emit_context_source_failed("posts", exc)
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
        _emit_context_source_failed("cost_logs", exc)
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
        _emit_context_source_failed("operator_notes", exc)
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
        _emit_context_source_failed("gh_token", exc)
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
        _emit_context_source_failed("gh_repo", exc)
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

        prs_task = _collect_merged_prs(
            hours_lookback, repo, gh_token, site_config=site_config,
        )
        commits_task = _collect_notable_commits(
            hours_lookback, repo, gh_token, site_config=site_config,
        )
        decisions_task = _collect_brain_decisions(pool, hours_lookback, confidence_floor)
        audit_task = _collect_audit_resolved(pool, hours_lookback)
        posts_task = _collect_recent_posts(pool, hours_lookback)
        cost_task = _collect_cost_summary(pool, hours_lookback)
        notes_task = _collect_operator_notes(pool, "dev_diary")

        import asyncio
        _gathered = await asyncio.gather(
            prs_task, commits_task, decisions_task, audit_task, posts_task,
            cost_task, notes_task,
        )
        prs: list[dict[str, Any]] = _gathered[0]  # type: ignore[assignment]
        commits: list[dict[str, Any]] = _gathered[1]  # type: ignore[assignment]
        decisions: list[dict[str, Any]] = _gathered[2]  # type: ignore[assignment]
        audit: list[dict[str, Any]] = _gathered[3]  # type: ignore[assignment]
        posts: list[dict[str, Any]] = _gathered[4]  # type: ignore[assignment]
        cost: dict[str, Any] = _gathered[5]  # type: ignore[assignment]
        notes: list[dict[str, Any]] = _gathered[6]  # type: ignore[assignment]

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
            lookback_hours=hours_lookback,
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
        # gh_repo is a DB setting (feedback_db_first_config): only an explicit
        # per-call override is honoured here; gather_context resolves the rest
        # of the chain (SiteConfig['gh_repo'] -> app_settings.gh_repo row ->
        # ctor arg -> default). No os.environ escape hatch.
        gh_repo = config.get("gh_repo") or None
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
