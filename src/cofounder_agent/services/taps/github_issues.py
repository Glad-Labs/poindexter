"""GitHubIssuesTap — ingest issues from the public + private GitHub repos.

Issues are where design decisions, incident post-mortems and rejected
approaches actually get written down. Embedding them makes that history
reachable from `search_memory` / the cofounder chat / the voice agent
instead of living only in a browser tab.

## Why this exists

``embeddings.source_table='issues'`` already held 112 rows — a one-off
backfill from the decommissioned Gitea, frozen since 2026-04-02 (126 days
by the time anyone noticed). There was no issues Tap on disk at all, so
nothing was keeping it current. The corpus-staleness panel added in
poindexter#989 is what surfaced it.

Those legacy rows keyed ``source_id`` on the **bare issue number**
(``185``), which cannot represent two repos — poindexter#185 and
glad-labs-stack#185 collide on the ``embeddings`` unique constraint, and
whichever embedded second would silently overwrite the first. This Tap
keys on ``github/{owner}/{repo}/issues/{number}`` instead. A migration
drops the legacy rows; they are superseded by this Tap's first run.

## Which repos — configured, never defaulted

``config.repos`` ships **empty**. Which trackers to ingest is per-install,
and a code default naming particular repos would make a fresh install
scrape a stranger's issue tracker. Unconfigured, the Tap logs a warning
and yields nothing.

Where a project files issues across more than one repo, list them all:
content-routed filing means the decision history is split, so ingesting
one of a pair loses half the reasoning. Private repos REQUIRE a token.

## Pull requests are excluded

GitHub's REST `/issues` endpoint returns PRs as issues — every PR carries
a ``pull_request`` key. Left unfiltered, roughly half the corpus would be
PR bodies duplicating commit messages already reachable via git. Filtered
out explicitly; ``pulls_skipped`` is logged per repo so the exclusion is
visible rather than assumed.

## No `since` watermark, on purpose

The obvious optimization is GitHub's ``since`` param to fetch only issues
updated since the last run. It is deliberately NOT used:

1. Listing is already cheap — 100 issues per request, so the whole
   ~4k-issue corpus is ~40 requests against a 5000/hour authenticated
   budget. The expensive thing would be per-issue comment fetches, which
   this Tap does not do (see below).
2. It would break zero-yield detection. A Tap that legitimately yields
   nothing on a quiet cycle is indistinguishable from one whose source
   went unreachable — exactly the failure mode
   ``services/taps/runner.py::is_zero_yield`` exists to catch. Yielding
   every issue every run keeps "zero documents" unambiguously a fault.

The runner's content-hash dedup means unchanged issues cost one hash
comparison and are reported as ``skipped``, not re-embedded — so there is
no embedding cost to re-yielding them.

## Comments are not ingested (yet)

Issue *discussion* is often where the reasoning lives, but comments cost
one request per issue (~4k/run vs ~40), and the body alone already covers
the problem statement and most resolutions. Deferred rather than
half-built; if added, gate it on a config flag and fetch only for issues
whose ``comments`` count is non-zero.

## Config (``plugin.tap.github_issues`` in ``app_settings``)

- ``enabled`` (default ``true``)
- ``interval_seconds`` (default ``21600`` — 6h; issue text is not
  fast-moving and this keeps well inside the API budget)
- ``config.repos`` — comma-separated ``owner/repo`` list. Empty by default;
  the Tap ingests nothing until set.
- ``config.state`` — ``all`` (default) / ``open`` / ``closed``. Closed
  issues are the most valuable half: that is where resolutions live.
- ``config.max_issues_per_repo`` (default ``0`` — unlimited).
- ``config.max_body_chars`` (default ``20000``) — truncate pathological
  bodies (dumped logs, stack traces) before the runner chunks them.

Auth: the ``gh_token`` **secret** row in app_settings. Public repos work
unauthenticated but at 60 requests/hour, which cannot complete a run — so
a missing token is a loud warning, not a silent degrade, and private repos
are skipped explicitly rather than 404-ing into a confusing empty result.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.tap import Document

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
_PER_PAGE = 100

# Hard ceiling on pagination so a misconfigured repo or an API change can
# never spin forever. 200 pages x 100 = 20k issues, far above either repo.
_MAX_PAGES = 200

# Empty by design. Which repos to ingest is per-install, and a code default
# naming anyone's repos would make a fresh install scrape a stranger's issue
# tracker. Unconfigured, the tap warns and ingests nothing (see ``extract``).
_DEFAULT_REPOS = ""
_DEFAULT_MAX_BODY_CHARS = 20000
_DEFAULT_TIMEOUT_S = 30.0


def _parse_repos(raw: Any) -> list[str]:
    """Parse the ``repos`` config into a validated ``owner/repo`` list."""
    if isinstance(raw, list):
        candidates = [str(r) for r in raw]
    else:
        candidates = str(raw or "").split(",")

    repos: list[str] = []
    for candidate in (c.strip() for c in candidates):
        if not candidate:
            continue
        if candidate.count("/") != 1 or candidate.startswith("/") or candidate.endswith("/"):
            # Fail loud on a malformed entry rather than silently skipping it
            # — a typo'd repo would otherwise read as "that repo has no
            # issues" (feedback_no_silent_defaults).
            logger.warning(
                "GitHubIssuesTap: ignoring malformed repo %r (want 'owner/repo')",
                candidate,
            )
            continue
        repos.append(candidate)
    return repos


def _build_source_id(repo: str, number: int) -> str:
    """``github/{owner}/{repo}/issues/{number}`` — unique across repos.

    The legacy Gitea-era rows used the bare issue number, which collides
    the moment a second repo is ingested.
    """
    return f"github/{repo}/issues/{number}"


def _render_issue(issue: dict[str, Any], repo: str, max_body_chars: int) -> str:
    """Flatten an issue into the text that gets embedded.

    Leads with repo + number + state + title so a retrieved chunk is
    self-identifying — a body fragment alone rarely says which issue it
    came from.
    """
    number = issue.get("number")
    title = (issue.get("title") or "").strip()
    state = issue.get("state") or "unknown"
    body = (issue.get("body") or "").strip()
    labels = ", ".join(
        lbl.get("name", "") for lbl in (issue.get("labels") or []) if isinstance(lbl, dict)
    )

    if len(body) > max_body_chars:
        body = body[:max_body_chars] + "\n[... truncated]"

    parts = [f"Issue {repo}#{number} ({state}): {title}"]
    if labels:
        parts.append(f"Labels: {labels}")
    if body:
        parts.append(body)
    return "\n".join(parts)


def _issue_metadata(issue: dict[str, Any], repo: str) -> dict[str, Any]:
    return {
        "repo": repo,
        "issue_number": issue.get("number"),
        "title": issue.get("title") or "",
        "state": issue.get("state") or "",
        "labels": [
            lbl.get("name", "")
            for lbl in (issue.get("labels") or [])
            if isinstance(lbl, dict)
        ],
        "url": issue.get("html_url") or "",
        "created_at": issue.get("created_at") or "",
        "updated_at": issue.get("updated_at") or "",
        "closed_at": issue.get("closed_at") or "",
        "comments": issue.get("comments", 0),
    }


async def _resolve_token(pool: Any, config: dict[str, Any]) -> str:
    """Read the ``gh_token`` secret. Config override wins (tests/overrides)."""
    override = config.get("gh_token")
    if override:
        return str(override)
    if pool is None:
        return ""
    try:
        from plugins.secrets import get_secret

        async with pool.acquire() as conn:
            return str(await get_secret(conn, "gh_token") or "")
    except Exception as e:  # noqa: BLE001 — a missing secret must not crash ingest
        logger.warning(
            "GitHubIssuesTap: could not read the gh_token secret (%s); "
            "continuing unauthenticated (60 req/hour — expect failures).",
            e,
        )
        return ""


async def _fetch_issue_pages(
    client: httpx.AsyncClient,
    repo: str,
    *,
    state: str,
    headers: dict[str, str],
) -> AsyncIterator[dict[str, Any]]:
    """Yield raw API items for one repo, following pagination.

    Yields everything the endpoint returns, pull requests included — the
    caller filters and applies any cap, so that the cap counts *issues*
    rather than raw API items (see ``extract``).
    """
    for page in range(1, _MAX_PAGES + 1):
        params = {
            "state": state,
            "per_page": _PER_PAGE,
            "page": page,
            # Stable ordering; without it GitHub's default (created desc)
            # still works, but pinning makes a truncated run reproducible.
            "sort": "created",
            "direction": "asc",
        }
        resp = await client.get(
            f"{_API_ROOT}/repos/{repo}/issues", params=params, headers=headers
        )
        if resp.status_code == 404:
            logger.warning(
                "GitHubIssuesTap: %s returned 404 — repo missing, renamed, or "
                "the token lacks access to it. Skipping.",
                repo,
            )
            return
        if resp.status_code == 401:
            logger.warning(
                "GitHubIssuesTap: %s returned 401 — the gh_token is invalid or "
                "expired. Skipping.",
                repo,
            )
            return
        if resp.status_code == 403:
            # Distinguish rate-limit exhaustion from a permissions problem;
            # they need completely different operator responses.
            remaining = resp.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                logger.warning(
                    "GitHubIssuesTap: %s hit the GitHub rate limit (resets at "
                    "%s). Partial run; the next cycle resumes.",
                    repo,
                    resp.headers.get("x-ratelimit-reset", "unknown"),
                )
            else:
                logger.warning(
                    "GitHubIssuesTap: %s returned 403 (not rate-limited) — the "
                    "token likely lacks scope for this repo. Skipping.",
                    repo,
                )
            return
        resp.raise_for_status()

        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            return

        for issue in batch:
            if not isinstance(issue, dict):
                continue
            yield issue

        if len(batch) < _PER_PAGE:
            return  # last page


class GitHubIssuesTap:
    """Ingest issues from every configured GitHub repo."""

    name = "github_issues"
    interval_seconds = 21600  # 6h

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        repos = _parse_repos(config.get("repos", _DEFAULT_REPOS))
        if not repos:
            logger.warning(
                "GitHubIssuesTap: no valid repos configured — ingesting nothing."
            )
            return

        state = str(config.get("state", "all") or "all")
        max_issues = int(config.get("max_issues_per_repo", 0) or 0)
        max_body_chars = int(
            config.get("max_body_chars", _DEFAULT_MAX_BODY_CHARS) or _DEFAULT_MAX_BODY_CHARS
        )
        timeout_s = float(config.get("timeout_seconds", _DEFAULT_TIMEOUT_S) or _DEFAULT_TIMEOUT_S)

        token = await _resolve_token(pool, config)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            # 60 req/hour cannot complete a full run, and a private repo will
            # 404 rather than 401 — which reads as "no issues" if unexplained.
            logger.warning(
                "GitHubIssuesTap: no gh_token available. Public repos are "
                "limited to 60 requests/hour and private repos will 404. Set "
                "the gh_token secret to ingest reliably."
            )

        total_emitted = 0
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            for repo in repos:
                emitted = 0
                pulls_skipped = 0
                try:
                    async for issue in _fetch_issue_pages(
                        client,
                        repo,
                        state=state,
                        headers=headers,
                    ):
                        # GitHub's /issues endpoint returns PRs too — they all
                        # carry a `pull_request` key. Roughly half the payload.
                        if "pull_request" in issue:
                            pulls_skipped += 1
                            continue

                        number = issue.get("number")
                        if number is None:
                            continue

                        # Cap on ISSUES emitted, not raw API items. Counting
                        # items lets a PR-heavy page exhaust the budget and
                        # emit zero — observed against the real repos, where
                        # a cap of 10 returned nothing at all for one of them
                        # because its oldest 10 items were all PRs. A tap
                        # yielding zero also trips the zero-yield finding, so
                        # this made a config knob look like an outage.
                        if max_issues and emitted >= max_issues:
                            logger.info(
                                "GitHubIssuesTap: %s hit max_issues_per_repo=%d",
                                repo,
                                max_issues,
                            )
                            break

                        text = _render_issue(issue, repo, max_body_chars)
                        if not text.strip():
                            continue

                        yield Document(
                            source_id=_build_source_id(repo, int(number)),
                            source_table="issues",
                            text=text,
                            metadata=_issue_metadata(issue, repo),
                            writer="github",
                        )
                        emitted += 1
                except httpx.HTTPError as e:
                    # One unreachable repo must not lose the other's issues.
                    logger.warning(
                        "GitHubIssuesTap: %s failed mid-fetch (%s); keeping the "
                        "%d issue(s) already yielded and continuing.",
                        repo,
                        e,
                        emitted,
                    )

                total_emitted += emitted
                logger.info(
                    "GitHubIssuesTap: %s emitted=%d pulls_skipped=%d",
                    repo,
                    emitted,
                    pulls_skipped,
                )

        logger.info(
            "GitHubIssuesTap: %d issue(s) across %d repo(s)", total_emitted, len(repos)
        )
